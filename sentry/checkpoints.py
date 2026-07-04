"""Rollback checkpoints. After every successful step execution a safe point is
created; 'undo' reverts the last one. Tool selection:
  git   — workdir is a git repo: commit everything as a checkpoint
  files — otherwise: snapshot key config files to <memory>/snapshots/
  docker— additionally, if the executed command was `docker build -t NAME ...`,
          the image is retagged NAME:sentry-step-N as a restorable reference
"""
import json
import os
import re
import shutil
import subprocess
import time

KEY_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", ".env",
             "Makefile", "package.json", "go.mod", "requirements.txt", "config.yaml")
BUILD_T = re.compile(r"docker\s+build\b.*?-t\s+(\S+)")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", s.lower())[:40].strip("-") or "task"


class CheckpointManager:
    def __init__(self, workdir: str, memory_dir: str):
        self.workdir = workdir
        self.snap_root = os.path.join(memory_dir, "snapshots")
        self.meta_path = os.path.join(memory_dir, "checkpoints.json")
        self.meta = self._load()

    def _load(self):
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self):
        with open(self.meta_path, "w") as f:
            json.dump(self.meta, f, indent=1)

    def _git(self, *args):
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=60, cwd=self.workdir)
        return p.returncode, (p.stdout + p.stderr).strip()

    def _is_git(self) -> bool:
        return os.path.isdir(os.path.join(self.workdir, ".git"))

    # ------------------------------------------------------------------
    def prepare(self, task: str, step_idx: int):
        """Call BEFORE executing a step. For non-git projects this snapshots the
        key files NOW, so undo restores the true pre-execution state."""
        if self._is_git():
            return None
        snap = os.path.join(self.snap_root, _slug(task), str(step_idx))
        os.makedirs(snap, exist_ok=True)
        files = []
        for fn in KEY_FILES:
            src = os.path.join(self.workdir, fn)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(snap, fn))
                files.append(fn)
        return {"snap": snap, "files": files}

    def create(self, task: str, step_idx: int, detail: str, command: str = "",
               pre=None) -> str:
        cp = {"task": task, "step": step_idx, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "tool": None, "commit": None, "image_tag": None, "files": []}

        if self._is_git():
            self._git("add", "-A")
            code, _ = self._git("commit", "-m",
                                f"[sentry-checkpoint] {task}: step {step_idx + 1}: {detail[:50]}")
            if code == 0:
                _, h = self._git("rev-parse", "HEAD")
                cp["tool"], cp["commit"] = "git", h.strip()
        if cp["tool"] is None:
            if pre is None:  # fallback (post-hoc snapshot is weaker; prefer prepare())
                pre = self.prepare(task, step_idx)
            cp["tool"] = "files"
            cp["snap"] = pre["snap"]
            cp["files"] = pre["files"]

        m = BUILD_T.search(command or "")
        if m:
            image = m.group(1).split(":")[0]
            tag = f"{image}:sentry-step-{step_idx}"
            p = subprocess.run(["docker", "tag", m.group(1), tag],
                               capture_output=True, text=True)
            if p.returncode == 0:
                cp["image_tag"] = tag

        self.meta.append(cp)
        # prune to 10 per task
        per = [c for c in self.meta if c["task"] == task]
        while len(per) > 10:
            old = per.pop(0)
            self.meta.remove(old)
            if old.get("snap") and os.path.isdir(old["snap"]):
                shutil.rmtree(old["snap"], ignore_errors=True)
        self._save()
        extra = f", image tagged {cp['image_tag']}" if cp["image_tag"] else ""
        return f"checkpoint saved ({cp['tool']}{extra})"

    def undo(self) -> str:
        if not self.meta:
            return "No checkpoints to undo."
        cp = self.meta.pop()
        self._save()
        if cp["tool"] == "git" and cp.get("commit"):
            code, out = self._git("revert", "--no-commit", cp["commit"])
            if code != 0:
                return f"Undo failed: {out[:300]}"
            return (f"Reverted step {cp['step'] + 1} of '{cp['task']}' "
                    f"(git revert staged, not committed — review with git diff). "
                    "Retry with 'do it', or skip with 'next'.")
        if cp["tool"] == "files":
            restored = []
            for fn in cp["files"]:
                src = os.path.join(cp["snap"], fn)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(self.workdir, fn))
                    restored.append(fn)
            what = ", ".join(restored) if restored else "nothing (no files were snapshotted)"
            return (f"Restored from step {cp['step'] + 1} of '{cp['task']}': {what}. "
                    "Retry with 'do it', or skip with 'next'.")
        return "Checkpoint had no restorable state."

    def count(self, task: str = None) -> int:
        return len([c for c in self.meta if task is None or c["task"] == task])
