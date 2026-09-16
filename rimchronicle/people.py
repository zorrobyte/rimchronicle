"""Who these people are: a dossier per colonist, kept in people.json and fed to the narrator.

Traits, backstory, age, relations, passions, the room they sleep in, what is bothering them, what has happened to
them (an "arc" of one-liners accumulated from the ledger), and, for the fallen, when and how. Refreshed from the
bridge's state.pawn a few times per chapter, never per tick.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .bridge import BridgeLike
from .store import Chronicle, Store

log = logging.getLogger("rimchronicle.people")

ARC_KINDS = {"colonist_died", "colonist_downed", "colonist_joined", "colonist_left", "mental_break", "tale", "relation", "health", "social"}
MAX_ARC = 14
SKILL_RE = re.compile(r"^(\d+)(!*)$")


def _passion(mark: str) -> str:
    return {"!": "interested", "!!": "burning"}.get(mark, "")


def _skills_line(skills: Any, n: int = 3) -> str:
    if not isinstance(skills, dict):
        return ""
    parsed = []
    for name, val in skills.items():
        m = SKILL_RE.match(str(val).strip())
        if not m:
            continue
        parsed.append((int(m.group(1)), name, m.group(2)))
    parsed.sort(reverse=True)
    out = []
    for lvl, name, mark in parsed[:n]:
        p = _passion(mark)
        out.append(f"{name} {lvl}" + (f" ({p})" if p else ""))
    return ", ".join(out)


def _relations(rel: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(rel, list):
        return out
    for r in rel:
        if isinstance(r, dict):
            kind = r.get("def") or r.get("relation") or r.get("label") or r.get("kind")
            other = r.get("other") or r.get("pawn") or r.get("name") or r.get("with")
            if isinstance(other, dict):
                other = other.get("name")
            if kind and other:
                out.append(f"{_humanize(str(kind))} of {other}")
        elif isinstance(r, str):
            out.append(r)
    return out[:6]


def _humanize(def_name: str) -> str:
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", def_name).replace("_", " ").strip().lower()
    return s


def _worst_thoughts(thoughts: Any, n: int = 3) -> list[str]:
    if not isinstance(thoughts, list):
        return []
    neg = [t for t in thoughts if isinstance(t, dict) and float(t.get("mood") or 0) < 0 and "%" not in str(t.get("label") or "")]
    neg.sort(key=lambda t: float(t.get("mood") or 0))
    return [str(t.get("label") or t.get("thought")) for t in neg[:n]]


NOISE_HEDIFFS = ("iud", "implanted", "vasectomy", "tubal", "sterilized")
QUALITY_RE = re.compile(r"\s*\((?:awful|poor|normal|good|excellent|masterwork|legendary)[^)]*\)", re.I)


def clean_label(label: Any) -> str:
    """'Revolver (normal 53%)' -> 'Revolver'."""
    return QUALITY_RE.sub("", str(label or "")).strip()


def _health_notes(detail: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for h in detail.get("hediffs") or []:
        if isinstance(h, dict):
            label = h.get("label") or h.get("def")
            part = h.get("part")
            if not label or any(w in str(label).lower() for w in NOISE_HEDIFFS):
                continue
            out.append(f"{label}" + (f" ({part})" if part and str(part).lower() not in str(label).lower() else ""))
        elif isinstance(h, str):
            out.append(h)
    caps = detail.get("capacities") or {}
    if isinstance(caps, dict):
        for k, v in caps.items():
            try:
                if float(v) < 60:
                    out.append(f"{k.lower()} {int(float(v))}%")
            except (TypeError, ValueError):
                continue
    return out[:5]


def dossier_from_detail(detail: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    d = dict(old or {})
    d.update({
        "id": detail.get("id") or d.get("id"), "name": detail.get("name") or d.get("name"),
        "gender": detail.get("gender"), "age": detail.get("age"), "race": detail.get("race"), "xenotype": detail.get("xenotype"),
        "childhood": _known(detail.get("childhood")), "adulthood": _known(detail.get("adulthood")), "ideo": detail.get("ideo"),
        "traits": [str(t.get("label") or t.get("trait")) if isinstance(t, dict) else str(t) for t in (detail.get("traits") or [])],
        "skills": _skills_line(detail.get("skills")), "skills_full": detail.get("skills") if isinstance(detail.get("skills"), dict) else {},
        "relations": _relations(detail.get("relations")),
        "mood": detail.get("mood"), "thoughts": _worst_thoughts(detail.get("thoughts")),
        "health": _health_notes(detail), "room": _known((detail.get("room") or {}).get("role")) if isinstance(detail.get("room"), dict) else None,
        "job": detail.get("job"), "weapon": clean_label(detail.get("weapon") or ((detail.get("equipment") or [{}])[0].get("label") if detail.get("equipment") else None)) or None,
        "thoughts_all": [str(t.get("label")) for t in (detail.get("thoughts") or []) if isinstance(t, dict)][:20],
    })
    d.setdefault("arc", [])
    d.setdefault("joined_day", None)
    return d


class People:
    def __init__(self, bridge: BridgeLike | None, store: Store, cfg: dict[str, Any] | None = None):
        self.bridge = bridge
        self.store = store
        self.cfg = cfg or {}
        self._cache: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- storage
    def load(self, game_id: str) -> dict[str, Any]:
        if game_id not in self._cache:
            data = self.store.read_json(self.store.people_path(game_id), {"colonists": {}, "fallen": {}})
            data.setdefault("colonists", {})
            data.setdefault("fallen", {})
            self._cache[game_id] = data
        return self._cache[game_id]

    def save(self, game_id: str) -> None:
        if game_id in self._cache:
            self.store.write_json(self.store.people_path(game_id), self._cache[game_id])

    # ---------------------------------------------------------------- updating
    def refresh(self, chron: Chronicle, roster: list[dict[str, Any]], day: int | None = None, limit: int = 16) -> int:
        """Pull state.pawn for the colonists on the roster (stalest first). Returns how many were refreshed."""
        data = self.load(chron.id)
        cols = data["colonists"]
        names = [str(p.get("name")) for p in roster if p.get("name")]
        # anyone on the roster we have never seen joined now
        for p in roster:
            n = str(p.get("name"))
            if n and n not in cols:
                cols[n] = {"name": n, "id": p.get("id"), "arc": [], "joined_day": day, "stale": 0}
        # anyone we knew who left the roster without a death event is marked absent (left, kidnapped, captured)
        for n, d in list(cols.items()):
            if n not in names and not d.get("absent"):
                d["absent"] = True
                d["absent_day"] = day
            elif n in names and d.get("absent"):
                d.pop("absent", None)
                d.pop("absent_day", None)
        n_done = 0
        if self.bridge is not None and hasattr(self.bridge, "pawn_detail"):
            order = sorted(names, key=lambda n: int(cols.get(n, {}).get("refreshed_day") or -1))
            for n in order[:limit]:
                try:
                    detail = self.bridge.pawn_detail(cols.get(n, {}).get("id") or n)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    detail = None
                if not detail:
                    continue
                cols[n] = dossier_from_detail(detail, cols.get(n))
                cols[n]["refreshed_day"] = day
                n_done += 1
        # cheap fields from the roster even when detail is unavailable
        for p in roster:
            n = str(p.get("name"))
            if n in cols:
                for k in ("mood", "job", "weapon", "top_skills", "health_pct"):
                    v = p.get("health" if k == "health_pct" else k)
                    if v is not None:
                        cols[n][k] = v
        self.save(chron.id)
        return n_done

    def note_events(self, chron: Chronicle, events: list[dict[str, Any]]) -> None:
        """Append arc one-liners for events that name a colonist; move the dead to the fallen."""
        data = self.load(chron.id)
        cols, fallen = data["colonists"], data["fallen"]
        changed = False
        names = list(cols.keys())
        for ev in events:
            kind = ev.get("kind")
            if kind not in ARC_KINDS:
                continue
            text = str(ev.get("text") or "")
            d = ev.get("data") or {}
            day = ev.get("day")
            involved = [n for n in names if _mentions(text, n) or n in (d.get("pawns") or []) or n in (d.get("initiator"), d.get("recipient"), d.get("a"), d.get("b"))]
            if kind == "colonist_died":
                for n in involved:
                    dead = cols.pop(n, None)
                    if dead is not None:
                        dead["died"] = {"day": day, "cause": d.get("cause") or "", "text": text}
                        fallen[n] = dead
                        changed = True
                continue
            line = _arc_line(kind, text, d)
            if not line:
                continue
            for n in involved:
                if kind == "relation":
                    other = d.get("b") if n == d.get("a") else d.get("a")
                    line = f"{'now' if d.get('added') else 'no longer'} {_humanize(str(d.get('def') or ''))} of {other}"
                arc = cols[n].setdefault("arc", [])
                if not arc or arc[-1].get("text") != line:
                    arc.append({"day": day, "text": line})
                    del arc[:-MAX_ARC]
                    changed = True
        if changed:
            self.save(chron.id)

    # ---------------------------------------------------------------- output
    def dossier_lines(self, chron: Chronicle, roster: list[dict[str, Any]], max_full: int = 12) -> list[str]:
        data = self.load(chron.id)
        cols = data["colonists"]
        lines: list[str] = []
        present = [str(p.get("name")) for p in roster if p.get("name")]
        for i, n in enumerate(present):
            d = cols.get(n) or {"name": n}
            r = next((p for p in roster if p.get("name") == n), {})
            lines.append(_full_line(d, r) if i < max_full else _short_line(d, r))
        fallen = data["fallen"]
        if fallen:
            bits = []
            for n, d in list(fallen.items())[-8:]:
                died = d.get("died") or {}
                cause = _humanize(str(died.get("cause") or "")) or "unknown cause"
                bits.append(f"{n} (day {died.get('day', '?')}, {cause}" + (f"; was {d['adulthood'].lower()}" if d.get("adulthood") else "") + ")")
            lines.append("The fallen: " + "; ".join(bits) + ".")
        absent = [n for n, d in cols.items() if d.get("absent")]
        if absent:
            lines.append("Gone from the colony but not known dead: " + ", ".join(absent) + ".")
        return lines

    def diarist_hint(self, chron: Chronicle, name: str) -> str:
        d = self.load(chron.id)["colonists"].get(name) or {}
        bits = []
        if d.get("traits"):
            bits.append(", ".join(str(t).lower() for t in d["traits"]))
        if d.get("adulthood"):
            bits.append(f"once a {str(d['adulthood']).lower()}")
        if d.get("thoughts"):
            bits.append("bothered by " + ", ".join(t.lower() for t in d["thoughts"][:2]))
        return "; ".join(bits) or "you write plainly"

    def pick_diarist(self, chron: Chronicle, roster: list[dict[str, Any]]) -> str:
        """The colonist with the highest Social skill, else the first on the roster."""
        cols = self.load(chron.id)["colonists"]
        best, best_v = "", -1
        for p in roster:
            n = str(p.get("name") or "")
            sk = (cols.get(n) or {}).get("skills_full") or {}
            m = SKILL_RE.match(str(sk.get("Social", "")).strip()) if isinstance(sk, dict) else None
            v = int(m.group(1)) if m else 0
            if v > best_v:
                best, best_v = n, v
        return best

    def to_dict(self, chron: Chronicle) -> dict[str, Any]:
        data = self.load(chron.id)
        return {"colonists": data["colonists"], "fallen": data["fallen"]}

    def portrait(self, chron: Chronicle, name: str, file: str, day: int | None) -> None:
        cols = self.load(chron.id)["colonists"]
        d = cols.setdefault(name, {"name": name, "arc": [], "joined_day": day})
        d["portrait"] = file
        d["portrait_day"] = day
        self.save(chron.id)


def _known(v: Any) -> Any:
    """None for the game's 'Unknown'/'None' placeholders."""
    if v is None or str(v).strip().lower() in ("unknown", "none", ""):
        return None
    return v


def _mentions(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", text) is not None


def _arc_line(kind: str, text: str, d: dict[str, Any]) -> str:
    if kind == "social" and d.get("minor"):
        return ""
    if kind == "tale":
        return _humanize(str(d.get("def") or "")) + (": " + ", ".join(d.get("pawns") or []) if d.get("pawns") else "")
    if kind == "relation":
        return f"{'now' if d.get('added') else 'no longer'} {_humanize(str(d.get('def') or ''))} of {d.get('b') or d.get('a')}"
    if kind == "mental_break":
        reason = str(d.get("reason") or "")
        straw = reason.split("final straw was:")[-1].strip().rstrip(".") if "final straw" in reason else ""
        return text + (f" (the last straw: {straw.lower()})" if straw else "")
    return text.strip()


def _full_line(d: dict[str, Any], r: dict[str, Any]) -> str:
    n = d.get("name")
    bits: list[str] = []
    who = []
    if d.get("age"):
        who.append(str(d["age"]))
    if d.get("gender"):
        who.append({"Female": "woman", "Male": "man"}.get(str(d["gender"]), str(d["gender"]).lower()))
    head = f"- {n}" + (f", {', '.join(who)}" if who else "") + "."
    back = [str(b) for b in (d.get("childhood"), d.get("adulthood")) if b]
    if len(back) == 2:
        bits.append(f"{back[0]}, then {back[1][0].lower() + back[1][1:]}.")
    elif back:
        bits.append(back[0] + ".")
    if d.get("traits"):
        bits.append("Traits: " + ", ".join(str(t).lower() for t in d["traits"]) + ".")
    sk = d.get("skills") or r.get("top_skills") or d.get("top_skills")
    if sk:
        bits.append(f"Skills: {sk}.")
    if d.get("relations"):
        rel = "; ".join(d["relations"])
        bits.append(rel[0].upper() + rel[1:] + ".")
    mood = r.get("mood", d.get("mood"))
    if mood is not None:
        th = d.get("thoughts") or []
        bits.append(mood_words(mood).capitalize() + ("; bothered by " + ", ".join(t.lower() for t in th) if th else "") + ".")
    if d.get("health"):
        bits.append("Health: " + ", ".join(d["health"]) + ".")
    elif r.get("health") is not None and float(r["health"]) < 95:
        bits.append(f"Health {int(float(r['health']))}%.")
    if d.get("room"):
        bits.append(f"Sleeps in the {str(d['room']).lower()}.")
    w = clean_label(r.get("weapon") or d.get("weapon"))
    bits.append(f"Carries a {w[0].lower() + w[1:]}." if w else "Unarmed.")
    if r.get("mental_state"):
        bits.append(f"Mental state: {r['mental_state']}.")
    if r.get("downed"):
        bits.append("Downed.")
    if r.get("job"):
        bits.append(f"Now: {str(r['job']).rstrip('.')}.")
    arc = d.get("arc") or []
    if arc:
        bits.append("So far: " + "; ".join(f"{a['text']} (day {a.get('day', '?')})" for a in arc[-5:]) + ".")
    return head + " " + " ".join(bits)


def _short_line(d: dict[str, Any], r: dict[str, Any]) -> str:
    bits = [str(t).lower() for t in (d.get("traits") or [])[:2]]
    sk = d.get("skills") or r.get("top_skills") or ""
    if sk:
        bits.append(str(sk).split(",")[0])
    mood = r.get("mood", d.get("mood"))
    if mood is not None:
        bits.append(mood_words(mood))
    return f"- {d.get('name')}: " + ", ".join(bits) + "."


def mood_words(mood: Any) -> str:
    try:
        m = float(mood)
    except (TypeError, ValueError):
        return "mood unknown"
    if m < 15:
        return "on the edge of breaking"
    if m < 30:
        return "miserable"
    if m < 45:
        return "unhappy"
    if m < 60:
        return "getting by"
    if m < 75:
        return "content"
    return "in good spirits"
