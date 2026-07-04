"""Step executor. Turns "do it" into actual execution of the current plan step:
extract the command from the step's detail, confirm with the user, run it,
capture output, and track repeated failures.
"""
import re
import subprocess

KNOWN_CMDS = ("docker", "docker-compose", "git", "make", "npm", "npx", "yarn",
              "pnpm", "python", "python3", "pip", "pip3", "go", "cargo", "mvn",
              "gradle", "kubectl", "helm", "terraform", "bash", "sh", "cd",
              "mkdir", "cp", "mv", "curl", "wget", "brew", "apt", "source",
              "ollama", "flask", "uvicorn", "node", "pytest", "echo", "ls")

BACKTICK = re.compile(r"`([^`]+)`")


def extract_command(detail: str):
    """Pull a runnable shell command out of a step's detail text, or None."""
    if not detail:
        return None
    m = BACKTICK.search(detail)
    if m:
        return m.group(1).strip()
    # "Build the image: docker build -t app ."  -> take text after the colon
    if ":" in detail:
        tail = detail.split(":", 1)[1].strip()
        if tail.split() and tail.split()[0] in KNOWN_CMDS:
            return tail
    # the whole detail is the command ("docker compose up -d")
    first = detail.strip().split()
    if first and first[0] in KNOWN_CMDS:
        return detail.strip()
    # "run docker build ..." / "execute make test"
    m = re.match(r"^(?:run|execute)\s+(.+)$", detail.strip(), re.I)
    if m and m.group(1).split()[0] in KNOWN_CMDS:
        return m.group(1).strip()
    return None


class StepExecutor:
    def __init__(self, workdir: str, confirm_fn):
        self.workdir = workdir
        self.confirm = confirm_fn
        self.fail_counts: dict[str, int] = {}

    def execute_step(self, step_key: str, detail: str):
        """Returns a dict describing what happened:
        {"status": "no_command"|"declined"|"ok"|"fail"|"give_up",
         "command", "exit", "output", "fails"}"""
        cmd = extract_command(detail)
        if not cmd:
            return {"status": "no_command", "command": None,
                    "output": ("This step has no directly runnable command — "
                               "it needs to be done manually or planned in more detail.")}
        if not self.confirm(f"About to execute:\n  $ {cmd}"):
            return {"status": "declined", "command": cmd,
                    "output": "Execution declined by user."}
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=300, cwd=self.workdir)
            exit_code = p.returncode
            out = ((p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")).strip()
        except subprocess.TimeoutExpired:
            exit_code, out = 124, "(timed out after 300s)"
        out = out[:5000] or "(no output)"

        if exit_code == 0:
            self.fail_counts.pop(step_key, None)
            return {"status": "ok", "command": cmd, "exit": 0, "output": out}

        self.fail_counts[step_key] = self.fail_counts.get(step_key, 0) + 1
        fails = self.fail_counts[step_key]
        status = "give_up" if fails >= 3 else "fail"
        return {"status": status, "command": cmd, "exit": exit_code,
                "output": out, "fails": fails}
