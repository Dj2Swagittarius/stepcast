"""Stepcast controller: Tk main loop + tray icon + global hotkey.

Tk runs on the main thread (owns the live panel and editor windows).
The tray icon and the global hotkey run on their own threads and marshal
every action back onto the Tk thread via root.after().
"""
import os
import threading
import tkinter as tk

import pystray
from PIL import Image, ImageDraw
from pynput import keyboard

from . import theme
from .guide import build_guide, build_html
from .recorder import MARKER_RGB, Recorder
from .ui import ControlWindow, Editor


def _make_icon(recording: bool) -> Image.Image:
    """Idle: black ring on white. Recording: magenta dot (the one accent)."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 60, 60], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255),
              width=5)
    d.ellipse([20, 20, 44, 44], fill=(*MARKER_RGB, 255) if recording
              else (0, 0, 0, 255))
    return img


class App:
    def __init__(self):
        self.rec = Recorder()
        self.busy = False
        self._quitting = False

        # hidden Tk root; the control window is the visible Toplevel
        self.root = tk.Tk()
        self.root.withdraw()
        theme.init(self.root)
        self.control = ControlWindow(
            self.root, self.rec, on_toggle=self._toggle, on_quit=self._quit
        )

        self.icon = pystray.Icon("Stepcast", _make_icon(False), "Stepcast")
        self.icon.menu = pystray.Menu(
            pystray.MenuItem(
                lambda i: "Stop recording" if self.rec.recording else "Start recording",
                lambda: self._marshal(self._toggle),
                default=True,
            ),
            pystray.MenuItem("Open sessions folder",
                             lambda: self._open(self.rec.sessions_dir)),
            pystray.MenuItem("Quit", lambda: self._marshal(self._quit)),
        )
        self._hotkey = keyboard.GlobalHotKeys(
            {"<ctrl>+<alt>+r": lambda: self._marshal(self._toggle)}
        )

    # ---------- thread marshaling ----------
    def _marshal(self, fn):
        """Run fn on the Tk thread."""
        self.root.after(0, fn)

    # ---------- actions (Tk thread) ----------
    def _toggle(self):
        if self.busy:
            return
        if self.rec.recording:
            self._stop()
        else:
            self._start()

    def _start(self):
        session = self.rec.start()
        self.icon.icon = _make_icon(True)
        self.icon.title = "Stepcast (RECORDING)"
        self.control.set_recording(True)
        self.icon.notify(f"Recording to {session.name}", "Stepcast")

    def _stop(self):
        self.busy = True
        self.control.set_recording(False)
        self.control.set_busy(True)
        self.icon.notify("Stopped. Transcribing your narration.", "Stepcast")

        def worker():
            session = None
            try:
                session = self.rec.stop()  # heavy: transcription
                if session:
                    build_guide(session)
                    build_html(session)
            except Exception as e:  # noqa: BLE001
                print(f"[stepcast] stop failed: {e}")
            finally:
                # always release the busy state, even if export blew up
                self._marshal(lambda: self._after_stop(session))

        threading.Thread(target=worker, daemon=True).start()

    def _after_stop(self, session):
        self.busy = False
        self.control.set_busy(False)
        self.icon.icon = _make_icon(False)
        self.icon.title = "Stepcast (idle)"
        if self._quitting:
            self._quit()
            return
        if session:
            self.icon.notify(f"Saved {session.name}. Review window is open.",
                             "Stepcast")
            Editor(self.root, session)

    def _open(self, path):
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(str(path))
        except OSError as e:
            print(f"[stepcast] open failed: {e}")

    def _quit(self):
        # finish an in-flight recording in the background (saves + transcribes)
        # instead of freezing the UI; _after_stop calls back here when done
        if self.rec.recording or self.busy:
            self._quitting = True
            self.control.withdraw()
            if not self.busy:
                self._stop()
            return
        self._hotkey.stop()
        self.icon.stop()
        self.root.quit()

    # ---------- run ----------
    def run(self):
        self._hotkey.start()
        threading.Thread(target=self.icon.run, daemon=True).start()
        print("[stepcast] running. Hotkey: Ctrl+Alt+R. Tray icon active.")
        self.root.mainloop()


def main():
    App().run()


if __name__ == "__main__":
    main()
