"""Config-file parsers. Each returns a dict of the useful facts, or None if the
file is missing/unparseable. Used by project.py to make plans reference the
project's REAL commands instead of generic guesses.
"""
import json
import os
import re

import yaml


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def parse_dockerfile(path: str):
    text = _read(path)
    if text is None:
        return None
    out = {"base_image": None, "exposed_ports": [], "entrypoint": None,
           "cmd": None, "workdir": None}
    for line in text.splitlines():
        s = line.strip()
        u = s.upper()
        if u.startswith("FROM ") and out["base_image"] is None:
            out["base_image"] = s.split(None, 1)[1].split(" AS ")[0].split(" as ")[0].strip()
        elif u.startswith("EXPOSE "):
            out["exposed_ports"] += re.findall(r"\d+", s)
        elif u.startswith("ENTRYPOINT "):
            out["entrypoint"] = s.split(None, 1)[1]
        elif u.startswith("CMD "):
            out["cmd"] = s.split(None, 1)[1]
        elif u.startswith("WORKDIR "):
            out["workdir"] = s.split(None, 1)[1]
    return out if out["base_image"] else None


def parse_docker_compose(path: str):
    text = _read(path)
    if text is None:
        return None
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None
    services = {}
    for name, svc in (doc.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        services[name] = {
            "image": svc.get("image"),
            "build": bool(svc.get("build")),
            "ports": [str(p) for p in (svc.get("ports") or [])],
        }
    return {"services": services} if services else None


def parse_makefile(path: str):
    text = _read(path)
    if text is None:
        return None
    targets = []
    prev_comment = ""
    for line in text.splitlines():
        if line.strip().startswith("#"):
            prev_comment = line.strip("# ").strip()
            continue
        m = re.match(r"^([A-Za-z0-9_\-\.]+)\s*:(?!=)", line)
        if m and m.group(1) not in (".PHONY",):
            targets.append({"name": m.group(1), "description": prev_comment})
        prev_comment = ""
    return {"targets": targets} if targets else None


def parse_package_json(path: str):
    text = _read(path)
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return None
    return {"scripts": doc.get("scripts") or {},
            "dependencies": doc.get("dependencies") or {}}


def parse_go_mod(path: str):
    text = _read(path)
    if text is None:
        return None
    mod = re.search(r"^module\s+(\S+)", text, re.M)
    ver = re.search(r"^go\s+([\d.]+)", text, re.M)
    if not mod:
        return None
    return {"module": mod.group(1), "go_version": ver.group(1) if ver else None}


def parse_all(workdir: str) -> dict:
    """Run every parser against a workdir. Missing files simply don't appear."""
    w = os.path.expanduser(workdir)
    cfg = {}
    d = parse_dockerfile(os.path.join(w, "Dockerfile"))
    if d:
        cfg["dockerfile"] = d
    for fn in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
        c = parse_docker_compose(os.path.join(w, fn))
        if c:
            cfg["compose"] = c
            cfg["compose_file"] = fn
            break
    m = parse_makefile(os.path.join(w, "Makefile"))
    if m:
        cfg["makefile"] = m
    p = parse_package_json(os.path.join(w, "package.json"))
    if p:
        cfg["package_json"] = p
    g = parse_go_mod(os.path.join(w, "go.mod"))
    if g:
        cfg["go_mod"] = g
    return cfg


def summarize(cfg: dict) -> str:
    """One compact paragraph for the system prompt."""
    if not cfg:
        return ""
    bits = []
    if "dockerfile" in cfg:
        d = cfg["dockerfile"]
        s = f"Dockerfile (base {d['base_image']}"
        if d["entrypoint"] or d["cmd"]:
            s += f", runs {d['entrypoint'] or d['cmd']}"
        if d["exposed_ports"]:
            s += f", ports {','.join(d['exposed_ports'])}"
        bits.append(s + ")")
    if "compose" in cfg:
        svcs = cfg["compose"]["services"]
        desc = ", ".join(f"{n}" + (f"[{v['image']}]" if v["image"] else "[build]")
                         for n, v in svcs.items())
        bits.append(f"{cfg['compose_file']} (services: {desc})")
    if "makefile" in cfg:
        names = [t["name"] for t in cfg["makefile"]["targets"][:8]]
        bits.append(f"Makefile (targets: {', '.join(names)} — prefer `make <target>`)")
    if "package_json" in cfg:
        scripts = [f"{k}: {v}" for k, v in list(cfg["package_json"]["scripts"].items())[:6]]
        bits.append(f"package.json (npm scripts — {'; '.join(scripts)})")
    if "go_mod" in cfg:
        g = cfg["go_mod"]
        bits.append(f"go.mod (module {g['module']}, go {g['go_version']})")
    return "Config files found — use their REAL names/commands in plans: " + "; ".join(bits)
