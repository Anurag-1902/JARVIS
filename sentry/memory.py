"""Cross-session memory. Records task outcomes (goal, steps, result, notes) and
recalls lessons when a similar task starts — "last time you forgot X".
Persists to data/memory.json.
"""
import json
import os
import re
import time

WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]+")
STOP = {"the", "a", "an", "and", "for", "with", "this", "that", "task", "set",
        "get", "run", "make", "build", "my", "our", "your", "into", "onto", "up"}


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text) if w.lower() not in STOP}


class Memory:
    def __init__(self, memory_dir: str):
        self.path = os.path.join(memory_dir, "memory.json")
        self.records = self._load()

    def _load(self) -> list[dict]:
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.records[-200:], f, indent=1)

    def record(self, goal: str, steps: list[dict], outcome: str, note: str = ""):
        """outcome: completed | dropped"""
        self.records.append({
            "goal": goal,
            "steps": [s["title"] for s in steps],
            "outcome": outcome,
            "note": note,
            "when": time.strftime("%Y-%m-%d %H:%M"),
        })
        self._save()

    def recall(self, goal: str, k: int = 2) -> str:
        """Return lessons from the most similar past tasks, or ''."""
        q = _keywords(goal)
        if not q or not self.records:
            return ""
        scored = []
        for r in self.records:
            overlap = len(q & _keywords(r["goal"])) / max(len(q), 1)
            if overlap >= 0.34:
                scored.append((overlap, r))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return ""
        lines = []
        for _s, r in scored[:k]:
            status = "completed" if r["outcome"] == "completed" else "was abandoned"
            line = f"Similar past task '{r['goal']}' ({r['when']}) {status}"
            if r["outcome"] == "dropped":
                line += " — consider what blocked it"
            if r.get("note"):
                line += f". Note: {r['note']}"
            lines.append(line + ".")
        return " ".join(lines)

    def stats(self) -> str:
        done = sum(1 for r in self.records if r["outcome"] == "completed")
        return f"{len(self.records)} tasks remembered ({done} completed, {len(self.records) - done} dropped)."
