"""Optional: the notes of an AI agent playing through its own dashboard, quoted as the overseer's log.

Never depended on. If the dashboard is not up the narrator writes from the ledger alone.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

log = logging.getLogger("rimchronicle.overseer")
KINDS = "think_end,reply,operator"


class Overseer:
    def __init__(self, url: str, enabled: bool = True, poll_seconds: float = 5.0, clock: Callable[[], float] = time.time, fetch: Callable[[int], list[dict[str, Any]]] | None = None):
        self.url = url.rstrip("/")
        self.enabled = enabled
        self.poll_seconds = poll_seconds
        self.clock = clock
        self._fetch = fetch or self._http_fetch
        self.notes: list[dict[str, Any]] = []
        self.cursor = 0
        self.reachable = False
        self._next_try = 0.0
        self._client = httpx.Client(timeout=1.5)

    def _http_fetch(self, since: int) -> list[dict[str, Any]]:
        r = self._client.get(f"{self.url}/api/events", params={"since": since, "kinds": KINDS, "limit": 200})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def poll(self) -> None:
        if not self.enabled:
            return
        now = self.clock()
        if now < self._next_try:
            return
        try:
            evs = self._fetch(self.cursor)
        except Exception:  # noqa: BLE001
            if self.reachable:
                log.info("overseer dashboard went away; chronicling from the ledger alone")
            self.reachable = False
            self._next_try = now + 60.0
            return
        if not self.reachable:
            log.info("overseer dashboard reachable at %s", self.url)
        self.reachable = True
        self._next_try = now + self.poll_seconds
        for ev in evs:
            seq = int(ev.get("seq") or 0)
            if seq <= self.cursor:
                continue
            self.cursor = seq
            kind = ev.get("kind")
            data = ev.get("data") or {}
            text = data.get("notes") if kind == "think_end" else data.get("text")
            if text and str(text).strip():
                who = {"think_end": "overseer", "reply": "overseer", "operator": "operator"}.get(kind, kind)
                self.notes.append({"who": who, "text": str(text).strip()[:600]})

    def take(self, limit: int = 12) -> list[dict[str, Any]]:
        notes, self.notes = self.notes[-limit:], []
        return notes
