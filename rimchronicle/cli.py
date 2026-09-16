"""Command line: serve, once, rewrite, voices, export, list."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .export import export_book
from .store import Store
from .voices import VOICES, get_voice, list_voices


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "PIL", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _check_voice(voice: str | None) -> int:
    if voice and voice.strip().lower() not in VOICES:
        print(f"unknown voice {voice!r}; one of: {', '.join(VOICES)}", file=sys.stderr)
        return 2
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .engine import Engine
    from .web import create_app

    cfg = load_config()
    engine = Engine(cfg)
    engine.start()
    app = create_app(engine)
    host, port = cfg["web"]["host"], int(args.port or cfg["web"]["port"])
    print(f"RimChronicle is reading at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    engine.stop()
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    from .engine import Engine

    if rc := _check_voice(args.voice):
        return rc
    cfg = load_config()
    engine = Engine(cfg)
    ch = engine.write_now(voice=(args.voice or None), focus=(args.focus or None))
    if ch is None:
        print("no chapter written: " + (engine.narrator.last_error or "the bridge is not running a game"), file=sys.stderr)
        return 1
    chron = engine.watcher.chronicle
    print(f"Chapter {ch.k}: {ch.title}")
    print(f"Day {ch.day}, hour {ch.hour:02d}. Voice: {get_voice(ch.voice).name} ({ch.voice}). Trigger: {ch.trigger}. Images: {', '.join(i['file'] for i in ch.images) or 'none'}")
    if args.focus:
        print(f"Focus: {args.focus}")
    print()
    print(ch.body)
    print()
    print(f"Saved to {engine.store.dir(chron.id) if chron else '?'}")
    return 0


def cmd_rewrite(args: argparse.Namespace) -> int:
    from .engine import Engine

    if rc := _check_voice(args.voice):
        return rc
    cfg = load_config()
    engine = Engine(cfg)
    if not engine.store.exists(args.game_id):
        print(f"no chronicle named {args.game_id}; try `rimchronicle list`", file=sys.stderr)
        return 1
    chron = engine.store.load(args.game_id)
    try:
        ch = engine.narrator.rewrite(chron, int(args.k), args.voice)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Chapter {ch.k}: {ch.title}")
    print(f"Day {ch.day}, hour {ch.hour:02d}. Voice: {get_voice(ch.voice).name} ({ch.voice}). Earlier renderings kept: {len(ch.versions)}.")
    print()
    print(ch.body)
    print()
    print(f"Saved to {engine.store.dir(chron.id)}")
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    for v in list_voices():
        print(f"{v['id']:12s} {v['name']:16s} {v['blurb']}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = Store(cfg["storage"]["path"])
    if not store.exists(args.game_id):
        print(f"no chronicle named {args.game_id}; try `rimchronicle list`", file=sys.stderr)
        return 1
    out = export_book(store, args.game_id, Path(args.out) if args.out else None)
    print(out)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    store = Store(cfg["storage"]["path"])
    chrons = store.list()
    if not chrons:
        print("no chronicles yet")
        return 0
    for c in chrons:
        title = c.title if c.title != c.id else ""
        print(f"{c.id:40s} {c.status:8s} {c.days_survived:4d} days  {len(c.chapters):3d} chapters  {title} {c.scenario or ''} {c.storyteller or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rimchronicle", description="Watches a running RimWorld colony and writes its illustrated chronicle.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="watcher + narrator + web UI in one process")
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_serve)
    o = sub.add_parser("once", help="write one chapter now and print it")
    o.add_argument("--voice", default=None, help="narrator voice for this chapter (see `voices`)")
    o.add_argument("--focus", default=None, help="what this chapter should dwell on")
    o.set_defaults(fn=cmd_once)
    r = sub.add_parser("rewrite", help="re-narrate a chapter in another voice; the old rendering is kept")
    r.add_argument("game_id")
    r.add_argument("k", type=int, help="chapter number")
    r.add_argument("--voice", required=True, help="narrator voice (see `voices`)")
    r.set_defaults(fn=cmd_rewrite)
    v = sub.add_parser("voices", help="list the narrator voices")
    v.set_defaults(fn=cmd_voices)
    e = sub.add_parser("export", help="write a self-contained HTML book")
    e.add_argument("game_id")
    e.add_argument("--out", default=None)
    e.set_defaults(fn=cmd_export)
    ls = sub.add_parser("list", help="list chronicles on disk")
    ls.set_defaults(fn=cmd_list)
    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
