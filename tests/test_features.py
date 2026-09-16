"""The timeline, the camera, the people, the voices, the wired narrator (diary, rewrite, saga) and engine settings."""
from __future__ import annotations

import json
import queue
import re

import pytest
import yaml

from rimchronicle.camera import Camera
from rimchronicle.config import filter_settings
from rimchronicle.engine import Engine
from rimchronicle.narrator import Narrator
from rimchronicle.overseer import Overseer
from rimchronicle.people import People
from rimchronicle.store import Chapter, Chronicle
from rimchronicle.timeline import Timeline
from rimchronicle.voices import BANNED_WORDS, VOICES, build_system_prompt, get_voice, list_voices
from rimchronicle.watcher import Watcher, compact_roster, is_dramatic, is_minor, is_moment, is_notable

SL = "State of the colony: day 3, 3 colonists, food 4.5 days, mood 55, threat none (80 points)."


# ---------------------------------------------------------------- helpers
def make(cfg, store, bridge, llm, clock, timeline=None):
    w = Watcher(bridge, store, cfg["bridge"], clock=clock, timeline=timeline)
    n = Narrator(cfg["narrator"], store, llm, w, overseer=None, clock=clock, timeline=timeline)
    return w, n


def make_engine(cfg, bridge, llm, clock):
    eng = Engine(cfg, bridge=bridge, llm=llm, overseer=Overseer("http://127.0.0.1:1", enabled=False))
    eng.watcher.clock = clock
    eng.narrator.clock = clock
    eng.camera.clock = clock
    return eng


def make_camera(cfg, store, bridge, clock):
    cfg["camera"]["base_width_cells"] = 50
    tl = Timeline(store, state_every_seconds=60)
    return Camera(bridge, store, tl, cfg["camera"], clock=clock), tl, Chronicle(id="cam-game", seed="s", name="Aswell")


def user_text(call):
    return call[1]["content"][0]["text"]


def system_text(call):
    return call[0]["content"]


def image_count(call):
    return sum(1 for p in call[1]["content"] if p["type"] == "image_url")


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# ---------------------------------------------------------------- 1. timeline
def test_timeline_append_read_paging_and_filters(store):
    tl = Timeline(store, state_every_seconds=60)
    assert tl.read("g") == ([], 0) and tl.count("g") == 0 and tl.overview("g") == {"records": 0, "kinds": {}, "first_day": None, "last_day": None}
    tl.append("g", "event", {"seq": 1, "text": "a", "kind": "message"}, day=3, hour=8, t=10.0)
    tl.append("g", "frame", {"file": "f1.jpg", "shot": "daily"}, day=3, hour=9, t=11.0)
    tl.append("g", "event", {"seq": 2, "text": "b"}, day=4, hour=1, t=12.0)
    tl.append("g", "chapter", {"k": 1, "title": "One"}, day=4, hour=2, t=13.0)
    items, nxt = tl.read("g")
    assert [x["kind"] for x in items] == ["event", "frame", "event", "chapter"]
    assert [x["i"] for x in items] == [1, 2, 3, 4] and nxt == 4
    assert items[0]["text"] == "a" and items[0]["day"] == 3 and items[0]["hour"] == 8 and items[0]["t"] == 10.0 and items[0]["seq"] == 1
    assert store.timeline_path("g").exists() and tl.count("g") == 4
    # paging: `since` is the number of lines already seen
    assert tl.read("g", since=nxt) == ([], 4)
    tl.append("g", "event", {"seq": 3, "text": "c"}, day=4, hour=3, t=14.0)
    items, nxt = tl.read("g", since=4)
    assert [x["text"] for x in items] == ["c"] and [x["i"] for x in items] == [5] and nxt == 5
    items, nxt = tl.read("g", since=2)
    assert [x["i"] for x in items] == [3, 4, 5] and nxt == 5
    # kinds filter and limit
    items, _ = tl.read("g", kinds={"event"})
    assert [x["text"] for x in items] == ["a", "b", "c"]
    items, nxt = tl.read("g", kinds={"event", "chapter"}, limit=2)
    assert [x["i"] for x in items] == [1, 3] and nxt == 3
    assert tl.read("g", kinds={"nothing"}) == ([], 5)
    # frames and the overview
    assert [f["file"] for f in tl.frames("g")] == ["f1.jpg"]
    assert tl.frames("g", since_t=11.0)[0]["file"] == "f1.jpg" and tl.frames("g", since_t=11.5) == []
    assert tl.overview("g") == {"records": 5, "kinds": {"event": 3, "frame": 1, "chapter": 1}, "first_day": 3, "last_day": 4}
    # a corrupt line is skipped but still counted as seen
    with store.timeline_path("g").open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    items, nxt = tl.read("g")
    assert len(items) == 5 and nxt == 6 and tl.count("g") == 6
    # games are kept apart
    tl.append("h", "event", {"text": "elsewhere"}, day=1, hour=0, t=1.0)
    assert tl.count("h") == 1 and tl.count("g") == 6


def test_timeline_state_sampling_is_throttled_and_series_downsamples(store):
    tl = Timeline(store, state_every_seconds=60)
    state = {"day": 3, "hour": "8", "colonists": 3, "mood_avg": 55, "food_days": 4.5, "wealth": 12000, "threat_points": 80, "downed": 0, "prisoners": 0,
             "animals": 1, "danger": "None", "weather": "Clear", "season": "Spring", "temp_outdoor": 21, "research_current": "Smithing", "home_center": [1, 2]}
    assert tl.sample_state("g", state, t=100.0)
    assert not tl.sample_state("g", state, t=130.0)            # inside the window
    assert tl.sample_state("g", state, t=130.0, force=True)    # unless forced
    assert not tl.sample_state("g", state, t=189.9)            # the forced sample restarted the window
    assert tl.sample_state("g", state, t=190.0)
    assert tl.sample_state("other", state, t=100.0)            # per game
    recs, _ = tl.read("g", kinds={"state"})
    assert len(recs) == 3 and [r["t"] for r in recs] == [100.0, 130.0, 190.0]
    rec = recs[0]
    assert rec["day"] == 3 and rec["hour"] == 8 and rec["colonists"] == 3 and rec["mood_avg"] == 55 and rec["food_days"] == 4.5
    assert rec["danger"] == "None" and rec["weather"] == "Clear" and rec["season"] == "Spring" and rec["temp_outdoor"] == 21
    assert "research_current" not in rec and "home_center" not in rec
    # series: everything when small, evenly spread and ending on the last sample when large
    fast = Timeline(store, state_every_seconds=0)
    for i in range(10):
        assert fast.sample_state("s", {"day": i, "colonists": i}, t=float(i))
    assert [x["day"] for x in fast.series("s")] == list(range(10))
    small = fast.series("s", max_points=4)
    assert [x["day"] for x in small] == [0, 3, 6, 9]
    assert fast.series("none") == []


def test_timeline_event_record_keeps_the_ledger_kind(cfg, store, bridge, clock):
    tl = Timeline(store, state_every_seconds=60)
    w = Watcher(bridge, store, cfg["bridge"], clock=clock, timeline=tl)
    bridge.add("colonist_died", "Kena is no more", cell=[100, 100], data={"cause": "Gunshot"})
    w.tick()
    rec = tl.read(w.game_id, kinds={"event"})[0][0]
    assert "colonist_died" in [v for v in rec.values() if isinstance(v, str)]


# ---------------------------------------------------------------- 2. watcher
def test_event_classification_for_the_social_layer():
    lover = {"kind": "tale", "text": "Kena and Kat became lovers", "cell": [118, 118], "data": {"def": "BecameLover", "pawns": ["Kena", "Kat"]}}
    assert is_notable(lover) and is_dramatic(lover) and is_moment(lover) and not is_minor(lover)
    wounded = {"kind": "tale", "text": "Lumi was wounded", "cell": [118, 118], "data": {"def": "Wounded", "pawns": ["Lumi"]}}
    assert is_notable(wounded) and is_minor(wounded) and not is_dramatic(wounded) and not is_moment(wounded)
    insult = {"kind": "social", "text": "Lumi insulted Kena", "data": {"def": "Insult", "initiator": "Lumi", "recipient": "Kena"}}
    assert is_notable(insult) and not is_dramatic(insult) and not is_minor(insult)
    chit = {"kind": "social", "text": "Kena chatted with Lumi", "data": {"def": "Chitchat", "initiator": "Kena", "recipient": "Lumi", "minor": True}}
    assert is_notable(chit) and is_minor(chit) and not is_dramatic(chit)
    lovers = {"kind": "relation", "text": "Kena and Kat are now lovers", "data": {"def": "Lover", "added": True, "a": "Kena", "b": "Kat"}}
    assert is_notable(lovers) and is_dramatic(lovers) and not is_minor(lovers)
    assert not is_dramatic({"kind": "relation", "text": "x", "data": {"def": "Lover", "added": False, "a": "Kena", "b": "Kat"}})
    assert not is_dramatic({"kind": "relation", "text": "x", "data": {"def": "Friend", "added": True, "a": "Kena", "b": "Kat"}})
    leg = {"kind": "health", "text": "Kat lost her left leg", "cell": [118, 118], "data": {"def": "MissingBodyPart", "part": "left leg"}}
    assert is_notable(leg) and is_dramatic(leg) and is_moment(leg)
    bleed = {"kind": "health", "text": "Kat: blood loss", "cell": [118, 118], "data": {"def": "BloodLoss", "serious": True}}
    assert is_notable(bleed) and not is_dramatic(bleed) and not is_moment(bleed)
    assert is_dramatic({"kind": "health", "text": "Lumi caught the plague", "data": {"def": "Plague"}})
    pet = {"kind": "pawn_died", "text": "Rex died", "cell": [110, 110], "data": {"faction": "Player", "animal": "Labrador"}}
    assert is_notable(pet) and not is_dramatic(pet) and is_moment(pet)
    bonded = {"kind": "pawn_died", "text": "Rex died", "data": {"animal": "Labrador", "bonded_to": "Kena"}}
    assert is_notable(bonded) and is_dramatic(bonded)
    assert not is_notable({"kind": "pawn_died", "text": "a deer died", "cell": [10, 10], "data": {"faction": None, "animal": "Deer"}})
    assert not is_notable({"kind": "pawn_died", "text": "a pirate died", "data": {"faction": "Pirates"}})
    trade = {"kind": "trade", "text": "traded with Bill", "data": {"trader": "Bill", "sold": ["10 rice"]}}
    assert is_notable(trade) and not is_dramatic(trade) and not is_minor(trade)
    assert not is_moment({"kind": "colonist_died", "text": "no cell, no picture"})


def test_watcher_records_everything_names_the_colony_and_counts_the_minor(cfg, store, bridge, clock):
    tl = Timeline(store, state_every_seconds=60)
    w = Watcher(bridge, store, cfg["bridge"], clock=clock, timeline=tl)
    bridge.hostiles = [{"kind": "Pirate", "pos": [40, 40]}, {"kind": "Pirate", "pos": [44, 44]}, {"kind": "Manhunter", "pos": [10, 10]}]
    bridge.add("message", "a wall was built")                                      # not notable
    bridge.add("pawn_died", "a deer died", cell=[10, 10], data={"animal": "Deer"})  # wildlife: not notable
    bridge.add("social", "Kena chatted with Lumi", data={"def": "Chitchat", "initiator": "Kena", "recipient": "Lumi", "minor": True})
    bridge.add("social", "Kena chatted with Kat", data={"def": "Chitchat", "initiator": "Kena", "recipient": "Kat", "minor": True})
    bridge.add("social", "Lumi insulted Kena", data={"def": "Insult", "initiator": "Lumi", "recipient": "Kena"})
    bridge.add("trade", "traded with Bill", data={"trader": "Bill", "faction": "Outlanders", "sold": ["10 rice"], "bought": ["a rifle"]})
    found = w.tick()
    chron = w.chronicle
    assert chron.name == "Aswell" and chron.faction == "Anditeria" and chron.title == "Aswell"
    assert store.load(w.game_id).name == "Aswell" and store.load(w.game_id).faction == "Anditeria"
    assert [e["kind"] for e in found] == ["social", "trade"] and [e["kind"] for e in w.pending] == ["social", "trade"]
    assert w.minor_since_chapter == {"Chitchat": 2}
    # every raw event is on the timeline, notable or not
    events, _ = tl.read(w.game_id, kinds={"event"})
    assert [e["text"] for e in events] == ["a wall was built", "a deer died", "Kena chatted with Lumi", "Kena chatted with Kat", "Lumi insulted Kena", "traded with Bill"]
    assert events[0]["seq"] == 1 and events[0]["day"] == 3 and events[0]["hour"] == 8 and events[0]["t"] == clock.t
    assert events[1]["cell"] == [10, 10] and events[4]["data"] == {"def": "Insult", "initiator": "Lumi", "recipient": "Kena"} and events[5]["data"]["sold"] == ["10 rice"]
    assert tl.overview(w.game_id)["kinds"] == {"chronicle": 1, "event": 6, "state": 1}
    assert tl.read(w.game_id, kinds={"chronicle"})[0][0]["name"] == "Aswell"
    # state samples go through the watcher's clock and are throttled
    clock.advance(30)
    w.refresh_state()
    assert tl.overview(w.game_id)["kinds"]["state"] == 1
    clock.advance(31)
    w.refresh_state()
    assert tl.overview(w.game_id)["kinds"]["state"] == 2
    # the minor tally is handed over once
    assert w.take_minor() == {"Chitchat": 2} and w.minor_since_chapter == {} and w.take_minor() == {}
    # open threads come from the summary's alerts and hostiles
    assert w.threads == ["alert: Major break risk", "hostiles on the map: 2 Pirate, 1 Manhunter"]
    bridge.alerts, bridge.hostiles = [], []
    clock.advance(60)
    w.refresh_state()
    assert w.threads == []


# ---------------------------------------------------------------- 3. camera
def test_camera_moments_debounce_and_cap(cfg, store, bridge, clock):
    camera, tl, chron = make_camera(cfg, store, bridge, clock)
    summary = bridge.rpc("state.summary")
    died = {"seq": 1, "kind": "colonist_died", "text": "Kena died", "cell": [100, 100], "day": 3, "hour": 8, "data": {"cause": "Gunshot"}}
    camera.on_events(chron, [died], summary)
    files = sorted(p.name for p in store.frames_dir(chron.id).iterdir())
    assert files == ["d003h08-1-0001-colonist_died.jpg"]
    assert (store.frames_dir(chron.id) / files[0]).read_bytes()[:3] == b"\xff\xd8\xff"
    assert [s for s in bridge.screenshots if s[2] != 8] == [(100, 100, 24)]
    frames = tl.frames(chron.id)
    assert len(frames) == 1 and frames[0]["kind"] == "frame" and frames[0]["dramatic"] is True and frames[0]["shot"] == "colonist_died"
    assert frames[0]["file"] == files[0] and frames[0]["event_seq"] == 1 and frames[0]["cell"] == [100, 100] and frames[0]["w"] == 24
    assert frames[0]["label"] == "Kena died, day 3 h08" and frames[0]["day"] == 3 and frames[0]["hour"] == 8 and "jpeg" not in frames[0]
    assert camera.count_since_chapter == 1 and len(camera.taken) == 1
    # the same kind again inside the debounce window is skipped; another kind is not
    camera.on_events(chron, [{**died, "seq": 2, "text": "Lumi died"}], summary)
    assert len(list(store.frames_dir(chron.id).iterdir())) == 1
    camera.on_events(chron, [{"seq": 3, "kind": "mental_break", "text": "Kat went berserk", "cell": [101, 101], "day": 3, "hour": 9}], summary)
    assert len(list(store.frames_dir(chron.id).iterdir())) == 2 and tl.frames(chron.id)[-1]["dramatic"] is False
    clock.advance(11)
    camera.on_events(chron, [{**died, "seq": 4, "text": "Lumi died"}], summary)
    assert len(list(store.frames_dir(chron.id).iterdir())) == 3 and camera.count_since_chapter == 3
    # not moments: no cell, a letter, a quiet tale, a bleed
    camera.on_events(chron, [
        {"seq": 5, "kind": "colonist_died", "text": "no cell", "day": 3, "hour": 9},
        {"seq": 6, "kind": "letter", "text": "news", "cell": [1, 1], "day": 3, "hour": 9},
        {"seq": 7, "kind": "tale", "text": "Lumi hunted", "cell": [1, 1], "day": 3, "hour": 9, "data": {"def": "Hunted"}},
        {"seq": 8, "kind": "health", "text": "Kat: blood loss", "cell": [1, 1], "day": 3, "hour": 9, "data": {"def": "BloodLoss"}},
    ], summary)
    assert camera.count_since_chapter == 3
    # a failed shot is not counted and leaves an error behind
    bridge.online = False
    camera.on_events(chron, [{"seq": 9, "kind": "colonist_downed", "text": "Kat downed", "cell": [90, 90], "day": 3, "hour": 10}], summary)
    assert camera.count_since_chapter == 3 and "unreachable" in camera.last_error
    bridge.online = True
    # the per-chapter cap, and the reset that lifts it
    camera.cfg["max_per_chapter"] = 4
    clock.advance(11)
    camera.on_events(chron, [
        {"seq": 10, "kind": "colonist_downed", "text": "Kat downed", "cell": [90, 90], "day": 3, "hour": 10},
        {"seq": 11, "kind": "manhunter", "text": "mad squirrels", "cell": [80, 80], "day": 3, "hour": 10},
    ], summary)
    assert camera.count_since_chapter == 4 and len(list(store.frames_dir(chron.id).iterdir())) == 4
    camera.reset_chapter()
    assert camera.count_since_chapter == 0
    camera.on_events(chron, [{"seq": 12, "kind": "manhunter", "text": "mad squirrels", "cell": [80, 80], "day": 3, "hour": 10}], summary)
    assert camera.count_since_chapter == 1 and bridge.screenshots[-1] == (80, 80, 24)
    # moments can be switched off
    camera.cfg["moments"] = False
    clock.advance(11)
    camera.on_events(chron, [{**died, "seq": 13}], summary)
    assert camera.count_since_chapter == 1
    assert camera.on_events(None, [died], summary) is None and camera.on_events(chron, [], summary) is None


def test_camera_fight_follow_ups_use_the_hostiles_centre_or_fall_back(cfg, store, bridge, clock):
    camera, tl, chron = make_camera(cfg, store, bridge, clock)
    raid = {"seq": 1, "kind": "hostile_group", "text": "Pirates arrive", "cell": [40, 40], "day": 3, "hour": 9, "data": {"faction": "Pirates", "count": 4}}
    camera.on_events(chron, [raid], bridge.rpc("state.summary"))
    assert [s for s in bridge.screenshots if s[2] != 8] == [(40, 40, 24)]
    assert [round(p["due"] - clock.t) for p in camera.pending] == [20, 60] and all(p["kind"] == "fight" for p in camera.pending)
    camera.tick(chron, bridge.rpc("state.summary"))
    assert len(bridge.screenshots) == 1  # nothing due yet
    bridge.hostiles = [{"kind": "Pirate", "pos": [40, 40]}, {"kind": "Pirate", "pos": [50, 60]}]
    clock.advance(20)
    camera.tick(chron, bridge.rpc("state.summary"))
    assert bridge.screenshots[-1] == (45, 50, 32) and len(camera.pending) == 1
    rec = tl.frames(chron.id)[-1]
    assert rec["shot"] == "fight" and rec["dramatic"] is True and rec["event_seq"] == 1 and rec["label"] == "the fight, day 3" and rec["day"] == 3
    bridge.hostiles = []
    clock.advance(40)
    camera.tick(chron, bridge.rpc("state.summary"))
    assert bridge.screenshots[-1] == (40, 40, 32) and camera.pending == [] and camera.count_since_chapter == 3 and len(camera.taken) == 3
    # at the cap a raid gets neither its moment nor its follow-ups
    camera.cfg["max_per_chapter"] = 3
    clock.advance(11)
    camera.on_events(chron, [{**raid, "seq": 2}], bridge.rpc("state.summary"))
    assert len(bridge.screenshots) == 3 and camera.pending == []
    # a due follow-up is dropped, not deferred, when the cap is reached meanwhile
    camera.reset_chapter()
    clock.advance(11)
    camera.on_events(chron, [{**raid, "seq": 3}], bridge.rpc("state.summary"))
    assert len(bridge.screenshots) == 4 and len(camera.pending) == 2
    camera.count_since_chapter = 3
    clock.advance(61)
    camera.tick(chron, bridge.rpc("state.summary"))
    assert len(bridge.screenshots) == 4 and camera.pending == []
    # follow-ups can be switched off
    camera.reset_chapter()
    camera.cfg["follow_fight"] = False
    clock.advance(11)
    camera.on_events(chron, [{**raid, "seq": 4}], bridge.rpc("state.summary"))
    assert len(bridge.screenshots) == 5 and camera.pending == []


def test_camera_daily_shot_and_portraits(cfg, store, bridge, clock):
    camera, tl, chron = make_camera(cfg, store, bridge, clock)
    summary = bridge.rpc("state.summary")
    day = {"seq": 1, "kind": "day", "text": "day 4", "day": 4, "hour": 0, "data": {"day": 4, "colonists": 3}}
    camera.on_events(chron, [day], summary)
    camera.on_events(chron, [day], summary)
    assert [s for s in bridge.screenshots if s[2] != 8] == [(120, 115, 50)]
    rec = tl.frames(chron.id)[0]
    assert rec["shot"] == "daily" and rec["label"] == "Aswell on day 4" and rec["day"] == 4 and rec["hour"] == 0 and rec["dramatic"] is False
    assert camera.taken == [] and camera.count_since_chapter == 0   # the time-lapse never counts against the chapter cap
    camera.on_events(chron, [{**day, "seq": 2, "text": "day 5", "day": 5, "data": {"day": 5}}], summary)
    assert len(bridge.screenshots) == 2
    # portraits: one per tick, each colonist once per day, only with a known position
    roster = [{"name": "Kena", "pos": [110, 110]}, {"name": "Lumi", "pos": [112, 112]}, {"name": "Kat"}]
    camera.tick(chron, summary, roster)
    assert bridge.screenshots[-1] == (110, 110, 8)
    camera.tick(chron, summary, roster)
    assert bridge.screenshots[-1] == (112, 112, 8)
    camera.tick(chron, summary, roster)
    assert len(bridge.screenshots) == 4
    ports = [f for f in tl.frames(chron.id) if f["shot"] == "portrait"]
    assert [p["who"] for p in ports] == ["Kena", "Lumi"] and ports[0]["label"] == "Kena, day 3" and ports[0]["file"].endswith("-portrait.jpg")
    assert camera.taken == [] and camera.count_since_chapter == 0
    camera.tick(chron, {**summary, "day": 4}, roster)
    assert len(bridge.screenshots) == 5 and tl.frames(chron.id)[-1]["who"] == "Kena" and tl.frames(chron.id)[-1]["label"] == "Kena, day 4"
    camera.cfg["portraits"] = False
    camera.tick(chron, {**summary, "day": 5}, roster)
    assert len(bridge.screenshots) == 5
    # the daily shot can be switched off too
    camera.cfg["daily"] = False
    camera.on_events(chron, [{**day, "seq": 3, "day": 6}], summary)
    assert len(bridge.screenshots) == 5
    # the wide shot the narrator asks for
    wide = camera.wide(chron, summary, 50, 7)
    assert wide["shot"] == "base" and wide["label"] == "Aswell on day 7" and wide["jpeg"][:3] == b"\xff\xd8\xff" and bridge.screenshots[-1] == (120, 115, 50)
    assert camera.wide(chron, {}, 50, 7) is None and camera.taken == []


def test_camera_pick_for_chapter_prefers_drama_then_time_order(cfg, store, bridge, clock):
    camera, tl, chron = make_camera(cfg, store, bridge, clock)
    summary = bridge.rpc("state.summary")
    t0 = clock.t
    camera.on_events(chron, [{"seq": 1, "kind": "colonist_downed", "text": "Kat downed", "cell": [90, 90], "day": 3, "hour": 8}], summary)
    clock.advance(11)
    camera.on_events(chron, [{"seq": 2, "kind": "mental_break", "text": "Lumi went berserk", "cell": [91, 91], "day": 3, "hour": 9}], summary)
    clock.advance(11)
    camera.on_events(chron, [{"seq": 3, "kind": "colonist_died", "text": "Kena died", "cell": [92, 92], "day": 3, "hour": 10}], summary)
    clock.advance(11)
    camera.on_events(chron, [{"seq": 4, "kind": "day", "text": "day 4", "day": 4, "hour": 0}], summary)  # the time-lapse is never a chapter moment
    assert len(camera.taken) == 3
    picked = camera.pick_for_chapter(chron, since_t=0.0, n=2)
    assert [p["event_seq"] for p in picked] == [1, 3]   # the death (dramatic) and the downing (highest rank), back in time order
    assert all(p["jpeg"][:3] == b"\xff\xd8\xff" for p in picked)
    assert picked[1]["jpeg"] == store.frame_path(chron.id, picked[1]["file"]).read_bytes() and picked[1]["dramatic"] is True
    assert [p["event_seq"] for p in camera.pick_for_chapter(chron, since_t=0.0, n=3)] == [1, 2, 3]
    assert [p["event_seq"] for p in camera.pick_for_chapter(chron, since_t=t0, n=3)] == [2, 3]   # strictly after the last chapter
    assert camera.pick_for_chapter(chron, since_t=clock.t, n=3) == []
    # a frame whose file has gone is skipped
    store.frame_path(chron.id, camera.taken[1]["file"]).unlink()
    assert [p["event_seq"] for p in camera.pick_for_chapter(chron, since_t=0.0, n=3)] == [1, 3]


# ---------------------------------------------------------------- 4. people
def test_people_dossiers_arcs_and_the_fallen(cfg, store, bridge):
    people = People(bridge, store, cfg["narrator"])
    chron = Chronicle(id="ppl", seed="s", name="Aswell")
    roster = compact_roster(bridge.rpc("state.pawns"))
    assert people.refresh(chron, roster, day=3) == 3
    data = json.loads(store.people_path(chron.id).read_text(encoding="utf-8"))
    lumi, kena, kat = (data["colonists"][n] for n in ("Lumi", "Kena", "Kat"))
    assert lumi["traits"] == ["Abrasive", "Wimp"] and lumi["adulthood"] == "Bounty hunter" and lumi["childhood"] == "Caravan child" and lumi["age"] == 54
    assert lumi["skills"] == "Shooting 12 (burning), Melee 12 (burning), Social 7 (interested)"
    assert lumi["thoughts"] == ["Awful barracks"] and lumi["room"] == "Barracks" and lumi["refreshed_day"] == 3 and lumi["joined_day"] == 3
    assert lumi["mood"] == 60 and lumi["weapon"] == "Bolt-action rifle" and lumi["top_skills"] == "Shooting 8, Mining 6"   # cheap fields from the roster
    assert kena["relations"] == ["lover of Kat"] and kena["skills"] == "Shooting 12 (burning), Melee 12 (burning), Intellectual 5"
    assert kat["health"] == ["missing left leg"] and kat["skills"].startswith("Intellectual 13 (burning)")   # the part is not repeated; moving 60% is not (yet) impaired
    assert data["fallen"] == {}
    lines = people.dossier_lines(chron, roster)
    assert len(lines) == 3 and [ln.split(",")[0] for ln in lines] == ["- Kena", "- Lumi", "- Kat"]
    assert lines[1].startswith("- Lumi, 54, woman. Caravan child, then bounty hunter. Traits: abrasive, wimp. Skills: Shooting 12 (burning), Melee 12 (burning), Social 7 (interested). Content; bothered by awful barracks.")
    assert "Sleeps in the barracks. Carries a bolt-action rifle. Now: hauling." in lines[1]
    assert "Lover of Kat." in lines[0] and lines[0].startswith("- Kena, 34, woman. Caravan child, then sniper. Traits: bookish.")
    assert "Health: missing left leg." in lines[2]
    assert "Mood 60" not in "".join(lines) and "%" not in "".join(lines)   # no scores or percentages reach the narrator
    # beyond max_full the line is short
    assert people.dossier_lines(chron, roster, max_full=1)[1] == "- Lumi: abrasive, wimp, Shooting 12 (burning), content."
    # arcs from the ledger, naming the pawn; minor socials and non-arc kinds are ignored; a consecutive repeat is folded
    people.note_events(chron, [
        {"seq": 1, "kind": "mental_break", "text": "Lumi went berserk", "day": 4, "data": {"reason": "Mood was low; final straw was: Awful barracks."}},
        {"seq": 2, "kind": "tale", "text": "tale", "day": 4, "data": {"def": "BecameLover", "pawns": ["Kena", "Kat"]}},
        {"seq": 3, "kind": "social", "text": "Lumi insulted Kena", "day": 4, "data": {"def": "Insult", "initiator": "Lumi", "recipient": "Kena"}},
        {"seq": 4, "kind": "social", "text": "Lumi insulted Kena", "day": 4, "data": {"def": "Insult", "initiator": "Lumi", "recipient": "Kena"}},
        {"seq": 5, "kind": "social", "text": "Kena chatted with Kat", "day": 4, "data": {"def": "Chitchat", "initiator": "Kena", "recipient": "Kat", "minor": True}},
        {"seq": 6, "kind": "relation", "text": "Kena and Kat are lovers", "day": 4, "data": {"def": "Lover", "added": True, "a": "Kena", "b": "Kat"}},
        {"seq": 7, "kind": "built", "text": "Kat built a bench", "day": 4, "data": {"def": "Bench"}},
    ])
    arcs = {n: [a["text"] for a in d["arc"]] for n, d in people.to_dict(chron)["colonists"].items()}
    assert arcs["Lumi"] == ["Lumi went berserk (the last straw: awful barracks)", "Lumi insulted Kena"]
    assert arcs["Kena"] == ["became lover: Kena, Kat", "Lumi insulted Kena", "now lover of Kat"]
    assert arcs["Kat"][0] == "became lover: Kena, Kat" and len(arcs["Kat"]) == 2
    assert json.loads(store.people_path(chron.id).read_text(encoding="utf-8"))["colonists"]["Lumi"]["arc"][0]["day"] == 4
    # a death moves the dossier to the fallen, with day and cause
    people.note_events(chron, [{"seq": 8, "kind": "colonist_died", "text": "Kat died", "day": 5, "data": {"cause": "Gunshot"}}])
    data = json.loads(store.people_path(chron.id).read_text(encoding="utf-8"))
    assert "Kat" not in data["colonists"] and data["fallen"]["Kat"]["died"] == {"day": 5, "cause": "Gunshot", "text": "Kat died"}
    assert data["fallen"]["Kat"]["adulthood"] == "Scholar" and data["fallen"]["Kat"]["arc"][0]["text"] == "became lover: Kena, Kat"
    roster = roster[:2]
    lines = people.dossier_lines(chron, roster)
    assert lines[-1] == "The fallen: Kat (day 5, gunshot; was scholar)."
    assert "So far: Lumi went berserk (the last straw: awful barracks) (day 4); Lumi insulted Kena (day 4)." in lines[1]
    # the diarist is the best talker
    assert people.pick_diarist(chron, roster) == "Lumi"
    assert people.pick_diarist(chron, compact_roster(bridge.rpc("state.pawns"))) == "Lumi"
    assert people.pick_diarist(chron, []) == ""
    assert people.diarist_hint(chron, "Lumi") == "abrasive, wimp; once a bounty hunter; bothered by awful barracks"
    assert people.diarist_hint(chron, "Nobody") == "you write plainly"
    # someone who leaves the roster without a death is absent, and back when seen again
    people.refresh(chron, roster[:1], day=6)
    assert people.load(chron.id)["colonists"]["Lumi"]["absent"] is True and people.load(chron.id)["colonists"]["Lumi"]["absent_day"] == 6
    assert people.dossier_lines(chron, roster[:1])[-1] == "Gone from the colony but not known dead: Lumi."
    people.refresh(chron, roster, day=7)
    assert "absent" not in people.load(chron.id)["colonists"]["Lumi"]
    # portraits are remembered on the dossier
    people.portrait(chron, "Lumi", "d007h00-0-0009-portrait.jpg", 7)
    assert people.load(chron.id)["colonists"]["Lumi"]["portrait"] == "d007h00-0-0009-portrait.jpg"
    # a fresh People reads the file back
    assert People(None, store).to_dict(chron)["fallen"]["Kat"]["died"]["cause"] == "Gunshot"


def test_relation_arc_names_the_other_pawn(cfg, store, bridge):
    people = People(bridge, store, cfg["narrator"])
    chron = Chronicle(id="ppl2", seed="s", name="Aswell")
    people.refresh(chron, compact_roster(bridge.rpc("state.pawns")), day=3)
    people.note_events(chron, [{"seq": 1, "kind": "relation", "text": "Kena and Kat are now lovers", "day": 4, "data": {"def": "Lover", "added": True, "a": "Kena", "b": "Kat"}}])
    arcs = {n: [a["text"] for a in d["arc"]] for n, d in people.to_dict(chron)["colonists"].items()}
    assert arcs["Kena"] == ["now lover of Kat"] and arcs["Kat"] == ["now lover of Kena"]


# ---------------------------------------------------------------- 5. voices
@pytest.mark.parametrize("vid", sorted(VOICES))
def test_every_voice_prompt_carries_the_core_rules(vid):
    v = VOICES[vid]
    p = build_system_prompt(v, colony="Aswell", state_line=SL, date="4th of Jugust", storyteller="Cassandra Classic", diarist="Lumi", diarist_hint="abrasive", custom_prompt="")
    assert SL in p and "Return only a JSON object" in p and "Rules that hold in every voice:" in p
    assert "Never use the words: " + ", ".join(f'"{w}"' for w in BANNED_WORDS) + "." in p
    assert "Aswell" in p
    assert f"- Length: {v.words[0]} to {v.words[1]} words" in p and f"in {v.paragraphs} paragraphs" in p
    assert "use the settlement's name, Aswell" in p
    assert v.title_rule in p and '"epitaph"' in p
    assert "{" not in p.split("Rules that hold in every voice")[0]   # every placeholder in the register was filled
    assert "Author's directive" not in p


def test_storyteller_custom_diary_and_directive_fills():
    st = VOICES["storyteller"]
    for name, expect in (
        ("Cassandra Classic", "You are Cassandra Classic, the storyteller of Aswell"),
        ("Randy Random", "You are Randy Random, the storyteller of Aswell"),
        ("Phoebe Chillax", "You are Phoebe Chillax, the storyteller of Aswell"),
        ("Igor Invader", "You are the storyteller of Aswell, the unseen hand"),
        ("", "You are the storyteller of Aswell, the unseen hand"),
    ):
        assert build_system_prompt(st, colony="Aswell", state_line=SL, storyteller=name).startswith(expect), name
    # the gazette's dateline drops the year
    g = build_system_prompt(VOICES["gazette"], colony="Aswell", state_line=SL, date="4th of Jugust, 5502")
    assert '("Aswell, 4th of Jugust.")' in g and "5502" not in g
    # custom: empty falls back to the chronicler's register; a prompt with stray braces is used verbatim
    chron = build_system_prompt(VOICES["chronicler"], colony="Aswell", state_line=SL)
    assert chron.startswith("You are the chronicler of Aswell, a settlement on a rim world.")
    assert build_system_prompt(VOICES["custom"], colony="Aswell", state_line=SL, custom_prompt="   ") == chron
    custom = build_system_prompt(VOICES["custom"], colony="Aswell", state_line=SL, custom_prompt="You are a pirate telling tales of {colony}.")
    assert custom.startswith("You are a pirate telling tales of Aswell.\n\nRules that hold in every voice:") and SL in custom
    braces = build_system_prompt(VOICES["custom"], colony="Aswell", state_line=SL, custom_prompt="Keep {this} verbatim, {colony}.")
    assert braces.startswith("Keep {this} verbatim, {colony}.") and SL in braces
    # the diary names its holder, their hint and any handover
    dp = build_system_prompt(VOICES["diary"], colony="Aswell", state_line=SL, diarist="Lumi", diarist_hint="abrasive, wimp", diarist_handover="The previous diarist, Kat, is gone.")
    assert dp.startswith("You are Lumi, a colonist of Aswell, writing in your diary") and "(abrasive, wimp)" in dp and "The previous diarist, Kat, is gone." in dp
    dp2 = build_system_prompt(VOICES["diary"], colony="", state_line=SL)
    assert dp2.startswith("You are a colonist, a colonist of the colony") and "(you write plainly)" in dp2
    # the directive is appended last, trimmed; a blank one is not
    d = build_system_prompt(VOICES["chronicler"], colony="Aswell", state_line=SL, directive="  Mention the rain. ")
    assert d.endswith("\n\nAuthor's directive for this chapter (obey it unless it would break a rule above): Mention the rain.")
    assert d.startswith(chron)
    assert build_system_prompt(VOICES["chronicler"], colony="Aswell", state_line=SL, directive="   ") == chron
    # lookups
    assert get_voice(" NOIR ").id == "noir" and get_voice("nope").id == "storyteller" and get_voice(None).id == "storyteller"
    assert [v["id"] for v in list_voices()] == list(VOICES) and list_voices()[0]["temperature"] == 0.7 and all(v["sample"] for v in list_voices())


# ---------------------------------------------------------------- 6. the narrator, wired through the engine
def test_engine_chapter_prompt_carries_people_threads_and_changes(cfg, store, bridge, llm, clock):
    bridge.colonists = 2
    eng = make_engine(cfg, bridge, llm, clock)
    q = eng.hub.subscribe()
    eng.step()
    chron = eng.watcher.chronicle
    assert len(chron.chapters) == 1
    ch1 = chron.chapters[0]
    text = user_text(llm.calls[0])
    assert "Colony: Aswell (of the faction Anditeria, Crashlanded, storyteller Cassandra, Rough). World seed test-seed. The chronicle began on day 3." in text
    assert "Now: day 3 of Aprimay, hour 08, Spring, Clear, 21 C outdoors, biome temperate forest." in text
    assert "The people of Aswell:\n- Kena, 34, woman. Caravan child, then sniper. Traits: bookish." in text
    assert "- Lumi, 54, woman. Caravan child, then bounty hunter. Traits: abrasive, wimp." in text
    assert "Open threads (unresolved as of now; use them for tension, do not resolve them):\n- alert: Major break risk\n" in text
    assert "This is the first chapter." in text and "Since the last chapter" not in text and "not listed" not in text
    assert "Ledger since the last chapter: nothing notable was recorded." in text
    assert "- the closing line must read: State of the colony: day 3, 2 colonists, food 4.5 days, mood 55, threat none (80 points)." in text
    assert text.endswith("Write chapter 1. Trigger: opening.")
    assert ch1.voice == "storyteller" and ch1.trigger == "opening" and llm.temperatures[-1] == VOICES["storyteller"].temperature == 0.8
    assert set(ch1.inputs) >= {"user_text", "images", "state", "roster", "closing", "date", "storyteller"}
    assert ch1.inputs["user_text"] == text and ch1.inputs["roster"] == ["Kena", "Lumi"] and ch1.inputs["state"]["colonists"] == 2 and ch1.inputs["closing"] is False
    assert ch1.inputs["images"] == ch1.images and ch1.inputs["date"] == "day 3 of Aprimay" and ch1.inputs["storyteller"] == "Cassandra"
    # the first picture is the camera's wide shot of the base; nothing happened, so there is no event shot either
    assert len(ch1.images) == 1 and ch1.images[0]["frame"] is True and ch1.images[0]["file"].endswith("-base.jpg") and ch1.images[0]["caption"] == "Aswell on day 3"
    assert [s for s in bridge.screenshots if s[2] != 8] == [(120, 115, 50)] and (store.frames_dir(chron.id) / ch1.images[0]["file"]).exists()
    assert image_count(llm.calls[0]) == 1 and "Pictures attached, in order" in text and "1. Aswell on day 3" in text
    recs, _ = eng.timeline.read(chron.id, kinds={"chapter"})
    assert len(recs) == 1 and recs[0]["k"] == 1 and recs[0]["voice"] == "storyteller" and recs[0]["trigger"] == "opening" and recs[0]["images"] == [ch1.images[0]["file"]]
    assert recs[0]["t"] == ch1.t == clock.t
    kinds = [e["kind"] for e in drain(q)]
    assert [k for k in kinds if k != "frame"] == ["bridge", "chronicle", "state", "writing", "chapter", "idle"] and "frame" in kinds
    assert store.load(chron.id).chapters[0].inputs["user_text"] == text

    # chapter 2: someone joined, minor chatter was counted, and the camera caught the arrival live
    clock.advance(200)
    bridge.colonists = 3
    bridge.add("colonist_joined", "Kat joined the colony", cell=[30, 30], data={"reason": "wanderer"})
    bridge.add("social", "Kena chatted with Lumi", data={"def": "Chitchat", "initiator": "Kena", "recipient": "Lumi", "minor": True})
    bridge.add("social", "Lumi chatted with Kena", data={"def": "Chitchat", "initiator": "Lumi", "recipient": "Kena", "minor": True})
    bridge.add("social", "Lumi insulted Kena", data={"def": "Insult", "initiator": "Lumi", "recipient": "Kena"})
    eng.step()
    assert len(chron.chapters) == 2
    ch2 = chron.chapters[1]
    text = user_text(llm.calls[1])
    assert ch2.trigger == "drama: Kat joined the colony"
    assert "Since the last chapter: joined: Kat." in text
    # the minor chatter is counted, not listed (the exact wording of this line is being tuned; the count and the word "chat" are the contract)
    assert re.search(r"not listed[^\n]*\b2 (idle chats|chitchats)\.", text), text
    assert "Kena chatted with Lumi" not in text
    assert re.search(r"Ledger since the last chapter \([^\n]*\):\n- Day 3, 08:00, Kat joined the colony far to the south-west\.\n- Day 3, 08:00, Lumi insulted Kena\.\n", text), text
    assert "(30, 30)" not in text   # places, never coordinates
    assert "Recent chapters (one line each):\n- Chapter 1, day 3: Chapter 1 of the test colony. Summary of chapter 1." in text
    assert "- Kat, 27, woman. Caravan child, then scholar." in text and "Health: missing left leg." in text
    assert ch2.inputs["roster"] == ["Kena", "Lumi", "Kat"] and ch2.events_used == [1, 4]
    assert [im["frame"] for im in ch2.images] == [True, True]
    assert ch2.images[1]["file"].endswith("-colonist_joined.jpg") and ch2.images[1]["caption"] == "Kat joined the colony, day 3 h08"
    assert image_count(llm.calls[1]) == 2 and "2. Kat joined the colony, day 3 h08" in text
    assert [s for s in bridge.screenshots if s[2] != 8][-2:] == [(30, 30, 24), (120, 115, 50)]   # the moment as it happened, then the wide shot at writing time

    # chapter 3: she died; the roster shrank and the fallen are listed
    clock.advance(200)
    bridge.colonists = 2
    bridge.add("colonist_died", "Kat died", cell=[30, 30], data={"cause": "Gunshot"})
    eng.step()
    ch3 = chron.chapters[2]
    text = user_text(llm.calls[2])
    assert "Since the last chapter: gone: Kat." in text
    assert "The fallen: Kat (day 3, gunshot; was scholar)." in text
    assert "- Kat, 27, woman" not in text and ch3.inputs["roster"] == ["Kena", "Lumi"]
    assert "- Day 3, 08:00, died: Kat died far to the south-west (cause gunshot)" in text
    assert ch3.images[1]["file"].endswith("-colonist_died.jpg") and ch3.images[1]["frame"] is True
    assert [r["k"] for r in eng.timeline.read(chron.id, kinds={"chapter"})[0]] == [1, 2, 3]
    assert eng.status()["colony"] == "Aswell" and eng.status()["pending_events"] == 0


def test_engine_falls_back_to_an_event_shot_when_no_moment_was_caught(cfg, store, bridge, llm, clock):
    eng = make_engine(cfg, bridge, llm, clock)
    bridge.add("incident", "Cargo pods crashed", cell=[20, 20], data={"def": "ResourcePodCrash", "category": "Misc"})
    ch = eng.write_now("manual: test")
    assert ch is not None and ch.trigger == "manual: test" and ch.k == 1
    assert [(im["frame"], im["caption"]) for im in ch.images] == [(True, "Aswell on day 3"), (False, "Cargo pods crashed, day 3")]
    assert ch.images[0]["file"].endswith("-base.jpg") and ch.images[1]["file"] == "ch001-event.jpg"
    assert [s for s in bridge.screenshots if s[2] != 8] == [(120, 115, 50), (20, 20, 30)]
    gid = eng.watcher.chronicle.id
    assert (store.image_dir(gid) / "ch001-event.jpg").exists() and (store.frames_dir(gid) / ch.images[0]["file"]).exists()
    assert eng.camera.taken == [] and eng.camera.count_since_chapter == 0
    assert image_count(llm.calls[0]) == 2
    # a rewrite finds both kinds of picture again
    eng.narrator.rewrite(eng.watcher.chronicle, 1, "quarterly")
    assert image_count(llm.calls[1]) == 2


def test_engine_step_publishes_frames_and_attaches_them(cfg, store, bridge, llm, clock):
    eng = make_engine(cfg, bridge, llm, clock)
    q = eng.hub.subscribe()
    bridge.add("colonist_died", "Kena died", cell=[30, 30], data={"cause": "Gunshot"})
    eng.step()
    evs = drain(q)
    kinds = [e["kind"] for e in evs]
    assert [k for k in kinds if k != "frame"] == ["bridge", "chronicle", "event", "state", "writing", "chapter", "idle"]
    assert kinds.count("frame") >= 2 and kinds.index("frame") < kinds.index("writing")  # the moment lands before the chapter is written
    frames = [e["data"] for e in evs if e["kind"] == "frame" and e["data"]["shot"] != "portrait"]   # portraits come and go with the roster
    assert [f["shot"] for f in frames] == ["colonist_died", "base"]
    moment = frames[0]
    assert moment["id"] == eng.watcher.game_id and moment["dramatic"] is True and "jpeg" not in moment and moment["cell"] == [30, 30] and moment["event_seq"] == 1
    ch = eng.watcher.chronicle.chapters[0]
    assert ch.trigger == "drama: Kena died"
    assert [im["frame"] for im in ch.images] == [True, True] and ch.images[1]["file"] == moment["file"] and ch.images[0]["file"] == frames[1]["file"]
    assert evs[-2]["data"]["k"] == 1 and evs[-2]["data"]["voice"] == "storyteller" and evs[-1]["data"] == {}
    assert eng.camera.count_since_chapter == 0   # reset once the chapter took its pictures
    assert eng.status()["camera"] == {"since_chapter": 0, "pending": 0, "error": ""}
    assert [f["shot"] for f in eng.timeline.frames(eng.watcher.game_id) if f["shot"] != "portrait"] == ["colonist_died", "base"]


# ---------------------------------------------------------------- 7. the diary
def test_diary_voice_picks_and_hands_over_the_book(cfg, store, bridge, llm, clock):
    cfg["narrator"]["voice"] = "diary"
    eng = make_engine(cfg, bridge, llm, clock)
    eng.step()
    chron = eng.watcher.chronicle
    ch1 = chron.chapters[0]
    assert chron.diarist == "Lumi" and ch1.voice == "diary" and ch1.inputs["diarist"] == "Lumi"
    assert ch1.inputs["diarist_hint"] == "abrasive, wimp; once a bounty hunter; bothered by awful barracks"
    system = system_text(llm.calls[0])
    assert system.startswith("You are Lumi, a colonist of Aswell, writing in your diary")
    assert "(abrasive, wimp; once a bounty hunter; bothered by awful barracks)" in system and "previous diarist" not in system
    assert llm.temperatures[-1] == VOICES["diary"].temperature == 0.8
    assert store.load(chron.id).diarist == "Lumi" and eng.status()["voice"] == "diary"
    # the book passes when its holder is gone: make Kat hold it, then lose her
    chron.diarist = "Kat"
    clock.advance(200)
    bridge.add("colonist_died", "Kat died", cell=[30, 30], data={"cause": "Gunshot"})
    bridge.colonists = 2
    eng.step()
    ch2 = chron.chapters[1]
    system = system_text(llm.calls[1])
    assert chron.diarist == "Lumi" and ch2.inputs["diarist"] == "Lumi" and ch2.voice == "diary"
    assert system.startswith("You are Lumi, a colonist of Aswell")
    assert "The previous diarist, Kat, is gone (dead or departed); you have taken up the book. Acknowledge that in your first lines." in system
    # a diarist who is still around keeps the book, without a handover
    clock.advance(200)
    eng.narrator.write("manual")
    assert chron.diarist == "Lumi" and "previous diarist" not in system_text(llm.calls[2]) and system_text(llm.calls[2]).startswith("You are Lumi")


# ---------------------------------------------------------------- 8. rewrite
def test_rewrite_keeps_versions_and_reuses_the_stored_prompt(cfg, store, bridge, llm, clock):
    eng = make_engine(cfg, bridge, llm, clock)
    bridge.add("colonist_died", "Kena died", cell=[30, 30], data={"cause": "Gunshot"})
    eng.step()
    chron = eng.watcher.chronicle
    ch = chron.chapters[0]
    old = {"title": ch.title, "summary": ch.summary, "body": ch.body, "voice": "storyteller", "pull_quote": ch.pull_quote, "t": ch.t}
    assert len(ch.images) == 2
    assert ch.pull_quote == "Kena hauled steel while Lumi watched the tree line."   # the model gave none, so the body's first strong sentence stands in
    clock.advance(5)
    llm.reply_override = json.dumps({"title": "the conduit job", "summary": "A noir summary.", "pull_quote": "Rain.", "body": "Rain. Kena was dead by four.\n\n" + SL})
    out = eng.narrator.rewrite(chron, 1, "noir")
    assert out is ch and ch.voice == "noir" and ch.title == "the conduit job" and ch.summary == "A noir summary." and ch.pull_quote == "Rain." and ch.t == clock.t
    assert ch.body.startswith("Rain. Kena was dead by four.") and ch.versions == [old]
    assert user_text(llm.calls[1]) == user_text(llm.calls[0]) == ch.inputs["user_text"]
    assert image_count(llm.calls[1]) == image_count(llm.calls[0]) == 2
    assert "hardboiled" in system_text(llm.calls[1]) and SL in system_text(llm.calls[1]) and llm.temperatures[-1] == VOICES["noir"].temperature
    saved = store.load(chron.id).chapters[0]
    assert saved.voice == "noir" and saved.versions[0]["voice"] == "storyteller" and saved.inputs["user_text"] == ch.inputs["user_text"]
    assert saved.public()["versions"] == [old] and "inputs" not in saved.public()
    # rewrite again through the engine's queue: versions accumulate, newest last
    llm.reply_override = None
    q = eng.hub.subscribe()
    eng.request_rewrite(chron.id, 1, "gazette")
    eng.step()
    assert [v["voice"] for v in ch.versions] == ["storyteller", "noir"] and ch.voice == "gazette" and ch.title.startswith("Chapter ")
    kinds = [e["kind"] for e in drain(q)]
    assert kinds[-3:] == ["writing", "chapter", "idle"]
    # a chapter without stored inputs cannot be re-narrated
    chron.chapters.append(Chapter(k=2, title="Old", summary="s", body="b", day=3, hour=8, t=1.0))
    with pytest.raises(ValueError, match="predates"):
        eng.narrator.rewrite(chron, 2, "noir")
    with pytest.raises(ValueError, match="no chapter 3"):
        eng.narrator.rewrite(chron, 3, "noir")
    assert len(llm.calls) == 3


# ---------------------------------------------------------------- 9. saga
def test_saga_rolls_up_older_chapters(cfg, store, bridge, llm, clock):
    cfg["narrator"]["saga_every"] = 3
    cfg["narrator"]["min_real_seconds_between"] = 0
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    chron = w.chronicle
    for k in (1, 2):
        clock.advance(10)
        assert n.write("manual").k == k
        assert chron.saga == "" and chron.saga_through == 0
    clock.advance(10)
    n.write("manual")
    assert chron.saga == "The colony of Aswell survived its first days: Kena, Lumi and Kat built and quarrelled." and chron.saga_through == 1
    roll = llm.calls[3]
    assert len(roll) == 1 and roll[0]["role"] == "user" and llm.temperatures[-1] is None
    roll_text = roll[0]["content"][0]["text"]
    assert roll_text.startswith("Condense the following chapter summaries")
    assert roll_text.endswith("Chapters to fold in:\n- Chapter 1, day 3: Chapter 1 of the test colony. Summary of chapter 1.")
    assert "Chapter 2" not in roll_text and "The story so far" not in roll_text
    assert store.load(chron.id).saga_through == 1 and store.load(chron.id).saga == chron.saga
    # the next prompt opens with the saga and lists only the chapters after it
    clock.advance(10)
    n.write("manual")
    text = user_text(llm.calls[4])
    assert "The story so far:\nThe colony of Aswell survived its first days: Kena, Lumi and Kat built and quarrelled.\n\nRecent chapters (one line each):\n- Chapter 2, day 3:" in text
    assert "- Chapter 1, day 3" not in text and "- Chapter 3, day 3" in text
    # the fourth chapter folded chapter 2 in, and the roll-up prompt carried the saga itself
    assert chron.saga_through == 2
    roll2 = llm.calls[5][0]["content"][0]["text"]
    assert "The story so far:\nThe colony of Aswell survived" in roll2 and roll2.endswith("- Chapter 2, day 3: Chapter 2 of the test colony. Summary of chapter 2.")
    # a roll-up that comes back too short leaves the saga as it was
    llm.reply_override = "short"
    clock.advance(10)
    n.write("manual")
    assert len(chron.chapters) == 5 and chron.saga_through == 2 and chron.saga.startswith("The colony of Aswell")
    # and it can be switched off
    llm.reply_override = None
    cfg["narrator"]["saga_every"] = 0
    clock.advance(10)
    n.write("manual")
    assert len(chron.chapters) == 6 and chron.saga_through == 2 and len(llm.calls) == 9   # six chapters and three roll-up attempts, none for the sixth


# ---------------------------------------------------------------- 10. settings
def test_filter_settings_coerces_and_drops_unknown_keys():
    out = filter_settings({
        "narrator": {"min_events": "5", "opening_chapter": "no", "voice": None, "saga_every": 2.9, "bogus": 1},
        "camera": {"moments": "yes", "debounce_seconds": "3", "follow_fight": False},
        "llm": {"temperature": "0.25", "timeout_s": 12.7, "max_tokens": "abc", "api_key": "k"},
        "web": {"port": 1}, "nope": {"a": 1}, "bridge": "not a dict",
    })
    assert out == {
        "narrator": {"min_events": 5, "opening_chapter": False, "voice": "", "saga_every": 2},
        "camera": {"moments": True, "debounce_seconds": 3, "follow_fight": False},
        "llm": {"temperature": 0.25, "timeout_s": 12, "api_key": "k"},
    }
    assert filter_settings({}) == {} and filter_settings({"web": {"port": 9}}) == {}


def test_engine_settings_hot_apply_persist_and_mask(cfg, store, bridge, llm, clock, tmp_path):
    eng = make_engine(cfg, bridge, llm, clock)
    eng.step()
    assert eng.watcher.chronicle.chapters[0].voice == "storyteller" and eng.status()["voice"] == "storyteller"
    view = eng.apply_settings({"narrator": {"voice": "gazette", "min_events": "7"}, "llm": {"temperature": 0.3}, "camera": {"moments": False, "max_per_chapter": "5"}, "web": {"port": 1}, "bogus": {"x": 1}})
    assert eng.cfg is cfg and cfg["narrator"]["voice"] == "gazette" and cfg["narrator"]["min_events"] == 7
    assert eng.narrator.cfg is cfg["narrator"] and eng.narrator.cfg["voice"] == "gazette"
    assert eng.camera.cfg is cfg["camera"] and eng.camera.cfg["moments"] is False and eng.camera.cfg["max_per_chapter"] == 5
    assert cfg["llm"]["temperature"] == 0.3 and cfg["web"]["port"] == 8771 and "bogus" not in cfg
    assert view["narrator"]["voice"] == "gazette" and view["camera"]["moments"] is False and view["llm"]["temperature"] == 0.3
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8"))
    assert saved == {"narrator": {"voice": "gazette", "min_events": 7}, "llm": {"temperature": 0.3}, "camera": {"moments": False, "max_per_chapter": 5}}
    # the LLM client was rebuilt from the new llm section; put the fake back and the next chapter uses the new voice without a restart
    assert eng.narrator.llm is eng.llm and eng.llm is not llm
    eng.narrator.llm = llm
    clock.advance(200)
    ch = eng.write_now("manual: after settings")
    assert ch.voice == "gazette" and llm.temperatures[-1] == VOICES["gazette"].temperature == 0.85
    assert eng.status()["voice"] == "gazette" and eng.status()["default_voice"] == "gazette"
    assert "The Aswell Gazette" in system_text(llm.calls[-1])
    # a per-chronicle voice wins over the default in the status
    eng.watcher.chronicle.voice = "saga"
    assert eng.status()["voice"] == "saga" and eng.status()["default_voice"] == "gazette"
    # masking: a real key is hidden, and a masked key sent back is ignored
    cfg["llm"]["api_key"] = "sk-secret-key-ab"
    view = eng.settings_view()
    assert view["llm"]["api_key"] == "••••••••ab" and "sk-secret" not in json.dumps(view)
    assert [v["id"] for v in view["voices"]] == list(VOICES) and view["root"] == str(tmp_path) and view["storage"] == str(tmp_path / "chronicles")
    assert set(view) == {"llm", "narrator", "camera", "overseer", "bridge", "voices", "root", "storage"}
    eng.apply_settings({"llm": {"api_key": "••••••••ab", "model": "other-model"}})
    assert cfg["llm"]["api_key"] == "sk-secret-key-ab" and cfg["llm"]["model"] == "other-model"
    saved = yaml.safe_load((tmp_path / "config.local.yaml").read_text(encoding="utf-8"))
    assert saved["llm"] == {"temperature": 0.3, "model": "other-model"} and saved["narrator"]["voice"] == "gazette"
    eng.apply_settings({"llm": {"api_key": "sk-new"}}, persist=False)
    assert cfg["llm"]["api_key"] == "sk-new" and "sk-new" not in (tmp_path / "config.local.yaml").read_text(encoding="utf-8")
    assert eng.settings_view()["llm"]["api_key"] == "••••••••ew"
    cfg["llm"]["api_key"] = "not-needed"
    assert eng.settings_view()["llm"]["api_key"] == "not-needed"
    # the overseer follows its section
    eng.apply_settings({"overseer": {"enabled": True, "url": "http://127.0.0.1:9/", "poll_seconds": "7"}}, persist=False)
    assert eng.overseer.enabled is True and eng.overseer.url == "http://127.0.0.1:9" and eng.overseer.poll_seconds == 7.0
    eng.apply_settings({"overseer": {"enabled": False}}, persist=False)
    assert eng.overseer.enabled is False
    # nothing to apply writes nothing
    (tmp_path / "config.local.yaml").unlink()
    eng.apply_settings({"web": {"port": 1}})
    assert not (tmp_path / "config.local.yaml").exists()
    # the status carries the colony, the voice, the open threads and the camera
    st = eng.status()
    assert st["colony"] == "Aswell" and st["chronicle"] == eng.watcher.game_id and st["online"] is True
    assert st["threads"] == ["alert: Major break risk"] and st["game"]["seed"] == "test-seed" and st["state"]["colonists"] == 3
    assert st["camera"] == {"since_chapter": 0, "pending": 0, "error": ""} and st["writing"] is False and st["overseer"] is False
