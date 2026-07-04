"""Multi-project workspaces. Each workspace gets its own workdir plus isolated
task plans, memory, and doc index under data/workspaces/<name>/.

Chat commands (handled in agent/core.py):
    workspace: levee /Users/me/dev/levee   -> create/switch, with a workdir
    workspace: levee                       -> switch (or create using current workdir)
    switch to levee                        -> same
    workspaces                             -> list all
"""
import json
import os
import re

SAFE = re.compile(r"[^a-z0-9_\-]")


def _safe(name: str) -> str:
    return SAFE.sub("", name.strip().lower().replace(" ", "-"))[:40] or "default"


class Workspaces:
    def __init__(self, base_dir: str, default_workdir: str):
        self.base = base_dir
        self.path = os.path.join(base_dir, "workspace.json")
        self.data = self._load(default_workdir)

    def _load(self, default_workdir):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"current": "default",
                "projects": {"default": {"workdir": default_workdir}}}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2)

    # ------------------------------------------------------------------
    @property
    def current(self) -> str:
        return self.data["current"]

    def workdir(self, name: str = None) -> str:
        name = name or self.current
        return self.data["projects"][name]["workdir"]

    def dir_for(self, name: str = None) -> str:
        """Per-workspace storage dir (tasks.json, memory.json, index.json)."""
        name = name or self.current
        d = os.path.join(self.base, "workspaces", name)
        os.makedirs(d, exist_ok=True)
        return d

    def switch(self, name: str, workdir: str = None) -> tuple[str, bool]:
        """Switch to (auto-creating) a workspace. Returns (message, created)."""
        name = _safe(name)
        created = name not in self.data["projects"]
        if created:
            wd = os.path.expanduser(workdir) if workdir else self.workdir()
            if not os.path.isdir(wd):
                return (f"Path '{wd}' doesn't exist — give me a real folder: "
                        f"workspace: {name} /path/to/project", False)
            self.data["projects"][name] = {"workdir": wd}
        elif workdir:  # existing workspace, path update requested
            wd = os.path.expanduser(workdir)
            if os.path.isdir(wd):
                self.data["projects"][name]["workdir"] = wd
        self.data["current"] = name
        self._save()
        verb = "Created and switched to" if created else "Switched to"
        return (f"{verb} workspace '{name}' (workdir: {self.workdir(name)})", created)

    def list(self) -> str:
        lines = []
        for n, p in self.data["projects"].items():
            mark = "▶" if n == self.current else " "
            lines.append(f"{mark} {n} — {p['workdir']}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"current": self.current,
                "projects": {n: p["workdir"] for n, p in self.data["projects"].items()}}
