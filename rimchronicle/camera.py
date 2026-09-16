"""The camera: pictures taken when things happen, not minutes later.

Moments (the cell where a death, raid, break or fire just happened), fight follow-ups (the hostiles' centre while a
raid is on the map), one wide shot per in-game day (the time-lapse), and colonist portraits. Every frame is a JPEG in
frames/ plus a "frame" record on the timeline. RimBridge renders through its own off-screen camera, so the player's
view never moves; a shot costs about 70 ms.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .bridge import BridgeError, BridgeLike
from .images import to_jpeg
from .store import Chronicle, Store
from .timeline import Timeline
from .watcher import is_dramatic, is_moment

log = logging.getLogger("rimchronicle.camera")

FOLLOW_UP_SECONDS = (20.0, 60.0)
RANK = {"colonist_died": 100, "pawn_died": 70, "colonist_downed": 80, "hostile_group": 75, "fight": 74, "incident": 70, "mental_break": 60,
        "tale": 55, "manhunter": 50, "health": 45, "colonist_joined": 40, "building_lost": 35, "event": 20, "base": 0, "daily": 0, "portrait": 0}


class Camera:
    def __init__(self, bridge: BridgeLike, store: Store, timeline: Timeline | None, cfg: dict[str, Any], clock: Callable[[], float] = time.time):
        self.bridge = bridge
        self.store = store
        self.timeline = timeline
        self.cfg = cfg
        self.clock = clock
        self.taken: list[dict[str, Any]] = []          # frame records since process start (moments only)
        self.pending: list[dict[str, Any]] = []        # scheduled shots: {"due", "kind", "label", "resolve"|"cell", "w", "seq"}
        self.count_since_chapter = 0
        self._last_by_kind: dict[str, float] = {}
        self._daily_done: dict[str, int] = {}          # game_id -> last day shot
        self._portrait_day: dict[str, int] = {}        # name -> day
        self._n = 0
        self.last_error = ""
        self.on_frame: Callable[[dict[str, Any]], None] | None = None

    # ---------------------------------------------------------------- triggers
    def on_events(self, chron: Chronicle | None, events: list[dict[str, Any]], summary: dict[str, Any] | None) -> None:
        if chron is None or not events:
            return
        now = self.clock()
        home = (summary or {}).get("home_center") if summary else None
        for ev in events:
            kind = str(ev.get("kind"))
            if kind == "day" and self.cfg.get("daily", True) and home:
                day = int(ev.get("day") or (summary or {}).get("day") or 0)
                if self._daily_done.get(chron.id) != day:
                    self._daily_done[chron.id] = day
                    self.shoot(chron, int(home[0]), int(home[1]), int(self.cfg.get("base_width_cells", 50) or 50), "daily", f"{chron.title} on day {day}", day=day, hour=ev.get("hour"))
                continue
            if not self.cfg.get("moments", True) or not is_moment(ev):
                continue
            if self.count_since_chapter >= int(self.cfg.get("max_per_chapter", 8)):
                continue
            if now - self._last_by_kind.get(kind, -1e9) < float(self.cfg.get("debounce_seconds", 10)):
                continue
            self._last_by_kind[kind] = now
            cx, cz = int(ev["cell"][0]), int(ev["cell"][1])
            label = f"{ev.get('text', kind)}, day {ev.get('day', '?')} h{int(ev.get('hour') or 0):02d}"
            rec = self.shoot(chron, cx, cz, int(self.cfg.get("moment_width_cells", 24)), kind, label, seq=ev.get("seq"), day=ev.get("day"), hour=ev.get("hour"), dramatic=is_dramatic(ev))
            if rec is not None:
                self.count_since_chapter += 1
            if kind == "hostile_group" and self.cfg.get("follow_fight", True):
                for delay in FOLLOW_UP_SECONDS:
                    self.pending.append({"due": now + delay, "kind": "fight", "label": f"the fight, day {ev.get('day', '?')}", "seq": ev.get("seq"), "fallback": (cx, cz)})

    def tick(self, chron: Chronicle | None, summary: dict[str, Any] | None, roster: list[dict[str, Any]] | None = None) -> None:
        """Drain due follow-ups and take portraits once per day."""
        if chron is None:
            return
        now = self.clock()
        due = [p for p in self.pending if p["due"] <= now]
        if due:
            self.pending = [p for p in self.pending if p["due"] > now]
            for p in due:
                if self.count_since_chapter >= int(self.cfg.get("max_per_chapter", 8)):
                    break
                cell = _hostiles_centre(summary) or p.get("fallback")
                if not cell:
                    continue
                rec = self.shoot(chron, int(cell[0]), int(cell[1]), int(self.cfg.get("moment_width_cells", 24)) + 8, "fight", p["label"], seq=p.get("seq"),
                                 day=(summary or {}).get("day"), hour=(summary or {}).get("hour"), dramatic=True)
                if rec is not None:
                    self.count_since_chapter += 1
        if roster and summary and self.cfg.get("portraits", True):
            self._portraits(chron, roster, summary)

    def _portraits(self, chron: Chronicle, roster: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        day = int(summary.get("day") or 0)
        for p in roster:
            n = str(p.get("name") or "")
            pos = p.get("pos")
            if not n or not pos or self._portrait_day.get(n) == day:
                continue
            self._portrait_day[n] = day
            rec = self.shoot(chron, int(pos[0]), int(pos[1]), int(self.cfg.get("portrait_width_cells", 8)), "portrait", f"{n}, day {day}", day=day, hour=summary.get("hour"), max_px=384, who=n)
            if rec is None:
                self._portrait_day.pop(n, None)
            return  # one portrait per tick keeps the loop responsive; the rest follow on later ticks

    # ---------------------------------------------------------------- shooting
    def shoot(self, chron: Chronicle, x: int, z: int, w: int, kind: str, label: str, *, seq: Any = None, day: Any = None, hour: Any = None, dramatic: bool = False, max_px: int | None = None, who: str | None = None) -> dict[str, Any] | None:
        try:
            png = self.bridge.screenshot(x, z, w)
        except BridgeError as e:
            self.last_error = str(e)
            log.warning("camera %s failed: %s", kind, e)
            return None
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.exception("camera %s failed", kind)
            return None
        try:
            jpeg = to_jpeg(png, quality=82, max_width=max_px or int(self.cfg.get("frame_max_px", 1024)))
        except Exception:  # noqa: BLE001
            log.exception("frame encode failed")
            return None
        self._n += 1
        self.last_error = ""
        d = _int(day)
        h = _int(hour)
        name = f"d{d or 0:03d}h{h or 0:02d}-{seq or 0}-{self._n:04d}-{kind}.jpg"
        self.store.save_frame(chron.id, name, jpeg)
        rec = {"file": name, "shot": kind, "label": label, "cell": [x, z], "w": w, "event_seq": seq, "t": self.clock(), "day": d, "hour": h, "dramatic": dramatic}
        if who:
            rec["who"] = who
        if self.timeline is not None:
            try:
                self.timeline.append(chron.id, "frame", rec, day=d, hour=h, t=rec["t"])
            except Exception:  # noqa: BLE001
                log.exception("timeline frame append failed")
        if kind not in ("daily", "portrait", "base"):
            self.taken.append(dict(rec))   # a copy: the jpeg is added to the caller's record only
            del self.taken[:-200]
        if self.on_frame is not None:
            try:
                self.on_frame(dict(rec))
            except Exception:  # noqa: BLE001
                log.exception("on_frame failed")
        rec["jpeg"] = jpeg
        return rec

    def wide(self, chron: Chronicle, summary: dict[str, Any] | None, width_cells: int, day: Any) -> dict[str, Any] | None:
        home = (summary or {}).get("home_center") if summary else None
        if not home:
            return None
        return self.shoot(chron, int(home[0]), int(home[1]), width_cells, "base", f"{chron.title} on day {day}", day=day, hour=(summary or {}).get("hour"))

    # ---------------------------------------------------------------- selection
    def pick_for_chapter(self, chron: Chronicle, since_t: float, n: int = 3) -> list[dict[str, Any]]:
        """The best moments since the last chapter: dramatic first, then spread over time."""
        cands = [r for r in self.taken if r.get("t", 0) > since_t and r.get("file")]
        if not cands:
            return []
        cands.sort(key=lambda r: (RANK.get(str(r.get("shot")), 10) + (25 if r.get("dramatic") else 0), r.get("t", 0)), reverse=True)
        chosen = cands[:n]
        chosen.sort(key=lambda r: r.get("t", 0))
        out = []
        for r in chosen:
            try:
                jpeg = self.store.frame_path(chron.id, r["file"]).read_bytes()
            except OSError:
                continue
            out.append({**r, "jpeg": jpeg})
        return out

    def reset_chapter(self) -> None:
        self.count_since_chapter = 0


def _hostiles_centre(summary: dict[str, Any] | None) -> tuple[int, int] | None:
    hs = (summary or {}).get("hostiles") or []
    pts = [h.get("pos") for h in hs if isinstance(h, dict) and isinstance(h.get("pos"), (list, tuple)) and len(h["pos"]) >= 2]
    if not pts:
        return None
    return int(sum(p[0] for p in pts) / len(pts)), int(sum(p[1] for p in pts) / len(pts))


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
