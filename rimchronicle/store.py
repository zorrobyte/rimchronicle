"""On-disk chronicles: chronicles/<game-id>/chronicle.json, chronicle.md, images/*.jpg, frames/*.jpg, people.json, timeline.jsonl."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_id(text: str) -> str:
    return SAFE.sub("-", text).strip("-") or "unnamed"


def make_game_id(seed: str | None, start_tick: int | None, world_uid: str | int | None = None) -> str:
    """seed + start date + the world's own random id.

    The seed alone is not an identity: an agent that replays a fixed list of seeds starts many
    unrelated colonies under the same seed string, and two fresh games of the same scenario tend to
    share a start tick as well (it comes from world generation, not the wall clock). `world_uid` is
    whatever the bridge can offer that is generated per world and saved with it (see
    `Bridge.world_uid`); it keeps two such games apart while still being stable across reloads of the
    same save. It may be missing on older bridges, hence the legacy two-part id.
    """
    seed = seed or "unknown-seed"
    parts = [seed]
    if start_tick is not None:
        parts.append(str(start_tick))
    if world_uid not in (None, ""):
        parts.append(str(world_uid))
    return safe_id("-".join(parts))


@dataclass
class Chapter:
    k: int
    title: str
    summary: str
    body: str
    day: int
    hour: int
    t: float
    images: list[dict[str, Any]] = field(default_factory=list)
    events_used: list[int] = field(default_factory=list)
    trigger: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    voice: str = ""
    pull_quote: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)   # what re-narration needs: user prompt text, image files, state
    versions: list[dict[str, Any]] = field(default_factory=list)  # earlier renderings of this chapter

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k, "title": self.title, "summary": self.summary, "body": self.body,
            "day": self.day, "hour": self.hour, "t": self.t, "images": self.images,
            "events_used": self.events_used, "trigger": self.trigger, "state": self.state,
            "voice": self.voice, "pull_quote": self.pull_quote, "inputs": self.inputs, "versions": self.versions,
        }

    def public(self) -> dict[str, Any]:
        """For the API: everything but the bulky prompt inputs."""
        d = self.to_dict()
        d.pop("inputs", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chapter":
        return cls(
            k=int(d.get("k", 0)), title=d.get("title", ""), summary=d.get("summary", ""), body=d.get("body", ""),
            day=int(d.get("day", 0)), hour=int(d.get("hour", 0)), t=float(d.get("t", 0)),
            images=list(d.get("images") or []), events_used=list(d.get("events_used") or []),
            trigger=d.get("trigger", ""), state=dict(d.get("state") or {}),
            voice=str(d.get("voice") or ""), pull_quote=str(d.get("pull_quote") or ""),
            inputs=dict(d.get("inputs") or {}), versions=list(d.get("versions") or []),
        )


@dataclass
class Chronicle:
    id: str
    seed: str
    name: str = ""               # settlement name (e.g. "Aswell"); the book's title when known
    faction: str = ""            # the player's faction name (e.g. "Anditeria")
    scenario: str = ""
    storyteller: str = ""
    difficulty: str = ""
    started: str = ""            # real-world ISO timestamp when the chronicle began
    started_day: int = 0         # in-game day when the chronicle began
    start_date: str = ""         # in-game date string when the chronicle began
    status: str = "running"      # running | ended
    chapters: list[Chapter] = field(default_factory=list)
    epitaph: str = ""
    last_seq: int = 0            # ledger cursor, so a restart resumes where it left off
    last_day: int = 0
    last_state: dict[str, Any] = field(default_factory=dict)
    last_roster: list[str] = field(default_factory=list)   # the colonist names last seen; used to tell colonies apart
    updated: float = 0.0
    voice: str = ""              # per-chronicle voice override ("" = the configured default)
    diarist: str = ""            # for the diary voice: who holds the book
    directive: str = ""          # per-chronicle author directive
    saga: str = ""               # rolling "the story so far" paragraph
    saga_through: int = 0        # the saga covers chapters 1..saga_through

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "seed": self.seed, "name": self.name, "faction": self.faction,
            "scenario": self.scenario, "storyteller": self.storyteller,
            "difficulty": self.difficulty, "started": self.started, "started_day": self.started_day,
            "start_date": self.start_date, "status": self.status, "chapters": [c.to_dict() for c in self.chapters],
            "epitaph": self.epitaph, "last_seq": self.last_seq, "last_day": self.last_day,
            "last_state": self.last_state, "last_roster": self.last_roster, "updated": self.updated,
            "voice": self.voice, "diarist": self.diarist, "directive": self.directive,
            "saga": self.saga, "saga_through": self.saga_through,
        }

    def public(self) -> dict[str, Any]:
        d = self.to_dict()
        d["chapters"] = [c.public() for c in self.chapters]
        d["title"] = self.title
        d["days"] = self.days_survived
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chronicle":
        c = cls(id=d["id"], seed=d.get("seed", ""))
        for k in ("name", "faction", "scenario", "storyteller", "difficulty", "started", "start_date", "status", "epitaph", "voice", "diarist", "directive", "saga"):
            setattr(c, k, str(d.get(k) or getattr(c, k)))
        c.started_day = int(d.get("started_day", 0))
        c.last_seq = int(d.get("last_seq", 0))
        c.last_day = int(d.get("last_day", 0))
        c.saga_through = int(d.get("saga_through", 0))
        c.last_state = dict(d.get("last_state") or {})
        c.last_roster = [str(x) for x in (d.get("last_roster") or [])]
        c.updated = float(d.get("updated", 0))
        c.chapters = [Chapter.from_dict(x) for x in d.get("chapters", [])]
        return c

    @property
    def days_survived(self) -> int:
        if self.chapters:
            return max(max(c.day for c in self.chapters), self.last_day)
        return self.last_day

    @property
    def title(self) -> str:
        return self.name or self.seed

    def summary_card(self) -> dict[str, Any]:
        last = self.chapters[-1] if self.chapters else None
        cover = None
        for ch in reversed(self.chapters):
            if ch.images:
                cover = ch.images[0]["file"]
                break
        return {
            "id": self.id, "seed": self.seed, "name": self.name, "faction": self.faction, "title": self.title,
            "scenario": self.scenario, "storyteller": self.storyteller,
            "status": self.status, "days": self.days_survived, "chapters": len(self.chapters),
            "cover": cover, "updated": self.updated or (last.t if last else 0), "started": self.started,
            "last_title": last.title if last else "", "pull_quote": (last.pull_quote if last else ""), "epitaph": self.epitaph,
            "colonists": (self.last_state or {}).get("colonists"), "voice": self.voice,
        }


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def dir(self, game_id: str) -> Path:
        return self.root / safe_id(game_id)

    def image_dir(self, game_id: str) -> Path:
        return self.dir(game_id) / "images"

    def frames_dir(self, game_id: str) -> Path:
        return self.dir(game_id) / "frames"

    def people_path(self, game_id: str) -> Path:
        return self.dir(game_id) / "people.json"

    def timeline_path(self, game_id: str) -> Path:
        return self.dir(game_id) / "timeline.jsonl"

    def list(self) -> list[Chronicle]:
        out = []
        for p in sorted(self.root.iterdir()) if self.root.exists() else []:
            f = p / "chronicle.json"
            if f.exists():
                try:
                    out.append(self.load(p.name))
                except Exception:  # noqa: BLE001
                    continue
        out.sort(key=lambda c: c.updated, reverse=True)
        return out

    def exists(self, game_id: str) -> bool:
        return (self.dir(game_id) / "chronicle.json").exists()

    def load(self, game_id: str) -> Chronicle:
        with self._lock:
            with (self.dir(game_id) / "chronicle.json").open("r", encoding="utf-8") as fh:
                return Chronicle.from_dict(json.load(fh))

    def save(self, chron: Chronicle) -> None:
        with self._lock:
            d = self.dir(chron.id)
            d.mkdir(parents=True, exist_ok=True)
            chron.updated = time.time()
            tmp = d / "chronicle.json.tmp"
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(chron.to_dict(), fh, indent=2, ensure_ascii=False)
            tmp.replace(d / "chronicle.json")
            (d / "chronicle.md").write_text(render_markdown(chron), encoding="utf-8")

    def save_image(self, game_id: str, name: str, jpeg: bytes) -> str:
        with self._lock:
            idir = self.image_dir(game_id)
            idir.mkdir(parents=True, exist_ok=True)
            (idir / name).write_bytes(jpeg)
        return name

    def image_path(self, game_id: str, name: str) -> Path:
        return self._safe_child(self.image_dir(game_id), name)

    def save_frame(self, game_id: str, name: str, jpeg: bytes) -> str:
        with self._lock:
            fdir = self.frames_dir(game_id)
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / name).write_bytes(jpeg)
        return name

    def frame_path(self, game_id: str, name: str) -> Path:
        return self._safe_child(self.frames_dir(game_id), name)

    def read_json(self, path: Path, default: Any) -> Any:
        with self._lock:
            if not path.exists():
                return default
            try:
                with path.open("r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return default

    def write_json(self, path: Path, data: Any) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, ensure_ascii=False)
            tmp.replace(path)

    @staticmethod
    def _safe_child(base: Path, name: str) -> Path:
        p = (base / safe_id(name)).resolve()
        if base.resolve() not in p.parents:
            raise FileNotFoundError(name)
        return p


def render_markdown(chron: Chronicle) -> str:
    lines = [f"# {chron.title}", ""]
    meta = [x for x in (chron.faction, chron.scenario, chron.storyteller, chron.difficulty) if x]
    if chron.name and chron.seed:
        meta.append(f"seed {chron.seed}")
    if meta:
        lines.append("*" + ", ".join(meta) + "*")
        lines.append("")
    lines.append(f"Status: {chron.status}. {chron.days_survived} days chronicled in {len(chron.chapters)} chapters.")
    lines.append("")
    for ch in chron.chapters:
        lines.append(f"## {ch.k}. {ch.title}")
        lines.append("")
        lines.append(f"*Day {ch.day}, hour {ch.hour:02d}*" + (f" · *{ch.voice}*" if ch.voice else ""))
        lines.append("")
        for im in ch.images:
            sub = "frames" if im.get("frame") else "images"
            lines.append(f"![{im.get('caption', '')}]({sub}/{im['file']})")
            lines.append("")
        lines.append(ch.body.strip())
        lines.append("")
    if chron.epitaph:
        lines.append("## Epitaph")
        lines.append("")
        lines.append(chron.epitaph.strip())
        lines.append("")
    return "\n".join(lines)
