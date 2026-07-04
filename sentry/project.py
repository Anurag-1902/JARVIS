"""Project detection. Scans the workdir for markers and produces a one-paragraph
profile injected into the system prompt, so plans reference what's actually there
instead of generic advice.
"""
import os

from .config_parser import parse_all, summarize as summarize_config

MARKERS = [
    # (filename or dirname, label)
    ("Dockerfile", "Docker"),
    ("docker-compose.yml", "docker-compose"),
    ("compose.yaml", "docker-compose"),
    ("requirements.txt", "Python (pip)"),
    ("pyproject.toml", "Python (pyproject)"),
    ("package.json", "Node.js"),
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("pom.xml", "Java (Maven)"),
    ("build.gradle", "Java (Gradle)"),
    ("CMakeLists.txt", "C/C++ (CMake)"),
    ("Makefile", "Makefile"),
    (".github/workflows", "GitHub Actions CI"),
    (".gitlab-ci.yml", "GitLab CI"),
    ("k8s", "Kubernetes manifests"),
    ("terraform", "Terraform"),
]

ENTRYPOINTS = ["main.py", "app.py", "main.go", "src/main.rs", "index.js", "src/index.ts"]


def detect(workdir: str) -> dict:
    workdir = os.path.expanduser(workdir)
    found, entries = [], []
    try:
        top = set(os.listdir(workdir))
    except OSError:
        return {"summary": "workdir unreadable", "stacks": []}

    for marker, label in MARKERS:
        if "/" in marker:
            if os.path.exists(os.path.join(workdir, marker)):
                found.append(label)
        elif marker in top:
            found.append(label)
    for ep in ENTRYPOINTS:
        if os.path.exists(os.path.join(workdir, ep)):
            entries.append(ep)

    is_git = os.path.isdir(os.path.join(workdir, ".git"))
    readme = next((f for f in top if f.lower().startswith("readme")), None)

    parts = []
    if found:
        parts.append("stacks: " + ", ".join(sorted(set(found))))
    if entries:
        parts.append("entrypoints: " + ", ".join(entries))
    parts.append("git repo" if is_git else "not a git repo")
    if readme:
        parts.append(f"has {readme}")

    config = parse_all(workdir)
    cfg_summary = summarize_config(config)
    summary = f"Workdir {workdir} — " + "; ".join(parts)
    if cfg_summary:
        summary += "\n" + cfg_summary

    return {
        "summary": summary,
        "stacks": sorted(set(found)),
        "is_git": is_git,
        "config": config,
    }
