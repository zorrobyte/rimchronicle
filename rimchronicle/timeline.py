"""The session record: one append-only JSONL file per chronicle.

Every ledger event (all kinds, not only the notable ones), every state sample, every frame the camera took,
every chapter and any overseer note lands here with in-game day/hour and real time. The reader's Timeline view
reads it; re-narration and recaps can read it; nothing in the write path depends on it.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from .store import Store

STATE_SERIES_KEYS = ("colonists", "mood_avg", "food_days", "wealth", "threat_points", "downed", "prisoners", "animals")


class Timeline:
    def __init__(self, store: Store, state_every_seconds: float = 60.0):
        self.store = store
        self.state_every_seconds = float(state_every_seconds)
        self._lock = threading.Lock()
        self._last_state_t: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    # ---------------------------------------------------------------- writing
    def append(self, game_id: str, kind: str, data: dict[str, Any] | None = None, day: int | None = None, hour: int | None = None, t: float | None = None) -> dict[str, Any]:
        rec: dict[str, Any] = {"t": round(t if t is not None else time.time(), 3), "day": day, "hour": hour, "kind": kind}
        for k, v in (data or {}).items():
            if k == "kind":
                rec["event_kind"] = v      # an event record keeps its ledger kind (colonist_died, social, ...)
            elif k not in rec:
                rec[k] = v
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            p = self.store.timeline_path(game_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._counts[game_id] = self._counts.get(game_id, 0) + 1
        return rec

    def sample_state(self, game_id: str, state: dict[str, Any], t: float | None = None, force: bool = False) -> bool:
        """Record a compact state sample, throttled to one per `state_every_seconds` unless forced."""
        now = t if t is not None else time.time()
        if not force and now - self._last_state_t.get(game_id, 0.0) < self.state_every_seconds:
            return False
        self._last_state_t[game_id] = now
        data = {k: state.get(k) for k in STATE_SERIES_KEYS if k in state}
        for k in ("danger", "weather", "season", "temp_outdoor"):
            if k in state:
                data[k] = state[k]
        self.append(game_id, "state", data, day=_int_or_none(state.get("day")), hour=_int_or_none(state.get("hour")), t=now)
        return True

    # ---------------------------------------------------------------- reading
    def read(self, game_id: str, since: int = 0, kinds: set[str] | None = None, limit: int = 2000) -> tuple[list[dict[str, Any]], int]:
        """Records after line index `since` (0-based count of lines already seen). Returns (items, next_since)."""
        p = self.store.timeline_path(game_id)
        if not p.exists():
            return [], 0
        out: list[dict[str, Any]] = []
        n = 0
        with p.open("r", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if n <= since:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if kinds and rec.get("kind") not in kinds:
                    continue
                rec["i"] = n
                out.append(rec)
                if len(out) >= limit:
                    break
        return out, n

    def count(self, game_id: str) -> int:
        p = self.store.timeline_path(game_id)
        if not p.exists():
            return 0
        with p.open("rb") as fh:
            return sum(1 for _ in fh)

    def series(self, game_id: str, max_points: int = 400) -> list[dict[str, Any]]:
        """State samples, downsampled evenly to at most max_points, for sparklines."""
        items, _ = self.read(game_id, kinds={"state"}, limit=10**7)
        if len(items) <= max_points:
            return items
        step = len(items) / (max_points - 1)
        return [items[int(i * step)] for i in range(max_points - 1)] + [items[-1]]

    def frames(self, game_id: str, since_t: float = 0.0) -> list[dict[str, Any]]:
        items, _ = self.read(game_id, kinds={"frame"}, limit=10**7)
        return [x for x in items if float(x.get("t") or 0) >= since_t]

    def overview(self, game_id: str) -> dict[str, Any]:
        """Cheap summary for the reader: counts by kind and the day range."""
        items, n = self.read(game_id, limit=10**7)
        kinds: dict[str, int] = {}
        days = [int(x["day"]) for x in items if x.get("day") is not None]
        for x in items:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        return {"records": n, "kinds": kinds, "first_day": min(days) if days else None, "last_day": max(days) if days else None}


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
