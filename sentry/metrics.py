"""Usage metrics. Counts real events (steps executed, undos, templates reused,
plans created/completed) and tracks response latency. Persisted to
data/metrics.json; surfaced via the 'stats' command, /api/metrics, and the UI.
"""
import json
import os
import threading
import time


class Metrics:
    FIELDS = ("turns", "plans_created", "tasks_completed", "steps_executed",
              "exec_failures", "undos", "templates_used", "repos_inspected",
              "researches")

    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, "metrics.json")
        self.lock = threading.Lock()
        self.data = {f: 0 for f in self.FIELDS}
        self.data.update({"resp_ms_total": 0, "resp_count": 0, "since": time.strftime("%Y-%m-%d")})
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.data.update(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=1)

    def bump(self, name: str, n: int = 1):
        with self.lock:
            self.data[name] = self.data.get(name, 0) + n
            self._save()

    def timing(self, ms: float):
        with self.lock:
            self.data["resp_ms_total"] += ms
            self.data["resp_count"] += 1
            self._save()

    def avg_ms(self) -> int:
        c = self.data.get("resp_count", 0)
        return int(self.data["resp_ms_total"] / c) if c else 0

    def as_dict(self) -> dict:
        d = {f: self.data.get(f, 0) for f in self.FIELDS}
        d["avg_response_ms"] = self.avg_ms()
        d["since"] = self.data.get("since", "?")
        return d

    def summary(self) -> str:
        d = self.as_dict()
        ok = d["steps_executed"]
        fails = d["exec_failures"]
        rate = f"{100 * ok // (ok + fails)}%" if (ok + fails) else "—"
        return (f"Stats since {d['since']}:\n"
                f"  {d['turns']} turns · avg response {d['avg_response_ms']}ms\n"
                f"  {d['plans_created']} plans created, {d['tasks_completed']} completed\n"
                f"  {ok} steps executed ({rate} success), {d['undos']} undos\n"
                f"  {d['templates_used']} template uses · {d['repos_inspected']} repos inspected "
                f"· {d['researches']} web researches")
