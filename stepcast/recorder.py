"""Core recorder: global mouse/keyboard hooks + screenshots -> JSON event log."""
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image
from pynput import keyboard, mouse

from .audio import MicRecorder
from .winutil import (foreground_is_own_process, foreground_process_name,
                      foreground_window_rect, foreground_window_title,
                      virtual_screen_origin)

MARKER_RGB = (255, 61, 139)  # accent magenta (theme.ACCENT_MAGENTA): the click point


def app_dir() -> Path:
    """Folder the app lives in: next to the exe when frozen, repo root otherwise."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


SESSIONS_DIR = app_dir() / "sessions"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Recorder:
    """Records input events and screenshots into a session folder."""

    ACTION_KINDS = ("click", "type", "key")

    def __init__(self, sessions_dir: Path = SESSIONS_DIR):
        self.sessions_dir = sessions_dir
        self.recording = False
        self.paused = False
        self.mic_ok = False
        self._lock = threading.Lock()
        self._events = []
        self._typing_buffer = []
        self._session_dir = None
        self._shots_dir = None
        self._start_ts = 0
        self._step = 0
        self._mouse_listener = None
        self._kbd_listener = None
        self._mic = MicRecorder()
        self._narration_path = None
        self._shot_queue = queue.Queue()
        self._shot_worker = None
        self._dropped_shots = set()  # deleted before the worker wrote them

    # ---------- lifecycle ----------
    def start(self) -> Path:
        if self.recording:
            return self._session_dir
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_dir = self.sessions_dir / f"session_{stamp}"
        self._shots_dir = self._session_dir / "screenshots"
        self._shots_dir.mkdir(parents=True, exist_ok=True)
        self._events = []
        self._typing_buffer = []
        self._dropped_shots = set()
        self._step = 0
        self.paused = False
        self._start_ts = _now_ms()
        self.recording = True

        self._shot_queue = queue.Queue()
        self._shot_worker = threading.Thread(
            target=self._shot_loop, name="stepcast-shots", daemon=True)
        self._shot_worker.start()

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._kbd_listener = keyboard.Listener(on_press=self._on_press)
        self._mouse_listener.start()
        self._kbd_listener.start()

        self._narration_path = self._session_dir / "narration.wav"
        self.mic_ok = self._mic.start(self._narration_path)

        self._add_event("session_start", note=f"Recording started {_iso()}")
        return self._session_dir

    def stop(self) -> Path | None:
        if not self.recording:
            return None
        self.recording = False
        self._flush_typing(force=True)
        self._add_event("session_end", note=f"Recording stopped {_iso()}")

        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kbd_listener:
            self._kbd_listener.stop()

        # drain pending screenshots, then stop the worker
        if self._shot_worker:
            self._shot_queue.put(None)  # sentinel
            self._shot_worker.join(timeout=30)
            self._shot_worker = None

        wav = self._mic.stop()
        narration, narration_words = [], []
        has_audio = bool(wav and wav.exists() and wav.stat().st_size > 1024)
        if has_audio:
            from .transcribe import transcribe

            result = transcribe(wav)
            narration = result["segments"]
            narration_words = result["words"]

        meta = {
            "version": 1,
            "created": _iso(),
            "duration_ms": _now_ms() - self._start_ts,
            "event_count": len(self._events),
            "has_audio": has_audio,
            "narration": narration,
            "narration_words": narration_words,
            "events": self._events,
        }
        out = self._session_dir / "session.json"
        out.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        saved = self._session_dir
        self._session_dir = None
        self.mic_ok = False
        return saved

    # ---------- read-only helpers for the UI ----------
    def elapsed_ms(self) -> int:
        return _now_ms() - self._start_ts if self.recording else 0

    def session_path(self, rel: str) -> Path | None:
        """Absolute path of a file inside the live session, if recording."""
        d = self._session_dir
        return d / rel if d else None

    # ---------- event helpers ----------
    def _add_event(self, kind: str, **fields) -> int:
        # Win32 lookups happen outside the lock so the input hook never waits
        # on another thread; the step id is allocated atomically inside it.
        ctx = {"window": foreground_window_title(), "app": foreground_process_name()}
        with self._lock:
            step = self._step
            self._step += 1
            self._events.append(
                {"step": step, "t_ms": _now_ms() - self._start_ts, "kind": kind,
                 **ctx, **fields})
        return step

    def _capture_click(self, x: int, y: int, button: str) -> None:
        """Log a click and queue its screenshot. Cheap: the grab/encode/save
        runs on the worker thread so the OS input hook is never blocked."""
        rect = foreground_window_rect()
        # use the active window only if it's sane and contains the click;
        # otherwise fall back to the full virtual screen
        if (rect
                and rect[2] - rect[0] > 50 and rect[3] - rect[1] > 50
                and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]):
            region = {"left": rect[0], "top": rect[1],
                      "width": rect[2] - rect[0], "height": rect[3] - rect[1]}
        else:
            region = None  # worker uses the virtual screen
            ox, oy = virtual_screen_origin()
            rect = (ox, oy)
        ctx = {"window": foreground_window_title(), "app": foreground_process_name()}
        with self._lock:
            step = self._step
            self._step += 1
            name = f"step_{step:04d}.png"
            self._events.append({
                "step": step, "t_ms": _now_ms() - self._start_ts, "kind": "click",
                **ctx, "button": button, "x": x, "y": y,
                # click point inside the saved image (for crop-to-click)
                "img_x": x - rect[0], "img_y": y - rect[1],
                "screenshot": f"screenshots/{name}",
            })
        self._shot_queue.put((name, x, y, region))

    def _shot_loop(self):
        """Background worker: pulls click jobs and writes annotated PNGs.

        mss GDI handles are thread-local on Windows, so the single mss instance
        is created and reused on this thread alone.
        """
        with mss.mss() as sct:
            while True:
                job = self._shot_queue.get()
                if job is None:  # sentinel -> shutdown
                    break
                name = job[0]
                with self._lock:
                    if name in self._dropped_shots:
                        continue
                try:
                    self._write_shot(sct, *job)
                except Exception as e:  # noqa: BLE001
                    print(f"[stepcast] screenshot failed: {e}")

    def _write_shot(self, sct, name: str, x: int, y: int, region) -> None:
        monitor = region or sct.monitors[0]  # [0] = virtual full screen
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        self._draw_marker(img, x - monitor["left"], y - monitor["top"])
        img.save(self._shots_dir / name)

    @staticmethod
    def _draw_marker(img: Image.Image, x: int, y: int, r: int = 22):
        from PIL import ImageDraw

        d = ImageDraw.Draw(img, "RGBA")
        d.ellipse([x - r, y - r, x + r, y + r], outline=(*MARKER_RGB, 255), width=5)
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(*MARKER_RGB, 255))

    # ---------- callbacks (OS hook threads) ----------
    def _ignored(self) -> bool:
        # never record interactions with Stepcast's own windows
        # (clicking Stop, editing a step, etc.)
        return not self.recording or self.paused or foreground_is_own_process()

    def _on_click(self, x, y, button, pressed):
        if not pressed or self._ignored():
            return
        self._flush_typing()
        self._capture_click(int(x), int(y), str(button).replace("Button.", ""))

    def _on_press(self, key):
        if self._ignored():
            return
        try:
            ch = key.char
        except AttributeError:
            ch = None
        if ch is not None:
            # Ctrl/Alt chords arrive as control characters (e.g. Ctrl+Alt+R);
            # they aren't typed text
            if ch.isprintable():
                with self._lock:
                    self._typing_buffer.append(ch)
            return
        special = str(key).replace("Key.", "")
        if special in ("enter", "tab", "esc"):
            self._flush_typing()
            self._add_event("key", key=special)
        elif special == "space":
            with self._lock:
                self._typing_buffer.append(" ")
        elif special == "backspace":
            with self._lock:
                if self._typing_buffer:
                    self._typing_buffer.pop()

    def _flush_typing(self, force: bool = False):
        with self._lock:
            if not self._typing_buffer:
                return
            text = "".join(self._typing_buffer)
            self._typing_buffer = []
        if text.strip() and (force or self.recording):
            self._add_event("type", text=text)

    # ---------- live editing API (thread-safe) ----------
    def toggle_pause(self) -> bool:
        """Flip capture pause. Returns new paused state."""
        self._flush_typing()
        self.paused = not self.paused
        return self.paused

    def action_events(self) -> list[dict]:
        """Snapshot of capturable steps (click/type/key) for the live panel."""
        with self._lock:
            return [dict(e) for e in self._events if e["kind"] in self.ACTION_KINDS]

    def _remove_locked(self, step_id: int) -> bool:
        ev = next((e for e in self._events if e["step"] == step_id), None)
        if not ev:
            return False
        shot = ev.get("screenshot")
        if shot and self._session_dir:
            self._dropped_shots.add(Path(shot).name)
            try:
                (self._session_dir / shot).unlink(missing_ok=True)
            except OSError:
                pass
        self._events.remove(ev)
        return True

    def delete_step(self, step_id: int) -> bool:
        with self._lock:
            return self._remove_locked(step_id)

    def delete_last_action(self) -> bool:
        with self._lock:
            for e in reversed(self._events):
                if e["kind"] in self.ACTION_KINDS:
                    return self._remove_locked(e["step"])
        return False

    def edit_step_text(self, step_id: int, new_text: str) -> bool:
        """Override a step's description (stored as `label`, used by the guide).

        The raw typed `text` is kept: the editor shows the full label
        ('Type "x" in Window'), so writing it back into `text` would nest it.
        """
        with self._lock:
            for e in self._events:
                if e["step"] == step_id:
                    e["label"] = new_text
                    return True
        return False
