"""Task templates. Save a proven plan once, reuse it across workspaces with
automatic path substitution and service-name sanity notes.
"""
import json
import os
import re
import time

NAME_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


class TemplateManager:
    def __init__(self, base_dir: str):
        self.dir = os.path.join(base_dir, "templates")
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, name: str) -> str:
        return os.path.join(self.dir, f"{name}.json")

    def save(self, name: str, goal: str, steps: list[dict],
             workspace: str, workdir: str, services: list[str]) -> str:
        name = name.strip().lower()
        if not NAME_RE.match(name):
            return "Template names must be alphanumeric with hyphens (e.g. levee-deploy)."
        with open(self._path(name), "w") as f:
            json.dump({"goal": goal,
                       "steps": [{"title": s["title"], "detail": s["detail"]} for s in steps],
                       "created": time.strftime("%Y-%m-%d %H:%M"),
                       "usage_count": 0,
                       "source_workspace": workspace,
                       "source_workdir": workdir,
                       "source_services": services}, f, indent=1)
        return f"Template '{name}' saved ({len(steps)} steps). Use it with: use template {name}"

    def load(self, name: str):
        p = self._path(name.strip().lower())
        if not os.path.exists(p):
            return None
        try:
            with open(p) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self, name: str) -> str:
        p = self._path(name.strip().lower())
        if not os.path.exists(p):
            return f"No template named '{name}'."
        os.remove(p)
        return f"Deleted template '{name}'."

    def list(self) -> str:
        rows = []
        for fn in sorted(os.listdir(self.dir)):
            if not fn.endswith(".json"):
                continue
            t = self.load(fn[:-5])
            if t:
                rows.append(f"  {fn[:-5]} — {t['goal']} "
                            f"({len(t['steps'])} steps, used {t['usage_count']}x, {t['created']})")
        return "Saved templates:\n" + "\n".join(rows) if rows else "No templates saved yet."

    def mark_used(self, name: str):
        t = self.load(name)
        if t is None:
            return
        t["usage_count"] += 1
        with open(self._path(name.strip().lower()), "w") as f:
            json.dump(t, f, indent=1)

    def customize(self, tpl, current_workdir, current_services):
        """Adapt a template's steps to the current project:
        - source workdir paths -> current workdir
        - flag service names that don't exist in this project (no silent rename)
        """
        src_wd = tpl.get("source_workdir", "")
        src_services = tpl.get("source_services", [])
        missing = [s for s in src_services if s not in current_services]
        out = []
        for s in tpl["steps"]:
            detail = s["detail"]
            if src_wd and src_wd != current_workdir:
                detail = detail.replace(src_wd.rstrip("/") + "/",
                                        current_workdir.rstrip("/") + "/")
                detail = detail.replace(src_wd, current_workdir)
            for svc in missing:
                if re.search(rf"\b{re.escape(svc)}\b", detail):
                    avail = ", ".join(current_services) or "none found"
                    detail += f"  [⚠ service '{svc}' not in this project — available: {avail}]"
            out.append({"title": s["title"], "detail": detail})
        return out
