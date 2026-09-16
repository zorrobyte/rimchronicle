from __future__ import annotations

import json

from rimchronicle.narrator import Narrator, parse_chapter, state_line
from rimchronicle.watcher import Watcher, is_notable


def make(cfg, store, bridge, llm, clock):
    w = Watcher(bridge, store, cfg["bridge"], clock=clock)
    n = Narrator(cfg["narrator"], store, llm, w, overseer=None, clock=clock)
    return w, n


def test_chapter_written_with_images_and_files(cfg, store, bridge, llm, clock):
    bridge.add("incident", "Raid", cell=[40, 40], data={"def": "RaidEnemy", "faction": "Pirates", "points": 120})
    bridge.add("colonist_downed", "Lumi downed", cell=[121, 118], data={"cause": "Gunshot"})
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    assert w.online and w.chronicle is not None
    assert w.chronicle.id == "test-seed-1000"
    assert len(w.pending) == 2
    trigger = n.due()
    assert trigger and trigger.startswith("drama")
    ch = n.write(trigger)
    assert ch is not None and ch.k == 1
    assert ch.title.startswith("Chapter 1")
    assert "State of the colony" in ch.body
    assert ch.events_used == [1, 2]
    # two images: the base and the raid cell (far from home)
    assert [i["file"] for i in ch.images] == ["ch001-base.jpg", "ch001-event.jpg"]
    assert bridge.screenshots == [(120, 115, 50), (40, 40, 30)]
    d = store.dir(w.chronicle.id)
    assert (d / "chronicle.json").exists() and (d / "chronicle.md").exists()
    for im in ch.images:
        p = d / "images" / im["file"]
        assert p.exists() and p.read_bytes()[:3] == b"\xff\xd8\xff"  # JPEG magic
    data = json.loads((d / "chronicle.json").read_text())
    assert data["seed"] == "test-seed" and data["scenario"] == "Crashlanded" and data["status"] == "running"
    assert data["chapters"][0]["images"][0]["caption"].startswith("The base on day")
    md = (d / "chronicle.md").read_text()
    assert "## 1. Chapter 1 of the test colony" in md and "images/ch001-base.jpg" in md
    # the prompt carried the events, the roster and both pictures
    msgs = llm.calls[0]
    text = msgs[1]["content"][0]["text"]
    assert "Raid" in text and "Kena" in text and "Ledger since the last chapter" in text
    assert sum(1 for part in msgs[1]["content"] if part["type"] == "image_url") == 2
    assert msgs[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert w.pending == [] and w.chronicle.last_seq == 2


def test_rate_limit_respected(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    assert n.due() == "opening"
    assert n.write("opening") is not None
    bridge.add("colonist_died", "Kat died", cell=[100, 100], data={"cause": "Gunshot"})
    w.tick()
    assert n.due() is None, "a dramatic event inside the cool-down must wait"
    clock.advance(119)
    assert n.due() is None
    clock.advance(2)
    assert n.due().startswith("drama")
    assert n.write(n.due()).k == 2
    # a quiet day with too few events does not earn a chapter, a busy one does
    clock.advance(500)
    bridge.pass_day()
    bridge.hour = 2  # 18 in-game hours after the last chapter
    w.tick()
    assert n.due() is None
    bridge.add("built", "Research bench built by Kat", cell=[118, 118], data={"def": "SimpleResearchBench"})
    bridge.add("research_finished", "Smithing", data={"def": "Smithing"})
    w.tick()
    assert n.due() == "day"


def test_max_hours_between(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    clock.advance(1000)
    bridge.hour = 8
    bridge.day += 1  # 24 hours later, nothing notable happened
    w.refresh_state()
    assert n.due() == "time"


def test_new_game_detection_starts_a_new_chronicle(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    first = w.chronicle.id
    bridge.add("letter", "Old news")
    w.tick()
    assert len(w.pending) == 1
    bridge.new_game("second-seed", 777)
    w.tick()
    assert w.chronicle.id == "second-seed-777" and w.chronicle.id != first
    assert w.pending == [] and w.chronicle.chapters == []
    # the same seed but a fresh start tick is also a new game
    bridge.new_game("test-seed", 5000)
    w.tick()
    assert w.chronicle.id == "test-seed-5000"
    assert {c.id for c in store.list()} == {first, "second-seed-777", "test-seed-5000"}
    # ...and going back to the original game resumes its chronicle with its chapters
    bridge.load_game("test-seed", 1000)
    w.tick()
    assert w.chronicle.id == first and len(w.chronicle.chapters) == 1


def test_two_games_with_the_same_seed_and_start_tick_are_separate_chronicles(cfg, store, bridge, llm, clock):
    """An agent replays a fixed list of seeds, so a dead colony and a fresh one share both the seed
    string and the start tick of a fresh scenario. They must not become one book."""
    bridge.new_game("rimagent-4", 20000, world_uid="111", names=["Tsuki", "Isamu", "Vargas"])
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    first = w.chronicle.id
    assert first == "rimagent-4-20000-111"
    bridge.add("letter", "Old news")
    w.tick()
    bridge.new_game("rimagent-4", 20000, world_uid="222", names=["Gnat", "Lupa", "Rhod"])
    w.tick()
    assert w.chronicle.id == "rimagent-4-20000-222" != first
    assert w.chronicle.chapters == [] and w.pending == []
    assert store.load(first).chapters and store.load(first).last_roster == ["Tsuki", "Isamu", "Vargas"]


def test_a_colliding_id_is_caught_by_the_roster(cfg, store, bridge, llm, clock):
    """Even with no world id to tell them apart (an older bridge), a colony of strangers is a new game."""
    bridge.new_game("rimagent-4", 20000, names=["Tsuki", "Isamu", "Vargas"])
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    first = w.chronicle.id
    assert first == "rimagent-4-20000"
    bridge.add("letter", "Old news")
    w.tick()
    bridge.new_game("rimagent-4", 20000, names=["Gnat", "Lupa", "Rhod"])
    w.tick()
    assert w.chronicle.id != first, "a completely different colony must not continue the old chronicle"
    assert w.chronicle.chapters == [] and w.chronicle.status == "running"
    assert store.load(first).chapters and store.load(first).status == "running"


def test_a_new_colony_is_not_blocked_by_the_old_ones_ending(cfg, store, bridge, llm, clock):
    """The wipe ends one chronicle; the next game under the same ids still gets chapters."""
    bridge.new_game("rimagent-4", 20000, names=["Tsuki", "Isamu", "Vargas"])
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    dead = w.chronicle.id
    bridge.colonists = 0
    w.refresh_state()
    clock.advance(200)
    w.tick()
    assert n.due().startswith("ending")
    n.write(n.due())
    assert store.load(dead).status == "ended"
    # a second, unrelated colony under the same seed and start tick
    bridge.add("letter", "Old news")
    w.tick()
    bridge.new_game("rimagent-4", 20000, names=["Gnat", "Lupa", "Rhod"])
    w.tick()
    assert w.chronicle.id != dead and w.chronicle.status == "running"
    clock.advance(200)
    assert n.due() == "opening"
    assert n.write("opening").k == 1
    assert store.load(w.chronicle.id).status == "running" and store.load(dead).status == "ended"


def test_the_roster_is_rechecked_between_identifications(cfg, store, bridge, llm, clock):
    """The colony can be swapped out without any signal the fast path looks at; the periodic state
    refresh must still notice."""
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    first = w.chronicle.id
    assert w.chronicle.last_roster == ["Kena", "Lumi", "Kat"]
    bridge.names = ["Gnat", "Lupa", "Rhod"]   # same seed, same tick, same ledger: only the people changed
    w.refresh_state()
    assert w.chronicle.id != first and w.chronicle.chapters == []
    assert store.load(first).chapters, "the old book keeps its chapter"


def test_reloading_the_same_save_continues_the_chronicle(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    gid = w.chronicle.id
    bridge.state = "menu"
    w.tick()
    assert w.game_left
    clock.advance(200)
    assert n.due().startswith("ending")
    n.write(n.due())
    assert w.chronicle.status == "ended"
    bridge.state = "playing"
    w.tick()
    assert w.chronicle.id == gid and not w.game_left and w.chronicle.status == "running"
    bridge.add("colonist_died", "Kat died", cell=[100, 100])
    clock.advance(200)
    w.tick()
    assert n.due().startswith("drama"), "the game came back, so the book is open again"
    assert n.write(n.due()).k == 3


def test_an_ended_chronicle_reopens_when_its_game_comes_back(cfg, store, bridge, llm, clock):
    """An earlier save of the same colony is loaded after the wipe: the book reopens, and a second
    ending can still be written."""
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    gid = w.chronicle.id
    bridge.colonists = 0
    w.refresh_state()
    clock.advance(200)
    w.tick()
    n.write(n.due())
    assert w.chronicle.status == "ended" and n.due() is None
    bridge.load_game("test-seed", 1000)      # the same game, rolled back to before the wipe
    bridge.colonists = 3
    clock.advance(200)
    w.tick()
    assert w.chronicle.id == gid and w.chronicle.status == "running" and not w.colony_wiped
    bridge.colonists = 0
    clock.advance(200)
    w.refresh_state()
    w.tick()
    assert w.colony_wiped and n.due().startswith("ending"), "the second ending is not swallowed by the first"
    assert n.write(n.due()).k == 3


def test_a_legacy_chronicle_is_resumed_when_the_bridge_learns_world_ids(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    legacy = w.chronicle.id
    assert legacy == "test-seed-1000"
    bridge.world_uid_value = "999"            # the mod now reports one; the same game is still running
    w.tick()
    w._needs_identify = True
    w._identify()
    assert w.chronicle.id == legacy and len(w.chronicle.chapters) == 1
    assert {c.id for c in store.list()} == {legacy}


def test_bridge_down_and_back(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    bridge.online = False
    assert w.tick() == [] and not w.online and w.chronicle is None
    assert n.due() is None
    bridge.online = True
    w.tick()
    assert w.online and w.chronicle is not None


def test_llm_failure_keeps_events_for_retry(cfg, store, bridge, llm, clock):
    bridge.add("colonist_died", "Kena died", cell=[100, 100])
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    llm.fail = True
    assert n.write("drama: test") is None
    assert n.last_error.startswith("RuntimeError") and len(w.pending) == 1
    assert n.due() is None  # backing off
    clock.advance(61)
    llm.fail = False
    assert n.due().startswith("drama")
    ch = n.write(n.due())
    assert ch is not None and ch.events_used == [1]


def test_closing_chapter_on_wipe(cfg, store, bridge, llm, clock):
    w, n = make(cfg, store, bridge, llm, clock)
    w.tick()
    n.write("opening")
    bridge.add("colonist_died", "Kena died", cell=[100, 100])
    bridge.colonists = 0
    w.refresh_state()
    w.tick()
    assert w.colony_wiped
    trig = n.due()
    assert trig and trig.startswith("ending")
    ch = n.write(trig)
    assert w.chronicle.status == "ended" and w.chronicle.epitaph == "They built a shelter and it was not enough."
    assert n.due() is None


def test_parse_chapter_variants():
    good = parse_chapter('{"title": "A Quiet Day", "summary": "Nothing.", "body": "Line one.\\n\\nLine two."}', 3)
    assert good["title"] == "A Quiet Day" and good["body"] == "Line one.\n\nLine two."
    fenced = parse_chapter('```json\n{"title": "Fenced", "summary": "s", "body": "b"}\n```', 3)
    assert fenced["title"] == "Fenced"
    chatter = parse_chapter('Here is the chapter:\n{"title": "Wrapped", "summary": "s", "body": "b"}\nHope you like it.', 3)
    assert chatter["title"] == "Wrapped"
    raw_newlines = parse_chapter('{"title": "Raw", "summary": "s", "body": "first\nsecond"}', 3)
    assert raw_newlines["body"] == "first\nsecond"
    prose = parse_chapter("The colony woke to rain. Nobody minded.", 7)
    assert prose["title"] == "Day 7" and prose["summary"] == "The colony woke to rain." and "rain" in prose["body"]
    assert parse_chapter("", 2)["body"]


def test_state_line_and_notable_filter():
    assert state_line({"day": 4, "colonists": 3, "food_days": 2.25, "mood_avg": 41.6, "danger": "Low", "threat_points": 90}) == "State of the colony: day 4, 3 colonists, food 2.2 days, mood 42, threat low (90 points)."
    assert not is_notable({"kind": "built", "text": "wall built by Kat", "data": {"def": "Wall"}})
    assert not is_notable({"kind": "built", "text": "power conduit built by Kat", "data": {"def": "PowerConduit"}})
    assert is_notable({"kind": "built", "text": "research bench built by Kat", "data": {"def": "SimpleResearchBench"}})
    assert not is_notable({"kind": "message", "text": "x"})
    assert not is_notable({"kind": "danger", "text": "danger Low -> None (0 hostile targets)"})
    assert is_notable({"kind": "danger", "text": "danger None -> High (5 hostile targets)"})
