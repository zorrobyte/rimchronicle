"""Polls RimBridge, buffers notable ledger events, keeps a compact colony state, records the session, and notices new games."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from .bridge import BridgeError, BridgeLike
from .store import Chronicle, Store, make_game_id

if TYPE_CHECKING:
    from .timeline import Timeline

log = logging.getLogger("rimchronicle.watcher")

NOTABLE_KINDS = {
    "colonist_died", "colonist_downed", "colonist_joined", "colonist_left", "incident", "hostile_group",
    "hostile_group_gone", "manhunter", "danger", "mental_break", "letter", "quest", "research_finished",
    "built", "building_lost", "construction_failed", "dialog_answered", "day", "trade",
    # the social layer (RimBridge >= the tale/social hooks)
    "tale", "social", "relation", "health", "pawn_died",
}
DRAMATIC_KINDS = {"colonist_died", "colonist_joined", "colonist_left", "hostile_group"}
DRAMATIC_TALES = {
    "KilledColonist", "KilledChild", "SocialFight", "Marriage", "BecameLover", "Breakup", "GaveBirth", "ExecutedPrisoner",
    "AteRawHumanlikeMeat", "ButcheredHumanlikeCorpse", "KidnappedColonist", "Captured", "KilledMajorThreat",
    "DefeatedHostileFactionLeader", "LandedInPod", "LaunchedShip", "Recruited", "SoldPrisoner", "EmbracedTheVoid", "ClosedTheVoid",
}
QUIET_TALES = {"Hunted", "ReadBook", "StruckMineable", "Exhausted", "MinedValuable", "CompletedLongConstructionProject", "CompletedLongCraftingProject", "IncreasedMenagerie", "CollapseDodged",
               "Wounded", "Downed", "KilledBy", "KilledCapacity", "KilledLongRange", "KilledMelee", "KilledMortar", "HealedMe"}  # per-hit or duplicated by other kinds
HEALTH_DRAMA = {"Plague", "WoundInfection", "Malaria", "SleepingSickness", "GutWorms", "MuscleParasites", "FibrousMechanites", "SensoryMechanites", "Carcinoma", "LungRot", "Abasia", "Dementia", "Alzheimers", "HeartAttack", "Anesthetic"} - {"Anesthetic"}
DRAMATIC_RELATIONS = {"Lover", "Spouse", "Fiance", "ExLover", "ExSpouse"}
MOMENT_KINDS = {"colonist_died", "colonist_downed", "pawn_died", "mental_break", "manhunter", "hostile_group", "colonist_joined", "building_lost", "health", "tale", "incident"}
FIRE_WORDS = ("fire", "flashstorm", "blaze")
BORING_BUILDS = ("wall", "conduit", "floor", "fence", "sandbag", "barricade", "embrasure", "column", "door", "carpet", "tile", "concrete", "pavement", "bridge")
STATE_KEYS = ("day", "hour", "date", "season", "weather", "temp_outdoor", "colonists", "downed", "prisoners", "animals", "wealth", "mood_avg", "food_days", "nutrition", "threat_points", "danger", "research_current", "research_progress", "biome")
EVENT_DATA_KEYS = ("faction", "points", "strategy", "arrival", "count", "job", "cause", "reason", "def", "label", "state", "to", "quest", "category",
                   "pawns", "initiator", "recipient", "minor", "a", "b", "added", "part", "serious", "trader", "sold", "bought", "silver_delta", "animal", "bonded_to", "kind", "by")


def _blob(ev: dict[str, Any]) -> str:
    data = ev.get("data") or {}
    return " ".join(str(x) for x in (ev.get("text"), data.get("def"), data.get("category"), data.get("label"))).lower()


def is_dramatic(ev: dict[str, Any]) -> bool:
    kind = ev.get("kind")
    data = ev.get("data") or {}
    if kind in DRAMATIC_KINDS:
        return True
    if kind == "incident":
        blob = _blob(ev)
        if any(w in blob for w in FIRE_WORDS):
            return True
        if "raid" in blob or str(data.get("category", "")).startswith("Threat"):
            return True
    if kind == "tale":
        return str(data.get("def")) in DRAMATIC_TALES
    if kind == "relation":
        return bool(data.get("added")) and str(data.get("def")) in DRAMATIC_RELATIONS
    if kind == "health":
        return _health_drama(ev)
    if kind == "pawn_died":
        return bool(data.get("bonded_to"))
    return False


def _health_drama(ev: dict[str, Any]) -> bool:
    """Lost limbs and real diseases; not every bleed or chill (those carry lethalSeverity too)."""
    data = ev.get("data") or {}
    d = str(data.get("def") or "")
    if d in HEALTH_DRAMA or d.startswith("Missing") or " lost " in f" {ev.get('text', '')} ":
        return True
    return False


def is_minor(ev: dict[str, Any]) -> bool:
    """Recorded and counted, but not listed in the ledger the narrator reads."""
    kind = ev.get("kind")
    data = ev.get("data") or {}
    if kind == "social":
        return bool(data.get("minor"))
    if kind == "tale":
        return str(data.get("def")) in QUIET_TALES
    return False


def is_notable(ev: dict[str, Any]) -> bool:
    kind = ev.get("kind")
    if kind not in NOTABLE_KINDS:
        return False
    data = ev.get("data") or {}
    if kind == "built":
        d = str(data.get("def", "")).lower()
        t = str(ev.get("text", "")).lower()
        if any(w in d or t.startswith(w) for w in BORING_BUILDS):
            return False
    if kind == "danger" and "-> None" in str(ev.get("text", "")):
        return False
    if kind == "pawn_died":
        # only the colony's own animals (bonded or player faction); wildlife and raiders are noise
        return str(data.get("faction") or "").lower() == "player" or bool(data.get("bonded_to"))
    return True


def is_moment(ev: dict[str, Any]) -> bool:
    """Worth a picture the moment it happens."""
    kind = ev.get("kind")
    if not ev.get("cell"):
        return False
    if kind == "incident":
        return is_dramatic(ev)
    if kind == "tale":
        return is_dramatic(ev)
    if kind == "health":
        return _health_drama(ev)
    return kind in MOMENT_KINDS


def compact_event(ev: dict[str, Any]) -> dict[str, Any]:
    out = {"seq": ev.get("seq"), "kind": ev.get("kind"), "text": ev.get("text", ""), "day": ev.get("day"), "hour": ev.get("hour")}
    if ev.get("cell"):
        out["cell"] = ev["cell"]
    if ev.get("thing"):
        out["thing"] = ev["thing"]
    data = ev.get("data") or {}
    keep = {}
    for key in EVENT_DATA_KEYS:
        if data.get(key) not in (None, "", 0, [], {}):
            keep[key] = data[key]
    if key_text := data.get("text"):
        if ev.get("kind") in ("letter", "social"):
            keep["text"] = str(key_text)[:400]
    if keep:
        out["data"] = keep
    return out


def compact_state(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: summary.get(k) for k in STATE_KEYS if k in summary}


def compact_roster(pawns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for p in pawns:
        out.append({
            "id": p.get("id"), "name": p.get("name"), "top_skills": p.get("top_skills"), "weapon": p.get("weapon"),
            "mood": p.get("mood"), "health": p.get("health"), "job": p.get("job"), "pos": p.get("pos"),
            "mental_state": p.get("mental_state"), "downed": p.get("job") == "downed",
        })
    return out


def roster_names(roster: list[dict[str, Any]]) -> list[str]:
    """The colonist names in a roster, for telling one colony from another."""
    return [str(p.get("name")).strip() for p in roster if str(p.get("name") or "").strip()]


def rosters_diverged(known: list[str], observed: list[str]) -> bool:
    """True when two rosters cannot be the same colony a little later.

    Colonies lose and gain people all the time, so anything that still shares a name is the same
    colony. Zero overlap between two non-empty rosters means the colony underneath was replaced
    wholesale -- a different playthrough that happened to compute the same chronicle id.
    """
    a = {n.casefold() for n in known if n}
    b = {n.casefold() for n in observed if n}
    if not a or not b:
        return False        # nothing recorded yet, or nobody alive to compare with: never guess
    return not (a & b)


def open_threads(summary: dict[str, Any] | None) -> list[str]:
    """Unresolved tensions the narrator can build suspense on: alerts, hostiles, quests, letters, low stocks."""
    if not summary:
        return []
    out: list[str] = []
    for a in (summary.get("alerts") or [])[:8]:
        label = a.get("label") if isinstance(a, dict) else str(a)
        if label:
            out.append(f"alert: {label}")
    hostiles = summary.get("hostiles") or []
    if hostiles:
        kinds: dict[str, int] = {}
        for h in hostiles:
            k = str(h.get("kind") or h.get("label") or "hostile") if isinstance(h, dict) else "hostile"
            kinds[k] = kinds.get(k, 0) + 1
        out.append("hostiles on the map: " + ", ".join(f"{n} {k}" for k, n in kinds.items()))
    for q in (summary.get("quests") or [])[:4]:
        if isinstance(q, dict) and q.get("name"):
            out.append(f"quest {q.get('state', '')}: {q['name']}".replace("quest :", "quest:"))
    pl = summary.get("pending_letters")
    if isinstance(pl, list) and pl:
        out.append("letters unanswered: " + ", ".join(str(x.get("label") if isinstance(x, dict) else x) for x in pl[:4]))
    elif isinstance(pl, int) and pl:
        out.append(f"{pl} letters unanswered")
    fd = summary.get("food_days")
    if fd is not None and float(fd) < 3:
        out.append(f"food for {float(fd):.1f} days")
    return out


class Watcher:
    """Call tick() every poll interval. It never raises for bridge problems; it flips `online` instead."""

    def __init__(self, bridge: BridgeLike, store: Store, cfg: dict[str, Any], clock: Callable[[], float] = time.time, timeline: "Timeline | None" = None):
        self.bridge = bridge
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.timeline = timeline
        self.online = False
        self.status: dict[str, Any] = {}
        self.game_id: str | None = None
        self.chronicle: Chronicle | None = None
        self.pending: list[dict[str, Any]] = []
        self.minor_since_chapter: dict[str, int] = {}   # e.g. {"Chitchat": 11, "DeepTalk": 2}
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
        self._legacy_id = ""            # the id this game would have had before world ids joined it
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

    def _record(self, kind: str, data: dict[str, Any], day: Any = None, hour: Any = None) -> None:
        if self.timeline is None or self.game_id is None:
            return
        try:
            self.timeline.append(self.game_id, kind, data, day=_int(day), hour=_int(hour), t=self.clock())
        except Exception:  # noqa: BLE001
            log.exception("timeline append failed")

    @property
    def hours_now(self) -> int | None:
        s = self.summary or self.status
        if not s or s.get("day") is None:
            return None
        return int(s.get("day", 0)) * 24 + int(s.get("hour", 0))

    @property
    def threads(self) -> list[str]:
        return open_threads(self.summary)

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
                self._record("game", {"state": status.get("state"), "left": True})
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
        world_uid = None
        try:
            world_uid = self.bridge.world_uid()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            world_uid = None
        gid = make_game_id(seed, start_tick, world_uid)
        self._legacy_id = make_game_id(seed, start_tick)
        self._needs_identify = False
        # The id agreeing is never on its own proof that this is the same game: an id can still
        # collide (an old chronicle from before world ids, or a bridge that cannot report one), so
        # ask the map who is alive down there before deciding nothing has changed. Identifying is
        # rare -- a reconnect, a "game" event, a tick that went backwards -- so the extra call is cheap.
        names = self._observed_names()
        if gid == self.game_id and self.chronicle is not None and not rosters_diverged(self.chronicle.last_roster, names):
            self._same_game_again(names)
            return
        self._open_chronicle(gid, seed, names)

    def _same_game_again(self, names: list[str]) -> None:
        """Re-identified as the game we are already watching, e.g. because its save was reloaded.

        The "the game was left" and "the colony is gone" latches are only true until the game comes
        back; left standing they make the narrator read every later tick as the ending it has already
        written, and the book never takes another chapter.
        """
        chron = self.chronicle
        alive = bool(names)
        if chron is not None and alive:
            chron.last_roster = names
        if not self.game_left and not (self.colony_wiped and alive):
            return
        self.game_left = False
        if alive:
            self.colony_wiped = False
        if chron is not None and chron.status == "ended" and alive:
            chron.status = "running"
            self.store.save(chron)
            log.info("chronicle %s continues: the same game is being played again", chron.id)

    def _observed_names(self) -> list[str]:
        """The colonist names on the map right now, fetched fresh (the cached roster belongs to whatever chronicle was open before)."""
        try:
            pawns = self.bridge.rpc("state.pawns", {"filter": "colonists"})
        except BridgeError:
            return []
        return roster_names(compact_roster(pawns)) if isinstance(pawns, list) else []

    def _free_id(self, gid: str) -> str:
        """`gid`, or the next free `gid-2`, `gid-3`... when a different colony already owns it."""
        if not self.store.exists(gid):
            return gid
        for n in range(2, 100):
            cand = f"{gid}-{n}"
            if not self.store.exists(cand):
                return cand
        return f"{gid}-{int(self.clock())}"

    def _open_chronicle(self, gid: str, seed: str, names: list[str] | None = None) -> None:
        previous = self.game_id
        names = self._observed_names() if names is None else names
        chron, resumed = None, False
        for cand, legacy in self._candidates(gid):
            if not self.store.exists(cand):
                continue
            c = self.store.load(cand)
            if rosters_diverged(c.last_roster, names):
                log.info("not resuming chronicle %s: its colony was %s, this one is %s", cand, c.last_roster, names)
                continue
            if legacy and c.status == "ended":
                # An id from before world ids: an ended chronicle under it is as likely to be a dead
                # colony that shared this seed as it is to be this game. Start a fresh book instead.
                log.info("not resuming ended legacy chronicle %s for a new game", cand)
                continue
            chron, resumed = c, True
            break
        if chron is None:
            gid = self._free_id(gid)
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
        gid = chron.id
        if names:
            chron.last_roster = names
        if not chron.name or not chron.faction:
            self._name_colony(chron)
        if chron.status == "ended":
            chron.status = "running"
        self.game_id = gid
        self.chronicle = chron
        self.pending = []
        self.minor_since_chapter = {}
        self.days_since_chapter = 0
        self.game_left = False
        self.colony_wiped = False
        self.summary = None
        self.roster = []
        self.cursor = chron.last_seq if resumed else 0
        self.store.save(chron)
        log.info("%s chronicle %s (%s, %s)", "resumed" if resumed else "new", gid, chron.title, seed)
        self.emit("chronicle", {"id": gid, "resumed": resumed, "previous": previous})
        self._record("chronicle", {"resumed": resumed, "name": chron.name, "seed": seed}, self.status.get("day"), self.status.get("hour"))

    def _candidates(self, gid: str) -> list[tuple[str, bool]]:
        """Chronicle ids this game may already own: its own, then the pre-world-id form of it."""
        out = [(gid, False)]
        legacy = getattr(self, "_legacy_id", "")
        if legacy and legacy != gid:
            out.append((legacy, True))
        return out

    def _name_colony(self, chron: Chronicle, force: bool = False) -> bool:
        """Settlement and faction names, so the book is 'Aswell' rather than a seed. Best effort; returns True if something changed."""
        changed = False
        for attr, path in (("name", "Find.CurrentMap.Parent.Label"), ("faction", "Find.FactionManager.OfPlayer.Name")):
            if getattr(chron, attr) and not force:
                continue
            try:
                v = self.bridge.rpc("engine.get", {"path": path, "depth": 1})
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, str) and v.strip() and v.strip() != getattr(chron, attr):
                setattr(chron, attr, v.strip())
                changed = True
        return changed

    def refresh_names(self) -> None:
        """The player may rename the settlement after landing; pick it up at chapter time."""
        if self.chronicle is not None and self.online:
            self._name_colony(self.chronicle, force=True)

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
                c = compact_event(ev)
                self._record("event", c, ev.get("day"), ev.get("hour"))   # the session record keeps everything
                if not is_notable(ev):
                    continue
                if is_minor(ev):
                    key = str((ev.get("data") or {}).get("def") or ev.get("kind"))
                    self.minor_since_chapter[key] = self.minor_since_chapter.get(key, 0) + 1
                    continue
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
                self._record("game", {"wiped": True}, summary.get("day"), summary.get("hour"))
            state = compact_state(summary)
            if self.chronicle is not None:
                self.chronicle.last_state = state
                self.chronicle.last_day = int(summary.get("day") or self.chronicle.last_day)
            self.emit("state", state)
            if self.timeline is not None and self.game_id:
                try:
                    self.timeline.sample_state(self.game_id, state, t=self.clock())
                except Exception:  # noqa: BLE001
                    log.exception("timeline state sample failed")
        try:
            pawns = self.bridge.rpc("state.pawns", {"filter": "colonists"})
        except BridgeError as e:
            self._note_error(f"state.pawns: {e}")
            return
        if not isinstance(pawns, list):
            return
        self.roster = compact_roster(pawns)
        names = roster_names(self.roster)
        chron = self.chronicle
        if chron is None or not names:
            return
        if rosters_diverged(chron.last_roster, names):
            # Every colonist was replaced between two refreshes: this is another playthrough wearing
            # the same chronicle id, not the colony we were writing about.
            log.warning("the colony changed completely (%s -> %s); re-identifying the game", chron.last_roster, names)
            self._needs_identify = True
            self._identify()
        else:
            chron.last_roster = names

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

    def take_minor(self) -> dict[str, int]:
        m, self.minor_since_chapter = self.minor_since_chapter, {}
        return m

    def restore(self, evs: list[dict[str, Any]]) -> None:
        """Put events back after a failed chapter so the next attempt still has them."""
        self.pending = evs + self.pending
        self.days_since_chapter = sum(1 for e in self.pending if e.get("kind") == "day")


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
