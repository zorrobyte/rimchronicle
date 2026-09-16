"""Decides when a chapter is due, builds the prompt (text plus images), asks the model, and stores the result."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from .bridge import BridgeError
from .images import data_url, overlay, to_jpeg
from .llm import ChatLike, image_part, text_part
from .overseer import Overseer
from .store import Chapter, Chronicle, Store
from .watcher import Watcher, is_dramatic

log = logging.getLogger("rimchronicle.narrator")

SYSTEM_PROMPT = """You are the chronicler of a RimWorld colony. You write its history as it happens, one chapter at a time, from the ledger of events, the numbers, and the pictures you are given.

Voice: third person, past tense. Dry, specific, occasionally wry, never purple. Name the colonists and the places. Say what happened, who did it, and what it cost. Small concrete details beat big adjectives. You may read the pictures for texture (weather, what the base looks like, who is standing where) but do not invent events that are not in the ledger. Do not moralise, do not address the reader, do not explain game mechanics, and do not use the words "unyielding", "testament", "tapestry" or "resilience".

Length: 180 to 400 words, in two to five paragraphs. The last line of the body must be exactly one line in this form, filled in from the numbers you were given:
State of the colony: day N, X colonists, food Y days, mood Z, threat T.

Return only a JSON object, no prose before or after it, no code fence:
{"title": "six to ten words, a chapter title, no quotation marks", "summary": "one sentence that a later chapter can refer back to", "body": "the chapter, paragraphs separated by blank lines"}
For a closing chapter also include "epitaph": one short line to carve on the colony's marker."""


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


def event_line(ev: dict[str, Any]) -> str:
    d = ev.get("data") or {}
    extras = []
    for key in ("faction", "points", "strategy", "arrival", "count", "cause", "reason", "to", "state"):
        if key in d:
            extras.append(f"{key}={d[key]}")
    tail = f" [{', '.join(extras)}]" if extras else ""
    if ev.get("kind") == "letter" and d.get("text"):
        tail += f' "{d["text"]}"'
    cell = f" at {tuple(ev['cell'])}" if ev.get("cell") else ""
    return f"- day {ev.get('day', '?')} h{int(ev.get('hour') or 0):02d} {ev.get('kind')}: {ev.get('text', '')}{cell}{tail}"


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
    """Pull {title, summary, body, epitaph?} out of whatever the model returned."""
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
    def __init__(self, cfg: dict[str, Any], store: Store, llm: ChatLike, watcher: Watcher, overseer: Overseer | None = None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.watcher = watcher
        self.overseer = overseer
        self.clock = clock
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

    # ---------------------------------------------------------------- writing
    def write(self, trigger: str = "manual") -> Chapter | None:
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
        try:
            chapter = self._write(chron, trigger, events)
        except Exception as e:  # noqa: BLE001
            log.exception("chapter failed (%s); will retry", trigger)
            self.last_error = f"{e.__class__.__name__}: {e}"
            w.restore(events)
            self.retry_after = self.clock() + 60.0
            self.busy = False
            w.emit("error", {"text": self.last_error})
            return None
        self.busy = False
        self.last_error = ""
        return chapter

    def _write(self, chron: Chronicle, trigger: str, events: list[dict[str, Any]]) -> Chapter:
        w = self.watcher
        state = dict(chron.last_state or {})
        if w.summary:
            from .watcher import compact_state
            state = compact_state(w.summary)
        k = len(chron.chapters) + 1
        day = int(state.get("day") or w.status.get("day") or chron.last_day or 0)
        hour = int(state.get("hour") or w.status.get("hour") or 0)
        closing = trigger.startswith("ending")
        images = self._capture_images(chron, k, day, trigger, events)
        notes = self.overseer.take() if self.overseer else []
        messages = self._build_messages(chron, k, trigger, events, state, images, notes, closing)
        self.last_prompt = messages
        reply = self.llm.chat(messages, max_tokens=int(self.cfg.get("max_tokens", 1200)) if "max_tokens" in self.cfg else None)
        parsed = parse_chapter(reply.content, day)
        body = parsed["body"]
        if "State of the colony:" not in body:
            body = body.rstrip() + "\n\n" + state_line(state)
        chapter = Chapter(
            k=k, title=parsed["title"], summary=parsed["summary"], body=body, day=day, hour=hour, t=self.clock(),
            images=[{"file": im["file"], "caption": im["caption"]} for im in images],
            events_used=[int(e["seq"]) for e in events if e.get("seq") is not None], trigger=trigger, state=state,
        )
        chron.chapters.append(chapter)
        chron.last_seq = w.cursor
        if closing:
            chron.status = "ended"
            chron.epitaph = parsed.get("epitaph") or parsed["summary"]
            self._ended_ids.add(chron.id)
        self.store.save(chron)
        self.last_write_t = chapter.t
        log.info("chapter %d written: %s (%s, %.1fs)", k, chapter.title, trigger, reply.elapsed)
        w.emit("chapter", {"id": chron.id, "k": k, "title": chapter.title, "day": day, "trigger": trigger})
        return chapter

    def _capture_images(self, chron: Chronicle, k: int, day: int, trigger: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        w = self.watcher
        out: list[dict[str, Any]] = []
        if not w.online:
            return out
        anchors = w.anchors() if self.cfg.get("label_anchors") else []
        grid = bool(self.cfg.get("grid"))
        home = (w.summary or {}).get("home_center") or None
        shots: list[tuple[str, int, int, int, str]] = []
        if home:
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
            out.append({"file": fname, "caption": caption, "jpeg": jpeg})
        return out

    def bridge_screenshot(self, x: int, z: int, w: int) -> bytes:
        return self.watcher.bridge.screenshot(x, z, w)

    def _build_messages(self, chron: Chronicle, k: int, trigger: str, events: list[dict[str, Any]], state: dict[str, Any], images: list[dict[str, Any]], notes: list[dict[str, Any]], closing: bool) -> list[dict[str, Any]]:
        w = self.watcher
        lines: list[str] = []
        meta = ", ".join(x for x in (chron.scenario, f"storyteller {chron.storyteller}" if chron.storyteller else "", chron.difficulty) if x)
        lines.append(f"Colony: world seed {chron.seed}" + (f" ({meta})" if meta else "") + f". The chronicle began on day {chron.started_day}.")
        if state.get("date"):
            lines.append(f"Now: {state.get('date')}, hour {int(state.get('hour') or 0):02d}, {state.get('season', '')}, {state.get('weather', '')}, {_fmt_num(state.get('temp_outdoor'))} C outdoors, biome {state.get('biome', '?')}.")
        lines.append("")
        if chron.chapters:
            lines.append("The chronicle so far (one line per chapter):")
            for ch in chron.chapters[-14:]:
                lines.append(f"- Chapter {ch.k}, day {ch.day}: {ch.title}. {ch.summary}")
        else:
            lines.append("This is the first chapter. Introduce the colony and its people as you found them; do not pretend to know how they got here beyond what the scenario and the numbers say.")
        lines.append("")
        lines.append("Colonists now:" if w.roster else "Colonists now: none alive.")
        lines.extend(roster_lines(w.roster))
        lines.append("")
        real_events = [e for e in events if e.get("kind") != "day"]
        if real_events:
            lines.append("Ledger since the last chapter (in order; a 'day' line marks midnight):")
            for ev in events[-80:]:
                lines.append(event_line(ev))
        else:
            lines.append("Ledger since the last chapter: nothing notable was recorded. Write about the ordinary work of the colony as the numbers and the picture show it.")
        lines.append("")
        if notes:
            lines.append("The overseer's log (notes written by whoever is running the colony; you may quote or paraphrase briefly):")
            for n in notes:
                lines.append(f"- {n['who']}: {n['text']}")
            lines.append("")
        lines.append("Numbers now:")
        lines.append(f"- day {state.get('day')}, hour {state.get('hour')}; colonists {state.get('colonists')} (downed {state.get('downed', 0)}, prisoners {state.get('prisoners', 0)}, animals {state.get('animals', 0)})")
        lines.append(f"- wealth {_fmt_num(state.get('wealth'))} silver; food {_fmt_num(state.get('food_days'), 1)} days ({_fmt_num(state.get('nutrition'), 1)} nutrition); average mood {_fmt_num(state.get('mood_avg'))}")
        lines.append(f"- threat {state.get('danger', 'none')} ({_fmt_num(state.get('threat_points'))} points); research {state.get('research_current') or 'none'} at {_fmt_num(state.get('research_progress'))} percent")
        lines.append(f"- the closing line must read: {state_line(state)}")
        lines.append("")
        if images:
            lines.append("Pictures attached, in order:")
            for i, im in enumerate(images, 1):
                lines.append(f"{i}. {im['caption']}")
            lines.append("")
        if closing:
            lines.append(f"This is the closing chapter ({trigger}). Give the colony its ending, name who is left or who is not, and include an epitaph.")
        else:
            lines.append(f"Write chapter {k}. Trigger: {trigger}.")
        content: list[dict[str, Any]] = [text_part("\n".join(lines))]
        for im in images:
            if im.get("jpeg"):
                content.append(image_part(data_url(to_jpeg(im["jpeg"], quality=80, max_width=1024))))
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]
