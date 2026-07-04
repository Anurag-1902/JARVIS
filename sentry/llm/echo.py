"""Echo provider — canned responses for testing the pipeline without a model."""
import json
from .base import LLMProvider


class EchoProvider(LLMProvider):
    def __init__(self, cfg: dict):
        self.calls = 0

    def chat(self, system: str, messages: list[dict]) -> str:
        self.calls += 1
        last = messages[-1]["content"].lower()
        if "plan" in last or "set up" in last or "task" in last:
            return json.dumps({
                "type": "plan",
                "goal": "Demo task plan",
                "steps": [
                    {"title": "First step", "detail": "This is what you do first."},
                    {"title": "Second step", "detail": "Then do this."},
                    {"title": "Verify", "detail": "Check that it all worked."},
                ],
            })
        if "list" in last and "file" in last:
            return json.dumps({"type": "tool", "name": "list_dir", "args": {"path": "."}})
        return json.dumps({"type": "reply", "text": "Echo provider online. Pipeline is working."})
