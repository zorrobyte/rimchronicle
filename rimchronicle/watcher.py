"""Polls RimBridge, buffers notable ledger events, keeps a compact colony state, and notices new games."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .bridge import BridgeError, BridgeLike
from .store import Chronicle, Store, make_game_id

log = logging.getLogger("rimchronicle.watcher")

NOTABLE_KINDS = {
    "colonist_died", "colonist_downed", "colonist_joined", "colonist_left", "incident", "hostile_group",
    "hostile_group_gone", "manhunter", "danger", "mental_break", "letter", "quest", "research_finished",
    "built", "building_lost", "construction_failed", "dialog_answered", "day", "trade",
}
DRAMATIC_KINDS = {"colonist_died", "colonist_joined", "colonist_left", "hostile_group"}
FIRE_WORDS = ("fire", "flashstorm", "blaze")
BORING_BUILDS = ("wall", "conduit", "floor", "fence", "sandbag", "barricade", "embrasure", "column", "door", "carpet", "tile", "concrete", "pavement", "bridge")
STATE_KEYS = ("day", "hour", "date", "season", "weather", "temp_outdoor", "colonists", "downed", "prisoners", "animals", "wealth", "mood_avg", "food_days", "nutrition", "threat_points", "danger", "research_current", "research_progress", "biome")


def is_dramatic(ev: dict[str, Any]) -> bool:
    kind = ev.get("kind")
    if kind in DRAMATIC_KINDS:
        return True
    if kind == "incident":
        data = ev.get("data") or {}
        blob = " ".join(str(x) for x in (ev.get("text"), data.get("def"), data.get("category"))).lower()
        if any(w in blob for w in FIRE_WORDS):
            return True
        if "raid" in blob or str(data.get("category", "")).startswith("Threat"):
            return True
    return False


def is_notable(ev: dict[str, Any]) -> bool:
    kind = ev.get("kind")
    if kind not in NOTABLE_KINDS:
        return False
    if kind == "built":
        d = str((ev.get("data") or {}).get("def", "")).lower()
        t = str(ev.get("text", "")).lower()
        if any(w in d or t.startswith(w) for w in BORING_BUILDS):
            return False
    if kind == "danger" and "-> None" in str(ev.get("text", "")):
        return False
    return True


def compact_event(ev: dict[str, Any]) -> dict[str, Any]:
    out = {"seq": ev.get("seq"), "kind": ev.get("kind"), "text": ev.get("text", ""), "day": ev.get("day"), "hour": ev.get("hour")}
    if ev.get("cell"):
        out["cell"] = ev["cell"]
    data = ev.get("data") or {}
    keep = {}
    for key in ("faction", "points", "strategy", "arrival", "count", "job", "cause", "reason", "def", "label", "state", "to", "quest", "category"):
        if data.get(key) not in (None, "", 0):
            keep[key] = data[key]
    if ev.get("kind") == "letter" and data.get("text"):
        keep["text"] = str(data["text"])[:400]
    if keep:
        out["data"] = keep
    return out


def compact_state(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary.get(k) for k in STATE_KEYS if k in summary}


def compact_roster(pawns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in pawns:
        out.append({
            "name": p.get("name"), "top_skills": p.get("top_skills"), "weapon": p.get("weapon"),
            "mood": p.get("mood"), "health": p.get("health"), "job": p.get("job"),
            "mental_state": p.get("mental_state"), "downed": p.get("job") == "downed",
        })
    return out


class Watcher:
    """Call tick() every poll interval. It never raises for bridge problems; it flips `online` instead."""

    def __init__(self, bridge: BridgeLike, store: Store, cfg: dict[str, Any], clock: Callable[[], float] = time.time):
        self.bridge = bridge
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.online = False
        self.status: dict[str, Any] = {}
        self.game_id: str | None = None
        self.chronicle: Chronicle | None = None
        self.pending: list[dict[str, Any]] = []
        self.cursor = 0
        self.summary: dict[str, Any] | None = None
        self.roster: list[dict[str, Any]] = []
        self.days_since_chapter = 0
        self.game_left = False          # a running game went back to the menu
        self.colony_wiped = False       # colonists reached zero
        self.listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._last_state_refresh = 0.0
        self._last_tick = -1
        self._needs_identify = True
        self._was_playing = False
        self._last_error = ""

    # ---------------------------------------------------------------- helpers
    def emit(self, kind: str, data: dict[str, Any] | None = None) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, data or {})
            except Exception:  # noqa: BLE001
                log.exception("listener failed")

    def _note_error(self, msg: str) -> None:
        if msg != self._last_error:
            log.warning(msg)
            self._last_error = msg

    @property
    def hours_now(self) -> int | None:
        s = self.summary or self.status
        if not s or s.get("day") is None:
            return None
        return int(s.get("day", 0)) * 24 + int(s.get("hour", 0))

    # ---------------------------------------------------------------- polling
    def tick(self) -> list[dict[str, Any]]:
        """One poll. Returns the notable events found this tick (already appended to `pending`)."""
        try:
            status = self.bridge.status()
        except BridgeError as e:
            if self.online:
                log.warning("bridge went away: %s", e)
                self.emit("bridge", {"online": False})
            self.online = False
            self._note_error(str(e))
            return []
        if not self.online:
            self.online = True
            self._last_error = ""
            self._needs_identify = True
            self.emit("bridge", {"online": True})
        self.status = status
        playing = status.get("state") == "playing"
        if not playing:
            if self._was_playing and self.chronicle is not None:
                self.game_left = True
                self.emit("game", {"state": status.get("state"), "left": True})
            self._was_playing = False
            self._needs_identify = True
            return []
        self._was_playing = True

        tick = int(status.get("tick") or 0)
        if self.game_id is None or status.get("seed") != (self.chronicle.seed if self.chronicle else None) or tick + 5000 < self._last_tick:
            self._needs_identify = True
        self._last_tick = tick
        if self._needs_identify:
            self._identify()

        new_events = self._drain_events()
        now = self.clock()
        if now - self._last_state_refresh >= float(self.cfg.get("state_refresh_seconds", 20)) or self.summary is None:
            self.refresh_state()
        return new_events

    def _identify(self) -> None:
        seed = self.status.get("seed") or "unknown-seed"
        start_tick = None
        try:
            start_tick = self.bridge.game_start_tick()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            start_tick = None
        if start_tick is None:
            # Fall back to the absolute start: TicksAbs - TicksGame stays constant for a given world.
            try:
                abs_tick = int(self.bridge.rpc("engine.get", {"path": "Find.TickManager.TicksAbs", "depth": 1}))
                start_tick = abs_tick - int(self.status.get("tick") or 0)
            except Exception:  # noqa: BLE001
                start_tick = None
        gid = make_game_id(seed, start_tick)
        self._needs_identify = False
        if gid == self.game_id and self.chronicle is not None:
            return
        self._open_chronicle(gid, seed)

    def _open_chronicle(self, gid: str, seed: str) -> None:
        previous = self.game_id
        if self.store.exists(gid):
            chron = self.store.load(gid)
            resumed = True
        else:
            chron = Chronicle(id=gid, seed=seed)
            chron.started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            chron.started_day = int(self.status.get("day") or 0)
            chron.start_date = str(self.status.get("date") or "")
            chron.storyteller = str(self.status.get("storyteller") or "")
            chron.difficulty = str(self.status.get("difficulty") or "")
            try:
                chron.scenario = self.bridge.scenario_name() or ""  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                chron.scenario = ""
            resumed = False
        if chron.status == "ended":
            chron.status = "running"
        self.game_id = gid
        self.chronicle = chron
        self.pending = []
        self.days_since_chapter = 0
        self.game_left = False
        self.colony_wiped = False
        self.summary = None
        self.roster = []
        self.cursor = chron.last_seq if resumed else 0
        self.store.save(chron)
        log.info("%s chronicle %s (%s)", "resumed" if resumed else "new", gid, seed)
        self.emit("chronicle", {"id": gid, "resumed": resumed, "previous": previous})

    def _drain_events(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for _ in range(20):  # the bridge may page; keep pulling until we reach the head
            try:
                r = self.bridge.events(self.cursor)
            except BridgeError as e:
                self._note_error(f"events: {e}")
                return found
            head = int(r.get("head_seq") or 0)
            if head < self.cursor:
                log.info("ledger reset (head %s < cursor %s); re-reading from the start", head, self.cursor)
                self.cursor = 0
                self._needs_identify = True
                continue
            evs = r.get("events") or []
            for ev in evs:
                seq = int(ev.get("seq") or 0)
                if seq <= self.cursor:
                    continue
                self.cursor = seq
                if ev.get("kind") == "game":
                    self._needs_identify = True  # "new game started" or "loaded save ...": re-check who we are watching
                if not is_notable(ev):
                    continue
                c = compact_event(ev)
                self.pending.append(c)
                found.append(c)
                if ev.get("kind") == "day":
                    self.days_since_chapter += 1
                    snap = ev.get("data") or {}
                    if self.chronicle is not None and snap:
                        self.chronicle.last_day = int(snap.get("day") or ev.get("day") or self.chronicle.last_day)
                self.emit("event", c)
            last = int(r.get("last_seq") or self.cursor)
            if last >= head or not evs:
                break
        if self.chronicle is not None and self.cursor != self.chronicle.last_seq and found:
            pass  # last_seq is persisted when a chapter lands (so unread events survive a restart)
        if self._needs_identify and self.online:
            self._identify()
        return found

    def refresh_state(self) -> None:
        self._last_state_refresh = self.clock()
        try:
            summary = self.bridge.rpc("state.summary")
        except BridgeError as e:
            self._note_error(f"state.summary: {e}")
            return
        if isinstance(summary, dict):
            was = self.summary
            self.summary = summary
            cols = int(summary.get("colonists") or 0)
            if cols == 0 and was is not None and int(was.get("colonists") or 0) > 0:
                self.colony_wiped = True
                self.emit("game", {"wiped": True})
            if self.chronicle is not None:
                self.chronicle.last_state = compact_state(summary)
                self.chronicle.last_day = int(summary.get("day") or self.chronicle.last_day)
            self.emit("state", compact_state(summary))
        try:
            pawns = self.bridge.rpc("state.pawns", {"filter": "colonists"})
            if isinstance(pawns, list):
                self.roster = compact_roster(pawns)
        except BridgeError as e:
            self._note_error(f"state.pawns: {e}")

    def anchors(self) -> list[dict[str, Any]]:
        try:
            r = self.bridge.rpc("anchor.list")
            return r if isinstance(r, list) else []
        except BridgeError:
            return []

    # ---------------------------------------------------------------- chapter bookkeeping
    def consume(self) -> list[dict[str, Any]]:
        """Take the pending events for a chapter and reset the day counter."""
        evs, self.pending = self.pending, []
        self.days_since_chapter = 0
        if self.chronicle is not None:
            self.chronicle.last_seq = self.cursor
        return evs

    def restore(self, evs: list[dict[str, Any]]) -> None:
        """Put events back after a failed chapter so the next attempt still has them."""
        self.pending = evs + self.pending
        self.days_since_chapter = sum(1 for e in self.pending if e.get("kind") == "day")
