"""FastAPI app: JSON API, SSE stream, and the static single-page reader (rimchronicle/static)."""
from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .export import render_book
from .store import Chronicle, safe_id
from .voices import VOICES, list_voices

STATIC_DIR = Path(__file__).resolve().parent / "static"
TIMELINE_KINDS = {"event", "frame", "chapter", "state", "chronicle", "game", "note"}


class ReaderStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if path in {"app.js", "app.css"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="RimChronicle", docs_url=None, redoc_url=None)
    store = engine.store
    app.mount("/static", ReaderStaticFiles(directory=str(STATIC_DIR)), name="static")

    def find(game_id: str) -> Chronicle:
        """The live chronicle object if it is the live game, else the one on disk; 404 otherwise."""
        gid = safe_id(game_id)
        live = engine.watcher.chronicle
        if live is not None and live.id == gid:
            return live
        if store.exists(gid):
            return store.load(gid)
        raise HTTPException(404, "no such chronicle")

    def is_live(chron: Chronicle) -> bool:
        return chron.id == engine.watcher.game_id

    # ---------------------------------------------------------------- the page
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # ---------------------------------------------------------------- status and library
    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return engine.status()

    @app.get("/api/voices")
    def api_voices() -> list[dict[str, Any]]:
        return list_voices()

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
        chron = find(game_id)
        d = chron.public()
        d["live"] = is_live(chron)
        return d

    # ---------------------------------------------------------------- the record
    @app.get("/api/chronicles/{game_id}/timeline")
    def api_timeline(game_id: str, since: int = 0, kinds: str | None = None, limit: int = 2000) -> dict[str, Any]:
        chron = find(game_id)
        wanted = {k.strip() for k in (kinds or "").split(",") if k.strip()} or None
        items, nxt = engine.timeline.read(chron.id, since=max(0, int(since)), kinds=wanted, limit=max(1, min(int(limit), 10000)))
        return {"items": items, "next": nxt, "overview": engine.timeline.overview(chron.id)}

    @app.get("/api/chronicles/{game_id}/timeline/series")
    def api_timeline_series(game_id: str) -> list[dict[str, Any]]:
        chron = find(game_id)
        return engine.timeline.series(chron.id)

    @app.get("/api/chronicles/{game_id}/people")
    def api_people(game_id: str) -> dict[str, Any]:
        chron = find(game_id)
        return engine.people.to_dict(chron)

    # ---------------------------------------------------------------- pictures and the book
    @app.get("/api/chronicles/{game_id}/images/{name}")
    def api_image(game_id: str, name: str) -> Response:
        try:
            p = store.image_path(safe_id(game_id), name)
        except FileNotFoundError:
            raise HTTPException(404, "no such image") from None
        if not p.exists():
            raise HTTPException(404, "no such image")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/chronicles/{game_id}/frames/{name}")
    def api_frame(game_id: str, name: str) -> Response:
        try:
            p = store.frame_path(safe_id(game_id), name)
        except FileNotFoundError:
            raise HTTPException(404, "no such frame") from None
        if not p.exists():
            raise HTTPException(404, "no such frame")
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/chronicles/{game_id}/book")
    def api_book(game_id: str) -> Response:
        gid = safe_id(game_id)
        if not store.exists(gid):
            raise HTTPException(404, "no such chronicle")
        chron = store.load(gid)
        html = render_book(chron, store)
        return Response(content=html, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{gid}-chronicle.html"'})

    # ---------------------------------------------------------------- writing
    @app.post("/api/chronicles/{game_id}/write")
    def api_write(game_id: str, body: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        gid = safe_id(game_id)
        if engine.watcher.game_id != gid:
            return JSONResponse({"ok": False, "error": "that chronicle is not the live game"}, status_code=409)
        if not engine.watcher.online:
            return JSONResponse({"ok": False, "error": "the bridge is offline"}, status_code=409)
        if engine.narrator.busy:
            return JSONResponse({"ok": False, "error": "a chapter is already being written"}, status_code=409)
        body = body if isinstance(body, dict) else {}
        voice = str(body.get("voice") or "").strip().lower() or None
        if voice is not None and voice not in VOICES:
            return JSONResponse({"ok": False, "error": f"unknown voice {voice}"}, status_code=422)
        focus = str(body.get("focus") or "").strip()[:600] or None
        engine.request_chapter(voice, focus)
        return JSONResponse({"ok": True, "voice": voice, "focus": focus})

    @app.post("/api/chronicles/{game_id}/chapters/{k}/rewrite")
    def api_rewrite(game_id: str, k: int, body: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        chron = find(game_id)
        body = body if isinstance(body, dict) else {}
        voice = str(body.get("voice") or "").strip().lower()
        if voice not in VOICES:
            return JSONResponse({"ok": False, "error": f"unknown voice {voice or '(none)'}"}, status_code=422)
        if k < 1 or k > len(chron.chapters):
            raise HTTPException(404, "no such chapter")
        if not (chron.chapters[k - 1].inputs or {}).get("user_text"):
            return JSONResponse({"ok": False, "error": "this chapter predates re-narration; its prompt was not stored"}, status_code=409)
        if engine.narrator.busy:
            return JSONResponse({"ok": False, "error": "a chapter is already being written"}, status_code=409)
        engine.request_rewrite(chron.id, k, voice)
        return JSONResponse({"ok": True, "k": k, "voice": voice})

    @app.put("/api/chronicles/{game_id}/settings")
    def api_chronicle_settings(game_id: str, body: dict[str, Any] = Body(...)) -> JSONResponse:
        chron = find(game_id)
        if not isinstance(body, dict):
            raise HTTPException(422, "expected a JSON object")
        if "voice" in body:
            voice = str(body.get("voice") or "").strip().lower()
            if voice and voice not in VOICES:
                return JSONResponse({"ok": False, "error": f"unknown voice {voice}"}, status_code=422)
            chron.voice = voice
        if "diarist" in body:
            chron.diarist = str(body.get("diarist") or "").strip()[:80]
        if "directive" in body:
            chron.directive = str(body.get("directive") or "").strip()[:2000]
        store.save(chron)
        return JSONResponse({"ok": True, "voice": chron.voice, "diarist": chron.diarist, "directive": chron.directive})

    # ---------------------------------------------------------------- settings
    @app.get("/api/settings")
    def api_settings() -> dict[str, Any]:
        return engine.settings_view()

    @app.put("/api/settings")
    def api_settings_put(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise HTTPException(422, "expected a JSON object of sections")
        return engine.apply_settings(body)

    @app.post("/api/settings/test-llm")
    def api_settings_test_llm(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
        body = body if isinstance(body, dict) else {}
        llm_cfg = body.get("llm")
        return engine.test_llm(llm_cfg if isinstance(llm_cfg, dict) else None)

    # ---------------------------------------------------------------- live
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
