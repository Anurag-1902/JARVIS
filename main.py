#!/usr/bin/env python3
"""SENTRY — local voice-driven agentic assistant.

Usage:
  python main.py                # text mode (works everywhere, no extra deps)
  python main.py --voice        # push-to-talk voice mode (needs voice deps)
  python main.py --provider anthropic   # override config
"""
import argparse
import sys

from sentry.config import load_config
from sentry.llm.base import get_provider
from sentry.agent.core import Agent

BANNER = r"""
  ███ SENTRY v0.2 — local agentic command center
  task: <goal>   -> step-by-step plan with directions (project-aware)
  next / done    -> advance the plan, hear the next step
  plan           -> show all steps      what's next -> current step
  index          -> build vector index of project docs/code
  memory         -> what sentry has learned from past tasks
  drop task      -> abandon plan        quit        -> exit
"""


def confirm(msg: str) -> bool:
    ans = input(f"\n⚠  {msg}\n   [y/N] > ").strip().lower()
    return ans in ("y", "yes")


def main():
    ap = argparse.ArgumentParser(description="SENTRY local assistant")
    ap.add_argument("--voice", action="store_true", help="enable push-to-talk voice mode")
    ap.add_argument("--wake", action="store_true",
                    help="hands-free: say 'hey jarvis' to activate (implies --voice)")
    ap.add_argument("--provider", help="override llm provider (ollama|anthropic)")
    ap.add_argument("--model", help="override model name")
    args = ap.parse_args()

    cfg = load_config()
    if args.provider:
        cfg["llm"]["provider"] = args.provider
    if args.model:
        cfg["llm"]["model"] = args.model

    llm = get_provider(cfg)

    # preflight: verify the brain is actually reachable before entering the REPL
    if hasattr(llm, "preflight"):
        try:
            notice = llm.preflight()
            if notice:
                print(f"  {notice}")
        except RuntimeError as e:
            print(f"\n✖ {e}\n")
            sys.exit(1)

    agent = Agent(cfg, llm, confirm_fn=confirm, log_fn=lambda s: print(f"  {s}"))

    print(BANNER)
    print(f"  brain: {llm.name()}   voice: {'on' if args.voice else 'off (text mode)'}\n")

    stt = tts = None
    if args.voice or args.wake:
        from sentry.audio.stt import STT
        from sentry.audio.tts import TTS
        stt, tts = STT(cfg), TTS(cfg)
        print("  Voice ready." + ("" if args.wake else " Press ENTER to talk, or type instead.") + "\n")

    if args.wake:
        from sentry.audio.wake import WakeListener
        wake = WakeListener(cfg, stt)
        print(f"  Hands-free mode ({wake.backend}). Say 'hey jarvis'. Ctrl+C to quit.\n")
        while True:
            try:
                wake.wait()
                print("● wake word detected")
                tts.speak("Yes sir?")
                text = stt.listen()
                if not text:
                    print("  (heard nothing)")
                    continue
                print(f"you (voice) > {text}")
                reply = agent.handle(text)
                print(f"\nsentry > {reply}\n")
                tts.speak(reply.splitlines()[0] if len(reply) > 400 else reply)
            except KeyboardInterrupt:
                print("\nSentry offline.")
                return
            except RuntimeError as e:
                print(f"\n✖ {e}\n")
        return

    while True:
        try:
            typed = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSentry offline.")
            break

        if typed.lower() in ("quit", "exit"):
            print("Sentry offline.")
            break

        if args.voice and typed == "":
            text = stt.listen()
            if not text:
                print("  (heard nothing)")
                continue
            print(f"you (voice) > {text}")
        else:
            text = typed
        if not text:
            continue

        # stream reply text live as tokens arrive
        streamed = {"started": False}

        def on_token(t):
            if not streamed["started"]:
                print("\nsentry > ", end="", flush=True)
                streamed["started"] = True
            print(t, end="", flush=True)

        try:
            reply = agent.handle(text, on_token=on_token)
        except RuntimeError as e:
            print(f"\n✖ {e}\n")
            continue
        except Exception as e:
            print(f"\n✖ {type(e).__name__}: {e}\n")
            continue

        if streamed["started"]:
            print("\n")           # streamed replies: just close the line
        else:
            print(f"\nsentry > {reply}\n")   # plans/status: printed whole
        if tts:
            tts.speak(reply.splitlines()[0] if len(reply) > 400 else reply)


if __name__ == "__main__":
    main()
