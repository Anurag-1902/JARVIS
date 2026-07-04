"""Task planner. Persists the active plan to disk and tracks progress.

The LLM emits a plan (goal + steps). This class stores it; the user advances
with "next" / "done", asks "where am I", or says "drop the task".
"""
import json
import os
import time


class TaskManager:
    def __init__(self, memory_dir: str, on_finish=None):
        self.path = os.path.join(memory_dir, "tasks.json")
        self.state = self._load()
        # on_finish(goal, steps, outcome) — wired to cross-session Memory
        self.on_finish = on_finish or (lambda goal, steps, outcome: None)

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"active": None, "history": []}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.state, f, indent=2)

    # ---- lifecycle -----------------------------------------------------
    def start(self, goal: str, steps: list[dict]) -> str:
        if self.state["active"]:
            self.state["history"].append(self.state["active"])
        self.state["active"] = {
            "goal": goal,
            "steps": [{"title": s.get("title", "step"), "detail": s.get("detail", ""), "done": False} for s in steps],
            "current": 0,
            "started": time.strftime("%Y-%m-%d %H:%M"),
        }
        self._save()
        return self.brief()

    def advance(self) -> str:
        t = self.state["active"]
        if not t:
            return "No active task. Give me a goal and I'll plan it."
        t["steps"][t["current"]]["done"] = True
        if t["current"] + 1 < len(t["steps"]):
            t["current"] += 1
            self._save()
            return self.brief()
        self.state["history"].append(t)
        self.state["active"] = None
        self._save()
        self.on_finish(t["goal"], t["steps"], "completed")
        return f"Task complete: {t['goal']}. All {len(t['steps'])} steps done."

    def drop(self) -> str:
        t = self.state["active"]
        if not t:
            return "Nothing to drop."
        self.state["history"].append(t)
        self.state["active"] = None
        self._save()
        self.on_finish(t["goal"], t["steps"], "dropped")
        return f"Dropped task: {t['goal']}"

    # ---- views ---------------------------------------------------------
    def brief(self) -> str:
        """Current step + what's next — the 'directions' the user asked for."""
        t = self.state["active"]
        if not t:
            return "No active task."
        i, steps = t["current"], t["steps"]
        cur = steps[i]
        lines = [
            f"TASK: {t['goal']}  [{i + 1}/{len(steps)}]",
            f"NOW → {cur['title']}: {cur['detail']}",
        ]
        if i + 1 < len(steps):
            lines.append(f"NEXT → {steps[i + 1]['title']}")
        remaining = len(steps) - i - 1
        if remaining > 1:
            lines.append(f"({remaining} steps remain after this)")
        return "\n".join(lines)

    def full(self) -> str:
        t = self.state["active"]
        if not t:
            return "No active task."
        out = [f"TASK: {t['goal']}"]
        for j, s in enumerate(t["steps"]):
            mark = "✔" if s["done"] else ("▶" if j == t["current"] else "·")
            out.append(f" {mark} {j + 1}. {s['title']} — {s['detail']}")
        return "\n".join(out)

    def context_for_llm(self) -> str:
        """Injected into the system prompt so the model knows task state."""
        t = self.state["active"]
        if not t:
            return "No active task."
        cur = t["steps"][t["current"]]
        return (
            f"Active task: {t['goal']} (step {t['current'] + 1}/{len(t['steps'])}). "
            f"Current step: {cur['title']} — {cur['detail']}"
        )
