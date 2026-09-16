"""Fakes: an in-memory RimBridge and a canned narrator model."""
from __future__ import annotations

import json
from typing import Any

import pytest

from rimchronicle.bridge import BridgeError
from rimchronicle.config import DEFAULTS, deep_merge
from rimchronicle.images import tiny_png
from rimchronicle.llm import Reply
from rimchronicle.store import Store


class FakeBridge:
    def __init__(self, seed: str = "test-seed", start_tick: int = 1000):
        self.seed = seed
        self.start_tick = start_tick
        self.online = True
        self.state = "playing"
        self.tick = 60000
        self.day = 3
        self.hour = 8
        self.colonists = 3
        self.ledger: list[dict[str, Any]] = []
        self.png = tiny_png()
        self.screenshots: list[tuple[int, int, int]] = []
        self.rpc_calls: list[str] = []

    # helpers for the tests
    def add(self, kind: str, text: str, cell=None, data=None, day=None, hour=None) -> dict[str, Any]:
        ev = {"seq": len(self.ledger) + 1, "kind": kind, "text": text, "tick": self.tick, "day": day if day is not None else self.day, "hour": hour if hour is not None else self.hour}
        if cell:
            ev["cell"] = list(cell)
        if data:
            ev["data"] = data
        self.ledger.append(ev)
        return ev

    def pass_day(self) -> None:
        self.day += 1
        self.tick += 60000
        self.add("day", f"day {self.day}", data={"colonists": self.colonists, "day": self.day})

    def new_game(self, seed: str, start_tick: int) -> None:
        self.seed, self.start_tick = seed, start_tick
        self.tick, self.day, self.hour = 100, 0, 6
        self.ledger = []
        self.add("game", "new game started")

    def load_game(self, seed: str, start_tick: int, tick: int = 500000, day: int = 9) -> None:
        """A save was loaded: the ledger restarts, as the real bridge does."""
        self.seed, self.start_tick, self.tick, self.day = seed, start_tick, tick, day
        self.ledger = []
        self.add("game", "loaded save x")
        self.add("game", "game loaded/started", data={"day": day})

    def _check(self) -> None:
        if not self.online:
            raise BridgeError("bridge unreachable: ConnectError")

    # bridge interface
    def health(self) -> dict[str, Any]:
        self._check()
        return {"ok": True}

    def status(self) -> dict[str, Any]:
        self._check()
        return {"state": self.state, "tick": self.tick, "day": self.day, "hour": self.hour, "seed": self.seed, "date": f"day {self.day} of Aprimay", "storyteller": "Cassandra", "difficulty": "Rough", "colonists": self.colonists}

    def events(self, since: int) -> dict[str, Any]:
        self._check()
        evs = [e for e in self.ledger if e["seq"] > since]
        head = self.ledger[-1]["seq"] if self.ledger else 0
        return {"events": evs, "last_seq": evs[-1]["seq"] if evs else since, "head_seq": head, "assisted": False}

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._check()
        self.rpc_calls.append(method)
        if method == "state.summary":
            return {"colonists": self.colonists, "downed": 0, "prisoners": 0, "animals": 1, "wealth": 12000, "mood_avg": 55, "food_days": 4.5, "nutrition": 21.6, "threat_points": 80, "danger": "None", "day": self.day, "hour": self.hour, "date": f"day {self.day} of Aprimay", "season": "Spring", "weather": "Clear", "temp_outdoor": 21, "biome": "TemperateForest", "home_center": [120, 115], "research_current": "Smithing", "research_progress": 40}
        if method == "state.pawns":
            return [{"name": n, "top_skills": "Shooting 8, Mining 6", "weapon": "Bolt-action rifle", "mood": 60, "health": 100, "job": "hauling"} for n in ("Kena", "Lumi", "Kat")[: self.colonists]]
        if method == "anchor.list":
            return [{"name": "shelter", "rect": {"min": [113, 117], "max": [122, 124]}}]
        if method == "engine.get":
            path = (params or {}).get("path", "")
            if "gameStartAbsTick" in path:
                return self.start_tick
            if "Scenario" in path:
                return "Crashlanded"
            if "TicksAbs" in path:
                return self.start_tick + self.tick
        if method == "game.status":
            return self.status()
        raise BridgeError(f"unknown method {method}")

    def screenshot(self, x: int, z: int, w: int) -> bytes:
        self._check()
        self.screenshots.append((x, z, w))
        return self.png

    def scenario_name(self) -> str | None:
        return "Crashlanded"

    def game_start_tick(self) -> int | None:
        return self.start_tick


class FakeLLM:
    def __init__(self):
        self.calls: list[list[dict[str, Any]]] = []
        self.n = 0
        self.reply_override: str | None = None
        self.fail = False

    def chat(self, messages, max_tokens=None) -> Reply:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("endpoint down")
        self.n += 1
        if self.reply_override is not None:
            return Reply(content=self.reply_override)
        text = messages[1]["content"][0]["text"]
        closing = "closing chapter" in text
        obj = {
            "title": f"Chapter {self.n} of the test colony",
            "summary": f"Summary of chapter {self.n}.",
            "body": "Kena hauled steel while Lumi watched the tree line.\n\nNothing burned, which counted as a good day.\n\nState of the colony: day 3, 3 colonists, food 4.5 days, mood 55, threat none.",
        }
        if closing:
            obj["epitaph"] = "They built a shelter and it was not enough."
        return Reply(content=json.dumps(obj))


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def cfg(tmp_path):
    c = deep_merge(DEFAULTS, {"storage": {"dir": str(tmp_path / "chronicles")}, "overseer": {"enabled": False}, "narrator": {"min_real_seconds_between": 120, "min_events": 3}})
    c["storage"]["path"] = tmp_path / "chronicles"
    c["root"] = tmp_path
    return c


@pytest.fixture
def store(cfg):
    return Store(cfg["storage"]["path"])


@pytest.fixture
def bridge():
    return FakeBridge()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def clock():
    return Clock()
