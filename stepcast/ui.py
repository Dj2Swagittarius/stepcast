"""Tkinter windows: live control panel, session picker, screenshot editor and
post-session guide editor.

Visual system: see theme.py — monochrome frame, pill
buttons, one pastel color block per window carrying recorder state.
"""
import json
import os
import shutil
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageFilter, ImageTk

from . import __version__, theme
from .guide import (build_guide, build_html, build_markdown, build_timeline,
                    plain_action_text)
from .theme import (BLOCK_LIME, CANVAS, F, HAIRLINE, HAIRLINE_SOFT, ICON, INK,
                    STATE_BLOCK, SURFACE_SOFT, Button, ColorBlock, Segmented,
                    Toast, eyebrow, hairline, px)

ACTION_KINDS = ("click", "type", "key")


def _fmt_ms(ms: int) -> str:
    s = int(ms or 0) // 1000
    return f"{s // 60}:{s % 60:02d}"


def _open_path(path) -> None:
    try:
        os.startfile(str(path))
    except OSError as e:
        print(f"[stepcast] open failed: {e}")


# ---------------------------------------------------------------- audio ----
class _Player:
    """Plays slices of the session narration.wav. One playback at a time."""

    def __init__(self, wav_path: Path):
        self.wav = Path(wav_path)
        self._data = None
        self._sr = None

    def available(self) -> bool:
        return self.wav.exists() and self.wav.stat().st_size > 1024

    def _load(self):
        if self._data is None:
            import soundfile as sf

            self._data, self._sr = sf.read(str(self.wav), dtype="float32")
        return self._data, self._sr

    def play(self, start_ms: int = 0, end_ms: int | None = None):
        if not self.available():
            return
        try:
            import sounddevice as sd

            data, sr = self._load()
            a = int(start_ms / 1000 * sr)
            b = len(data) if end_ms is None else int(end_ms / 1000 * sr)
            sd.stop()
            sd.play(data[a:b], sr)
        except Exception as e:  # noqa: BLE001
            print(f"[stepcast] playback failed: {e}")

    @staticmethod
    def stop():
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------- control window ----
class ControlWindow(tk.Toplevel):
    """Persistent always-on-top panel: state block, Start/Stop, live steps."""

    THUMB = (64, 38)

    def __init__(self, master, recorder, on_toggle, on_quit):
        theme.init(master)
        super().__init__(master)
        self.rec = recorder
        self._on_toggle = on_toggle
        self._busy = False
        self._thumbs = {}  # screenshot rel path -> PhotoImage (keep refs)
        self._sig = None   # last rendered signature, to skip useless redraws

        self.title("Stepcast")
        self.configure(bg=CANVAS)
        self.attributes("-topmost", True)
        self.geometry(f"{px(380)}x{px(640)}+{px(40)}+{px(40)}")
        self.minsize(px(340), px(500))
        self.protocol("WM_DELETE_WINDOW", on_quit)
        ttk.Style(self).configure("Live.Stepcast.Treeview",
                                  rowheight=px(self.THUMB[1] + 12))
        pad = px(16)

        top = tk.Frame(self, bg=CANVAS)
        top.pack(fill="x", padx=pad, pady=(px(12), px(8)))
        tk.Label(top, text="Stepcast", font=F["headline"], bg=CANVAS,
                 fg=INK).pack(side="left")
        Button(top, "Sessions", self._browse_sessions, kind="ghost",
               icon=ICON["folder"], height=30).pack(side="right")

        self.block = ColorBlock(self, height=140)
        self.block.pack(fill="x", padx=pad)

        self.toggle_btn = Button(self, "Start capture", self._toggle,
                                 kind="primary", icon=ICON["record"],
                                 height=44, full=True)
        self.toggle_btn.pack(fill="x", padx=pad - px(3), pady=(px(12), px(4)))

        row = tk.Frame(self, bg=CANVAS)
        row.pack(fill="x", padx=pad - px(3))
        self.pause_btn = Button(row, "Pause", self._pause, icon=ICON["pause"],
                                height=32)
        self.pause_btn.pack(side="left")
        self.undo_btn = Button(row, "Undo last", self._del_last,
                               icon=ICON["undo"], height=32)
        self.undo_btn.pack(side="left", padx=px(4))

        # footer first (side=bottom) so the list, not the footer, gives up space
        foot = tk.Frame(self, bg=CANVAS)
        foot.pack(side="bottom", fill="x", padx=pad - px(3), pady=(px(6), px(12)))
        self.edit_btn = Button(foot, "Edit", self._edit, kind="ghost",
                               icon=ICON["edit"], height=30)
        self.edit_btn.pack(side="left")
        self.delsel_btn = Button(foot, "Delete", self._del_sel, kind="ghost",
                                 icon=ICON["delete"], height=30)
        self.delsel_btn.pack(side="left")
        eyebrow(foot, f"v{__version__}  ·  Ctrl+Alt+R").pack(side="right",
                                                        padx=px(3))

        head = tk.Frame(self, bg=CANVAS)
        head.pack(fill="x", padx=pad, pady=(px(16), px(6)))
        eyebrow(head, "Steps").pack(side="left")
        self.count_lbl = eyebrow(head, "")
        self.count_lbl.pack(side="right")

        wrap = tk.Frame(self, bg=CANVAS, highlightbackground=HAIRLINE,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=pad)
        self.tree = ttk.Treeview(wrap, style="Live.Stepcast.Treeview", show="tree",
                                 selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<Double-Button-1>", lambda e: self._edit())
        self.tree.bind("<Return>", lambda e: self._edit())
        self.tree.bind("<F2>", lambda e: self._edit())
        self.tree.bind("<Delete>", lambda e: self._del_sel())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_buttons())
        self.empty = tk.Label(
            wrap, bg=CANVAS, fg=INK, font=F["body_sm"], justify="left",
            wraplength=px(280),
            text="Each click, typed entry and keypress lands here as a step, "
                 "with a thumbnail. Double-click a step to reword it.")
        self.empty.place(x=px(14), y=px(14))

        self._render_state()
        self._sync_buttons()

    # ---------- state ----------
    def _state(self) -> str:
        if self._busy:
            return "busy"
        if self.rec.recording:
            return "paused" if self.rec.paused else "recording"
        return "idle"

    def _render_state(self, n_steps: int = 0):
        state = self._state()
        t = _fmt_ms(self.rec.elapsed_ms())
        steps = f"{n_steps} step" + ("" if n_steps == 1 else "s")
        mic = ("Mic on: narrate as you work." if self.rec.mic_ok
               else "No microphone: capturing steps only.")
        eyebrow_txt, title, body = {
            "idle": ("Ready", "Start capturing",
                     "Press Ctrl+Alt+R from any app. Clicks, typing and "
                     "your voice become a guide."),
            "recording": (f"Recording  {t}", steps, mic),
            "paused": (f"Paused  {t}", steps,
                       "Nothing is captured. Safe to type a password."),
            "busy": ("Transcribing", "Writing your guide",
                     "Runs on this PC. Review opens when it's done."),
        }[state]
        self.block.set(STATE_BLOCK[state], eyebrow_txt, title, body)

    def _sync_buttons(self):
        live = self.rec.recording and not self._busy
        has_sel = bool(self.tree.selection())
        self.pause_btn.set(enabled=live)
        self.undo_btn.set(enabled=live and bool(self.tree.get_children()))
        self.edit_btn.set(enabled=live and has_sel)
        self.delsel_btn.set(enabled=live and has_sel)

    def set_recording(self, recording: bool):
        if recording:
            self.toggle_btn.set(text="Stop capture", icon=ICON["stop"])
            self._sig = None
            self._refresh()
        else:
            self.toggle_btn.set(text="Start capture", icon=ICON["record"])
            self.tree.delete(*self.tree.get_children())
            self._thumbs = {}
            self.empty.place(x=px(14), y=px(14))
            self.count_lbl.config(text="")
            self._render_state()
        self._sync_buttons()

    def set_busy(self, busy: bool):
        self._busy = busy
        self.toggle_btn.set(enabled=not busy)
        self._render_state()
        self._sync_buttons()

    # ---------- live list ----------
    def _thumb_for(self, rel: str | None):
        if not rel:
            return None
        if rel in self._thumbs:
            return self._thumbs[rel]
        path = self.rec.session_path(rel)
        if not path or not path.exists():
            return None  # worker hasn't written it yet; retry next tick
        try:
            img = Image.open(path)
            img.thumbnail((px(self.THUMB[0]), px(self.THUMB[1])))
            tkimg = ImageTk.PhotoImage(img)
        except OSError:
            return None
        self._thumbs[rel] = tkimg
        return tkimg

    def _refresh(self):
        if not self.rec.recording:
            return
        events = self.rec.action_events()
        # thumbnail readiness is part of the signature: screenshots are written
        # by a worker thread, so a row may need a redraw once its PNG lands
        sig = tuple((e["step"], e.get("label"), e.get("text"),
                     self._thumb_for(e.get("screenshot")) is not None)
                    for e in events)
        if sig != self._sig:
            self._sig = sig
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            for n, e in enumerate(events, 1):
                thumb = self._thumb_for(e.get("screenshot"))
                kw = {"image": thumb} if thumb else {}
                self.tree.insert("", "end", iid=str(e["step"]),
                                 text=f"  {n:02d}   {plain_action_text(e)}", **kw)
            if sel and self.tree.exists(sel[0]):
                self.tree.selection_set(sel[0])
            kids = self.tree.get_children()
            if kids and not sel:
                self.tree.see(kids[-1])
            if kids:
                self.empty.place_forget()
            else:
                self.empty.place(x=px(14), y=px(14))
            self.count_lbl.config(text=str(len(kids)) if kids else "")
            self._sync_buttons()
        self.pause_btn.set(text="Resume" if self.rec.paused else "Pause",
                           icon=ICON["play"] if self.rec.paused else ICON["pause"])
        self._render_state(len(events))
        self.after(500, self._refresh)

    # ---------- actions ----------
    def _toggle(self):
        if not self._busy:
            self._on_toggle()

    def _pause(self):
        self.rec.toggle_pause()
        self._sig = None

    def _del_last(self):
        self.rec.delete_last_action()

    def _selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _del_sel(self):
        sid = self._selected_id()
        if sid is not None and self.rec.recording:
            self.rec.delete_step(sid)

    def _edit(self):
        sid = self._selected_id()
        if sid is None or not self.rec.recording:
            return
        ev = next((e for e in self.rec.action_events() if e["step"] == sid), None)
        if not ev:
            return
        new = theme.ask_text(self, "Edit step", "Step description",
                             plain_action_text(ev))
        if new:
            self.rec.edit_step_text(sid, new)

    def _browse_sessions(self):
        SessionPicker(self.master, self.rec.sessions_dir)


# ------------------------------------------------------- session picker ----
class SessionPicker(tk.Toplevel):
    """Browse past sessions; open one in the Editor, show folder, delete."""

    def __init__(self, master, sessions_dir: Path):
        theme.init(master)
        super().__init__(master)
        self.sessions_dir = Path(sessions_dir)
        self.title("Stepcast · Sessions")
        self.configure(bg=CANVAS)
        self.geometry(f"{px(720)}x{px(480)}+{px(90)}+{px(80)}")
        self.minsize(px(520), px(320))
        self.attributes("-topmost", True)
        pad = px(24)

        head = tk.Frame(self, bg=CANVAS)
        head.pack(fill="x", padx=pad, pady=(px(20), px(12)))
        eyebrow(head, "Library").pack(anchor="w")
        tk.Label(head, text="Past sessions", font=F["title"], bg=CANVAS,
                 fg=INK).pack(anchor="w")

        bar = tk.Frame(self, bg=CANVAS)
        bar.pack(side="bottom", fill="x", padx=pad - px(3), pady=px(14))
        self.open_btn = Button(bar, "Open in editor", self._open, kind="primary",
                               icon=ICON["edit"])
        self.open_btn.pack(side="left")
        self.folder_btn = Button(bar, "Show folder", self._open_folder,
                                 icon=ICON["folder"])
        self.folder_btn.pack(side="left", padx=px(4))
        self.del_btn = Button(bar, "Delete", self._delete, kind="danger",
                              icon=ICON["delete"])
        self.del_btn.pack(side="right")

        wrap = tk.Frame(self, bg=CANVAS, highlightbackground=HAIRLINE,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=pad)
        cols = ("title", "when", "length", "steps", "voice")
        self.tree = ttk.Treeview(wrap, style="Stepcast.Treeview", columns=cols,
                                 show="headings", selectmode="browse")
        for c, label, w, anchor in (("title", "GUIDE", 280, "w"),
                                    ("when", "RECORDED", 150, "w"),
                                    ("length", "LENGTH", 70, "e"),
                                    ("steps", "STEPS", 60, "e"),
                                    ("voice", "VOICE", 60, "center")):
            self.tree.heading(c, text=label, anchor=anchor)
            self.tree.column(c, width=px(w), anchor=anchor,
                             stretch=(c == "title"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=1, pady=1)
        self.tree.bind("<Double-Button-1>", lambda e: self._open())
        self.tree.bind("<Return>", lambda e: self._open())
        self.tree.bind("<Delete>", lambda e: self._delete())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync())
        self.empty = tk.Label(wrap, bg=CANVAS, fg=INK, font=F["body_sm"],
                              text="No sessions yet. Record one from the "
                                   "control window and it will show up here.")

        self._reload()
        self.tree.focus_set()

    def _reload(self):
        self.tree.delete(*self.tree.get_children())
        dirs = []
        if self.sessions_dir.exists():
            dirs = sorted((d for d in self.sessions_dir.iterdir()
                           if d.is_dir() and (d / "session.json").exists()),
                          reverse=True)
        for d in dirs:
            try:
                data = json.loads((d / "session.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            try:
                when = datetime.strptime(d.name, "session_%Y%m%d_%H%M%S")
                when_txt = when.strftime("%b %d, %Y  %H:%M")
            except ValueError:
                when_txt = d.name
            if data.get("timeline"):
                steps = sum(1 for r in data["timeline"] if r["kind"] != "narration")
            else:
                steps = sum(1 for e in data.get("events", [])
                            if e.get("kind") in ACTION_KINDS)
            self.tree.insert("", "end", iid=str(d), values=(
                data.get("title") or "Untitled guide", when_txt,
                _fmt_ms(data.get("duration_ms", 0)), steps,
                "Yes" if data.get("has_audio") else "No"))
        kids = self.tree.get_children()
        if kids:
            self.empty.place_forget()
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])
        else:
            self.empty.place(x=px(14), y=px(44))
        self._sync()

    def _sync(self):
        on = bool(self.tree.selection())
        for b in (self.open_btn, self.folder_btn, self.del_btn):
            b.set(enabled=on)

    def _sel(self) -> Path | None:
        sel = self.tree.selection()
        return Path(sel[0]) if sel else None

    def _open(self):
        d = self._sel()
        if d:
            Editor(self.master, d, on_change=self._reload)

    def _open_folder(self):
        d = self._sel()
        if d:
            _open_path(d)

    def _delete(self):
        d = self._sel()
        if not d:
            return
        if messagebox.askyesno("Delete session",
                               f"Permanently delete {d.name}, including its "
                               "screenshots, audio and guide?\n\n"
                               "This cannot be undone.", parent=self,
                               icon="warning", default="no"):
            shutil.rmtree(d, ignore_errors=True)
            self._reload()


# ----------------------------------------------------- screenshot viewer ----
class ShotViewer(tk.Toplevel):
    """Full-size screenshot editor: redact (blur), crop, crop-to-click."""

    def __init__(self, master, img_path: Path, row: dict, on_saved=None):
        super().__init__(master)
        self.path = Path(img_path)
        self.row = row
        self.on_saved = on_saved
        self.backup = self.path.with_suffix(".orig.png")
        self.img = Image.open(self.path).convert("RGB")
        self._rect = None        # canvas rect id
        self._drag = None        # (x0, y0) canvas coords
        self._sel = None         # (x0, y0, x1, y1) canvas coords
        self.dirty = False
        self.max_w = min(px(1200), int(self.winfo_screenwidth() * 0.85))
        self.max_h = min(px(720), int(self.winfo_screenheight() * 0.7))

        self.title(f"Screenshot · {self.path.name}")
        self.configure(bg=CANVAS)
        pad = px(16)

        bar = tk.Frame(self, bg=CANVAS)
        bar.pack(fill="x", padx=pad, pady=(px(12), px(6)))
        self.mode = Segmented(bar, [("view", "View", ICON["view"]),
                                    ("blur", "Redact", ICON["redact"]),
                                    ("crop", "Crop", ICON["crop"])],
                              command=self._mode_changed)
        self.mode.pack(side="left")
        self.apply_btn = Button(bar, "Apply", self._apply, icon=ICON["check"])
        self.apply_btn.pack(side="left", padx=(px(12), 0))
        if self.row.get("x") is not None and self.row.get("y") is not None:
            Button(bar, "Crop to click", self._crop_to_click, kind="soft",
                   icon=ICON["zoom"]).pack(side="left", padx=px(4))
        self.undo_btn = Button(bar, "Undo all", self._undo, kind="ghost",
                               icon=ICON["undo"])
        self.undo_btn.pack(side="left")
        Button(bar, "Save", self._save, kind="primary",
               icon=ICON["save"]).pack(side="right")

        self.hint = eyebrow(self, "")
        self.hint.pack(anchor="w", padx=pad + px(3))

        self.canvas = tk.Canvas(self, bg=SURFACE_SOFT, highlightthickness=0,
                                cursor="arrow")
        self.canvas.pack(padx=pad, pady=(px(8), pad))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda e: self._close())
        self.bind("<Control-s>", lambda e: self._save())

        self._show()
        self._mode_changed("view")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.focus_set()

    # ---------- display ----------
    def _show(self):
        w, h = self.img.size
        self.scale = min(self.max_w / w, self.max_h / h, 1.0)
        disp = self.img.resize((max(1, int(w * self.scale)),
                                max(1, int(h * self.scale))), Image.LANCZOS)
        self.tkimg = ImageTk.PhotoImage(disp)
        self.canvas.config(width=disp.width, height=disp.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.tkimg, anchor="nw")
        self._rect = None
        self._sel = None
        self.apply_btn.set(enabled=False)
        self.undo_btn.set(enabled=self.backup.exists())

    def _mode_changed(self, m):
        self.hint.config(text={
            "view": "Pick Redact or Crop, then drag over the image",
            "blur": "Drag over anything sensitive, then Apply",
            "crop": "Drag the area to keep, then Apply",
        }[m].upper())
        self.canvas.config(cursor="arrow" if m == "view" else "crosshair")
        if self._rect:
            self.canvas.delete(self._rect)
            self._rect = None
        self._sel = None
        self.apply_btn.set(enabled=False)

    # ---------- drag ----------
    def _press(self, e):
        if self.mode.value == "view":
            return
        self._drag = (e.x, e.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y,
                                                  outline=INK, width=2,
                                                  dash=(6, 3))

    def _motion(self, e):
        if self._drag and self._rect:
            self.canvas.coords(self._rect, *self._drag, e.x, e.y)

    def _release(self, e):
        if not self._drag:
            return
        x0, y0 = self._drag
        self._drag = None
        x0, x1 = sorted((x0, e.x))
        y0, y1 = sorted((y0, e.y))
        if x1 - x0 > 4 and y1 - y0 > 4:
            self._sel = (x0, y0, x1, y1)
            self.apply_btn.set(enabled=True)

    # ---------- ops ----------
    def _img_box(self):
        x0, y0, x1, y1 = self._sel
        w, h = self.img.size
        box = (max(0, int(x0 / self.scale)), max(0, int(y0 / self.scale)),
               min(w, int(x1 / self.scale)), min(h, int(y1 / self.scale)))
        return box if box[2] - box[0] > 2 and box[3] - box[1] > 2 else None

    def _ensure_backup(self):
        if not self.backup.exists():
            shutil.copy2(self.path, self.backup)

    def _apply(self):
        if not self._sel:
            return
        box = self._img_box()
        if not box:
            return
        self._ensure_backup()
        if self.mode.value == "blur":
            region = self.img.crop(box).filter(ImageFilter.GaussianBlur(14))
            self.img.paste(region, box)
        elif self.mode.value == "crop":
            self.img = self.img.crop(box)
        self.dirty = True
        self._show()

    def _crop_to_click(self):
        # click coords are global screen coords; the saved shot may be a
        # window crop, so use the image-space coords when the recorder stored them
        x = self.row.get("img_x", self.row.get("x"))
        y = self.row.get("img_y", self.row.get("y"))
        if x is None or y is None:
            return
        w, h = self.img.size
        if not (0 <= x < w and 0 <= y < h):
            x, y = w // 2, h // 2
        cw, ch = min(w, max(320, int(w * 0.45))), min(h, max(200, int(h * 0.45)))
        x0 = min(max(0, x - cw // 2), w - cw)
        y0 = min(max(0, y - ch // 2), h - ch)
        self._ensure_backup()
        self.img = self.img.crop((x0, y0, x0 + cw, y0 + ch))
        self.dirty = True
        self._show()

    def _undo(self):
        if self.backup.exists():
            self.img = Image.open(self.backup).convert("RGB")
            self.dirty = True
            self._show()

    def _save(self):
        self.img.save(self.path)
        self.dirty = False
        if self.on_saved:
            self.on_saved(self.path)
        self._close(force=True)

    def _close(self, force=False):
        if self.dirty and not force:
            if not messagebox.askyesno("Discard changes",
                                       "Discard your unsaved screenshot edits?",
                                       parent=self, default="no"):
                return
        self.destroy()


# ----------------------------------------------------------------- editor ----
class Editor(tk.Toplevel):
    """Post-session review: title/description, step editing with screenshot
    previews + screenshot editing, full transcript with playback, exports."""

    THUMB_W = 320

    def __init__(self, master, session_dir, on_change=None):
        theme.init(master)
        super().__init__(master)
        self.session_dir = Path(session_dir)
        self.on_change = on_change
        self.data = json.loads(
            (self.session_dir / "session.json").read_text(encoding="utf-8"))
        self.rows = build_timeline(self.data)
        self._thumbs = {}   # keep ImageTk refs alive
        self._entries = []  # row index -> Entry widget
        self._undo = None   # (index, row) of the last deleted row
        self._dirty = False
        self.player = _Player(self.session_dir / "narration.wav")

        self.title(f"Stepcast · {self.data.get('title') or self.session_dir.name}")
        self.configure(bg=CANVAS)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(px(940), sw - 80)}x{min(px(860), sh - 100)}"
                      f"+{px(120)}+{px(30)}")
        self.minsize(px(640), px(520))
        pad = px(28)

        # ---- header ----
        head = tk.Frame(self, bg=CANVAS)
        head.pack(fill="x", padx=pad, pady=(px(20), px(12)))
        eyebrow(head, f"Guide  ·  {self.session_dir.name}").grid(
            row=0, column=0, sticky="w")
        self.title_var = tk.StringVar(value=self.data.get("title") or "Process Guide")
        te = tk.Entry(head, textvariable=self.title_var, bg=CANVAS, fg=INK,
                      insertbackground=INK, relief="flat", font=F["display"],
                      highlightthickness=1, highlightbackground=CANVAS,
                      highlightcolor=INK)
        te.grid(row=1, column=0, sticky="ew", pady=(px(2), 0))
        n_steps = sum(1 for r in self.rows if r["kind"] != "narration")
        meta = [_fmt_ms(self.data.get("duration_ms", 0)),
                f"{n_steps} steps", self.data.get("created", "")[:10]]
        if self.data.get("has_audio"):
            meta.append("narrated")
        eyebrow(head, "  ·  ".join(m for m in meta if m)).grid(
            row=2, column=0, sticky="w", pady=(px(2), px(12)))
        eyebrow(head, "Description").grid(row=3, column=0, sticky="w")
        self.desc = tk.Text(head, height=2, bg=CANVAS, fg=INK, insertbackground=INK,
                            relief="flat", font=F["body"], wrap="word",
                            highlightbackground=HAIRLINE, highlightcolor=INK,
                            highlightthickness=1, padx=px(10), pady=px(8))
        self.desc.insert("1.0", self.data.get("description") or "")
        self.desc.grid(row=4, column=0, sticky="ew", pady=(px(4), 0))
        self.desc.edit_modified(False)
        self.desc.bind("<<Modified>>", self._desc_modified)
        head.columnconfigure(0, weight=1)
        self.title_var.trace_add("write", lambda *a: self._touch())

        # ---- tab switch ----
        tabs = tk.Frame(self, bg=CANVAS)
        tabs.pack(fill="x", padx=pad - px(3), pady=(px(4), px(8)))
        n_segs = len(self.data.get("narration", []))
        self.tabs = Segmented(tabs, [("steps", f"Steps  {n_steps}", None),
                                     ("trans", f"Transcript  {n_segs}", None)],
                              command=self._switch_tab)
        self.tabs.pack(side="left")
        self.toast = Toast(tabs)
        self.toast.label.pack(side="right", padx=px(3))
        hairline(self).pack(fill="x")

        # ---- bottom bar (packed before content so it never gets squeezed) ----
        bottom = tk.Frame(self, bg=CANVAS)
        bottom.pack(side="bottom", fill="x")
        hairline(bottom).pack(fill="x")
        bar = tk.Frame(bottom, bg=CANVAS)
        bar.pack(fill="x", padx=pad - px(3), pady=px(12))
        Button(bar, "Save & export", self._export, kind="primary",
               icon=ICON["save"]).pack(side="right")
        Button(bar, "Open web page", self._export_html,
               icon=ICON["globe"]).pack(side="right", padx=px(4))
        Button(bar, "Copy Markdown", self._copy_md,
               icon=ICON["copy"]).pack(side="right")
        Button(bar, "Folder", lambda: _open_path(self.session_dir), kind="ghost",
               icon=ICON["folder"]).pack(side="right", padx=px(4))
        Button(bar, "Add step", lambda: self._add_step(None), kind="soft",
               icon=ICON["add"]).pack(side="left")
        Button(bar, "Delete session", self._delete_session, kind="danger",
               icon=ICON["delete"]).pack(side="left", padx=px(4))

        # ---- content ----
        self.steps_tab = tk.Frame(self, bg=CANVAS)
        self.trans_tab = tk.Frame(self, bg=CANVAS)
        self.steps_canvas, self.body = self._scrollable(self.steps_tab)
        self.trans_canvas, self.tbody = self._scrollable(self.trans_tab)
        self.steps_tab.pack(fill="both", expand=True)
        # wheel events from any child bubble to the Toplevel via bindtags
        self.bind("<MouseWheel>", self._on_wheel)

        self.bind("<Control-s>", lambda e: self._save())
        self.bind("<Control-z>", self._undo_delete)

        self._render_rows()
        self._render_transcript()
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- scaffolding ----------
    def _touch(self):
        self._dirty = True

    def _desc_modified(self, _e=None):
        if self.desc.edit_modified():
            self._touch()
            self.desc.edit_modified(False)

    def _scrollable(self, parent):
        canvas = tk.Canvas(parent, bg=CANVAS, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=CANVAS)
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        return canvas, body

    def _switch_tab(self, value):
        self.player.stop()
        if value == "steps":
            self.trans_tab.pack_forget()
            self.steps_tab.pack(fill="both", expand=True)
        else:
            self.steps_tab.pack_forget()
            self.trans_tab.pack(fill="both", expand=True)

    def _on_wheel(self, e):
        canvas = self.steps_canvas if self.tabs.value == "steps" else self.trans_canvas
        if canvas.yview() != (0.0, 1.0):
            canvas.yview_scroll(int(-e.delta / 120), "units")

    # ---------- steps tab ----------
    def _render_rows(self, focus: int | None = None):
        y = self.steps_canvas.yview()[0]
        for w in self.body.winfo_children():
            w.destroy()
        self._entries = []
        tk.Frame(self.body, bg=CANVAS, height=px(8)).pack()
        step_no = 0
        for i, row in enumerate(self.rows):
            if row["kind"] != "narration":
                step_no += 1
            self._entries.append(self._render_row(i, row, step_no))
        if not self.rows:
            box = tk.Frame(self.body, bg=CANVAS)
            box.pack(fill="x", padx=px(28), pady=px(40))
            tk.Label(box, text="No steps in this guide", font=F["title"],
                     bg=CANVAS, fg=INK).pack(anchor="w")
            tk.Label(box, text="Add a step to write instructions by hand.",
                     font=F["body"], bg=CANVAS, fg=INK).pack(anchor="w")
            Button(box, "Add step", lambda: self._add_step(None), kind="primary",
                   icon=ICON["add"]).pack(anchor="w", pady=px(12))
        tk.Frame(self.body, bg=CANVAS, height=px(24)).pack()
        self.update_idletasks()
        self.steps_canvas.yview_moveto(y)
        if focus is not None and 0 <= focus < len(self._entries):
            ent = self._entries[focus]
            ent.focus_set()
            ent.icursor("end")
            self._scroll_into_view(ent)

    def _scroll_into_view(self, widget):
        self.update_idletasks()
        total = max(1, self.body.winfo_height())
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        self.steps_canvas.yview_moveto(max(0, (top - px(120)) / total))

    def _bind_text(self, ent, i):
        def on_key(e, i=i):
            if self.rows[i]["text"] != e.widget.get():
                self.rows[i]["text"] = e.widget.get()
                self._touch()
        ent.bind("<KeyRelease>", on_key)

    def _render_row(self, i, row, step_no):
        pad = px(28)
        if row["kind"] == "narration":
            # voice sits on the soft surface; no card, no stripe
            card = tk.Frame(self.body, bg=SURFACE_SOFT)
            card.pack(fill="x", padx=pad, pady=(px(2), px(8)))
            head = tk.Frame(card, bg=SURFACE_SOFT)
            head.pack(fill="x", padx=px(10), pady=(px(8), 0))
            if self.player.available():
                Button(head, icon=ICON["play"], kind="ghost", height=26,
                       bg=SURFACE_SOFT, tooltip="Play this part",
                       command=lambda r=row: self.player.play(
                           r.get("t", 0), r.get("end"))).pack(side="left")
            eyebrow(head, f"Narration  {_fmt_ms(row.get('t', 0))}",
                    bg=SURFACE_SOFT).pack(side="left", padx=px(6))
            self._row_buttons(head, i, SURFACE_SOFT)
            ent = theme.entry(card, bg=SURFACE_SOFT, font=F["body_sm"])
            ent.configure(highlightbackground=SURFACE_SOFT)
            ent.insert(0, row["text"])
            ent.pack(fill="x", padx=px(14), pady=(px(2), px(12)), ipady=px(4))
            self._bind_text(ent, i)
            return ent

        card = tk.Frame(self.body, bg=CANVAS, highlightbackground=HAIRLINE,
                        highlightthickness=1)
        card.pack(fill="x", padx=pad, pady=(px(10), px(6)))
        head = tk.Frame(card, bg=CANVAS)
        head.pack(fill="x", padx=px(16), pady=(px(12), 0))
        kind = {"note": "written"}.get(row["kind"], row["kind"])
        eyebrow(head, f"Step {step_no:02d}  ·  {kind}").pack(side="left")
        self._row_buttons(head, i, CANVAS)

        ent = theme.entry(card)
        ent.insert(0, row["text"])
        ent.pack(fill="x", padx=px(16), pady=(px(8), px(14)), ipady=px(6))
        self._bind_text(ent, i)

        shot = row.get("screenshot")
        if shot and (self.session_dir / shot).exists():
            thumb = self._thumb(self.session_dir / shot)
            if thumb:
                holder = tk.Frame(card, bg=CANVAS, highlightbackground=HAIRLINE,
                                  highlightthickness=1)
                holder.pack(padx=px(16), anchor="w")
                lbl = tk.Label(holder, image=thumb, bg=CANVAS, cursor="hand2", bd=0)
                lbl.pack()
                lbl.bind("<Button-1>", lambda e, r=row: self._view_shot(r))
                eyebrow(card, "Click the screenshot to redact or crop").pack(
                    padx=px(16), pady=(px(6), px(14)), anchor="w")
        return ent

    def _row_buttons(self, head, i, bg):
        for icon, tip, cmd in (
                (ICON["delete"], "Delete", lambda i=i: self._delete(i)),
                (ICON["duplicate"], "Duplicate", lambda i=i: self._duplicate(i)),
                (ICON["add"], "Add step below", lambda i=i: self._add_step(i)),
                (ICON["down"], "Move down", lambda i=i: self._move(i, 1)),
                (ICON["up"], "Move up", lambda i=i: self._move(i, -1))):
            Button(head, icon=icon, command=cmd, kind="ghost", height=26, bg=bg,
                   tooltip=tip).pack(side="right")

    def _thumb(self, path):
        key = str(path)
        if key in self._thumbs:
            return self._thumbs[key]
        try:
            img = Image.open(path)
            w = px(self.THUMB_W)
            img = img.resize((w, max(1, int(img.height * w / img.width))),
                             Image.LANCZOS)
            tkimg = ImageTk.PhotoImage(img)
        except OSError:
            return None
        self._thumbs[key] = tkimg
        return tkimg

    def _view_shot(self, row):
        ShotViewer(self, self.session_dir / row["screenshot"], row,
                   on_saved=self._shot_saved)

    def _shot_saved(self, path: Path):
        self._thumbs.pop(str(path), None)
        self._touch()
        self._render_rows()

    # ---------- row ops ----------
    def _delete(self, i):
        self._undo = (i, self.rows.pop(i))
        self._touch()
        self._render_rows()
        self.toast.show("Step removed  ·  Ctrl+Z to undo")

    def _undo_delete(self, _e=None):
        if not self._undo:
            return None
        i, row = self._undo
        self._undo = None
        self.rows.insert(min(i, len(self.rows)), row)
        self._render_rows()
        self.toast.show("Step restored")
        return "break"

    def _move(self, i, d):
        j = i + d
        if 0 <= j < len(self.rows):
            self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
            self._touch()
            self._render_rows()

    def _duplicate(self, i):
        self.rows.insert(i + 1, dict(self.rows[i]))
        self._touch()
        self._render_rows(focus=i + 1)

    def _add_step(self, after: int | None):
        """Insert an empty written step inline and focus it (no dialog)."""
        if self.tabs.value != "steps":
            self.tabs.select("steps")
        idx = len(self.rows) if after is None else after + 1
        t = self.rows[after]["t"] if after is not None and self.rows else (
            self.rows[-1]["t"] if self.rows else 0)
        self.rows.insert(idx, {"kind": "note", "t": t, "text": "",
                               "screenshot": None})
        self._touch()
        self._render_rows(focus=idx)

    # ---------- transcript tab ----------
    def _render_transcript(self):
        for w in self.tbody.winfo_children():
            w.destroy()
        narration = self.data.get("narration", [])
        pad = px(28)
        block = ColorBlock(self.tbody, height=128)
        block.pack(fill="x", padx=pad, pady=(px(16), px(10)))
        if narration:
            block.set(BLOCK_LIME, "Narration",
                      f"{len(narration)} segments, "
                      f"{_fmt_ms(narration[-1].get('end_ms', 0))}",
                      "Fix transcription typos here. Play any line to hear "
                      "what was actually said.")
        else:
            block.set(BLOCK_LIME, "Narration", "Nothing was said",
                      "This session has no transcribed audio. Steps still "
                      "export normally.")
        if self.player.available():
            bar = tk.Frame(self.tbody, bg=CANVAS)
            bar.pack(fill="x", padx=pad - px(3), pady=(0, px(10)))
            Button(bar, "Play all", lambda: self.player.play(0), kind="primary",
                   icon=ICON["play"]).pack(side="left")
            Button(bar, "Stop", self.player.stop,
                   icon=ICON["stop"]).pack(side="left", padx=px(4))
        for idx, seg in enumerate(narration):
            hairline(self.tbody, HAIRLINE_SOFT).pack(fill="x", padx=pad)
            rowf = tk.Frame(self.tbody, bg=CANVAS)
            rowf.pack(fill="x", padx=pad)
            tk.Label(rowf, text=_fmt_ms(seg.get("start_ms", 0)), fg=INK,
                     bg=CANVAS, width=6, anchor="w", font=F["caption"]).pack(
                side="left", padx=(px(4), 0))
            if self.player.available():
                Button(rowf, icon=ICON["play"], kind="ghost", height=26,
                       tooltip="Play segment",
                       command=lambda s=seg: self.player.play(
                           s.get("start_ms", 0), s.get("end_ms"))).pack(side="left")
            ent = theme.entry(rowf)
            ent.configure(highlightbackground=CANVAS)
            ent.insert(0, seg.get("text", ""))
            ent.pack(side="left", fill="x", expand=True, padx=px(8),
                     pady=px(6), ipady=px(4))
            ent.bind("<KeyRelease>",
                     lambda e, idx=idx: self._edit_transcript(idx, e.widget.get()))
        tk.Frame(self.tbody, bg=CANVAS, height=px(24)).pack()

    def _edit_transcript(self, idx: int, text: str):
        narration = self.data.get("narration", [])
        if idx >= len(narration):
            return
        seg = narration[idx]
        old = seg.get("text", "")
        if old == text:
            return
        seg["text"] = text
        self._touch()
        # keep the steps-tab narration row in sync when it's the same span
        for r in self.rows:
            if (r["kind"] == "narration"
                    and r.get("t") == seg.get("start_ms")
                    and r["text"] == old):
                r["text"] = text
                break

    # ---------- persistence / exports ----------
    def _persist(self):
        clean = []
        for r in self.rows:
            if r["kind"] == "note" and not r["text"].strip():
                continue  # an added step left blank is dropped
            row = {"kind": r["kind"], "t": r.get("t", 0),
                   "text": r["text"], "screenshot": r.get("screenshot")}
            for k in ("x", "y", "img_x", "img_y", "end"):
                if r.get(k) is not None:
                    row[k] = r[k]
            clean.append(row)
        self.data["timeline"] = clean
        self.data["title"] = self.title_var.get().strip() or "Process Guide"
        self.data["description"] = self.desc.get("1.0", "end").strip()
        (self.session_dir / "session.json").write_text(
            json.dumps(self.data, indent=2), encoding="utf-8")
        self._dirty = False
        if self.on_change:
            self.on_change()

    def _save(self):
        self._persist()
        build_guide(self.session_dir)
        self.toast.show("Saved")
        return "break"

    def _export(self):
        """Primary export: writes GUIDE.md + GUIDE.html, opens the web page."""
        self._persist()
        build_guide(self.session_dir)
        page = build_html(self.session_dir)
        self.toast.show("Exported GUIDE.md and GUIDE.html")
        _open_path(page)

    def _export_html(self):
        self._persist()
        build_guide(self.session_dir)
        _open_path(build_html(self.session_dir))

    def _copy_md(self):
        self._persist()
        self.clipboard_clear()
        self.clipboard_append(build_markdown(self.session_dir))
        self.toast.show("Markdown copied to clipboard")

    def _delete_session(self):
        if not messagebox.askyesno(
            "Delete session",
            f"Permanently delete {self.session_dir.name} and all its "
            "screenshots, audio and guide?\n\nThis cannot be undone.",
            parent=self, icon="warning", default="no",
        ):
            return
        self.player.stop()
        self.destroy()
        shutil.rmtree(self.session_dir, ignore_errors=True)
        if self.on_change:
            self.on_change()

    def _close(self):
        if self._dirty:
            ans = messagebox.askyesnocancel(
                "Unsaved changes", "Save your changes to this guide?",
                parent=self)
            if ans is None:
                return
            if ans:
                self._save()
        self.player.stop()
        self.destroy()
