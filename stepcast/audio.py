"""Mic narration recorder. Streams microphone to narration.wav for a session."""
import queue
import threading
from pathlib import Path

import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000  # 16 kHz mono — ideal for Whisper
CHANNELS = 1


class MicRecorder:
    """Background mic capture to a WAV file. Start/stop alongside a session."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._thread = None
        self._stop = threading.Event()
        self._path = None
        self.active = False
        self.error = None

    def _callback(self, indata, frames, time_info, status):  # noqa: ARG002
        if status:
            print(f"[stepcast] mic status: {status}")
        self._q.put(indata.copy())

    def _writer(self, path: Path):
        with sf.SoundFile(
            str(path), mode="w", samplerate=self.sample_rate,
            channels=CHANNELS, subtype="PCM_16",
        ) as f:
            while not self._stop.is_set() or not self._q.empty():
                try:
                    block = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                f.write(block)

    def start(self, path: Path) -> bool:
        """Begin recording mic to `path`. Returns False if no mic / error."""
        self._path = Path(path)
        self._stop.clear()
        self.error = None
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate, channels=CHANNELS,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            print(f"[stepcast] mic unavailable: {e}")
            self.active = False
            return False
        self._thread = threading.Thread(target=self._writer, args=(self._path,), daemon=True)
        self._thread.start()
        self.active = True
        return True

    def stop(self) -> Path | None:
        """Stop recording, flush file. Returns wav path or None."""
        if not self.active:
            return None
        self._stop.set()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread:
            self._thread.join(timeout=5)
        self.active = False
        return self._path
