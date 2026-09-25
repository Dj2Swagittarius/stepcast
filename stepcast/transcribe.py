"""Transcribe narration.wav with faster-whisper -> timestamped segments."""
from pathlib import Path

_MODEL = None


def _get_model(size: str = "base"):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        from .recorder import app_dir

        # Keep the downloaded model next to the app so the portable exe
        # stays self-contained (no scattered cache in the user profile).
        models_dir = app_dir() / "models"
        models_dir.mkdir(exist_ok=True)
        # CPU + int8 = fast, low memory, no GPU needed
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8",
                              download_root=str(models_dir))
    return _MODEL


def transcribe(wav_path: Path, model_size: str = "base") -> dict:
    """Transcribe with word-level timestamps.

    Returns {"segments": [{start_ms, end_ms, text}],
             "words":    [{start_ms, end_ms, word}]}.
    Word timestamps let the guide split narration at each action boundary
    so quotes land under the step they were spoken at.
    """
    empty = {"segments": [], "words": []}
    wav_path = Path(wav_path)
    if not wav_path.exists() or wav_path.stat().st_size < 1024:
        return empty
    try:
        model = _get_model(model_size)
        segments, _ = model.transcribe(str(wav_path), language="en",
                                       vad_filter=True, word_timestamps=True)
        segs, words = [], []
        for s in segments:
            text = s.text.strip()
            if not text:
                continue
            segs.append(
                {
                    "start_ms": int(s.start * 1000),
                    "end_ms": int(s.end * 1000),
                    "text": text,
                }
            )
            for w in (s.words or []):
                token = w.word.strip()
                if token:
                    words.append(
                        {
                            "start_ms": int(w.start * 1000),
                            "end_ms": int(w.end * 1000),
                            "word": token,
                        }
                    )
        return {"segments": segs, "words": words}
    except Exception as e:  # noqa: BLE001
        print(f"[stepcast] transcription failed: {e}")
        return empty
