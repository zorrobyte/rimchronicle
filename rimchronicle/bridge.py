"""HTTP client for RimBridge, the RimWorld mod that exposes the running game on loopback."""
from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

log = logging.getLogger("rimchronicle.bridge")


class BridgeError(RuntimeError):
    """Raised when the bridge is unreachable or an RPC reports ok=false."""


class BridgeLike(Protocol):
    """What the watcher and narrator need from a bridge. The tests substitute an in-memory fake."""

    def health(self) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def events(self, since: int) -> dict[str, Any]: ...
    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any: ...
    def screenshot(self, x: int, z: int, w: int) -> bytes: ...


class Bridge:
    def __init__(self, url: str = "http://127.0.0.1:8765", timeout_s: float = 15.0):
        self.url = url.rstrip("/")
        self._client = httpx.Client(base_url=self.url, timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: Any) -> Any:
        try:
            r = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as e:
            raise BridgeError(f"bridge unreachable: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise BridgeError(f"GET {path} -> {r.status_code}")
        return r

    def health(self) -> dict[str, Any]:
        return self._get("/health").json()

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = self._client.post("/rpc", json={"method": method, "params": params or {}})
        except httpx.HTTPError as e:
            raise BridgeError(f"bridge unreachable: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise BridgeError(f"rpc {method} -> {r.status_code}")
        body = r.json()
        if not body.get("ok", False):
            raise BridgeError(f"rpc {method}: {body.get('error', 'unknown error')}")
        return body.get("result")

    def status(self) -> dict[str, Any]:
        return self.rpc("game.status")

    def events(self, since: int) -> dict[str, Any]:
        body = self._get("/events", since=since).json()
        if not body.get("ok", True):
            raise BridgeError(f"events: {body.get('error')}")
        return body.get("result", body)

    def screenshot(self, x: int, z: int, w: int) -> bytes:
        r = self._get("/screenshot", x=x, z=z, w=w)
        ctype = r.headers.get("content-type", "")
        if "image" not in ctype:
            raise BridgeError(f"screenshot returned {ctype or 'no content type'}")
        return r.content

    def scenario_name(self) -> str | None:
        try:
            v = self.rpc("engine.get", {"path": "Find.Scenario.name", "depth": 1})
            return str(v) if v else None
        except BridgeError:
            return None

    def pawn_detail(self, pawn: str) -> dict[str, Any] | None:
        """Full pawn detail (traits, backstory, thoughts, relations...). None if the bridge lacks it."""
        try:
            v = self.rpc("state.pawn", {"pawn": pawn})
            return v if isinstance(v, dict) else None
        except BridgeError:
            return None

    def world_uid(self) -> str | None:
        """A value generated with the world and saved with it, so two games that share a seed string
        (an agent replaying a fixed seed list starts many unrelated colonies under one seed) still get
        different chronicle ids, while reloading the same save keeps the same one. None if the bridge
        or the game version does not expose it."""
        try:
            v = self.rpc("engine.get", {"path": "Find.World.info.persistentRandomValue", "depth": 1})
        except BridgeError:
            return None
        if isinstance(v, (int, str)) and str(v).strip() not in ("", "0", "None"):
            return str(v).strip()
        return None

    def game_start_tick(self) -> int | None:
        """Absolute tick the game world started at; constant across saves of the same game."""
        try:
            v = self.rpc("engine.get", {"path": "Find.TickManager.gameStartAbsTick", "depth": 1})
            return int(v)
        except (BridgeError, TypeError, ValueError):
            return None
