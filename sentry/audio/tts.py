"""Text-to-speech, all local. Engine chain:

  kokoro  -> Kokoro-82M, voice 'bm_george': calm British male — the JARVIS vibe.
             pip install kokoro-onnx soundfile sounddevice
             (model ~310MB auto-downloads on first use, then fully offline)
  say     -> macOS built-in; 'Daniel' is a decent British voice, zero setup
  piper   -> good Linux option if configured with a voice .onnx
  pyttsx3 -> cross-platform espeak/SAPI last resort

Default 'auto' = kokoro if installed, else say (Mac), else pyttsx3.
"""
import os
import platform
import shutil
import subprocess
import urllib.request

KOKORO_FILES = {
    "kokoro-v1.0.onnx":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}


def _kokoro_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


class TTS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.kokoro = None
        mode = cfg["voice"]["tts"]
        if mode == "off":
            self.engine = "off"
        elif mode != "auto":
            self.engine = mode
        elif _kokoro_available():
            self.engine = "kokoro"
        elif platform.system() == "Darwin" and shutil.which("say"):
            self.engine = "say"
        elif shutil.which("piper") and cfg["voice"].get("piper_voice"):
            self.engine = "piper"
        else:
            self.engine = "pyttsx3"

    # ---- kokoro ----------------------------------------------------------
    def _ensure_kokoro(self):
        if self.kokoro is not None:
            return
        from kokoro_onnx import Kokoro
        model_dir = self.cfg["memory"]["dir"]
        paths = {}
        for fname, url in KOKORO_FILES.items():
            p = os.path.join(model_dir, fname)
            if not os.path.exists(p):
                print(f"  downloading {fname} (one-time, ~{'310MB' if fname.endswith('onnx') else '27MB'})…")
                urllib.request.urlretrieve(url, p)
            paths[fname] = p
        self.kokoro = Kokoro(paths["kokoro-v1.0.onnx"], paths["voices-v1.0.bin"])

    def _speak_kokoro(self, text: str):
        import sounddevice as sd
        self._ensure_kokoro()
        voice = self.cfg["voice"].get("kokoro_voice", "bm_george")
        samples, sr = self.kokoro.create(text, voice=voice, speed=1.0, lang="en-gb")
        sd.play(samples, sr)
        sd.wait()

    # ---- dispatch ----------------------------------------------------------
    def speak(self, text: str):
        text = text.strip()
        if not text or self.engine == "off":
            return
        try:
            if self.engine == "kokoro":
                self._speak_kokoro(text)
            elif self.engine == "say":
                subprocess.run(["say", "-v", "Daniel", text], check=False)
            elif self.engine == "piper":
                voice = self.cfg["voice"]["piper_voice"]
                p1 = subprocess.Popen(
                    ["piper", "--model", voice, "--output-raw"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                p2 = subprocess.Popen(
                    ["aplay", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                    stdin=p1.stdout)
                p1.stdin.write(text.encode()); p1.stdin.close()
                p2.wait()
            else:
                import pyttsx3
                eng = pyttsx3.init()
                eng.setProperty("rate", 185)
                eng.say(text)
                eng.runAndWait()
        except Exception as e:
            print(f"[tts unavailable ({self.engine}): {e}]")
            if self.engine == "kokoro":  # degrade gracefully to `say`
                self.engine = "say" if shutil.which("say") else "pyttsx3"
