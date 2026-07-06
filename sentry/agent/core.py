"""Agent core. One turn = user text in, spoken-ready reply out (streamed live).

Model-agnostic tool protocol: the model must answer with a single JSON object:
  {"type":"reply","text":"..."}                       -> final answer
  {"type":"plan","goal":"...","steps":[{...},...]}    -> creates a task plan
  {"type":"tool","name":"...","args":{...}}           -> tool call; result fed back

Works with any model (small Ollama locals included) — no native function
calling needed, just JSON output. Replies stream token-by-token via ReplyStream.
"""
import json
import os
import re

from ..llm.base import LLMProvider
from ..github import RepoInspector, find_github_url
from ..history import ChatHistory
from ..checkpoints import CheckpointManager
from ..templates import TemplateManager
from ..executor import StepExecutor
from ..memory import Memory
from ..metrics import Metrics
from ..project import detect
from ..rag import DocIndex
from ..repo import RepoInspector, find_repo_url
from ..research import Researcher, should_research
from ..workspace import Workspaces
from .planner import TaskManager
from .stream import ReplyStream
from .tools import ToolBox

SYSTEM_TMPL = """You are SENTRY, a local voice-driven assistant and task copilot running on the user's machine. {persona}The user is a computer engineering student. Be technical and direct.

You MUST respond with exactly one JSON object and NOTHING before or after it — no explanation, no prose, no markdown, no ```json fences. The very first character of your response must be a brace. Formats:
1. Answer / explain / converse: {{"type":"reply","text":"your answer"}}
   Use this for questions, explanations, discussion, and help with the current task step.
   For concept questions ("what is X", "explain Y", "how does Z work") give a real,
   complete explanation — a solid paragraph or two, like a good teacher. Do NOT turn
   knowledge questions into plans. Short confirmations can be one line.
2. Create a step-by-step plan ONLY when the user states a concrete multi-step goal
   they want to accomplish on this machine (build/deploy/set up/install something):
   {{"type":"plan","goal":"short goal","steps":[{{"title":"short","detail":"one concrete instruction with real commands"}}]}}
   Use 3-8 steps. Never re-plan an active task unless the user asks to start over.
3. Use a tool when you need facts from the machine, the project, or the web:
   {{"type":"tool","name":"tool_name","args":{{...}}}}

Available tools:
{tools}

Project context: {project}
Task state: {task}
{lessons}
If an ACTIVE GITHUB REPO appears in the project context: the user pasted that repo.
Answer questions about it from its README/configs. When asked how to run/install/
use it, give exact step-by-step terminal commands (clone, cd, install deps from
its real requirements/package files, run its real entrypoint) — as a plan if they
say task:, otherwise as a numbered reply. Prefer the repo's own documented commands.
How to behave with an active task:
- "what's next" / "where am I" -> restate the current step from Task state. Do not re-plan.
- "help me with this step" / "do it" / "how do I do this" -> actively help EXECUTE the
  current step: give the exact commands to paste, or use tools (run_shell etc.) to do
  it for them, then report the result and what to check.
- If they ask an unrelated question mid-task (e.g. "what is an LLM?"), just answer it
  normally with a reply — the task stays active, no need to mention it.
- If a step fails, diagnose from the error, suggest the fix, and offer to run it.
Prefer checking reality with tools (git_status, docker_ps, search_docs, versions) over guessing. Never invent command output."""


def extract_json(text: str):
    """Tolerant JSON extraction — local models often wrap JSON in prose/fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class Agent:
    # commands handled locally, no LLM round-trip
    LOCAL = {
        "next": "advance", "done": "advance", "step done": "advance",
        "status": "full", "show plan": "full", "plan": "full",
        "where am i": "brief", "what's next": "brief", "whats next": "brief",
        "drop task": "drop", "cancel task": "drop",
    }

    def __init__(self, cfg: dict, llm: LLMProvider, confirm_fn=None, log_fn=None):
        self.cfg = cfg
        self.llm = llm
        self.log = log_fn or (lambda s: None)
        self._confirm = confirm_fn
        # global (cross-workspace) services
        self.ws = Workspaces(cfg["memory"]["dir"], cfg["agent"]["workdir"])
        self.metrics = Metrics(cfg["memory"]["dir"])
        def _emb(texts):
            return self.llm.embed(texts)
        self.chat_history = ChatHistory(
            __import__("os").path.join(cfg["memory"]["dir"], "chats.db"),
            embed_fn=_emb if hasattr(llm, "embed") else None)
        self.researcher = Researcher()
        self.repo_inspector = RepoInspector(cfg["memory"]["dir"])
        self.repo_context = ""  # digest of the most recently pasted GitHub repo
        self.history: list[dict] = []
        self._lessons = ""
        self._last_kind = "reply"
        self._load_workspace()

    def _load_workspace(self):
        """(Re)build all per-workspace context: workdir, plans, memory, index."""
        import copy
        name = self.ws.current
        wcfg = copy.deepcopy(self.cfg)
        wcfg["agent"]["workdir"] = self.ws.workdir()
        wcfg["memory"]["dir"] = self.ws.dir_for()
        self.wcfg = wcfg
        self.memory = Memory(wcfg["memory"]["dir"])
        self.doc_index = DocIndex(wcfg, llm=self.llm)
        self.tools = ToolBox(wcfg, self._confirm, doc_index=self.doc_index,
                             history=self.chat_history, researcher=self.researcher,
                             repo_inspector=self.repo_inspector)
        def _finished(*a, **k):
            self.metrics.bump("tasks_completed")
            return self.memory.record(*a, **k)
        self.tasks = TaskManager(wcfg["memory"]["dir"], on_finish=_finished)
        self.executor = StepExecutor(wcfg["agent"]["workdir"],
                                     self._confirm or (lambda m: True))
        self.checkpoints = CheckpointManager(wcfg["agent"]["workdir"],
                                             wcfg["memory"]["dir"])
        self.templates = TemplateManager(self.cfg["memory"]["dir"])  # global, cross-workspace
        self.project = detect(wcfg["agent"]["workdir"])
        self._last_plan = None  # (goal, steps) of the most recent completed/active plan
        self.history = []  # fresh conversation context per workspace

    PERSONAS = {
        "jarvis": ("Speak like a composed British butler-engineer: unflappable, precise, "
                   "dry wit, occasionally addressing the user as 'sir'. Never theatrical. "),
        "plain": "",
    }

    PAST_HINTS = ("what did we", "last time", "previously", "remember when",
                  "did we", "have we", "earlier we", "what have we")

    def _system(self) -> str:
        lessons = f"Lessons from past tasks: {self._lessons}\n" if self._lessons else ""
        persona = self.PERSONAS.get(self.cfg["agent"].get("persona", "jarvis"), "")
        # inject a trimmed digest: full version stays stored, but only ~3.5k chars
        # go into the prompt — prompt prefill is the dominant latency on local models
        repo = f"\n{self.repo_context[:3500]}\n" if self.repo_context else ""
        return SYSTEM_TMPL.format(
            tools=self.tools.spec(),
            project=f"[workspace: {self.ws.current}] " + self.project["summary"] + repo,
            task=self.tasks.context_for_llm(),
            lessons=lessons,
            persona=persona,
        )

    # ------------------------------------------------------------------
    def build_index(self) -> str:
        return self.doc_index.build()

    def switch_workspace(self, name: str, workdir: str = None) -> str:
        msg, _created = self.ws.switch(name, workdir)
        if "doesn't exist" not in msg:
            self._load_workspace()
        return msg

    def handle(self, user_text: str, on_token=None) -> str:
        import time as _t
        _t0 = _t.time()
        reply = self._handle_inner(user_text, on_token)
        self.metrics.bump("turns")
        self.metrics.timing((_t.time() - _t0) * 1000)
        try:
            self.chat_history.record(user_text, reply, self.ws.current, self._last_kind)
        except Exception:
            pass  # history is best-effort; never break a turn over it
        return reply

    def _handle_inner(self, user_text: str, on_token=None) -> str:
        self._last_kind = "reply"
        cmd = user_text.strip().lower().rstrip(".!?")
        if cmd in self.LOCAL:
            return getattr(self.tasks, self.LOCAL[cmd])()
        if cmd == "index":
            return self.build_index()
        if cmd == "memory":
            return self.memory.stats()
        if cmd in ("stats", "metrics"):
            return self.metrics.summary()
        if cmd == "workspaces":
            return self.ws.list()
        if cmd in ("undo", "rollback", "revert"):
            self._last_kind = "tool"
            self.metrics.bump("undos")
            return self.checkpoints.undo()
        if cmd == "templates":
            return self.templates.list()
        mt = re.match(r"^save (?:plan )?as ([a-zA-Z0-9\-]+)$", cmd)
        if mt:
            t = self.tasks.state.get("active") or self._last_plan
            if not t:
                return "No plan to save — complete or create a task first."
            svcs = list(self.project.get("config", {}).get("compose", {})
                        .get("services", {}).keys())
            return self.templates.save(mt.group(1), t["goal"], t["steps"],
                                       self.ws.current, self.ws.workdir(), svcs)
        mt = re.match(r"^(?:use template|task:.*using)\s+([a-zA-Z0-9\-]+)$", cmd)
        if mt:
            tpl = self.templates.load(mt.group(1))
            if tpl is None:
                return f"No template '{mt.group(1)}'. Say 'templates' to list them."
            svcs = list(self.project.get("config", {}).get("compose", {})
                        .get("services", {}).keys())
            steps = self.templates.customize(tpl, self.ws.workdir(), svcs)
            self.templates.mark_used(mt.group(1))
            self.metrics.bump("templates_used")
            self._last_kind = "plan"
            brief = self.tasks.start(tpl["goal"], steps)
            return (f"Loaded template '{mt.group(1)}' (customized for this project).\n"
                    + brief + "\nReview each step before 'do it'.")
        mt = re.match(r"^delete template ([a-zA-Z0-9\-]+)$", cmd)
        if mt:
            return self.templates.delete(mt.group(1))

        mc = re.match(r"^clone\s+(\S+)$", user_text.strip(), re.I)
        if mc:
            ref = find_github_url(mc.group(1))
            if not ref:
                return "Give me a github.com/<owner>/<repo> URL to clone."
            import subprocess
            base = os.path.expanduser("~/sentry-repos")
            os.makedirs(base, exist_ok=True)
            dest = os.path.join(base, ref["repo"])
            if not os.path.isdir(dest):
                if not (self._confirm or (lambda m: True))(
                        f"About to run:\n  $ git clone {ref['url']} {dest}"):
                    return "Clone declined."
                p = subprocess.run(["git", "clone", "--depth", "1", ref["url"], dest],
                                   capture_output=True, text=True, timeout=300)
                if p.returncode != 0:
                    return f"Clone failed: {p.stderr[:300]}"
            self._last_kind = "tool"
            msg = self.switch_workspace(ref["repo"], dest)
            return (f"Cloned {ref['owner']}/{ref['repo']} → {dest}\n{msg}\n"
                    "Say 'index' to build its search index, or 'task: <goal>' to start.")

        m = re.match(r"^(?:workspace:\s*|switch to\s+)(\S+)(?:\s+(\S+))?$",
                     user_text.strip(), re.I)
        if m:
            return self.switch_workspace(m.group(1), m.group(2))

        # ---- execute the current step ("do it" / "execute this step" / "run it")
        if cmd in ("do it", "execute", "execute this step", "run it", "run this step",
                   "execute step", "run this"):
            t = self.tasks.state.get("active")
            if not t:
                return "No active task — give me a goal first (task: <goal>)."
            i = t["current"]
            step = t["steps"][i]
            key = f"{t['goal']}#{i}"
            pre = self.checkpoints.prepare(t["goal"], i)
            res = self.executor.execute_step(key, step["detail"])
            self._last_kind = "tool"

            if res["status"] in ("no_command", "declined"):
                return res["output"]

            if res["status"] == "ok":
                self.metrics.bump("steps_executed")
                self._last_plan = {"goal": t["goal"], "steps": t["steps"]}
                cp_msg = self.checkpoints.create(t["goal"], i, step["detail"],
                                                 command=res["command"], pre=pre)
                advance_msg = self.tasks.advance()
                return (f"✔ Executed: {res['command']}\n"
                        f"{res['output'][:800]}\n({cp_msg})\n\n{advance_msg}")

            self.metrics.bump("exec_failures") if res["status"] in ("fail","give_up") else None
            if res["status"] == "give_up":
                return (f"✖ '{res['command']}' has now failed {res['fails']} times "
                        f"(exit {res['exit']}).\n{res['output'][:600]}\n\n"
                        "Options: say 'next' to skip this step, 'drop task' to abandon, "
                        "or describe what you changed and I'll retry.")

            # single failure -> hand the output to the model to diagnose
            self.history.append({"role": "user", "content":
                f"TOOL RESULT (execute_step, exit {res['exit']}, attempt {res['fails']}):\n"
                f"$ {res['command']}\n{res['output'][:2500]}\n\n"
                "Diagnose this failure briefly and give the exact fix. Do not re-plan."})
            user_text = None  # already appended the diagnostic request

        if user_text is not None:
            # GitHub URL pasted? Fetch and hold the repo as active context.
            gh = find_repo_url(user_text)
            if gh:
                self.log(f"[repo] inspecting {gh[0]}/{gh[1]}")
                self.metrics.bump("repos_inspected")
                try:
                    self.repo_context = self.repo_inspector.inspect(*gh)
                except Exception as e:
                    self.repo_context = ""
                    self.history.append({"role": "user", "content": user_text})
                    return (f"Couldn't fetch that repo ({type(e).__name__}: {e}). "
                            "Check the URL or your internet connection.")
                if "not found" in self.repo_context[:80]:
                    msg = self.repo_context
                    self.repo_context = ""
                    return msg

            # recall lessons before the model plans anything
            self._lessons = self.memory.recall(user_text)
            self.history.append({"role": "user", "content": user_text})

            low = user_text.lower()
            # auto-context: past-conversation questions get history injected
            if any(h in low for h in self.PAST_HINTS):
                found = self.chat_history.search(user_text, k=5)
                self.log("[auto] injected chat-history search")
                self.history.append(
                    {"role": "user", "content": f"TOOL RESULT (search_history):\n{found}"})
            # auto-context: research-worthy questions get live web info injected
            elif should_research(user_text):
                self.log("[auto] web research triggered")
                self.metrics.bump("researches")
                found = self.researcher.research(user_text)
                self.history.append(
                    {"role": "user", "content": f"TOOL RESULT (research):\n{found}"})

        self.history = self.history[-10:]  # tighter context = faster prefill

        for _ in range(self.cfg["agent"]["max_tool_rounds"]):
            rs = ReplyStream(on_token or (lambda t: None))
            for tok in self.llm.chat_stream(self._system(), self.history):
                rs.feed(tok)
            raw = rs.raw

            obj = extract_json(raw)

            # If the model emitted a plan or tool call, that takes priority over
            # any prose it streamed first (small models often narrate, then act).
            # We only trust streamed text when there's NO actionable JSON.
            if obj is not None and obj.get("type") in ("plan", "tool"):
                kind = obj.get("type")
                if kind == "plan":
                    self._last_kind = "plan"
                    self.metrics.bump("plans_created")
                    goal = obj.get("goal", "task")
                    steps = obj.get("steps", [])
                    if not steps:
                        msg = "The model produced an empty plan — try rephrasing the task."
                        if on_token:
                            on_token("\n" + msg)
                        return msg
                    brief = self.tasks.start(goal, steps)
                    self._last_plan = {"goal": goal, "steps": self.tasks.state["active"]["steps"]}
                    self.history.append(
                        {"role": "assistant", "content": f"Planned: {goal} ({len(steps)} steps)"})
                    extra = f"\n💡 {self._lessons}" if self._lessons else ""
                    out = brief + extra + "\nSay 'next' when a step is done, 'plan' to see all steps."
                    if on_token:
                        on_token("\n\n" + out)  # append below whatever prose streamed
                    return out
                # tool
                self._last_kind = "tool"
                name, args = obj.get("name", ""), obj.get("args", {})
                self.log(f"[tool] {name}({json.dumps(args)[:120]})")
                result = self.tools.call(name, args)
                self.history.append({"role": "assistant", "content": raw})
                self.history.append(
                    {"role": "user", "content": f"TOOL RESULT ({name}):\n{result}"})
                continue

            if rs.streamed:  # plain reply, already shown live token-by-token
                text = rs.streamed_text.strip() or "(empty reply)"
                self.history.append({"role": "assistant", "content": text})
                return text

            if obj is not None and obj.get("type") == "reply":
                text = obj.get("text", "").strip() or "(empty reply)"
                self.history.append({"role": "assistant", "content": text})
                if on_token:
                    on_token(text)
                return text

            # no JSON at all: treat raw as a plain reply
            self.history.append({"role": "assistant", "content": raw})
            if on_token and not rs.streamed:
                on_token(raw.strip())
            return raw.strip()

        return "Hit the tool-call limit for one turn — ask me to continue."
