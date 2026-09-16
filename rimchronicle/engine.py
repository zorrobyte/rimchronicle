"""One process: the watcher loop, the camera, the people, the narrator, the optional overseer feed, and an event hub for the web UI."""
from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from typing import Any

from .bridge import Bridge, BridgeLike
from .camera import Camera
from .config import SETTINGS_KEYS, filter_settings, save_settings
from .llm import LLM, ChatLike
from .narrator import Narrator
from .overseer import Overseer
from .people import People
from .store import Chapter, Store
from .timeline import Timeline
from .voices import list_voices
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
        cfg.setdefault("camera", {})
        cfg.setdefault("timeline", {})
        self.store = store or Store(cfg["storage"]["path"])
        self.bridge = bridge or Bridge(cfg["bridge"]["url"], timeout_s=float(cfg["bridge"].get("timeout_s", 15)))
        self.llm = llm or LLM(cfg["llm"])
        self.hub = Hub()
        self.timeline = Timeline(self.store, state_every_seconds=float(cfg["timeline"].get("state_every_seconds", 60)))
        self.watcher = Watcher(self.bridge, self.store, cfg["bridge"], timeline=self.timeline)
        self.camera = Camera(self.bridge, self.store, self.timeline, cfg["camera"])
        self.camera.cfg.setdefault("base_width_cells", cfg["narrator"].get("base_width_cells", 50))
        self.people = People(self.bridge, self.store, cfg["narrator"])
        self.camera.on_frame = self._on_frame
        ocfg = cfg.get("overseer", {})
        self.overseer = overseer if overseer is not None else Overseer(ocfg.get("url", "http://127.0.0.1:8770"), enabled=bool(ocfg.get("enabled", False)), poll_seconds=float(ocfg.get("poll_seconds", 5)))
        self.narrator = Narrator(cfg["narrator"], self.store, self.llm, self.watcher, self.overseer, camera=self.camera, people=self.people, timeline=self.timeline)
        self.watcher.listeners.append(self.hub.publish)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._manual: queue.Queue = queue.Queue()

    def _on_frame(self, rec: dict[str, Any]) -> None:
        """A frame landed: tell the reader, and link portraits to their dossiers."""
        public = {k: v for k, v in rec.items() if k != "jpeg"}
        self.hub.publish("frame", {"id": self.watcher.game_id, **public})
        if rec.get("shot") == "portrait" and rec.get("who") and self.watcher.chronicle is not None:
            try:
                self.people.portrait(self.watcher.chronicle, str(rec["who"]), str(rec["file"]), rec.get("day"))
            except Exception:  # noqa: BLE001
                log.exception("could not link portrait")

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self.loop, name="rimchronicle-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.step()
            except Exception:  # noqa: BLE001
                log.exception("engine step failed")
            poll = float(self.cfg["bridge"].get("poll_seconds", 2))
            self._stop.wait(max(0.2, poll - (time.time() - t0)))

    def step(self) -> None:
        """One watcher tick, the camera, the people, and a cadence check. Safe to call from tests."""
        w = self.watcher
        evs = w.tick()
        chron = w.chronicle
        if chron is not None and w.online:
            if evs:
                try:
                    self.camera.on_events(chron, evs, w.summary)
                except Exception:  # noqa: BLE001
                    log.exception("camera failed on events")
                try:
                    self.people.note_events(chron, evs)
                except Exception:  # noqa: BLE001
                    log.exception("people failed on events")
            try:
                self.camera.tick(chron, w.summary, w.roster)
            except Exception:  # noqa: BLE001
                log.exception("camera tick failed")
        self.overseer.poll()
        job: Any = None
        try:
            job = self._manual.get_nowait()
        except queue.Empty:
            trigger = self.narrator.due()
            if trigger:
                job = {"trigger": trigger}
        if job:
            if "rewrite" in job:
                self._run_rewrite(*job["rewrite"])
            else:
                self._run_write(job["trigger"], job.get("voice"), job.get("focus"))

    def _run_write(self, trigger: str, voice: str | None = None, focus: str | None = None) -> Chapter | None:
        with self._write_lock:
            self.hub.publish("writing", {"trigger": trigger, "id": self.watcher.game_id, "voice": voice})
            ch = self.narrator.write(trigger, voice=voice, focus=focus)
            self.hub.publish("idle", {"error": self.narrator.last_error} if ch is None else {})
            return ch

    def _run_rewrite(self, game_id: str, k: int, voice: str) -> Chapter | None:
        with self._write_lock:
            chron = self.watcher.chronicle if self.watcher.chronicle is not None and self.watcher.chronicle.id == game_id else self.store.load(game_id)
            self.hub.publish("writing", {"trigger": f"rewrite chapter {k} as {voice}", "id": game_id, "voice": voice})
            self.narrator.busy = True
            try:
                ch = self.narrator.rewrite(chron, k, voice)
            except Exception as e:  # noqa: BLE001
                log.exception("rewrite failed")
                self.narrator.busy = False
                self.hub.publish("idle", {"error": f"{e.__class__.__name__}: {e}"})
                return None
            self.narrator.busy = False
            self.hub.publish("idle", {})
            return ch

    # ---------------------------------------------------------------- commands
    def request_chapter(self, voice: str | None = None, focus: str | None = None) -> None:
        """Ask the loop to write a chapter on its next step (used by the web button)."""
        self._manual.put({"trigger": "manual: requested from the reader", "voice": voice or None, "focus": focus or None})

    def request_rewrite(self, game_id: str, k: int, voice: str) -> None:
        self._manual.put({"rewrite": (game_id, int(k), voice)})

    def write_now(self, trigger: str = "manual: once", voice: str | None = None, focus: str | None = None) -> Chapter | None:
        """Synchronous: poll once so the watcher is warm, then write."""
        for _ in range(3):
            self.step_quiet()
            if self.watcher.chronicle is not None:
                break
            time.sleep(1.0)
        if self.watcher.chronicle is None:
            return None
        self.overseer.poll()
        return self._run_write(trigger, voice, focus)

    def step_quiet(self) -> None:
        """A tick without the cadence check (so `once` does not race an automatic chapter)."""
        w = self.watcher
        evs = w.tick()
        if w.chronicle is not None and w.online and evs:
            try:
                self.camera.on_events(w.chronicle, evs, w.summary)
                self.people.note_events(w.chronicle, evs)
            except Exception:  # noqa: BLE001
                log.exception("camera/people failed")

    # ---------------------------------------------------------------- settings
    def settings_view(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for section, keys in SETTINGS_KEYS.items():
            sub = self.cfg.get(section, {})
            out[section] = {k: sub.get(k) for k in keys}
        key = str(out["llm"].get("api_key") or "")
        out["llm"]["api_key"] = ("•" * 8 + key[-2:]) if key and key != "not-needed" else key
        out["voices"] = list_voices()
        out["root"] = str(self.cfg.get("root", ""))
        out["storage"] = str(self.cfg["storage"].get("path", ""))
        return out

    def apply_settings(self, patch: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        """Hot-apply allowed settings in place (the narrator, camera and watcher hold these dicts by reference) and persist them."""
        clean = filter_settings(patch)
        if "llm" in clean and str(clean["llm"].get("api_key", "")).startswith("••"):
            clean["llm"].pop("api_key")
        for section, sub in clean.items():
            self.cfg.setdefault(section, {}).update(sub)
        if "llm" in clean:
            try:
                self.llm = LLM(self.cfg["llm"])
                self.narrator.llm = self.llm
            except Exception:  # noqa: BLE001
                log.exception("could not rebuild the LLM client; keeping the old one")
        if "narrator" in clean and "base_width_cells" in clean["narrator"]:
            self.camera.cfg["base_width_cells"] = clean["narrator"]["base_width_cells"]
        if "overseer" in clean:
            self.overseer.enabled = bool(self.cfg["overseer"].get("enabled", False))
            self.overseer.url = str(self.cfg["overseer"].get("url", self.overseer.url)).rstrip("/")
            self.overseer.poll_seconds = float(self.cfg["overseer"].get("poll_seconds", 5))
        if persist and clean and self.cfg.get("root"):
            try:
                save_settings(self.cfg["root"], copy.deepcopy(clean))
            except Exception:  # noqa: BLE001
                log.exception("could not write config.local.yaml")
        return self.settings_view()

    def test_llm(self, llm_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = dict(self.cfg["llm"])
        for k, v in (llm_cfg or {}).items():
            if k in SETTINGS_KEYS["llm"] and v not in (None, "") and not str(v).startswith("••"):
                cfg[k] = v
        cfg["timeout_s"] = min(float(cfg.get("timeout_s", 240)), 60.0)
        t0 = time.time()
        try:
            client = LLM(cfg)
            reply = client.chat([{"role": "user", "content": [{"type": "text", "text": "Reply with the single word: ready"}]}], max_tokens=8)
            return {"ok": True, "model": cfg.get("model"), "elapsed": round(time.time() - t0, 2), "reply": (reply.content or "").strip()[:80]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "model": cfg.get("model"), "elapsed": round(time.time() - t0, 2), "error": f"{e.__class__.__name__}: {e}"}

    def status(self) -> dict[str, Any]:
        w = self.watcher
        chron = w.chronicle
        ncfg = self.cfg["narrator"]
        return {
            "online": w.online, "game": {k: w.status.get(k) for k in ("state", "seed", "day", "hour", "date", "colonists", "storyteller", "paused")},
            "chronicle": chron.id if chron else None, "colony": chron.title if chron else None, "pending_events": len(w.pending), "days_since_chapter": w.days_since_chapter,
            "writing": self.narrator.busy, "last_error": self.narrator.last_error, "overseer": self.overseer.reachable,
            "voice": (chron.voice if chron and chron.voice else ncfg.get("voice", "storyteller")), "default_voice": ncfg.get("voice", "storyteller"),
            "state": chron.last_state if chron else {}, "threads": w.threads,
            "camera": {"since_chapter": self.camera.count_since_chapter, "pending": len(self.camera.pending), "error": self.camera.last_error},
            "next_allowed_in": max(0.0, (self.narrator.last_write_t + float(ncfg.get("min_real_seconds_between", 120))) - time.time()) if self.narrator.last_write_t else 0.0,
        }
