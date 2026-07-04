# SENTRY

**A local, voice-driven agentic assistant for developer automation.**
Plans multi-step tasks, executes them with approval gates, rolls back failures
atomically, and reuses proven workflows across projects — all running on your
own machine with a local LLM.

Built and tested on an M2 MacBook Air (8 GB RAM) with Ollama + qwen2.5:7b.
No cloud required. Optionally swaps in the Anthropic API for a stronger brain
with zero code changes.

---

## Demo workflow

```
you    > workspace: levee ~/dev/levee
sentry > Switched to workspace 'levee' (workdir: /Users/anurag/dev/levee)

you    > task: build and run this project
sentry > TASK: Build and run Levee  [1/4]
         NOW → Build image: docker build -t levee:latest .
         (plan built from the project's ACTUAL Dockerfile and compose services)

you    > do it
sentry > ⚠ About to execute: docker build -t levee:latest .   [Approve] [Deny]
         ✔ Executed. (checkpoint saved (git))
         NOW → Start services: docker-compose up -d web db

you    > undo                        # something looks wrong? one word.
sentry > Reverted step 1 (git revert staged, not committed — review with git diff)

you    > save plan as levee-deploy   # it worked — keep it forever
sentry > Template 'levee-deploy' saved (4 steps)
```

<!-- TODO: add screenshot: docs/screenshot-webui.png (web UI mid-deploy) -->

---

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │                  INTERFACES                │
                        │   main.py (REPL / --voice / --wake)        │
                        │   webui.py (Flask + SSE) → web/index.html  │
                        └─────────────────────┬──────────────────────┘
                                              │ user text / voice
                                              ▼
       ┌───────────────────────────────  AGENT CORE  ──────────────────────────────┐
       │  agent/core.py — one JSON protocol, any model                             │
       │                                                                           │
       │   {"type":"reply"|"plan"|"tool", ...}  ← streamed & parsed live           │
       │                                                                           │
       │   local intents (no LLM round-trip):                                      │
       │   task:/next/plan/do it/undo/workspace:/templates/save as/use template    │
       └───┬─────────┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
           │         │          │          │          │          │
           ▼         ▼          ▼          ▼          ▼          ▼
      TaskManager Executor Checkpoints Templates  ToolBox     Memory/RAG
      (planner.py)(executor)(checkpoints)(templates)(tools.py) (memory, rag,
       step state  extract   git commit/  save/load  shell*,git, history.py)
       + progress  command,  pre-exec     customize  docker,     lessons from
       persisted   confirm,  file snap-   paths &    search_docs past tasks +
       per         run, 3-   shots, undo  flag       research†   SQLite chat
       workspace   strike                 services   fetch_url   history

       ┌──────────────────────  LLM PROVIDERS (llm/)  ─────────────────────┐
       │  ollama.py (local, auto-selects installed model) │ anthropic.py   │
       │  echo.py (offline testing)                       │ (API, streamed)│
       └──────────────────────────────────────────────────┴────────────────┘

       * shell commands require user approval; destructive ones are double-gated
       † research = DuckDuckGo search + page fetch + source-cited digest
```

Per-workspace isolation: every workspace (project) gets its own task state,
memory, doc index, and checkpoints under `data/workspaces/<name>/`.
Templates and chat history are global, shared across workspaces.

---

## Why this design

**Why a JSON protocol instead of native function-calling?**
Small local models (3B–7B) don't reliably support native tool-calling APIs, and
every provider does it differently. A plain-text contract — *respond with exactly
one JSON object: reply, plan, or tool* — works identically on qwen2.5:3b, Claude,
or anything in between. The streaming parser extracts reply text live from the
JSON wrapper, so you still get token-by-token output. Swapping brains is a
config change, not a rewrite.

**Why pre-execution snapshots?**
The first implementation snapshotted files *after* a step succeeded — which meant
the "backup" contained the already-modified files, and undo restored the wrong
state. Caught by a test, fixed by design: for non-git projects, key files are
snapshotted *before* the command runs, so `undo` restores the true prior state.
For git repos each step becomes a `[sentry-checkpoint]` commit and undo is a
staged `git revert` — staged, not committed, so you review before accepting.

**Why atomic checkpoints per step (not per task)?**
Deployments fail in the middle. Task-level rollback throws away the four steps
that worked to undo the one that didn't. Step-level checkpoints make failure
cheap: try, fail, `undo`, fix, `do it` again — iteration without fear.

**Why approval gates if the point is automation?**
The executor extracts the command and shows it before running (destructive
patterns like `rm -rf` are double-gated). An agent running shell commands on
your machine should earn trust step by step. After 3 failures on the same step
it stops retrying and offers skip/re-plan — no infinite loops, no burned tokens.

**Why templates flag mismatched services instead of auto-renaming them?**
When a template written for services `web, db` loads into a project with
`api, postgres`, guessing the mapping could `docker-compose up` the wrong
container. Sentry rewrites paths automatically (safe) but *flags* unknown
service names inline with what's actually available (honest), leaving the
one-word edit to a human.

**Why local-first?**
Cost (free), privacy (code never leaves the machine), and honesty about the
tradeoff: a 7B model plans and executes well but explains less deeply than a
frontier model — so the provider is pluggable.

---

## Features

- **Task planning** — `task: <goal>` produces a step-by-step plan built from the
  project's *real* configs: Dockerfile base image, compose service names,
  Makefile targets, npm scripts, go.mod (config_parser.py)
- **Step execution** — `do it` extracts the command, asks approval, runs it,
  auto-advances on success, hands failures to the LLM for diagnosis
- **Rollback** — `undo` reverts the last executed step (git revert / file restore)
- **Templates** — `save plan as levee-deploy`, then `use template levee-deploy`
  in any workspace; paths auto-rewritten, usage tracked
- **Multi-workspace** — `workspace: levee ~/dev/levee` switches projects with
  isolated plans, memory, and index
- **Memory** — cross-session task outcomes ("last time this failed because…")
  plus full SQLite chat history, searchable in chat and in the web UI
- **RAG** — `index` builds a local vector index of the project (Ollama
  embeddings, TF-IDF fallback); `search_docs` answers from your own code
- **Web research** — "best practices for X" triggers live search + fetch with
  numbered source citations
- **GitHub repo understanding** — paste ANY public repo URL and Sentry fetches
  its README, file tree, and key configs via the GitHub API; ask "how do I run
  it" and get exact step-by-step commands from the repo's own docs. Digests are
  cached 24h; set GITHUB_TOKEN for a 5000/hr API limit (60/hr without)
- **Voice** — push-to-talk (`--voice`), hands-free wake word "hey jarvis"
  (`--wake`, openWakeWord), JARVIS-style British TTS (Kokoro bm_george)
- **Web UI** — text-generation-webui-inspired dark interface: streaming chat,
  Execute/Undo/Template buttons, workspace picker, history search, approval modal

## Quick start

```bash
# 1. brain (once)
brew install ollama && ollama pull qwen2.5:7b
ollama serve                        # keep running in its own terminal

# 2. sentry
cd sentry
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python webui.py                     # → http://localhost:7700
# or: python main.py                # terminal REPL

# optional voice
pip install faster-whisper sounddevice numpy kokoro-onnx soundfile
pip install openwakeword onnxruntime     # wake word
python main.py --wake                     # say "hey jarvis"

# optional stronger brain
export ANTHROPIC_API_KEY=sk-ant-...
python webui.py --provider anthropic
```

## Command reference

| Command | Effect |
|---|---|
| `task: <goal>` | plan from the project's real configs |
| `do it` / Execute button | run current step (approval-gated, checkpointed) |
| `next` / `plan` / `drop task` | advance / view / abandon |
| `undo` | revert last executed step |
| `save plan as <name>` / `use template <name>` / `templates` | template workflow |
| `workspace: <name> [path]` / `workspaces` | project switching |
| `index` / `memory` | build RAG index / memory stats |
| `what did we do about <x>?` | auto-searches chat history |
| `best practices for <x>` | auto web research with citations |
| paste a github.com URL | fetch + understand any public repo; then ask anything about it |

## Project structure

```
sentry/
├── main.py / webui.py / web/     interfaces (REPL, voice, Flask+SSE UI)
├── config.yaml                    provider, model, persona, workdir
└── sentry/
    ├── agent/    core.py (protocol+orchestration), planner, tools, stream
    ├── llm/      ollama, anthropic, echo (one ABC)
    ├── audio/    stt (faster-whisper), tts (Kokoro), wake (openWakeWord)
    ├── executor.py · checkpoints.py · templates.py · workspace.py
    ├── config_parser.py · project.py · rag.py · memory.py · history.py
    ├── research.py
    └── repo.py
```

## Honest limitations

- Plan/explanation quality tracks the local model; 7B is good, not frontier.
- Web research parsing targets DuckDuckGo Lite's markup; if DDG changes it,
  the tool degrades gracefully to "no usable results."
- Command extraction from steps is heuristic — prose-only steps correctly
  report "no runnable command" rather than guessing.
- Docker image-tag checkpoints exist but are the least-tested path (no Docker
  in the CI sandbox); git and file checkpoints are fully tested.
