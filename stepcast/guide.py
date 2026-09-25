"""Turn a recorded session.json into a step-by-step Markdown guide."""
import base64
import html as _html
import json
from pathlib import Path


def plain_action_text(ev: dict) -> str:
    """Editable plain-text label for a step (no markdown), for the editor."""
    if ev.get("label"):
        return ev["label"]
    win = ev.get("window") or ev.get("app") or "the screen"
    kind = ev["kind"]
    if kind == "click":
        btn = ev.get("button", "left")
        verb = "Click" if btn == "left" else f"{btn.capitalize()}-click"
        return f'{verb} at ({ev["x"]}, {ev["y"]}) in {win}'
    if kind == "type":
        return f'Type "{ev["text"]}" in {win}'
    if kind == "key":
        return f'Press {ev["key"].upper()} in {win}'
    return kind


def _narration_words(data: dict) -> list[dict]:
    """Word stream with timestamps. Falls back to interpolating word times
    inside segment spans for sessions recorded before word-level support."""
    words = data.get("narration_words")
    if words:
        return sorted(words, key=lambda w: w["start_ms"])
    out = []
    for s in data.get("narration", []):
        toks = s.get("text", "").split()
        if not toks:
            continue
        t0 = s.get("start_ms", 0)
        span = max(1, s.get("end_ms", t0) - t0)
        for k, tok in enumerate(toks):
            out.append(
                {
                    "start_ms": t0 + span * k // len(toks),
                    "end_ms": t0 + span * (k + 1) // len(toks),
                    "word": tok,
                }
            )
    return out


def _narration_chunks(data: dict, cuts: list[int]) -> list[dict]:
    """Split the narration word stream at each action timestamp so each chunk
    lands under the step it was spoken at."""
    words = _narration_words(data)
    if not words:
        return []
    chunks, cur, ci = [], [], 0
    for w in words:
        if ci < len(cuts) and w["start_ms"] >= cuts[ci]:
            while ci < len(cuts) and w["start_ms"] >= cuts[ci]:
                ci += 1
            if cur:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return [
        {
            "kind": "narration",
            "t": c[0]["start_ms"],
            "end": c[-1]["end_ms"],
            "text": " ".join(x["word"] for x in c),
            "screenshot": None,
        }
        for c in chunks
    ]


def build_timeline(data: dict) -> list[dict]:
    """Unified, time-ordered rows (actions + narration) for the editor."""
    if data.get("timeline"):
        return [dict(r) for r in data["timeline"]]
    rows = []
    for e in data.get("events", []):
        if e["kind"] in ("click", "type", "key"):
            row = {
                "kind": e["kind"],
                "t": e.get("t_ms", 0),
                "text": plain_action_text(e),
                "screenshot": e.get("screenshot"),
            }
            if e["kind"] == "click":
                for k in ("x", "y", "img_x", "img_y"):
                    if e.get(k) is not None:
                        row[k] = e[k]
            rows.append(row)
    cuts = sorted(r["t"] for r in rows)
    rows.extend(_narration_chunks(data, cuts))
    # ties: action first, then the narration spoken right after it
    rows.sort(key=lambda r: (r["t"], 1 if r["kind"] == "narration" else 0))
    return rows


def _fmt_duration(ms: int) -> str:
    sec = int(ms or 0) // 1000
    return f"{sec // 60}:{sec % 60:02d}"


def _meta_parts(data: dict, rows: list[dict]) -> list[str]:
    steps = sum(1 for r in rows if r["kind"] != "narration")
    parts = [str(data.get("created", ""))[:10],
             _fmt_duration(data.get("duration_ms", 0)),
             f"{steps} step" + ("" if steps == 1 else "s")]
    if data.get("has_audio"):
        parts.append("narrated")
    return [p for p in parts if p]


def _header(data: dict, rows: list[dict]) -> list[str]:
    lines = [
        f"# {data.get('title') or 'Process Guide'}",
        "",
        f"*Recorded {' · '.join(_meta_parts(data, rows))}*",
        "",
    ]
    if data.get("description"):
        lines += [data["description"], ""]
    lines += ["---", ""]
    return lines


def _timeline_to_md(data: dict, rows: list[dict]) -> str:
    lines = _header(data, rows)
    step = 0
    for r in rows:
        if r["kind"] == "narration":
            lines.append(f"> **Narration:** {r['text']}")
            lines.append("")
        else:
            step += 1
            lines.append(f"### Step {step}: {r['text']}")
            if r.get("screenshot"):
                lines.append("")
                lines.append(f"![Step {step}]({r['screenshot']})")
            lines.append("")
    if step == 0 and not data.get("has_audio"):
        lines.append("_No actionable steps captured._")
    return "\n".join(lines)


def build_guide(session_dir: Path) -> Path:
    """Read session.json, write GUIDE.md alongside it. Returns the guide path.

    Renders from the saved (edited) timeline when present, otherwise from
    raw events with narration split per-step by word timestamps.
    """
    session_dir = Path(session_dir)
    data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    out = session_dir / "GUIDE.md"
    out.write_text(_timeline_to_md(data, build_timeline(data)), encoding="utf-8")
    return out


def build_markdown(session_dir: Path) -> str:
    """Render the session to a Markdown string (without writing a file)."""
    session_dir = Path(session_dir)
    data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    return _timeline_to_md(data, build_timeline(data))


# Visual system: same tokens as theme.py. Monochrome editorial page, mono eyebrows,
# one lime color block to close the guide. No shadows, no gradients.
_HTML_CSS = """
:root{--ink:#000;--canvas:#fff;--hairline:#e6e6e6;--hairline-soft:#f1f1f1;
  --soft:#f7f7f5;--lime:#dceeb1;
  --sans:"Segoe UI Variable Display","Segoe UI",system-ui,-apple-system,
    "Helvetica Neue",Arial,sans-serif;
  --mono:"Cascadia Mono","SF Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--canvas);color:var(--ink);font-family:var(--sans);
  font-weight:350;font-size:18px;line-height:1.45;letter-spacing:-.01em;
  font-kerning:normal}
main{max-width:880px;margin:0 auto;padding:96px 48px}
.eyebrow{font-family:var(--mono);font-size:13px;font-weight:400;
  letter-spacing:.06em;text-transform:uppercase;margin:0 0 12px}
h1{font-size:clamp(40px,7vw,72px);font-weight:300;line-height:1.02;
  letter-spacing:-.025em;margin:0 0 20px;text-wrap:balance}
.lede{font-size:clamp(20px,2.4vw,26px);font-weight:300;line-height:1.35;
  letter-spacing:-.01em;max-width:34em;margin:24px 0 0}
.flow{margin-top:72px}
.step{border-top:1px solid var(--hairline);padding:32px 0 40px}
.step h2{font-size:clamp(20px,2.2vw,24px);font-weight:450;line-height:1.35;
  letter-spacing:-.012em;margin:0;max-width:38em;overflow-wrap:anywhere}
.step figure{margin:24px 0 0}
.step img{display:block;max-width:100%;height:auto;border:1px solid var(--hairline);
  border-radius:8px}
.narr{background:var(--soft);border-radius:8px;padding:20px 24px;margin:0 0 32px}
.narr p:last-child{margin:0;font-size:18px;font-weight:300;max-width:40em}
.end{background:var(--lime);border-radius:24px;padding:48px;margin-top:48px}
.end p:last-child{margin:0;font-size:clamp(20px,2.4vw,26px);font-weight:300;
  line-height:1.35}
@media (max-width:768px){
  main{padding:48px 16px}
  .end{border-radius:0;margin-left:-16px;margin-right:-16px;padding:40px 16px}
  .narr{padding:16px}
}
@media print{
  main{padding:0}
  .step,.narr{break-inside:avoid}
}
"""


def build_html(session_dir: Path) -> Path:
    """Export the session to a standalone GUIDE.html with embedded screenshots."""
    session_dir = Path(session_dir)
    data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    rows = build_timeline(data)
    esc = _html.escape

    title = esc(data.get("title") or "Process Guide")
    meta = esc("  ·  ".join(_meta_parts(data, rows)))
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{title}</title><style>{_HTML_CSS}</style></head><body><main>",
        "<header>",
        "<p class='eyebrow'>Process guide</p>",
        f"<h1>{title}</h1>",
        f"<p class='eyebrow'>{meta}</p>",
    ]
    if data.get("description"):
        parts.append(f"<p class='lede'>{esc(data['description'])}</p>")
    parts.append("</header><div class='flow'>")

    step = 0
    for r in rows:
        text = esc(r["text"])
        if r["kind"] == "narration":
            parts.append(
                f"<aside class='narr'><p class='eyebrow'>Narration · "
                f"{_fmt_duration(r.get('t', 0))}</p><p>{text}</p></aside>")
            continue
        step += 1
        parts.append(f"<section class='step' id='step-{step}'>"
                     f"<p class='eyebrow'>Step {step:02d}</p><h2>{text}</h2>")
        shot = r.get("screenshot")
        if shot and (session_dir / shot).exists():
            b64 = base64.b64encode((session_dir / shot).read_bytes()).decode()
            parts.append(f"<figure><img src='data:image/png;base64,{b64}' "
                         f"alt='Screenshot for step {step}: {text}' "
                         f"loading='lazy'></figure>")
        parts.append("</section>")

    parts.append(
        "</div><footer class='end'><p class='eyebrow'>End of guide</p>"
        f"<p>{step} step{'' if step == 1 else 's'}, recorded locally "
        "with Stepcast.</p></footer>")
    parts.append("</main></body></html>")
    out = session_dir / "GUIDE.html"
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m stepcast.guide <session_dir>")
        raise SystemExit(1)
    print(build_guide(Path(sys.argv[1])))
