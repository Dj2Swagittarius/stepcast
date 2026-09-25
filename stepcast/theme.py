"""Design system for the Tk UI (Figma-inspired: monochrome + pastel blocks).

Monochrome frame (white canvas, black ink, hairlines) + one pastel color
block per window as the state surface. Every text button is a pill, every
icon button a circle. Shapes are rendered with PIL at 4x and downsampled so
curves are antialiased (Tk's canvas draws jagged ovals on Windows).
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

# ------------------------------------------------------------ colors ----
INK = "#000000"
CANVAS = "#ffffff"
HAIRLINE = "#e6e6e6"
HAIRLINE_SOFT = "#f1f1f1"
SURFACE_SOFT = "#f7f7f5"
BLOCK_LIME = "#dceeb1"
BLOCK_LILAC = "#c5b0f4"
BLOCK_CREAM = "#f4ecd6"
BLOCK_PINK = "#efd4d4"
BLOCK_MINT = "#c8e6cd"
BLOCK_CORAL = "#f3c9b6"
ACCENT_MAGENTA = "#ff3d8b"
# interaction states (derived from ink / surface, not new hues)
INK_HOVER = "#262626"
INK_PRESS = "#404040"
SOFT_HOVER = "#eeeeeb"
SOFT_PRESS = "#e3e3df"
DISABLED_FILL = "#ececea"
DISABLED_INK = "#8c8c8c"  # the only non-ink text color: disabled controls

# recorder state -> color-block surface
STATE_BLOCK = {
    "idle": BLOCK_LIME,
    "recording": BLOCK_CORAL,
    "paused": BLOCK_CREAM,
    "busy": BLOCK_LILAC,
}

# ------------------------------------------------------------- icons ----
# Segoe MDL2 Assets codepoints (one stroke family, ships with Windows 10+)
ICON = {
    "record": "\ue7c8", "stop": "\ue71a", "pause": "\ue769", "play": "\ue768",
    "undo": "\ue7a7", "delete": "\ue74d", "edit": "\ue70f", "folder": "\ue8b7",
    "copy": "\ue8c8", "duplicate": "\ue16f", "add": "\ue710", "up": "\ue70e",
    "down": "\ue70d", "save": "\ue74e", "globe": "\ue774", "mic": "\ue720",
    "crop": "\ue7a8", "view": "\ue890", "redact": "\ue8b3", "zoom": "\ue71e",
    "check": "\ue73e", "close": "\ue711", "history": "\ue81c",
}

# ------------------------------------------------------------- scale ----
_S = 1.0
_READY = False


def init(root: tk.Misc) -> None:
    """Idempotent: DPI scale, fonts, ttk styles. Needs a Tk root to exist."""
    global _S, _READY
    if _READY:
        return
    _READY = True
    # tk scaling is pixels-per-point; 96 dpi == 1.333
    _S = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
    _resolve_fonts(root)
    _style_ttk(root)


def px(n: float) -> int:
    """Design pixels -> device pixels under display scaling."""
    return int(round(n * _S))


# ------------------------------------------------------------- fonts ----
# figmaSans' variable axis maps onto Segoe UI's optical weights:
# 320-340 -> Light/Semilight, 480 -> Regular, 540 -> Semibold.
F = {
    "display": ("Segoe UI Light", 22),
    "title": ("Segoe UI Light", 17),
    "headline": ("Segoe UI Semibold", 12),
    "body": ("Segoe UI Semilight", 11),
    "body_sm": ("Segoe UI Semilight", 10),
    "strong": ("Segoe UI Semibold", 10),
    "button": ("Segoe UI", 10),
    "eyebrow": ("Cascadia Mono", 8),
    "caption": ("Cascadia Mono", 8),
    "icon": ("Segoe MDL2 Assets", 10),
    "icon_lg": ("Segoe MDL2 Assets", 12),
}


_FONTS: dict = {}


def font(key: str) -> tkfont.Font:
    """Cached Font object for measuring text."""
    f = _FONTS.get(key)
    if f is None:
        f = _FONTS[key] = tkfont.Font(font=F[key])
    return f


def _resolve_fonts(root):
    have = set(tkfont.families(root))
    mono = next((m for m in ("Cascadia Mono", "Consolas") if m in have), "Courier New")
    for key, (fam, size) in list(F.items()):
        if fam == "Cascadia Mono":
            F[key] = (mono, size)
        elif fam.startswith("Segoe UI") and fam not in have:
            F[key] = ("Segoe UI", size)


def _style_ttk(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Vertical.TScrollbar", background=SURFACE_SOFT,
                    troughcolor=CANVAS, bordercolor=CANVAS, lightcolor=SURFACE_SOFT,
                    darkcolor=SURFACE_SOFT, arrowcolor=INK, gripcount=0,
                    borderwidth=0, arrowsize=px(11))
    style.map("Vertical.TScrollbar", background=[("active", SOFT_PRESS)])
    style.configure("Stepcast.Treeview", background=CANVAS, fieldbackground=CANVAS,
                    foreground=INK, borderwidth=0, font=F["body_sm"],
                    rowheight=px(30))
    # selected = primary surface (spec: pricing-tab-selected)
    style.map("Stepcast.Treeview", background=[("selected", INK)],
              foreground=[("selected", CANVAS)])
    style.configure("Stepcast.Treeview.Heading", background=CANVAS, foreground=INK,
                    font=F["caption"], relief="flat", borderwidth=0,
                    padding=(px(8), px(6)))
    style.map("Stepcast.Treeview.Heading", background=[("active", SURFACE_SOFT)])
    style.layout("Stepcast.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])


# ------------------------------------------------------------ shapes ----
_IMG_CACHE: dict = {}


def rounded_image(w, h, r, fill, bg, outline=None, width=1):
    """Antialiased rounded rectangle as a PhotoImage (cached)."""
    w, h = max(1, int(w)), max(1, int(h))
    key = (w, h, r, fill, bg, outline, width)
    img = _IMG_CACHE.get(key)
    if img is None:
        k = 4
        im = Image.new("RGB", (w * k, h * k), bg)
        ImageDraw.Draw(im).rounded_rectangle(
            [0, 0, w * k - 1, h * k - 1], radius=min(r, h / 2, w / 2) * k,
            fill=fill, outline=outline, width=width * k if outline else 0)
        img = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
        if len(_IMG_CACHE) > 400:
            _IMG_CACHE.clear()
        _IMG_CACHE[key] = img
    return img


# ----------------------------------------------------------- tooltip ----
class Tooltip:
    """Hover label for icon-only buttons (their accessible name)."""

    def __init__(self, widget, text):
        self.widget, self.text, self._tip, self._job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._job = self.widget.after(450, self._show)

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=self.text, bg=INK, fg=CANVAS, font=F["caption"],
                 padx=px(8), pady=px(4)).pack()
        tw.geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ------------------------------------------------------------ button ----
_KINDS = {
    #            fill,        hover,       press,       ink,    outline
    "primary": (INK, INK_HOVER, INK_PRESS, CANVAS, None),
    "secondary": (CANVAS, SURFACE_SOFT, SOFT_PRESS, INK, INK),
    "soft": (SURFACE_SOFT, SOFT_HOVER, SOFT_PRESS, INK, None),
    "ghost": (None, SURFACE_SOFT, SOFT_PRESS, INK, None),
    "danger": (BLOCK_PINK, "#e8c4c4", "#deb3b3", INK, None),
}


class Button(tk.Canvas):
    """Pill (text, optional leading icon) or circle (icon only).

    kinds: primary | secondary | soft | ghost | danger. Keyboard focusable,
    Enter/Space activate, focus ring drawn outside the shape.
    """

    def __init__(self, parent, text="", command=None, kind="secondary", icon=None,
                 bg=CANVAS, height=36, full=False, tooltip=None):
        self._h = px(height)
        self._ring = px(3)
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        self.command = command
        self.kind, self.text, self.icon, self._bg = kind, text, icon, bg
        self.full = full
        self.enabled = True
        self._hover = self._press = self._focus = False
        self._tip = Tooltip(self, tooltip) if tooltip else None
        self._fixed_w = None
        self._layout()
        for seq, fn in (("<Enter>", lambda e: self._flag("_hover", True)),
                        ("<Leave>", lambda e: self._flag("_hover", False, "_press")),
                        ("<ButtonPress-1>", lambda e: self._flag("_press", True)),
                        ("<ButtonRelease-1>", self._release),
                        ("<FocusIn>", lambda e: self._flag("_focus", True)),
                        ("<FocusOut>", lambda e: self._flag("_focus", False)),
                        ("<Return>", lambda e: self.invoke()),
                        ("<space>", lambda e: self.invoke())):
            self.bind(seq, fn, add="+")
        if full:
            self.bind("<Configure>", lambda e: self._draw(e.width), add="+")

    # ----- public -----
    def set(self, text=None, icon=None, kind=None, enabled=None, tooltip=None):
        if text is not None:
            self.text = text
        if icon is not None:
            self.icon = icon or None
        if kind is not None:
            self.kind = kind
        if enabled is not None:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow",
                           takefocus=1 if enabled else 0)
        if tooltip is not None and self._tip:
            self._tip.text = tooltip
        self._layout()

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    # ----- internals -----
    def _flag(self, name, val, also=None):
        setattr(self, name, val)
        if also:
            setattr(self, also, False)
        self._draw()

    def _release(self, e):
        was = self._press
        self._press = False
        self._draw()
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        if was and inside:
            self.invoke()

    def _content_w(self):
        tw = font("button").measure(self.text) if self.text else 0
        iw = font("icon").measure(self.icon) if self.icon else 0
        gap = px(8) if (self.text and self.icon) else 0
        return tw, iw, gap

    def _layout(self):
        tw, iw, gap = self._content_w()
        if self.text:
            w = tw + iw + gap + px(36)
        else:
            w = self._h
        r2 = self._ring * 2
        if not self.full:
            self.configure(width=w + r2)
        self.configure(height=self._h + r2)
        self._draw()

    def _draw(self, width=None):
        self.delete("all")
        r = self._ring
        total_w = width or (self.winfo_width() if self.full else int(self["width"]))
        if total_w <= 2 * r:
            return
        w, h = total_w - 2 * r, self._h
        fill, hov, prs, ink, outline = _KINDS[self.kind]
        if not self.enabled:
            fill, ink, outline = DISABLED_FILL, DISABLED_INK, None
        elif self._press:
            fill = prs
        elif self._hover:
            fill = hov
        fill = fill or self._bg
        if self._focus and self.enabled:
            ring = rounded_image(w + 2 * r, h + 2 * r, (h + 2 * r) / 2, self._bg,
                                 self._bg, outline=INK, width=max(1, px(2)))
            self.create_image(0, 0, image=ring, anchor="nw")
            self._ring_img = ring
        img = rounded_image(w, h, h / 2, fill, self._bg, outline=outline)
        self._img = img  # keep a reference
        self.create_image(r, r, image=img, anchor="nw")
        tw, iw, gap = self._content_w()
        cx, cy = r + w / 2, r + h / 2
        x = cx - (tw + iw + gap) / 2
        if self.icon:
            self.create_text(x + iw / 2, cy + px(1), text=self.icon, fill=ink,
                             font=F["icon"])
            x += iw + gap
        if self.text:
            self.create_text(x + tw / 2, cy, text=self.text, fill=ink,
                             font=F["button"])


class Segmented(tk.Frame):
    """Pill toggle group; selected option takes the primary (black) surface."""

    def __init__(self, parent, options, command=None, bg=CANVAS, height=32):
        super().__init__(parent, bg=bg)
        self.command = command
        self.value = options[0][0]
        self._btns = {}
        for i, (value, label, icon) in enumerate(options):
            b = Button(self, label, lambda v=value: self.select(v), kind="ghost",
                       icon=icon, bg=bg, height=height)
            b.pack(side="left", padx=(0 if i == 0 else px(2), 0))
            self._btns[value] = b
        self._paint()

    def select(self, value, fire=True):
        self.value = value
        self._paint()
        if fire and self.command:
            self.command(value)

    def _paint(self):
        for v, b in self._btns.items():
            b.set(kind="primary" if v == self.value else "ghost")


# -------------------------------------------------------- color block ----
class ColorBlock(tk.Canvas):
    """Rounded pastel panel: mono eyebrow, light headline, body line."""

    def __init__(self, parent, bg=CANVAS, height=132):
        super().__init__(parent, bg=bg, height=px(height), highlightthickness=0, bd=0)
        self._bg = bg
        self._state = (BLOCK_LIME, "", "", "")
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, color, eyebrow="", title="", body=""):
        self._state = (color, eyebrow, title, body)
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        color, eyebrow, title, body = self._state
        self.delete("all")
        self._img = rounded_image(w, h, px(20), color, self._bg)
        self.create_image(0, 0, image=self._img, anchor="nw")
        pad = px(20)
        self.create_text(pad, pad, anchor="nw", text=eyebrow.upper(), fill=INK,
                         font=F["eyebrow"])
        self.create_text(pad, pad + px(20), anchor="nw", text=title, fill=INK,
                         font=F["display"], width=w - 2 * pad)
        self.create_text(pad, h - pad, anchor="sw", text=body, fill=INK,
                         font=F["body_sm"], width=w - 2 * pad)


# ----------------------------------------------------------- helpers ----
def eyebrow(parent, text, bg=CANVAS, **kw):
    return tk.Label(parent, text=text.upper(), bg=bg, fg=INK, font=F["eyebrow"],
                    **kw)


def entry(parent, bg=CANVAS, font=None, **kw):
    """Hairline input; focus is a black ring, not a fill change."""
    return tk.Entry(parent, bg=bg, fg=INK, insertbackground=INK, relief="flat",
                    highlightthickness=1, highlightbackground=HAIRLINE,
                    highlightcolor=INK, font=font or F["body"], **kw)


def hairline(parent, color=HAIRLINE):
    return tk.Frame(parent, bg=color, height=1)


class Toast:
    """Inline, auto-dismissing status text (replaces 'OK' message boxes)."""

    def __init__(self, parent, bg=CANVAS):
        self.label = tk.Label(parent, text="", bg=bg, fg=INK, font=F["caption"])
        self._job = None

    def show(self, text, ms=3500):
        self.label.config(text=text.upper())
        if self._job:
            self.label.after_cancel(self._job)
        self._job = self.label.after(ms, lambda: self.label.config(text=""))


def ask_text(parent, title, label, initial=""):
    """Small styled prompt. Returns the text, or None if cancelled."""
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=CANVAS)
    top.transient(parent)
    top.resizable(False, False)
    top.attributes("-topmost", True)
    result = {"v": None}

    body = tk.Frame(top, bg=CANVAS)
    body.pack(fill="both", expand=True, padx=px(20), pady=px(18))
    eyebrow(body, label).pack(anchor="w")
    var = tk.StringVar(value=initial)
    ent = entry(body, textvariable=var, width=46)
    ent.pack(fill="x", pady=(px(8), px(16)), ipady=px(6))
    bar = tk.Frame(body, bg=CANVAS)
    bar.pack(fill="x")

    def ok(_e=None):
        result["v"] = var.get().strip()
        top.destroy()

    Button(bar, "Save", ok, kind="primary").pack(side="right")
    Button(bar, "Cancel", top.destroy, kind="ghost").pack(side="right", padx=px(4))
    top.bind("<Return>", ok)
    top.bind("<Escape>", lambda e: top.destroy())
    top.update_idletasks()
    x = parent.winfo_rootx() + px(24)
    y = parent.winfo_rooty() + px(80)
    top.geometry(f"+{x}+{y}")
    ent.focus_set()
    ent.select_range(0, "end")
    top.grab_set()
    parent.wait_window(top)
    return result["v"] or None
