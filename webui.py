#!/usr/bin/env python3
"""SENTRY web UI — run `python webui.py` then open http://localhost:7700

Same brain as the terminal, but in the browser:
  - streaming replies (SSE)
  - live task plan sidebar with progress
  - tool-call log
  - Approve / Deny buttons for shell commands (replaces the [y/N] prompt)
  - browser voice: mic input (Chrome/Edge) + spoken replies, no extra deps
"""
import argparse
import json
import os
import queue
import threading

from flask import Flask, Response, jsonify, request, send_from_directory

from sentry.config import load_config
from sentry.llm.base import get_provider
from sentry.agent.core import Agent

app = Flask(__name__, static_folder=None)
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# ---- single-user session state --------------------------------------------
agent: Agent = None
busy = threading.Lock()          # one turn at a time
current_q: queue.Queue = None    # event stream for the in-flight turn

_confirm_event = threading.Event()
_confirm_answer = {"ok": False}


def web_confirm(msg: str) -> bool:
    """Called from the agent thread when a tool wants permission.
    Pushes a confirm event to the browser and blocks until the user clicks."""
    if current_q is None:
        return False  # no browser attached — refuse rather than run blind
    _confirm_event.clear()
    current_q.put({"type": "confirm", "message": msg})
    ok = _confirm_event.wait(timeout=300)  # 5 min to decide, else deny
    return _confirm_answer["ok"] if ok else False


def web_log(msg: str):
    if current_q is not None:
        current_q.put({"type": "tool", "text": msg})


def plan_state() -> dict:
    t = agent.tasks.state.get("active")
    if not t:
        return {"active": False}
    return {
        "active": True,
        "goal": t["goal"],
        "current": t["current"],
        "steps": [{"title": s["title"], "detail": s["detail"], "done": s["done"]}
                  for s in t["steps"]],
    }


# ---- routes -----------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/state")
def state():
    return jsonify({
        "brain": agent.llm.name(),
        "project": agent.project["summary"],
        "plan": plan_state(),
        "memory": agent.memory.stats(),
        "workspaces": agent.ws.as_dict(),
    })


@app.route("/api/workspace", methods=["POST"])
def workspace():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    msg = agent.switch_workspace(name, data.get("workdir"))
    return jsonify({"message": msg, "workspaces": agent.ws.as_dict(),
                    "project": agent.project["summary"], "plan": plan_state()})


@app.route("/api/templates")
def templates():
    return jsonify({"text": agent.templates.list()})


@app.route("/api/undo", methods=["POST"])
def undo():
    return jsonify({"message": agent.checkpoints.undo(), "plan": plan_state()})


@app.route("/api/metrics")
def metrics():
    return jsonify(agent.metrics.as_dict() | {"text": agent.metrics.summary()})


@app.route("/api/history")
def history():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})
    return jsonify({"results": agent.chat_history.search_rows(q, k=20)})


@app.route("/api/confirm", methods=["POST"])
def confirm():
    _confirm_answer["ok"] = bool(request.json.get("approve"))
    _confirm_event.set()
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    global current_q
    text = (request.json or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "empty message"}), 400
    if not busy.acquire(blocking=False):
        return jsonify({"error": "Sentry is mid-turn — wait for it to finish."}), 409

    q = queue.Queue()
    current_q = q

    def run():
        global current_q
        try:
            reply = agent.handle(text, on_token=lambda t: q.put({"type": "token", "text": t}))
            q.put({"type": "final", "text": reply, "plan": plan_state()})
        except Exception as e:
            q.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)
            current_q = None
            busy.release()

    threading.Thread(target=run, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def main():
    global agent
    ap = argparse.ArgumentParser(description="SENTRY web UI")
    ap.add_argument("--port", type=int, default=7700)
    ap.add_argument("--provider", help="override llm provider (ollama|anthropic)")
    ap.add_argument("--model", help="override model name")
    args = ap.parse_args()

    cfg = load_config()
    if args.provider:
        cfg["llm"]["provider"] = args.provider
    if args.model:
        cfg["llm"]["model"] = args.model

    llm = get_provider(cfg)
    if hasattr(llm, "preflight"):
        notice = llm.preflight()   # raises with a clear message if Ollama is down
        if notice:
            print(f"  {notice}")

    agent = Agent(cfg, llm, confirm_fn=web_confirm, log_fn=web_log)
    print(f"\n  ███ SENTRY web UI — http://localhost:{args.port}")
    print(f"  brain: {llm.name()}   workdir: {cfg['agent']['workdir']}\n")
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
