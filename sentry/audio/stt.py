"""Speech-to-text: records from the mic, transcribes with faster-whisper. 100% local.

Optional deps: pip install faster-whisper sounddevice numpy
First run downloads the whisper model (~150MB for base.en) then works offline.
"""


class STT:
    def __init__(self, cfg: dict):
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                f"Voice deps missing ({e.name}). Install with:\n"
                "  pip install faster-whisper sounddevice numpy"
            )
        self.cfg = cfg
        self.samplerate = 16000
        size = cfg["voice"]["stt_model"]
        # int8 keeps it fast on CPU; Apple Silicon handles small.en in ~realtime
        self.model = WhisperModel(size, device="cpu", compute_type="int8")

    def record(self) -> "numpy.ndarray":
        import numpy as np
        import sounddevice as sd
        secs = self.cfg["voice"]["record_seconds"]
        print(f"● recording ({secs}s)… speak now")
        audio = sd.rec(int(secs * self.samplerate), samplerate=self.samplerate,
                       channels=1, dtype="float32")
        sd.wait()
        return np.squeeze(audio)

    def listen(self) -> str:
        audio = self.record()
        segments, _info = self.model.transcribe(audio, language="en", vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        return text
