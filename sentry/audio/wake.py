"""Wake word detection — say "hey jarvis" to activate, hands-free.

Preferred backend: openWakeWord, which ships a pretrained "hey jarvis" model.
    pip install openwakeword onnxruntime
    (models auto-download on first run, ~few MB, then fully offline)

Fallback (no extra installs): short-chunk transcription with the existing
faster-whisper model, watching for wake keywords. Heavier on CPU but works
with only the base voice deps.
"""
import time


class WakeListener:
    def __init__(self, cfg: dict, stt=None):
        self.cfg = cfg
        self.stt = stt
        self.threshold = float(cfg["voice"].get("wake_threshold", 0.5))
        self.keywords = [k.lower() for k in cfg["voice"].get("wake_words", ["jarvis", "sentry"])]
        self.backend = self._init_oww() or ("whisper" if stt else None)
        if self.backend is None:
            raise RuntimeError(
                "No wake-word backend available. Install one of:\n"
                "  pip install openwakeword onnxruntime     (recommended)\n"
                "  pip install faster-whisper sounddevice numpy  (fallback)"
            )

    def _init_oww(self):
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            import openwakeword
            from openwakeword.model import Model
        except ImportError:
            return None
        try:
            try:  # first run: fetch the pretrained models (~small, one-time)
                openwakeword.utils.download_models(["hey_jarvis"])
            except Exception:
                pass
            self.oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            return "openwakeword"
        except Exception as e:
            print(f"  [wake] openwakeword unavailable ({e}); falling back to whisper")
            return None

    # ------------------------------------------------------------------
    def wait(self) -> None:
        """Block until the wake word is heard."""
        if self.backend == "openwakeword":
            self._wait_oww()
        else:
            self._wait_whisper()

    def _wait_oww(self):
        import numpy as np
        import sounddevice as sd
        sr, frame = 16000, 1280  # 80ms frames, as openWakeWord expects
        self.oww.reset()
        with sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                            blocksize=frame) as stream:
            while True:
                data, _ = stream.read(frame)
                scores = self.oww.predict(np.squeeze(data))
                if any(v >= self.threshold for k, v in scores.items()
                       if "jarvis" in k.lower()):
                    return

    def _wait_whisper(self):
        """Listen in short chunks and transcribe, watching for keywords."""
        import numpy as np
        import sounddevice as sd
        sr, secs = 16000, 2.5
        while True:
            audio = sd.rec(int(secs * sr), samplerate=sr, channels=1, dtype="float32")
            sd.wait()
            audio = np.squeeze(audio)
            if float(np.abs(audio).mean()) < 0.004:   # silence — skip transcribe
                continue
            segs, _ = self.stt.model.transcribe(audio, language="en", vad_filter=True)
            heard = " ".join(s.text for s in segs).lower()
            if any(k in heard for k in self.keywords):
                return
            time.sleep(0.05)
