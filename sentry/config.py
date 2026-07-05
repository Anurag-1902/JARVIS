"""Config loading for Sentry. Reads config.yaml, falls back to defaults."""
import os
import yaml

DEFAULTS = {
    "llm": {
        "provider": "ollama",            # ollama | anthropic
        "model": "qwen2.5:7b",
        "ollama_url": "http://localhost:11434",
        "anthropic_model": "claude-sonnet-4-6",
        "max_tokens": 1800,
        "temperature": 0.3,
        "keep_alive": "30m",
        "num_ctx": 8192,
    },
    "voice": {
        "enabled": False,                 # start in text mode; --voice flag overrides
        "stt_model": "base.en",           # faster-whisper size: tiny.en/base.en/small.en
        "record_seconds": 6,
        "tts": "auto",                    # auto | say | pyttsx3 | piper | off
        "piper_voice": "",                # path to .onnx if using piper
        "kokoro_voice": "bm_george",      # JARVIS-style British male
        "wake_words": ["jarvis", "sentry"],
        "wake_threshold": 0.5,
    },
    "agent": {
        "workdir": os.path.expanduser("~"),
        "allow_shell": True,
        "shell_confirm": True,            # ask before running any shell command
        "max_tool_rounds": 6,
        "persona": "jarvis",              # jarvis | plain
    },
    "memory": {
        "dir": os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str = None) -> dict:
    cfg = DEFAULTS
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    if os.path.exists(path):
        with open(path) as f:
            user = yaml.safe_load(f) or {}
        cfg = _merge(DEFAULTS, user)
    # YAML gotcha: an unquoted `~` parses as null, not the home directory.
    # Guard every path-like/None-able field so a config typo can't crash startup.
    if not cfg["agent"].get("workdir"):
        cfg["agent"]["workdir"] = os.path.expanduser("~")
    if not cfg["memory"].get("dir"):
        cfg["memory"]["dir"] = DEFAULTS["memory"]["dir"]
    os.makedirs(cfg["memory"]["dir"], exist_ok=True)
    return cfg
