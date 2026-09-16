"""Command line: serve, once, export, list."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config
from .export import export_book
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "PIL", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


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

    cfg = load_config()
    engine = Engine(cfg)
    ch = engine.write_now()
    if ch is None:
        print("no chapter written: " + (engine.narrator.last_error or "the bridge is not running a game"), file=sys.stderr)
        return 1
    chron = engine.watcher.chronicle
    print(f"Chapter {ch.k}: {ch.title}")
    print(f"Day {ch.day}, hour {ch.hour:02d}. Trigger: {ch.trigger}. Images: {', '.join(i['file'] for i in ch.images) or 'none'}")
    print()
    print(ch.body)
    print()
    print(f"Saved to {engine.store.dir(chron.id) if chron else '?'}")
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
        print(f"{c.id:40s} {c.status:8s} {c.days_survived:4d} days  {len(c.chapters):3d} chapters  {c.scenario or ''} {c.storyteller or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rimchronicle", description="Watches a running RimWorld colony and writes its illustrated chronicle.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="watcher + narrator + web UI in one process")
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_serve)
    o = sub.add_parser("once", help="write one chapter now and print it")
    o.set_defaults(fn=cmd_once)
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
