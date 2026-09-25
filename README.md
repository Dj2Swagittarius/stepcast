# Stepcast

Local screen-process recorder. Captures your clicks, keystrokes, active
windows, and **spoken narration** across the **whole desktop**, screenshots each
click, transcribes your voice, and exports a **step-by-step Markdown guide** with
your explanations woven in — ideal as training context for an AI on how you do a
task. 100% local, nothing leaves your machine.

## Portable exe

Current version: **2.0.0** (shown in the control window footer and the exe's
file properties). Bump it in `stepcast/__init__.py` **and** `version_info.txt`.

`dist\Stepcast.exe` is a self-contained single-file build — copy it anywhere
(USB stick, another PC) and run it; no Python needed. It creates `sessions\`
and `models\` (Whisper model, downloaded once, ~145 MB) next to the exe.

Rebuild it with (Python 3.13 env that has the requirements installed):

```powershell
pip install pyinstaller
py -3.13 -m PyInstaller --noconfirm --clean Stepcast.spec
```

`Stepcast.spec` bundles faster-whisper/ctranslate2/onnxruntime, embeds the
version resource, and excludes heavy optional packages (torch, tensorflow,
transformers, pandas, scipy, cv2…) that would otherwise balloon the exe.

## Install (from source)

```powershell
pip install -r requirements.txt
```

## Run

```powershell
python run.py
```

A **control window** appears (plus a tray icon: black dot = idle, pink dot = recording).
Its colored panel always shows state: **lime** ready · **coral** recording (with
timer, step count, mic status) · **cream** paused · **lilac** transcribing.

- **Toggle recording:** the black **Start/Stop capture** button, `Ctrl+Alt+R`,
  or right-click the tray icon. All three do the same thing.
- **Talk as you work** — your mic records the whole time. Explain what you're doing
  and why; it gets transcribed and woven into the guide as quotes at the right step.
- Clicks and typing inside Stepcast's own windows are never recorded.
- While recording, the step list shows **screenshot thumbnails**. Fix mistakes
  without stopping:
  - **Pause/Resume** — stop capturing temporarily (e.g. to type a password).
  - **Undo last** — drop the step you just botched.
  - **Edit / Delete** (or double-click, `F2`, `Del`) — reword or remove any step.
  - **Sessions** — browse old recordings; open any in the editor, show its
    folder, or delete it.
- On stop, it transcribes, then opens the **Review window**:
  - Set a **guide title and description**.
  - **Steps** — edit any step's text inline; per-step icons move up/down,
    duplicate, add a step below, or delete (`Ctrl+Z` restores the last delete).
    Click a screenshot to open the **screenshot editor**: **Redact** (blur),
    **Crop**, **Crop to click**, undo all, save. Originals are kept as `*.orig.png`.
  - **Transcript** — the full narration with timestamps; fix typos inline,
    **play** any segment (or play all) to check it.
  - **Save & export** writes `GUIDE.md` + a standalone `GUIDE.html` (screenshots
    embedded) and opens it; **Open web page**, **Copy Markdown**, **Folder**.
    `Ctrl+S` saves. Closing with unsaved edits asks first.
  - **Delete session** wipes the whole recording if it's no good.
- Quitting mid-recording saves and transcribes the session before exiting.

First stop after install downloads the Whisper `base` model (~145 MB, one time).
Transcription runs locally on CPU — a few seconds per minute of audio.
No mic? Recording still works; it just skips narration.

## Output

Each recording lands in `sessions/session_<timestamp>/`:

```
session.json          raw event log (timestamps, coords, keys, windows, narration)
screenshots/          one PNG per click — active window only, circle on the click point
narration.wav         your spoken mic audio for the session
GUIDE.md              step-by-step guide: screenshots + narration quotes
GUIDE.html            optional standalone HTML export (screenshots embedded)
```

Feed `session.json` (raw structured data) or `GUIDE.md` (readable) straight into
Claude as context to teach it your process.

## What gets captured

| Event   | Logged as |
|---------|-----------|
| Mouse click | button, x/y, screenshot, active window/app |
| Typing  | full text content, flushed on click / Enter / Tab / Esc |
| Keys    | Enter / Tab / Esc as discrete steps |
| Voice   | mic → `narration.wav`, transcribed (Whisper) into guide quotes |

## ⚠️ Privacy

Keystroke capture records **full text**, including anything typed while recording
— **passwords, secrets, card numbers**. Stop recording before entering sensitive
data, or delete the relevant `type` events from `session.json` afterward.
All data stays in the local `sessions/` folder. To redact typed content, change
`_flush_typing` in `stepcast/recorder.py` to log field focus instead of text.

## Rebuild a guide manually

```powershell
python -m stepcast.guide sessions\session_<timestamp>
```

## Files

```
run.py                 entry point
stepcast/recorder.py     global hooks + screenshots -> session.json
stepcast/guide.py        session.json -> GUIDE.md
stepcast/app.py          tray icon + hotkey
stepcast/winutil.py      active-window detection (ctypes, no pywin32)
stepcast/audio.py        mic capture -> narration.wav
stepcast/transcribe.py   narration.wav -> timestamped text (faster-whisper)
stepcast/ui.py           control window (Start/Stop + live edit) + review editor (Tkinter)
stepcast/theme.py        design tokens + pill/circle buttons, color blocks (see DESIGN-figma.md)
```

## Re-edit a finished session

Easiest: control window → **📁 Past sessions…** → double-click a session.
Or from a shell:

```powershell
python -c "import tkinter as tk; from stepcast.ui import Editor; r=tk.Tk(); r.withdraw(); Editor(r, r'sessions\session_<timestamp>'); r.mainloop()"
```

Edits save back into `session.json` (as a `timeline`) and re-export `GUIDE.md`.
