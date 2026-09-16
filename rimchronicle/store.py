"""On-disk chronicles: chronicles/<game-id>/chronicle.json, chronicle.md and images/*.jpg."""
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


def make_game_id(seed: str | None, start_tick: int | None) -> str:
    """seed + start date. A new game with the same seed has a different start tick."""
    seed = seed or "unknown-seed"
    if start_tick is None:
        return safe_id(seed)
    return safe_id(f"{seed}-{start_tick}")


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k, "title": self.title, "summary": self.summary, "body": self.body,
            "day": self.day, "hour": self.hour, "t": self.t, "images": self.images,
            "events_used": self.events_used, "trigger": self.trigger, "state": self.state,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chapter":
        return cls(
            k=int(d.get("k", 0)), title=d.get("title", ""), summary=d.get("summary", ""), body=d.get("body", ""),
            day=int(d.get("day", 0)), hour=int(d.get("hour", 0)), t=float(d.get("t", 0)),
            images=list(d.get("images") or []), events_used=list(d.get("events_used") or []),
            trigger=d.get("trigger", ""), state=dict(d.get("state") or {}),
        )


@dataclass
class Chronicle:
    id: str
    seed: str
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
    updated: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "seed": self.seed, "scenario": self.scenario, "storyteller": self.storyteller,
            "difficulty": self.difficulty, "started": self.started, "started_day": self.started_day,
            "start_date": self.start_date, "status": self.status, "chapters": [c.to_dict() for c in self.chapters],
            "epitaph": self.epitaph, "last_seq": self.last_seq, "last_day": self.last_day,
            "last_state": self.last_state, "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chronicle":
        c = cls(id=d["id"], seed=d.get("seed", ""))
        for k in ("scenario", "storyteller", "difficulty", "started", "start_date", "status", "epitaph"):
            setattr(c, k, d.get(k, getattr(c, k)))
        c.started_day = int(d.get("started_day", 0))
        c.last_seq = int(d.get("last_seq", 0))
        c.last_day = int(d.get("last_day", 0))
        c.last_state = dict(d.get("last_state") or {})
        c.updated = float(d.get("updated", 0))
        c.chapters = [Chapter.from_dict(x) for x in d.get("chapters", [])]
        return c

    @property
    def days_survived(self) -> int:
        if self.chapters:
            return max(c.day for c in self.chapters)
        return self.last_day

    @property
    def title(self) -> str:
        return f"{self.seed}: {self.days_survived} days"

    def summary_card(self) -> dict[str, Any]:
        last = self.chapters[-1] if self.chapters else None
        cover = None
        for ch in reversed(self.chapters):
            if ch.images:
                cover = ch.images[0]["file"]
                break
        return {
            "id": self.id, "seed": self.seed, "scenario": self.scenario, "storyteller": self.storyteller,
            "status": self.status, "days": self.days_survived, "chapters": len(self.chapters),
            "cover": cover, "updated": self.updated or (last.t if last else 0), "started": self.started,
            "last_title": last.title if last else "", "epitaph": self.epitaph,
            "colonists": (self.last_state or {}).get("colonists"),
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
        p = (self.image_dir(game_id) / safe_id(name)).resolve()
        if self.image_dir(game_id).resolve() not in p.parents:
            raise FileNotFoundError(name)
        return p


def render_markdown(chron: Chronicle) -> str:
    lines = [f"# {chron.seed}", ""]
    meta = [x for x in (chron.scenario, chron.storyteller, chron.difficulty) if x]
    if meta:
        lines.append("*" + ", ".join(meta) + "*")
        lines.append("")
    lines.append(f"Status: {chron.status}. {chron.days_survived} days chronicled in {len(chron.chapters)} chapters.")
    lines.append("")
    for ch in chron.chapters:
        lines.append(f"## {ch.k}. {ch.title}")
        lines.append("")
        lines.append(f"*Day {ch.day}, hour {ch.hour:02d}*")
        lines.append("")
        for im in ch.images:
            lines.append(f"![{im.get('caption', '')}](images/{im['file']})")
            lines.append("")
        lines.append(ch.body.strip())
        lines.append("")
    if chron.epitaph:
        lines.append("## Epitaph")
        lines.append("")
        lines.append(chron.epitaph.strip())
        lines.append("")
    return "\n".join(lines)
