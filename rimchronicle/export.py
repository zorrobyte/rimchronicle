"""Export a chronicle as one self-contained HTML book (images inline as data URIs)."""
from __future__ import annotations

import html
import time
from pathlib import Path

from .images import data_url
from .store import Chronicle, Store

FONTS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap" rel="stylesheet">'

BOOK_CSS = """
:root{--bg:#f6f1e7;--paper:#fffdf8;--ink:#2a2420;--muted:#6f655b;--rule:#d9cfbf;--accent:#8a3b2a;--shadow:0 1px 2px rgba(0,0,0,.06),0 12px 32px rgba(60,40,20,.10)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#17150f;--paper:#1f1c15;--ink:#ece4d4;--muted:#a99e8c;--rule:#3a352b;--accent:#e08a5a;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35)}}
:root[data-theme="dark"]{--bg:#17150f;--paper:#1f1c15;--ink:#ece4d4;--muted:#a99e8c;--rule:#3a352b;--accent:#e08a5a;--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35)}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Newsreader",Georgia,"Times New Roman",serif;font-size:19px;line-height:1.6;padding:0 16px}
.book{max-width:65ch;margin:0 auto;padding:48px 0 96px}
.cover{text-align:center;padding:64px 0 40px;border-bottom:1px solid var(--rule);margin-bottom:48px}
.cover .kicker,.chapter .kicker{font-variant:all-small-caps;letter-spacing:.14em;color:var(--muted);font-size:.85em}
.cover h1{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:2.6em;line-height:1.1;margin:.2em 0 .3em}
.cover .meta{color:var(--muted);font-style:italic}
.chapter{margin:0 0 72px}
.chapter h2{font-family:"Fraunces",Georgia,serif;font-weight:600;font-size:1.9em;line-height:1.15;margin:.2em 0 .5em}
figure{margin:24px calc(50% - 50vw + 16px);max-width:calc(100vw - 32px)}
@media (min-width: 900px){figure{margin:28px -12ch;max-width:none}}
figure img{width:100%;height:auto;display:block;border-radius:6px;box-shadow:var(--shadow)}
figcaption{font-size:.8em;color:var(--muted);text-align:center;margin-top:8px;font-style:italic}
.chapter p{margin:0 0 1em}.chapter p:first-of-type::first-letter{font-family:"Fraunces",Georgia,serif;font-size:3.2em;float:left;line-height:.8;padding:6px 8px 0 0;color:var(--accent)}
.state{font-family:ui-monospace,Menlo,monospace;font-size:.72em;color:var(--muted);border-top:1px solid var(--rule);padding-top:10px;margin-top:20px}
.epitaph{text-align:center;font-style:italic;padding:48px 0;border-top:1px solid var(--rule)}
.colophon{color:var(--muted);font-size:.8em;text-align:center;border-top:1px solid var(--rule);padding-top:24px}
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


def render_book(chron: Chronicle, store: Store | None = None, image_dir: Path | None = None) -> str:
    idir = image_dir or (store.image_dir(chron.id) if store else None)
    parts = [f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             f"<title>{html.escape(chron.seed)}: a RimWorld chronicle</title>{FONTS}<style>{BOOK_CSS}</style></head><body><main class=\"book\">"]
    meta = ", ".join(x for x in (chron.scenario, chron.storyteller, chron.difficulty) if x)
    status = "the colony endures" if chron.status == "running" else "the record is closed"
    parts.append(f"<header class=\"cover\"><div class=\"kicker\">A RimWorld chronicle</div><h1>{html.escape(chron.seed)}</h1>"
                 f"<div class=\"meta\">{html.escape(meta)}</div><div class=\"meta\">{chron.days_survived} days, {len(chron.chapters)} chapters, {status}</div></header>")
    for ch in chron.chapters:
        parts.append(f"<article class=\"chapter\" id=\"ch{ch.k}\"><div class=\"kicker\">Chapter {ch.k}, day {ch.day}, hour {ch.hour:02d}</div><h2>{html.escape(ch.title)}</h2>")
        for im in ch.images:
            src = ""
            if idir is not None:
                p = idir / im["file"]
                if p.exists():
                    src = data_url(p.read_bytes())
            if src:
                parts.append(f"<figure><img src=\"{src}\" alt=\"{html.escape(im.get('caption', ''))}\"><figcaption>{html.escape(im.get('caption', ''))}</figcaption></figure>")
        body_html, state = paragraphs(ch.body)
        parts.append(body_html)
        if state:
            parts.append(f"<div class=\"state\">{html.escape(state)}</div>")
        parts.append("</article>")
    if chron.epitaph:
        parts.append(f"<div class=\"epitaph\">{html.escape(chron.epitaph)}</div>")
    parts.append(f"<footer class=\"colophon\">Written by RimChronicle from the colony's ledger. Exported {time.strftime('%Y-%m-%d %H:%M')}.</footer></main></body></html>")
    return "".join(parts)


def export_book(store: Store, game_id: str, out: Path | None = None) -> Path:
    chron = store.load(game_id)
    out = out or (store.dir(game_id) / "book.html")
    out.write_text(render_book(chron, store), encoding="utf-8")
    return out
