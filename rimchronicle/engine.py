"""One process: the watcher loop, the narrator, the optional overseer feed, and an event hub for the web UI."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from .bridge import Bridge, BridgeLike
from .llm import LLM, ChatLike
from .narrator import Narrator
from .overseer import Overseer
from .store import Chapter, Store
from .watcher import Watcher

log = logging.getLogger("rimchronicle.engine")


class Hub:
    """Fan-out of engine events to SSE subscribers (thread-safe)."""

    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.seq = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.seq += 1
            ev = {"seq": self.seq, "t": time.time(), "kind": kind, "data": data}
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass


class Engine:
    def __init__(self, cfg: dict[str, Any], bridge: BridgeLike | None = None, llm: ChatLike | None = None, store: Store | None = None, overseer: Overseer | None = None):
        self.cfg = cfg
        self.store = store or Store(cfg["storage"]["path"])
        self.bridge = bridge or Bridge(cfg["bridge"]["url"], timeout_s=float(cfg["bridge"].get("timeout_s", 15)))
        self.llm = llm or LLM(cfg["llm"])
        self.hub = Hub()
        self.watcher = Watcher(self.bridge, self.store, cfg["bridge"])
        ocfg = cfg.get("overseer", {})
        self.overseer = overseer if overseer is not None else Overseer(ocfg.get("url", "http://127.0.0.1:8770"), enabled=bool(ocfg.get("enabled", True)), poll_seconds=float(ocfg.get("poll_seconds", 5)))
        self.narrator = Narrator(cfg["narrator"], self.store, self.llm, self.watcher, self.overseer)
        self.watcher.listeners.append(self.hub.publish)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._manual: queue.Queue = queue.Queue()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="rimchronicle-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def loop(self) -> None:
        poll = float(self.cfg["bridge"].get("poll_seconds", 2))
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("engine step failed")
            self._stop.wait(max(0.2, poll - (time.time() - t0)))

    def step(self) -> None:
        """One watcher tick plus a cadence check. Safe to call from tests."""
        self.watcher.tick()
        self.overseer.poll()
        trigger = None
        try:
            trigger = self._manual.get_nowait()
        except queue.Empty:
            trigger = self.narrator.due()
        if trigger:
            self._run_write(trigger)

    def _run_write(self, trigger: str) -> Chapter | None:
        with self._write_lock:
            self.hub.publish("writing", {"trigger": trigger, "id": self.watcher.game_id})
            ch = self.narrator.write(trigger)
            if ch is None:
                self.hub.publish("idle", {"error": self.narrator.last_error})
            else:
                self.hub.publish("idle", {})
            return ch

    # ---------------------------------------------------------------- commands
    def request_chapter(self) -> None:
        """Ask the loop to write a chapter on its next step (used by the web button)."""
        self._manual.put("manual: requested from the reader")

    def write_now(self, trigger: str = "manual: once") -> Chapter | None:
        """Synchronous: poll once so the watcher is warm, then write."""
        for _ in range(3):
            self.watcher.tick()
            if self.watcher.chronicle is not None:
                break
            time.sleep(1.0)
        if self.watcher.chronicle is None:
            return None
        self.overseer.poll()
        return self._run_write(trigger)

    def status(self) -> dict[str, Any]:
        w = self.watcher
        chron = w.chronicle
        return {
            "online": w.online, "game": {k: w.status.get(k) for k in ("state", "seed", "day", "hour", "date", "colonists", "storyteller", "paused")},
            "chronicle": chron.id if chron else None, "pending_events": len(w.pending), "days_since_chapter": w.days_since_chapter,
            "writing": self.narrator.busy, "last_error": self.narrator.last_error, "overseer": self.overseer.reachable,
            "state": chron.last_state if chron else {}, "next_allowed_in": max(0.0, (self.narrator.last_write_t + float(self.cfg["narrator"].get("min_real_seconds_between", 120))) - time.time()) if self.narrator.last_write_t else 0.0,
        }
