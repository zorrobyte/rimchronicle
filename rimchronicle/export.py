"""Export a chronicle as one self-contained HTML book (images inline as data URIs).

After the chapters: the people of the colony (from people.json, portraits inline) and a contact sheet of the
daily wide shots from the session record.
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Any

from .images import data_url, to_jpeg
from .store import Chronicle, Store
from .timeline import Timeline
from .voices import get_voice

FONTS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap" rel="stylesheet">'

BOOK_CSS = """
:root{--bg:#f6f1e7;--paper:#fffdf8;--ink:#2a2420;--muted:#6f655b;--rule:#d9cfbf;--accent:#8a3b2a;--pill:#ebe3d3;--shadow:0 1px 2px rgba(0,0,0,.06),0 12px 32px rgba(60,40,20,.10)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#17150f;--paper:#1f1c15;--ink:#ece4d4;--muted:#a99e8c;--rule:#3a352b;--accent:#e08a5a;--pill:#2b261d;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35)}}
:root[data-theme="dark"]{--bg:#17150f;--paper:#1f1c15;--ink:#ece4d4;--muted:#a99e8c;--rule:#3a352b;--accent:#e08a5a;--pill:#2b261d;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Newsreader",Georgia,"Times New Roman",serif;font-size:19px;line-height:1.6;padding:0 16px}
.book{max-width:65ch;margin:0 auto;padding:48px 0 96px}
.cover{text-align:center;padding:64px 0 40px;border-bottom:1px solid var(--rule);margin-bottom:48px}
.cover .kicker,.chapter .kicker,.appendix .kicker{font-variant:all-small-caps;letter-spacing:.14em;color:var(--muted);font-size:.85em}
.cover h1{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:2.6em;line-height:1.1;margin:.2em 0 .3em}
.cover .meta{color:var(--muted);font-style:italic}
.chapter{margin:0 0 72px}
.chapter h2,.appendix h2{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:1.9em;line-height:1.15;margin:.2em 0 .5em}
figure{margin:24px calc(50% - 50vw + 16px);max-width:calc(100vw - 32px)}
@media (min-width: 900px){figure{margin:28px -12ch;max-width:none}}
figure img{width:100%;height:auto;display:block;border-radius:6px;box-shadow:var(--shadow)}
figcaption{font-size:.8em;color:var(--muted);text-align:center;margin-top:8px;font-style:italic}
.chapter p{margin:0 0 1em}.chapter p:first-of-type::first-letter{font-family:"Fraunces",Georgia,serif;font-size:3.2em;float:left;line-height:.8;padding:6px 8px 0 0;color:var(--accent)}
.state{font-family:ui-monospace,Menlo,monospace;font-size:.72em;color:var(--muted);border-top:1px solid var(--rule);padding-top:10px;margin-top:20px}
.epitaph{text-align:center;font-style:italic;padding:48px 0;border-top:1px solid var(--rule)}
.appendix{border-top:1px solid var(--rule);padding-top:40px;margin:48px 0 0}
.appendix h3{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:1.3em;margin:36px 0 12px}
.people{display:grid;grid-template-columns:1fr;gap:16px}
@media (min-width: 900px){.people{grid-template-columns:1fr 1fr;margin:0 -8ch}}
.person{background:var(--paper);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;font-size:.84em;line-height:1.45;break-inside:avoid}
.person .head{display:flex;gap:12px;align-items:center;margin-bottom:6px}
.person .portrait{flex:none;width:56px;height:56px;border-radius:50%;overflow:hidden;background:var(--pill);display:grid;place-items:center;border:1px solid var(--rule);font-family:"Fraunces",Georgia,serif;font-size:1.5em;color:var(--accent)}
.person .portrait img{width:100%;height:100%;object-fit:cover;display:block}
.person h4{margin:0;font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:1.25em;line-height:1.1}
.person .sub{color:var(--muted);font-size:.9em}
.person .back{font-style:italic;margin:0 0 6px}
.person .pills{display:flex;flex-wrap:wrap;gap:4px;margin:0 0 6px}
.person .pills span{background:var(--pill);border-radius:999px;padding:0 8px;font-size:.82em}
.person .line{margin:2px 0}.person .line b{font-weight:500;color:var(--muted);font-variant:all-small-caps;letter-spacing:.08em;margin-right:6px}
.person .died{color:var(--accent);font-style:italic}
.person ul{margin:6px 0 0;padding-left:18px}.person li{margin:1px 0}.person li small{color:var(--muted);font-family:ui-monospace,Menlo,monospace;font-size:.8em;margin-right:6px}
.sheet{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
@media (min-width: 900px){.sheet{margin:0 -12ch}}
.sheet figure{margin:0;max-width:none}.sheet img{border-radius:4px;aspect-ratio:4/3;object-fit:cover}.sheet figcaption{margin-top:4px;font-size:.72em}
.colophon{color:var(--muted);font-size:.8em;text-align:center;border-top:1px solid var(--rule);padding-top:24px;margin-top:48px}
"""


def paragraphs(body: str) -> tuple[str, str]:
    """Split the body into HTML paragraphs and the trailing 'State of the colony' line."""
    body = body.strip()
    state = ""
    lines = body.split("\n")
    if lines and lines[-1].strip().startswith("State of the colony:"):
        state = lines[-1].strip()
        body = "\n".join(lines[:-1]).strip()
    paras = [p.strip() for p in body.replace("\r", "").split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras), state


def _inline(path: Path | None, max_width: int | None = None, quality: int = 85) -> str:
    if path is None or not path.exists():
        return ""
    try:
        data = path.read_bytes()
        if max_width:
            data = to_jpeg(data, quality=quality, max_width=max_width)
        return data_url(data)
    except Exception:  # noqa: BLE001
        return ""


def _humanize(s: Any) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(s or "")).replace("_", " ").strip().lower()


def _person(d: dict[str, Any], fdir: Path | None, kind: str = "here") -> str:
    e = html.escape
    name = str(d.get("name") or "?")
    who = [str(d["age"]) if d.get("age") is not None else "", {"Female": "woman", "Male": "man"}.get(str(d.get("gender") or ""), str(d.get("gender") or "").lower())]
    sub = ", ".join(x for x in who if x)
    if d.get("room"):
        sub += (", " if sub else "") + f"sleeps in the {str(d['room']).lower()}"
    src = _inline(fdir / str(d["portrait"]), max_width=192, quality=80) if fdir is not None and d.get("portrait") else ""
    portrait = f'<img src="{src}" alt="">' if src else e(name[:1])
    parts = [f'<div class="person"><div class="head"><div class="portrait">{portrait}</div><div><h4>{e(name)}</h4><div class="sub">{e(sub)}</div></div></div>']
    if kind == "fallen":
        died = d.get("died") or {}
        cause = _humanize(died.get("cause")) or ""
        parts.append(f'<div class="died">Died on day {e(str(died.get("day", "?")))}' + (f", {e(cause)}" if cause else "") + (f'. {e(str(died["text"]))}' if died.get("text") else "") + "</div>")
    elif kind == "gone":
        parts.append('<div class="died">Gone from the colony' + (f", since day {e(str(d['absent_day']))}" if d.get("absent_day") is not None else "") + ", not known dead.</div>")
    back = [str(b) for b in (d.get("childhood"), d.get("adulthood")) if b]
    if len(back) == 2:
        parts.append(f'<p class="back">{e(back[0])}, then {e(back[1][0].lower() + back[1][1:])}.</p>')
    elif back:
        parts.append(f'<p class="back">{e(back[0])}.</p>')
    if d.get("traits"):
        parts.append('<div class="pills">' + "".join(f"<span>{e(str(t))}</span>" for t in d["traits"]) + "</div>")
    sk = d.get("skills") or d.get("top_skills")
    if sk:
        parts.append(f'<div class="line"><b>Skills</b>{e(str(sk))}</div>')
    if d.get("relations"):
        parts.append(f'<div class="line"><b>Relations</b>{e("; ".join(str(r) for r in d["relations"]))}</div>')
    if d.get("health"):
        parts.append(f'<div class="line"><b>Health</b>{e(", ".join(str(h) for h in d["health"]))}</div>')
    arc = d.get("arc") or []
    if arc:
        parts.append('<div class="line"><b>So far</b></div><ul>' + "".join(f'<li><small>day {e(str(a.get("day", "?")))}</small>{e(str(a.get("text", "")))}</li>' for a in arc[-8:]) + "</ul>")
    parts.append("</div>")
    return "".join(parts)


def people_section(chron: Chronicle, people: dict[str, Any], fdir: Path | None) -> str:
    cols = people.get("colonists") or {}
    fallen = people.get("fallen") or {}
    here = [d for d in cols.values() if isinstance(d, dict) and not d.get("absent")]
    gone = [d for d in cols.values() if isinstance(d, dict) and d.get("absent")]
    dead = [d for d in fallen.values() if isinstance(d, dict)]
    if not here and not gone and not dead:
        return ""
    parts = [f'<section class="appendix" id="people"><div class="kicker">Appendix</div><h2>The people of {html.escape(chron.title)}</h2>']
    if here:
        parts.append('<div class="people">' + "".join(_person(d, fdir) for d in here) + "</div>")
    if dead:
        parts.append('<h3>The fallen</h3><div class="people">' + "".join(_person(d, fdir, "fallen") for d in dead) + "</div>")
    if gone:
        parts.append('<h3>Gone</h3><div class="people">' + "".join(_person(d, fdir, "gone") for d in gone) + "</div>")
    parts.append("</section>")
    return "".join(parts)


def days_section(frames: list[dict[str, Any]], fdir: Path | None, max_days: int = 60) -> str:
    """One small wide shot per in-game day (the daily shot when there is one, else the chapter's base shot)."""
    if fdir is None:
        return ""
    by_day: dict[int, dict[str, Any]] = {}
    for f in sorted(frames, key=lambda r: float(r.get("t") or 0)):
        if f.get("shot") not in ("daily", "base") or f.get("day") is None or not f.get("file"):
            continue
        day = int(f["day"])
        if day not in by_day or (f["shot"] == "daily" and by_day[day].get("shot") != "daily"):
            by_day[day] = f
    days = sorted(by_day)
    if len(days) > max_days:
        picked = sorted({round(i * (len(days) - 1) / (max_days - 1)) for i in range(max_days)})
        days = [days[i] for i in picked]
    cells = []
    for day in days:
        src = _inline(fdir / str(by_day[day]["file"]), max_width=360, quality=70)
        if src:
            cells.append(f'<figure><img src="{src}" alt="day {day}" loading="lazy"><figcaption>day {day}</figcaption></figure>')
    if not cells:
        return ""
    return f'<section class="appendix" id="days"><div class="kicker">Appendix</div><h2>Days</h2><div class="sheet">{"".join(cells)}</div></section>'


def render_book(chron: Chronicle, store: Store | None = None, image_dir: Path | None = None) -> str:
    idir = image_dir or (store.image_dir(chron.id) if store else None)
    fdir = store.frames_dir(chron.id) if store else (image_dir.parent / "frames" if image_dir else None)
    e = html.escape
    parts = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             f"<title>{e(chron.title)}: a RimWorld chronicle</title>{FONTS}<style>{BOOK_CSS}</style></head><body><main class=\"book\">"]
    ident = ", ".join(x for x in (f"of the faction {chron.faction}" if chron.faction else "", f"seed {chron.seed}" if chron.seed and chron.name else "") if x)
    meta = ", ".join(x for x in (chron.scenario, chron.storyteller, chron.difficulty) if x)
    status = "the colony endures" if chron.status == "running" else "the record is closed"
    parts.append(f"<header class=\"cover\"><div class=\"kicker\">A RimWorld chronicle</div><h1>{e(chron.title)}</h1>"
                 + (f"<div class=\"meta\">{e(ident)}</div>" if ident else "")
                 + (f"<div class=\"meta\">{e(meta)}</div>" if meta else "")
                 + f"<div class=\"meta\">{chron.days_survived} days, {len(chron.chapters)} chapters, {status}</div></header>")
    for ch in chron.chapters:
        voice = get_voice(ch.voice).name if ch.voice else ""
        parts.append(f"<article class=\"chapter\" id=\"ch{ch.k}\"><div class=\"kicker\">Chapter {ch.k}, day {ch.day}, hour {ch.hour:02d}" + (f" &middot; {e(voice)}" if voice else "") + f"</div><h2>{e(ch.title)}</h2>")
        for im in ch.images:
            base = fdir if im.get("frame") else idir
            src = _inline(base / im["file"]) if base is not None else ""
            if src:
                parts.append(f"<figure><img src=\"{src}\" alt=\"{e(im.get('caption', ''))}\"><figcaption>{e(im.get('caption', ''))}</figcaption></figure>")
        body_html, state = paragraphs(ch.body)
        parts.append(body_html)
        if state:
            parts.append(f"<div class=\"state\">{e(state)}</div>")
        parts.append("</article>")
    if chron.epitaph:
        parts.append(f"<div class=\"epitaph\">{e(chron.epitaph)}</div>")
    if store is not None:
        people = store.read_json(store.people_path(chron.id), {"colonists": {}, "fallen": {}})
        parts.append(people_section(chron, people if isinstance(people, dict) else {}, fdir))
        try:
            frames = Timeline(store).frames(chron.id)
        except Exception:  # noqa: BLE001
            frames = []
        parts.append(days_section(frames, fdir))
    parts.append(f"<footer class=\"colophon\">Written by RimChronicle from the colony's ledger. Exported {time.strftime('%Y-%m-%d %H:%M')}.</footer></main></body></html>")
    return "".join(parts)


def export_book(store: Store, game_id: str, out: Path | None = None) -> Path:
    chron = store.load(game_id)
    out = out or (store.dir(game_id) / "book.html")
    out.write_text(render_book(chron, store), encoding="utf-8")
    return out
