"""Decides when a chapter is due, builds the prompt (text plus pictures), asks the model in the chosen voice, and stores the result."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Callable

from .bridge import BridgeError
from .images import data_url, overlay, to_jpeg
from .llm import ChatLike, image_part, text_part
from .overseer import Overseer
from .store import Chapter, Chronicle, Store
from .voices import Voice, build_system_prompt, get_voice
from .watcher import Watcher, compact_state, is_dramatic

if TYPE_CHECKING:
    from .camera import Camera
    from .people import People
    from .timeline import Timeline

log = logging.getLogger("rimchronicle.narrator")

SOCIAL_VERBS = {
    "Insult": "insulted", "Slight": "slighted", "KindWords": "said kind words to", "RomanceAttempt": "made a pass at",
    "MarriageProposal": "proposed marriage to", "Breakup": "broke up with", "Reassure": "reassured", "Counsel": "counselled",
    "Chitchat": "chatted with", "DeepTalk": "had a deep talk with", "RecruitAttempt": "tried to recruit", "ConvertIdeoAttempt": "tried to convert",
    "EnslaveAttempt": "tried to enslave", "Speech": "gave a speech to", "Trial": "put on trial", "TameAttempt": "tried to tame", "TrainAttempt": "trained",
}
SAGA_PROMPT = """Condense the following chapter summaries of a colony's history into one paragraph of at most 180 words: plain third person, past tense, naming the people and the turning points, keeping deaths, arrivals, and unresolved troubles. Return only the paragraph."""


def _fmt_num(v: Any, digits: int = 0) -> str:
    if v is None:
        return "?"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:.{digits}f}" if digits else str(int(round(f)))


def state_line(state: dict[str, Any]) -> str:
    threat = state.get("danger") or "none"
    tp = state.get("threat_points")
    threat_s = f"{str(threat).lower()}" + (f" ({_fmt_num(tp)} points)" if tp is not None else "")
    return (f"State of the colony: day {_fmt_num(state.get('day'))}, {_fmt_num(state.get('colonists'))} colonists, "
            f"food {_fmt_num(state.get('food_days'), 1)} days, mood {_fmt_num(state.get('mood_avg'))}, threat {threat_s}.")


def _humanize(def_name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(def_name)).replace("_", " ").strip().lower()


def describe_cell(cell: Any, home: Any, map_size: Any = None) -> str:
    """'at the base', 'just north of the base', 'far to the east', 'at the west edge of the map': never coordinates."""
    try:
        x, z = float(cell[0]), float(cell[1])
    except (TypeError, ValueError, IndexError):
        return ""
    try:
        w, h = (float(map_size[0]), float(map_size[1])) if map_size else (250.0, 250.0)
    except (TypeError, ValueError, IndexError):
        w, h = 250.0, 250.0
    edge = []
    if x <= 10:
        edge.append("west")
    elif x >= w - 11:
        edge.append("east")
    if z >= h - 11:
        edge.insert(0, "north")
    elif z <= 10:
        edge.insert(0, "south")
    if edge:
        return f"at the {'-'.join(edge)} edge of the map"
    if not home:
        return ""
    try:
        dx, dz = x - float(home[0]), z - float(home[1])
    except (TypeError, ValueError, IndexError):
        return ""
    dist = (dx * dx + dz * dz) ** 0.5
    if dist < 14:
        return "at the base"
    import math
    ang = math.degrees(math.atan2(dz, dx))  # 0 = east, 90 = north
    dirs = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]
    d = dirs[int(((ang + 22.5) % 360) // 45)]
    if dist < 35:
        return f"just {d} of the base"
    if dist < 70:
        return f"some way {d} of the base"
    return f"far to the {d}"


def event_line(ev: dict[str, Any], home: Any = None, map_size: Any = None) -> str:
    d = ev.get("data") or {}
    kind = ev.get("kind")
    when = f"- Day {ev.get('day', '?')}, {int(ev.get('hour') or 0):02d}:00,"
    place = describe_cell(ev.get("cell"), home, map_size) if ev.get("cell") else ""
    cell = f" {place}" if place else ""
    if kind == "social":
        verb = SOCIAL_VERBS.get(str(d.get("def")), _humanize(str(d.get("def") or "spoke to")))
        who = f"{d.get('initiator', '?')} {verb} {d.get('recipient')}" if d.get("recipient") else f"{d.get('initiator', '?')}: {_humanize(str(d.get('def') or ''))}"
        return f"{when} {who}."
    if kind == "tale":
        pawns = ", ".join(d.get("pawns") or [])
        return f"{when} {_humanize(str(d.get('def') or d.get('label') or 'tale'))}: {pawns or ev.get('text', '')}{cell}."
    if kind == "relation":
        return f"{when} {d.get('a')} and {d.get('b')} are {'now' if d.get('added') else 'no longer'} {_humanize(str(d.get('def') or ''))}s."
    if kind == "health":
        return f"{when} {ev.get('text', '')}" + (f" ({d['part']})" if d.get("part") else "") + "."
    if kind == "trade":
        bits = []
        if d.get("sold"):
            bits.append("sold " + ", ".join(str(x) for x in d["sold"][:8]))
        if d.get("bought"):
            bits.append("bought " + ", ".join(str(x) for x in d["bought"][:8]))
        return f"{when} traded with {d.get('trader') or 'a trader'}" + (f" of {d['faction']}" if d.get("faction") else "") + (": " + "; ".join(bits) if bits else "") + "."
    if kind == "pawn_died":
        return f"{when} {ev.get('text', '')}{cell}" + (f", bonded to {d['bonded_to']}" if d.get("bonded_to") else "") + (f" [{d['cause']}]" if d.get("cause") else "") + "."
    if kind == "quest":
        st = _humanize(str(d.get("state") or "")).replace("not yet accepted", "offered")
        return f"{when} quest {st or 'offered'}: {ev.get('text', '')}."
    if kind == "colonist_joined":
        return f"{when} {ev.get('text', '')}{cell}."
    if kind == "incident":
        bits = [str(ev.get("text", "")).strip()]
        if d.get("faction"):
            bits.append(f"from {d['faction']}")
        if d.get("strategy"):
            bits.append(f"({_humanize(str(d['strategy']))})")
        if d.get("arrival"):
            bits.append(f"arriving by {_humanize(str(d['arrival']))}")
        return f"{when} {' '.join(bits)}{cell}."
    extras = []
    for key in ("cause", "reason", "to", "count"):
        if key in d:
            extras.append(f"{key} {_humanize(str(d[key])) if key != 'count' else d[key]}")
    tail = f" ({', '.join(extras)})" if extras else ""
    if kind == "letter" and d.get("text"):
        tail += f' The letter read: "{str(d["text"]).strip()}"'
    label = {"colonist_died": "died", "colonist_downed": "downed", "colonist_left": "left", "hostile_group": "hostiles arrived", "hostile_group_gone": "hostiles gone",
             "mental_break": "mental break", "research_finished": "research finished", "building_lost": "building lost", "construction_failed": "construction failed",
             "dialog_answered": "a decision was made", "danger": "danger", "manhunter": "manhunter", "built": "built", "letter": "letter", "day": "midnight"}.get(str(kind), _humanize(str(kind)))
    return f"{when} {label}: {ev.get('text', '')}{cell}{tail}"


def verified_pull_quote(quote: str, body: str) -> str:
    """The model's pull quote only if it really is in the body; otherwise the body's strongest short sentence."""
    q = (quote or "").strip().strip('"').strip()
    text = body.split("State of the colony:")[0]
    norm = lambda t: re.sub(r"[\s\u201c\u201d\"']+", " ", t).strip().lower()  # noqa: E731
    if q and norm(q) in norm(text):
        return q
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if x.strip()]
    good = [x for x in sents if 40 <= len(x) <= 160 and not x.lower().startswith(("the settlement", "the colony", "the camp"))]
    return (good or sents or [""])[0].strip('"')


def roster_lines(roster: list[dict[str, Any]]) -> list[str]:
    out = []
    for p in roster:
        bits = [p.get("top_skills") or "no notable skills"]
        bits.append(f"armed with {p['weapon']}" if p.get("weapon") else "unarmed")
        if p.get("mood") is not None:
            bits.append(f"mood {_fmt_num(p['mood'])}")
        if p.get("health") is not None and float(p["health"]) < 95:
            bits.append(f"health {_fmt_num(p['health'])}")
        if p.get("mental_state"):
            bits.append(f"mental state {p['mental_state']}")
        if p.get("downed"):
            bits.append("downed")
        if p.get("job"):
            bits.append(f"now: {p['job']}")
        out.append(f"- {p.get('name')}: " + "; ".join(str(b) for b in bits))
    return out


def parse_chapter(content: str, fallback_day: int) -> dict[str, str]:
    """Pull {title, summary, body, pull_quote?, epitaph?} out of whatever the model returned."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    candidates = [text]
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        candidates.insert(0, m.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            # tolerate raw newlines inside strings, a common model slip
            try:
                obj = json.loads(re.sub(r"(?<!\\)\n", "\\\\n", cand))
            except json.JSONDecodeError:
                continue
        if isinstance(obj, dict) and obj.get("body"):
            out = {
                "title": str(obj.get("title") or f"Day {fallback_day}").strip().strip('"'),
                "summary": str(obj.get("summary") or "").strip(),
                "body": str(obj["body"]).replace("\\n", "\n").strip(),
            }
            if obj.get("pull_quote"):
                out["pull_quote"] = str(obj["pull_quote"]).strip().strip('"')
            if obj.get("epitaph"):
                out["epitaph"] = str(obj["epitaph"]).strip()
            return out
    # Fallback: treat the whole reply as prose.
    body = text
    title = f"Day {fallback_day}"
    tm = re.search(r'"title"\s*:\s*"([^"\n]{3,120})"', text)
    if tm:
        title = tm.group(1)
        body = re.sub(r'"title"\s*:\s*"[^"\n]*"\s*,?', "", body)
    bm = re.search(r'"body"\s*:\s*"(.*)', text, flags=re.S)
    if bm:
        body = bm.group(1).rstrip().rstrip("}").rstrip().rstrip('"').replace("\\n", "\n")
    summary = re.split(r"(?<=[.!?])\s", body.strip(), maxsplit=1)[0][:200] if body.strip() else ""
    return {"title": title, "summary": summary, "body": body.strip() or "(the chronicler was silent)"}


class Narrator:
    def __init__(self, cfg: dict[str, Any], store: Store, llm: ChatLike, watcher: Watcher, overseer: Overseer | None = None, clock: Callable[[], float] = time.time,
                 camera: "Camera | None" = None, people: "People | None" = None, timeline: "Timeline | None" = None):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.watcher = watcher
        self.overseer = overseer
        self.clock = clock
        self.camera = camera
        self.people = people
        self.timeline = timeline
        self.last_write_t = 0.0
        self.last_attempt_t = 0.0
        self.retry_after = 0.0
        self.busy = False
        self.last_prompt: list[dict[str, Any]] | None = None
        self.last_error = ""
        self._ended_ids: set[str] = set()

    # ---------------------------------------------------------------- cadence
    def due(self) -> str | None:
        """Return a trigger name if a chapter should be written now, else None."""
        w = self.watcher
        chron = w.chronicle
        if chron is None or self.busy:
            return None
        now = self.clock()
        if now < self.retry_after:
            return None
        if chron.status != "ended":
            # The watcher re-opened this book (a new game, or the same one played on), so the
            # in-memory "this one is finished" latch must not outlive it.
            self._ended_ids.discard(chron.id)
        ncfg = self.cfg
        closing = None
        if w.colony_wiped:
            closing = "ending: the colony is gone"
        elif w.game_left:
            closing = "ending: the game was left"
        if closing:
            return None if chron.id in self._ended_ids or chron.status == "ended" else closing
        if not w.online or w.status.get("state") != "playing":
            return None
        last_t = chron.chapters[-1].t if chron.chapters else self.last_write_t
        if last_t and now - last_t < float(ncfg.get("min_real_seconds_between", 120)):
            return None
        for ev in w.pending:
            if is_dramatic(ev):
                return f"drama: {ev.get('text', ev.get('kind'))}"
        if not chron.chapters and ncfg.get("opening_chapter", True) and w.summary:
            return "opening"
        if w.days_since_chapter >= 1 and len(w.pending) >= int(ncfg.get("min_events", 4)):
            return "day"
        hours = w.hours_now
        if hours is not None and chron.chapters:
            last = chron.chapters[-1]
            if hours - (last.day * 24 + last.hour) >= int(ncfg.get("max_hours_between", 24)):
                return "time"
        return None

    # ---------------------------------------------------------------- voices
    def voice_for(self, chron: Chronicle, override: str | None = None) -> Voice:
        return get_voice(override or chron.voice or self.cfg.get("voice") or "storyteller")

    def _resolve_diarist(self, chron: Chronicle) -> tuple[str, str]:
        """(diarist, handover text). Picks one when none is set or the current one is gone."""
        names = [str(p.get("name")) for p in self.watcher.roster if p.get("name")]
        prev = chron.diarist
        if prev and prev in names:
            return prev, ""
        new = ""
        if self.people is not None:
            new = self.people.pick_diarist(chron, self.watcher.roster)
        if not new and names:
            new = names[0]
        chron.diarist = new
        if prev and new and prev != new:
            return new, f"The previous diarist, {prev}, is gone (dead or departed); you have taken up the book. Acknowledge that in your first lines."
        return new, ""

    # ---------------------------------------------------------------- writing
    def write(self, trigger: str = "manual", voice: str | None = None, focus: str | None = None) -> Chapter | None:
        w = self.watcher
        chron = w.chronicle
        if chron is None:
            log.warning("no chronicle open; nothing to write")
            return None
        self.busy = True
        self.last_attempt_t = self.clock()
        if w.summary is None and w.online:
            w.refresh_state()
        events = w.consume()
        minor = w.take_minor()
        try:
            chapter = self._write(chron, trigger, events, minor, voice, focus)
        except Exception as e:  # noqa: BLE001
            log.exception("chapter failed (%s); will retry", trigger)
            self.last_error = f"{e.__class__.__name__}: {e}"
            w.restore(events)
            for k, v in minor.items():
                w.minor_since_chapter[k] = w.minor_since_chapter.get(k, 0) + v
            self.retry_after = self.clock() + 60.0
            self.busy = False
            w.emit("error", {"text": self.last_error})
            return None
        self.busy = False
        self.last_error = ""
        return chapter

    def _write(self, chron: Chronicle, trigger: str, events: list[dict[str, Any]], minor: dict[str, int], voice_id: str | None, focus: str | None) -> Chapter:
        w = self.watcher
        state = dict(chron.last_state or {})
        if w.summary:
            state = compact_state(w.summary)
        k = len(chron.chapters) + 1
        day = int(state.get("day") or w.status.get("day") or chron.last_day or 0)
        hour = int(state.get("hour") or w.status.get("hour") or 0)
        closing = trigger.startswith("ending")
        w.refresh_names()
        if self.people is not None and w.online:
            try:
                self.people.refresh(chron, w.roster, day=day)
            except Exception:  # noqa: BLE001
                log.exception("people refresh failed; writing with what we have")
        voice = self.voice_for(chron, voice_id)
        images = self._gather_images(chron, k, day, trigger, events)
        notes = self.overseer.take() if self.overseer else []
        messages, user_text, sysinfo = self._build_messages(chron, k, trigger, events, state, images, notes, closing, voice, focus, minor)
        self.last_prompt = messages
        reply = self._chat(messages, voice)
        parsed = parse_chapter(reply.content, day)
        body = parsed["body"]
        if "State of the colony:" not in body:
            body = body.rstrip() + "\n\n" + state_line(state)
        parsed["pull_quote"] = verified_pull_quote(parsed.get("pull_quote", ""), body)
        chapter = Chapter(
            k=k, title=parsed["title"], summary=parsed["summary"], body=body, day=day, hour=hour, t=self.clock(),
            images=[{"file": im["file"], "caption": im["caption"], "frame": bool(im.get("frame"))} for im in images],
            events_used=[int(e["seq"]) for e in events if e.get("seq") is not None], trigger=trigger, state=state,
            voice=voice.id, pull_quote=parsed.get("pull_quote", ""),
            inputs={"user_text": user_text, "images": [{"file": im["file"], "caption": im["caption"], "frame": bool(im.get("frame"))} for im in images],
                    "state": state, "closing": closing, "roster": [str(p.get("name")) for p in w.roster], **sysinfo},
        )
        chron.chapters.append(chapter)
        chron.last_seq = w.cursor
        if closing:
            chron.status = "ended"
            chron.epitaph = parsed.get("epitaph") or parsed["summary"]
            self._ended_ids.add(chron.id)
        self.store.save(chron)
        self.last_write_t = chapter.t
        if self.camera is not None:
            self.camera.reset_chapter()
        if self.timeline is not None:
            try:
                self.timeline.append(chron.id, "chapter", {"k": k, "title": chapter.title, "voice": voice.id, "trigger": trigger, "images": [im["file"] for im in chapter.images]}, day=day, hour=hour, t=chapter.t)
            except Exception:  # noqa: BLE001
                log.exception("timeline chapter append failed")
        log.info("chapter %d written: %s (%s, %s, %.1fs)", k, chapter.title, voice.id, trigger, reply.elapsed)
        w.emit("chapter", {"id": chron.id, "k": k, "title": chapter.title, "day": day, "trigger": trigger, "voice": voice.id})
        self._maybe_roll_saga(chron)
        return chapter

    def _chat(self, messages: list[dict[str, Any]], voice: Voice):
        max_tokens = int(self.cfg.get("max_tokens", 1200)) if "max_tokens" in self.cfg else None
        try:
            return self.llm.chat(messages, max_tokens=max_tokens, temperature=voice.temperature)
        except TypeError:
            return self.llm.chat(messages, max_tokens=max_tokens)

    # ---------------------------------------------------------------- rewriting
    def rewrite(self, chron: Chronicle, k: int, voice_id: str) -> Chapter:
        """Re-narrate chapter k from its stored inputs in another voice, keeping the previous rendering as a version."""
        if k < 1 or k > len(chron.chapters):
            raise ValueError(f"no chapter {k}")
        ch = chron.chapters[k - 1]
        inputs = ch.inputs or {}
        if not inputs.get("user_text"):
            raise ValueError("this chapter predates re-narration; its prompt was not stored")
        voice = get_voice(voice_id)
        state = inputs.get("state") or ch.state or {}
        diarist, hint = inputs.get("diarist", ""), inputs.get("diarist_hint", "")
        if voice.id == "diary" and not diarist:
            names = inputs.get("roster") or []
            diarist = chron.diarist or (names[0] if names else "a colonist")
            if self.people is not None:
                hint = self.people.diarist_hint(chron, diarist)
        system = build_system_prompt(voice, colony=chron.title, state_line=state_line(state), date=str(state.get("date") or ""), storyteller=chron.storyteller,
                                     diarist=diarist, diarist_hint=hint, custom_prompt=str(self.cfg.get("custom_prompt") or ""), directive=chron.directive or str(self.cfg.get("directive") or ""))
        content: list[dict[str, Any]] = [text_part(inputs["user_text"])]
        for im in inputs.get("images") or []:
            try:
                p = self.store.frame_path(chron.id, im["file"]) if im.get("frame") else self.store.image_path(chron.id, im["file"])
                content.append(image_part(data_url(to_jpeg(p.read_bytes(), quality=80, max_width=1024 if not im.get("frame") else 768))))
            except (OSError, FileNotFoundError):
                continue
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        self.last_prompt = messages
        reply = self._chat(messages, voice)
        parsed = parse_chapter(reply.content, ch.day)
        body = parsed["body"]
        if "State of the colony:" not in body:
            body = body.rstrip() + "\n\n" + state_line(state)
        parsed["pull_quote"] = verified_pull_quote(parsed.get("pull_quote", ""), body)
        ch.versions.append({"title": ch.title, "summary": ch.summary, "body": ch.body, "voice": ch.voice, "pull_quote": ch.pull_quote, "t": ch.t})
        del ch.versions[:-6]
        ch.title, ch.summary, ch.body, ch.voice, ch.pull_quote, ch.t = parsed["title"], parsed["summary"] or ch.summary, body, voice.id, parsed.get("pull_quote", ""), self.clock()
        if inputs.get("closing") and parsed.get("epitaph"):
            chron.epitaph = parsed["epitaph"]
        self.store.save(chron)
        log.info("chapter %d rewritten as %s: %s (%.1fs)", k, voice.id, ch.title, reply.elapsed)
        self.watcher.emit("chapter", {"id": chron.id, "k": k, "title": ch.title, "day": ch.day, "trigger": "rewrite", "voice": voice.id, "rewrite": True})
        return ch

    # ---------------------------------------------------------------- saga
    def _maybe_roll_saga(self, chron: Chronicle) -> None:
        every = int(self.cfg.get("saga_every", 8) or 0)
        n = len(chron.chapters)
        if every <= 0 or n - chron.saga_through < every:
            return
        upto = n - 2  # keep the last two chapters verbatim in the prompt
        if upto <= chron.saga_through:
            return
        lines = [SAGA_PROMPT, ""]
        if chron.saga:
            lines += ["The story so far:", chron.saga, ""]
        lines.append("Chapters to fold in:")
        for ch in chron.chapters[chron.saga_through:upto]:
            lines.append(f"- Chapter {ch.k}, day {ch.day}: {ch.title}. {ch.summary}")
        try:
            reply = self.llm.chat([{"role": "user", "content": [text_part("\n".join(lines))]}], max_tokens=400)
        except Exception:  # noqa: BLE001
            log.exception("saga rollup failed; keeping chapter lines")
            return
        text = (reply.content or "").strip().strip('"')
        if len(text) < 40:
            return
        chron.saga, chron.saga_through = text, upto
        self.store.save(chron)
        log.info("saga rolled through chapter %d", upto)

    # ---------------------------------------------------------------- pictures
    def _gather_images(self, chron: Chronicle, k: int, day: int, trigger: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        w = self.watcher
        if not w.online:
            return []
        if self.camera is None:
            return self._capture_images(chron, k, day, trigger, events)
        out: list[dict[str, Any]] = []
        wide = self.camera.wide(chron, w.summary, int(self.cfg.get("base_width_cells", 50)), day)
        if wide is not None:
            out.append({"file": wide["file"], "caption": wide["label"], "jpeg": wide["jpeg"], "frame": True})
        since_t = chron.chapters[-1].t if chron.chapters else 0.0
        for m in self.camera.pick_for_chapter(chron, since_t, int(self.cfg.get("moments_per_chapter", 3))):
            out.append({"file": m["file"], "caption": m["label"], "jpeg": m["jpeg"], "frame": True})
        if len(out) <= 1:
            # no moments were caught live; fall back to a shot of the most interesting event cell now
            for im in self._capture_images(chron, k, day, trigger, events, only_event=True):
                out.append(im)
        return out

    def _capture_images(self, chron: Chronicle, k: int, day: int, trigger: str, events: list[dict[str, Any]], only_event: bool = False) -> list[dict[str, Any]]:
        w = self.watcher
        out: list[dict[str, Any]] = []
        if not w.online:
            return out
        anchors = w.anchors() if self.cfg.get("label_anchors") else []
        grid = bool(self.cfg.get("grid"))
        home = (w.summary or {}).get("home_center") or None
        shots: list[tuple[str, int, int, int, str]] = []
        if home and not only_event:
            shots.append(("base", int(home[0]), int(home[1]), int(self.cfg.get("base_width_cells", 50)), f"The base on day {day}"))
        focus = None
        for ev in events:
            if ev.get("cell") and is_dramatic(ev):
                focus = ev
                break
        if focus is None:
            for ev in reversed(events):
                if ev.get("cell") and ev.get("kind") not in ("day", "built"):
                    focus = ev
                    break
        if focus is not None:
            cx, cz = int(focus["cell"][0]), int(focus["cell"][1])
            if not home or abs(cx - home[0]) > 8 or abs(cz - home[1]) > 8:
                shots.append(("event", cx, cz, int(self.cfg.get("event_width_cells", 30)), f"{focus.get('text', 'The event')}, day {focus.get('day', day)}"))
        for name, x, z, wcells, caption in shots[:2]:
            try:
                png = self.bridge_screenshot(x, z, wcells)
            except BridgeError as e:
                log.warning("screenshot %s failed: %s", name, e)
                continue
            if anchors or grid:
                try:
                    png = overlay(png, x, z, wcells, anchors=anchors, grid=grid)
                except Exception:  # noqa: BLE001
                    log.exception("overlay failed; using the raw frame")
            jpeg = to_jpeg(png, quality=85)
            fname = f"ch{k:03d}-{name}.jpg"
            self.store.save_image(chron.id, fname, jpeg)
            out.append({"file": fname, "caption": caption, "jpeg": jpeg, "frame": False})
        return out

    def bridge_screenshot(self, x: int, z: int, w: int) -> bytes:
        return self.watcher.bridge.screenshot(x, z, w)

    # ---------------------------------------------------------------- the prompt
    def _build_messages(self, chron: Chronicle, k: int, trigger: str, events: list[dict[str, Any]], state: dict[str, Any], images: list[dict[str, Any]],
                        notes: list[dict[str, Any]], closing: bool, voice: Voice, focus: str | None, minor: dict[str, int]) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        w = self.watcher
        colony = chron.title
        lines: list[str] = []
        meta = ", ".join(x for x in (f"of the faction {chron.faction}" if chron.faction else "", chron.scenario, f"storyteller {chron.storyteller}" if chron.storyteller else "", chron.difficulty) if x)
        lines.append(f"Colony: {colony}" + (f" ({meta})" if meta else "") + f". World seed {chron.seed}. The chronicle began on day {chron.started_day}.")
        if state.get("date"):
            lines.append(f"Now: {state.get('date')}, hour {int(state.get('hour') or 0):02d}, {state.get('season', '')}, {state.get('weather', '')}, {_fmt_num(state.get('temp_outdoor'))} C outdoors, biome {_humanize(str(state.get('biome', '?')))}.")
        lines.append("")
        if chron.chapters:
            if chron.saga:
                lines.append("The story so far:")
                lines.append(chron.saga)
                lines.append("")
            recent = chron.chapters[chron.saga_through:][-12:]
            if recent:
                lines.append("Recent chapters (one line each):")
                for ch in recent:
                    lines.append(f"- Chapter {ch.k}, day {ch.day}: {ch.title}. {ch.summary}")
        else:
            lines.append("This is the first chapter. Introduce the colony and its people as you found them; do not pretend to know how they got here beyond what the scenario, the backstories and the numbers say.")
        lines.append("")
        lines.append(f"The people of {colony}:" if w.roster else f"The people of {colony}: none alive.")
        if self.people is not None:
            lines.extend(self.people.dossier_lines(chron, w.roster, int(self.cfg.get("dossier_colonists", 12))))
        else:
            lines.extend(roster_lines(w.roster))
        lines.append("")
        diff = self._since_last(chron, state)
        if diff:
            lines.append("Since the last chapter: " + "; ".join(diff) + ".")
            lines.append("")
        now_h = w.hours_now
        if now_h is not None:
            # a loaded save can roll the clock back; events from the abandoned future would confuse the narrator
            events = [e for e in events if e.get("day") is None or int(e.get("day") or 0) * 24 + int(e.get("hour") or 0) <= now_h + 1]
        real_events = [e for e in events if e.get("kind") != "day"]
        if real_events:
            lines.append("Ledger since the last chapter (in order, oldest first; 'Day N, HH:00' is the in-game day and hour; a 'day' line marks midnight; places are relative to the base):")
            home = (w.summary or {}).get("home_center")
            map_size = w.status.get("map_size")
            for ev in events[-80:]:
                lines.append(event_line(ev, home, map_size))
        else:
            lines.append("Ledger since the last chapter: nothing notable was recorded. Write about the ordinary work of the colony as the numbers, the people and the pictures show it.")
        if minor:
            words = {"Chitchat": "idle chats", "DeepTalk": "long talks", "BuildRapport": "friendly talks", "AnimalChat": "words with the animals", "Nuzzle": "nuzzles from an animal", "BabyPlay": "play with a baby"}
            parts = [f"{n} {words.get(k, _humanize(k))}" for k, n in sorted(minor.items(), key=lambda x: -x[1])[:6]]
            lines.append("Small talk not listed above, for texture only: " + ", ".join(parts) + ".")
        lines.append("")
        threads = w.threads
        if threads:
            lines.append("Open threads (unresolved as of now; use them for tension, do not resolve them):")
            lines.extend(f"- {t}" for t in threads[:10])
            lines.append("")
        if notes:
            lines.append("Notes left by whoever is running the colony (you may quote or paraphrase briefly; they are opinions, not events):")
            for n in notes:
                lines.append(f"- {n['who']}: {n['text']}")
            lines.append("")
        lines.append("Numbers now (for the closing line and your own orientation; do not narrate wealth, points, percentages or mood scores):")
        lines.append(f"- day {state.get('day')}, hour {state.get('hour')}; colonists {state.get('colonists')} (downed {state.get('downed', 0)}, prisoners {state.get('prisoners', 0)}, animals {state.get('animals', 0)})")
        lines.append(f"- wealth {_fmt_num(state.get('wealth'))} silver; food {_fmt_num(state.get('food_days'), 1)} days ({_fmt_num(state.get('nutrition'), 1)} nutrition); average mood {_fmt_num(state.get('mood_avg'))}")
        lines.append(f"- threat {state.get('danger', 'none')} ({_fmt_num(state.get('threat_points'))} points); research {state.get('research_current') or 'none'} at {_fmt_num(state.get('research_progress'))} percent")
        lines.append(f"- the closing line must read: {state_line(state)}")
        lines.append("")
        if images:
            lines.append("Pictures attached, in order (the first is the whole base from above; the others were taken as things happened):")
            for i, im in enumerate(images, 1):
                lines.append(f"{i}. {im['caption']}")
            lines.append("")
        if closing:
            lines.append(f"This is the closing chapter ({trigger}). Give the colony its ending, name who is left or who is not, and include an epitaph.")
        else:
            lines.append(f"Write chapter {k}. Trigger: {trigger}.")
        if focus and focus.strip():
            lines.append("Whoever asked for this chapter wants it to focus on: " + focus.strip())
        user_text = "\n".join(lines)
        content: list[dict[str, Any]] = [text_part(user_text)]
        for i, im in enumerate(images):
            if im.get("jpeg"):
                content.append(image_part(data_url(to_jpeg(im["jpeg"], quality=80, max_width=1024 if i == 0 else 768))))
        sysinfo: dict[str, Any] = {"date": str(state.get("date") or ""), "storyteller": chron.storyteller}
        diarist = hint = handover = ""
        if voice.id == "diary":
            diarist, handover = self._resolve_diarist(chron)
            hint = self.people.diarist_hint(chron, diarist) if self.people is not None else ""
            sysinfo.update({"diarist": diarist, "diarist_hint": hint})
        system = build_system_prompt(voice, colony=colony, state_line=state_line(state), date=sysinfo["date"], storyteller=chron.storyteller,
                                     diarist=diarist, diarist_hint=hint, diarist_handover=handover,
                                     custom_prompt=str(self.cfg.get("custom_prompt") or ""), directive=chron.directive or str(self.cfg.get("directive") or ""))
        return [{"role": "system", "content": system}, {"role": "user", "content": content}], user_text, sysinfo

    def _since_last(self, chron: Chronicle, state: dict[str, Any]) -> list[str]:
        if not chron.chapters:
            return []
        last = chron.chapters[-1]
        out: list[str] = []
        prev_names = set((last.inputs or {}).get("roster") or [])
        now_names = {str(p.get("name")) for p in self.watcher.roster if p.get("name")}
        if prev_names:
            joined = sorted(now_names - prev_names)
            gone = sorted(prev_names - now_names)
            if joined:
                out.append("joined: " + ", ".join(joined))
            if gone:
                out.append("gone: " + ", ".join(gone))
        ps = last.state or {}
        for key, label, digits in (("mood_avg", "mood", 0), ("wealth", "wealth", 0), ("food_days", "food days", 1)):
            a, b = ps.get(key), state.get(key)
            if a is None or b is None:
                continue
            try:
                delta = float(b) - float(a)
            except (TypeError, ValueError):
                continue
            if abs(delta) >= (5 if key != "food_days" else 1):
                out.append(f"{label} {_fmt_num(a, digits)} -> {_fmt_num(b, digits)}")
        try:
            days = int(state.get("day") or 0) - int(ps.get("day") or 0)
            if days >= 1:
                out.append(f"{days} day{'s' if days != 1 else ''} passed")
        except (TypeError, ValueError):
            pass
        return out
