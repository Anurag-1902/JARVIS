"""Tools the agent can call. Each returns a string result fed back to the model.

Safety: shell commands require user confirmation by default (config: agent.shell_confirm),
and file writes are restricted to the configured workdir.
"""
import os
import platform
import subprocess

import requests


DANGEROUS = ("rm -rf", "rm -r ", "mkfs", "dd if=", ":(){", "> /dev/",
             "git reset --hard", "git push --force", "docker system prune",
             "chmod -R 777", "mv / ", "truncate -s 0")


class ToolBox:
    def __init__(self, cfg: dict, confirm_fn=None, doc_index=None,
                 history=None, researcher=None, repo_inspector=None):
        self.cfg = cfg
        self.workdir = os.path.expanduser(cfg["agent"]["workdir"])
        self.confirm = confirm_fn or (lambda msg: True)
        self.doc_index = doc_index
        self.history = history
        self.researcher = researcher
        self.repo_inspector = repo_inspector

    # ---- registry ------------------------------------------------------
    def spec(self) -> str:
        """Tool descriptions injected into the system prompt."""
        return (
            "- run_shell(command): run a shell command, returns stdout/stderr. "
            "Use for checking versions, installing, running builds, docker.\n"
            "- git_status(): branch, staged/unstaged changes, unpushed commits.\n"
            "- git_diff(path?): what changed (summary + first 4KB of diff).\n"
            "- git_log(n?): last n commits, default 5.\n"
            "- docker_ps(all?): running containers (all=true includes stopped).\n"
            "- docker_logs(container, tail?): recent logs from a container.\n"
            "- docker_images(): local images with sizes.\n"
            "- search_docs(query): semantic search over the project's own code and docs. "
            "Use this FIRST when asked about how this specific project works.\n"
            "- search_history(query): search past conversations with the user "
            "(use when they reference something 'we did' or 'last time').\n"
            "- research(query): live web search + fetch top pages; returns source-cited "
            "current info. Use for best practices, latest versions, unfamiliar topics.\n"
            "- inspect_repo(url): understand any GitHub repo — description, languages, "
            "file tree, configs, README. Use when a github.com URL is mentioned.\n"
            "- read_file(path): return the contents of a text file (truncated to 8KB).\n"
            "- write_file(path, content): create/overwrite a file inside the workdir.\n"
            "- list_dir(path): list files in a directory.\n"
            "- fetch_url(url): fetch a web page/API and return its text (truncated).\n"
            "- sys_info(): OS, architecture, python version, current directory."
        )

    def call(self, name: str, args: dict) -> str:
        fn = getattr(self, f"tool_{name}", None)
        if not fn:
            return f"ERROR: unknown tool '{name}'"
        try:
            return fn(**(args or {}))
        except TypeError as e:
            return f"ERROR: bad arguments for {name}: {e}"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    # ---- tools ---------------------------------------------------------
    def tool_run_shell(self, command: str) -> str:
        if not self.cfg["agent"]["allow_shell"]:
            return "ERROR: shell is disabled in config (agent.allow_shell)."
        risky = any(d in command for d in DANGEROUS)
        if risky and not self.confirm(f"⚠ DESTRUCTIVE command — are you absolutely sure?\n  $ {command}"):
            return "User declined the destructive command."
        if self.cfg["agent"]["shell_confirm"] and not risky:
            if not self.confirm(f"Run shell command?\n  $ {command}"):
                return "User declined to run this command."
        p = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=120, cwd=self.workdir,
        )
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        out = out.strip() or "(no output)"
        return f"exit={p.returncode}\n{out[:6000]}"

    def tool_read_file(self, path: str) -> str:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.workdir, path)
        with open(path, "r", errors="replace") as f:
            data = f.read(8192)
        return data or "(empty file)"

    def tool_write_file(self, path: str, content: str) -> str:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.workdir, path)
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(self.workdir)):
            return f"ERROR: refusing to write outside workdir ({self.workdir})."
        if not self.confirm(f"Write {len(content)} chars to {real}?"):
            return "User declined the write."
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {real}"

    def tool_list_dir(self, path: str = ".") -> str:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.join(self.workdir, path)
        entries = sorted(os.listdir(path))[:100]
        return "\n".join(entries) or "(empty)"

    def tool_fetch_url(self, url: str) -> str:
        r = requests.get(url, timeout=20, headers={"User-Agent": "sentry-agent/0.1"})
        text = r.text
        return f"status={r.status_code}\n{text[:6000]}"

    # ---- git (no confirm needed: read-only) ------------------------------
    def _git(self, *args) -> str:
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=30, cwd=self.workdir)
        if p.returncode != 0:
            return f"git error: {p.stderr.strip()[:400]}"
        return p.stdout.strip() or "(no output)"

    def tool_git_status(self) -> str:
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        status = self._git("status", "--short")
        ahead = self._git("log", "--oneline", "@{u}..HEAD")
        out = [f"branch: {branch}", f"changes:\n{status}"]
        if ahead and "git error" not in ahead and ahead != "(no output)":
            out.append(f"unpushed commits:\n{ahead}")
        return "\n".join(out)[:4000]

    def tool_git_diff(self, path: str = None) -> str:
        stat = self._git("diff", "--stat") + "\n\n"
        diff = self._git("diff", path) if path else self._git("diff")
        return (stat + diff)[:4000]

    def tool_git_log(self, n: int = 5) -> str:
        return self._git("log", f"-{int(n)}", "--oneline", "--decorate")

    def tool_search_docs(self, query: str) -> str:
        if self.doc_index is None:
            return "ERROR: doc index not initialized."
        return self.doc_index.search(query)

    def tool_search_history(self, query: str) -> str:
        if self.history is None:
            return "ERROR: chat history not initialized."
        return self.history.search(query, k=5)

    def tool_research(self, query: str) -> str:
        if self.researcher is None:
            return "ERROR: researcher not initialized."
        return self.researcher.research(query)

    def tool_inspect_repo(self, url: str) -> str:
        if self.repo_inspector is None:
            return "ERROR: repo inspector not initialized."
        return self.repo_inspector.inspect(url)

    # ---- docker (read-only: no confirm needed) ---------------------------
    def _docker(self, *args) -> str:
        try:
            p = subprocess.run(["docker", *args], capture_output=True, text=True,
                               timeout=30, cwd=self.workdir)
        except FileNotFoundError:
            return "Docker isn't installed on this machine (or not on PATH)."
        if p.returncode != 0:
            err = p.stderr.strip()[:400]
            if "Cannot connect to the Docker daemon" in err:
                return "Docker daemon isn't running — start Docker Desktop first."
            if "command not found" in err or p.returncode == 127:
                return "Docker isn't installed on this machine."
            return f"docker error: {err}"
        return p.stdout.strip() or "(no output)"

    def tool_docker_ps(self, all: bool = False) -> str:
        args = ["ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
        if all:
            args.insert(1, "-a")
        return self._docker(*args)[:4000]

    def tool_docker_logs(self, container: str, tail: int = 50) -> str:
        return self._docker("logs", "--tail", str(int(tail)), container)[:4000]

    def tool_docker_images(self) -> str:
        return self._docker("images", "--format",
                            "table {{.Repository}}\t{{.Tag}}\t{{.Size}}")[:4000]

    def tool_sys_info(self) -> str:
        return (
            f"os={platform.system()} {platform.release()} arch={platform.machine()} "
            f"python={platform.python_version()} cwd={self.workdir}"
        )
