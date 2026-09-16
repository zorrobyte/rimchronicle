"""FastAPI app: JSON API, SSE stream, and the single-page reader."""
from __future__ import annotations

import asyncio
import json
import queue
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from .engine import Engine
from .export import render_book
from .store import safe_id
from .webpage import PAGE


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="RimChronicle", docs_url=None, redoc_url=None)
    store = engine.store

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return engine.status()

    @app.get("/api/chronicles")
    def api_chronicles() -> list[dict[str, Any]]:
        live = engine.watcher.game_id
        cards = []
        for c in store.list():
            card = c.summary_card()
            card["live"] = c.id == live
            cards.append(card)
        return cards

    @app.get("/api/chronicles/{game_id}")
    def api_chronicle(game_id: str) -> dict[str, Any]:
        gid = safe_id(game_id)
        if engine.watcher.chronicle is not None and engine.watcher.chronicle.id == gid:
            chron = engine.watcher.chronicle
        elif store.exists(gid):
            chron = store.load(gid)
        else:
            raise HTTPException(404, "no such chronicle")
        d = chron.to_dict()
        d["live"] = gid == engine.watcher.game_id
        d["days"] = chron.days_survived
        return d

    @app.get("/api/chronicles/{game_id}/images/{name}")
    def api_image(game_id: str, name: str) -> Response:
        try:
            p = store.image_path(safe_id(game_id), name)
        except FileNotFoundError:
            raise HTTPException(404, "no such image") from None
        if not p.exists():
            raise HTTPException(404, "no such image")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/chronicles/{game_id}/book")
    def api_book(game_id: str) -> Response:
        gid = safe_id(game_id)
        if not store.exists(gid):
            raise HTTPException(404, "no such chronicle")
        chron = store.load(gid)
        html = render_book(chron, store)
        return Response(content=html, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{gid}-chronicle.html"'})

    @app.post("/api/chronicles/{game_id}/write")
    def api_write(game_id: str) -> JSONResponse:
        gid = safe_id(game_id)
        if engine.watcher.game_id != gid:
            return JSONResponse({"ok": False, "error": "that chronicle is not the live game"}, status_code=409)
        if not engine.watcher.online:
            return JSONResponse({"ok": False, "error": "the bridge is offline"}, status_code=409)
        if engine.narrator.busy:
            return JSONResponse({"ok": False, "error": "a chapter is already being written"}, status_code=409)
        engine.request_chapter()
        return JSONResponse({"ok": True})

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        q = engine.hub.subscribe()

        async def gen():
            try:
                yield "event: hello\ndata: {}\n\n"
                idle = 0.0
                while True:
                    try:
                        ev = q.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.5)
                        idle += 0.5
                        if idle >= 15:
                            idle = 0.0
                            yield ": keepalive\n\n"
                        continue
                    idle = 0.0
                    yield f"id: {ev['seq']}\nevent: {ev['kind']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
            finally:
                engine.hub.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
