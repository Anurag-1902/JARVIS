"""Streaming reply parser.

The model answers in a JSON protocol, so we can't pipe raw tokens to the user —
half of them would be braces. This parser watches the token stream: if it opens
with {"type":"reply","text":" it streams the string content live (handling JSON
escapes); anything else (plan/tool JSON) is buffered silently for normal parsing.
"""
import re

# tolerant of whitespace and an optional ```json fence before the object
PREFIX = re.compile(
    r'^\s*(?:```(?:json)?\s*)?\{\s*"type"\s*:\s*"reply"\s*,\s*"text"\s*:\s*"'
)
# once the buffer is longer than this without matching, it's not a streamable reply
GIVE_UP = 120

ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "r": ""}


class ReplyStream:
    def __init__(self, on_token):
        self.on_token = on_token
        self.buf = ""            # full raw text (always kept, for fallback parsing)
        self.mode = "sniff"      # sniff -> emit | passthrough
        self.pos = 0             # emit cursor into buf
        self.esc = False         # inside an escape sequence
        self.closed = False      # reached the closing quote of "text"
        self.emitted = []

    def feed(self, token: str):
        self.buf += token
        if self.mode == "sniff":
            m = PREFIX.match(self.buf)
            if m:
                self.mode = "emit"
                self.pos = m.end()
            elif len(self.buf) > GIVE_UP:
                self.mode = "passthrough"
                return
            else:
                return
        if self.mode == "emit" and not self.closed:
            self._drain()

    def _drain(self):
        out = []
        i = self.pos
        while i < len(self.buf):
            c = self.buf[i]
            if self.esc:
                out.append(ESCAPES.get(c, c))
                self.esc = False
            elif c == "\\":
                self.esc = True
            elif c == '"':          # end of the text field
                self.closed = True
                i += 1
                break
            else:
                out.append(c)
            i += 1
        self.pos = i
        if out:
            text = "".join(out)
            self.emitted.append(text)
            self.on_token(text)

    @property
    def streamed(self) -> bool:
        return self.mode == "emit" and bool(self.emitted)

    @property
    def streamed_text(self) -> str:
        return "".join(self.emitted)

    @property
    def raw(self) -> str:
        return self.buf
