"""The web layer: static page, timeline and people endpoints, settings, write options, rewrite, export appendices."""
from __future__ import annotations

from fastapi.testclient import TestClient

from rimchronicle.cli import main as cli_main
from rimchronicle.engine import Engine
from rimchronicle.export import render_book
from rimchronicle.overseer import Overseer
from rimchronicle.web import create_app


def make_engine(cfg, bridge, llm):
    return Engine(cfg, bridge=bridge, llm=llm, overseer=Overseer("http://127.0.0.1:1", enabled=False))


def test_static_page_and_assets(cfg, bridge, llm):
    c = TestClient(create_app(make_engine(cfg, bridge, llm)))
    r = c.get("/")
    assert r.status_code == 200 and "RimChronicle" in r.text and "/static/app.js" in r.text and "/static/app.css" in r.text
    js = c.get("/static/app.js")
    assert js.status_code == 200 and "#/timeline/" in js.text and "#/people/" in js.text and "#/settings" in js.text
    css = c.get("/static/app.css")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"] and "prefers-color-scheme: dark" in css.text
    assert c.get("/static/nope.js").status_code == 404


def test_voices_and_settings(cfg, bridge, llm):
    cfg["llm"]["api_key"] = "sk-secret-42"
    eng = make_engine(cfg, bridge, llm)
    c = TestClient(create_app(eng))
    vs = c.get("/api/voices").json()
    assert len(vs) == 9 and {v["id"] for v in vs} >= {"chronicler", "saga", "gazette", "naturalist", "diary", "noir", "storyteller", "quarterly", "custom"}
    assert all(v["name"] and v["blurb"] and v["sample"] for v in vs)
    s = c.get("/api/settings").json()
    assert s["llm"]["api_key"].startswith("••") and s["llm"]["api_key"].endswith("42") and "secret" not in s["llm"]["api_key"]
    assert s["narrator"]["voice"] == "storyteller" and len(s["voices"]) == 9 and s["storage"]
    # a partial PUT changes only what it names; the masked key sent back is ignored
    s2 = c.put("/api/settings", json={"narrator": {"voice": "gazette", "min_events": "5"}, "llm": {"api_key": s["llm"]["api_key"]}, "web": {"port": 1}}).json()
    assert s2["narrator"]["voice"] == "gazette" and s2["narrator"]["min_events"] == 5 and s2["llm"]["api_key"].endswith("42")
    assert cfg["llm"]["api_key"] == "sk-secret-42" and cfg["narrator"]["voice"] == "gazette" and cfg["web"]["port"] != 1
    assert (cfg["root"] / "config.local.yaml").exists() and "gazette" in (cfg["root"] / "config.local.yaml").read_text()
    assert c.get("/api/status").json()["default_voice"] == "gazette"


def test_timeline_and_people_endpoints(cfg, bridge, llm):
    bridge.add("incident", "Flashstorm", cell=[30, 30], data={"def": "Flashstorm"})
    eng = make_engine(cfg, bridge, llm)
    c = TestClient(create_app(eng))
    assert c.get("/api/chronicles/nope/timeline").status_code == 404
    assert c.get("/api/chronicles/nope/people").status_code == 404
    eng.step()
    gid = eng.watcher.chronicle.id
    t = c.get(f"/api/chronicles/{gid}/timeline").json()
    kinds = {x["kind"] for x in t["items"]}
    assert {"event", "state", "frame", "chapter"} <= kinds
    assert t["next"] == len(t["items"]) == t["overview"]["records"] and t["overview"]["kinds"]["chapter"] == 1
    assert [x["i"] for x in t["items"]] == list(range(1, t["next"] + 1))
    ev = next(x for x in t["items"] if x["kind"] == "event")
    assert ev["text"] == "Flashstorm" and ev["day"] == 3 and ev["cell"] == [30, 30]
    fr = [x for x in t["items"] if x["kind"] == "frame"]
    assert {"incident", "base"} <= {x["shot"] for x in fr} and all(x["file"].endswith(".jpg") for x in fr)
    ch = next(x for x in t["items"] if x["kind"] == "chapter")
    assert ch["k"] == 1 and ch["voice"] == "storyteller"
    # paging with since, and a kinds filter
    t2 = c.get(f"/api/chronicles/{gid}/timeline", params={"since": t["next"]}).json()
    assert t2["items"] == [] and t2["next"] == t["next"]
    first = c.get(f"/api/chronicles/{gid}/timeline", params={"limit": 2}).json()
    rest = c.get(f"/api/chronicles/{gid}/timeline", params={"since": first["next"]}).json()
    assert len(first["items"]) == 2 and len(first["items"]) + len(rest["items"]) == len(t["items"])
    sel = c.get(f"/api/chronicles/{gid}/timeline", params={"kinds": "frame,chapter"}).json()
    assert {x["kind"] for x in sel["items"]} == {"frame", "chapter"}
    series = c.get(f"/api/chronicles/{gid}/timeline/series").json()
    assert series and series[0]["kind"] == "state" and series[0]["mood_avg"] == 55 and series[0]["day"] == 3
    people = c.get(f"/api/chronicles/{gid}/people").json()
    assert set(people["colonists"]) == {"Kena", "Lumi", "Kat"} and people["fallen"] == {}
    assert people["colonists"]["Lumi"]["traits"] == ["Abrasive", "Wimp"] and people["colonists"]["Kat"]["adulthood"] == "Scholar"
    # the chronicle endpoint hides the prompt inputs but keeps the title and liveness
    full = c.get(f"/api/chronicles/{gid}").json()
    assert "inputs" not in full["chapters"][0] and full["title"] == "Aswell" and full["live"] and full["days"] == 3
    # every frame the timeline names is served
    for x in fr:
        assert c.get(f"/api/chronicles/{gid}/frames/{x['file']}").status_code == 200


def test_write_options_and_chronicle_settings(cfg, bridge, llm):
    eng = make_engine(cfg, bridge, llm)
    c = TestClient(create_app(eng))
    eng.step()
    gid = eng.watcher.chronicle.id
    chron = eng.watcher.chronicle
    assert chron.chapters[0].voice == "storyteller"
    # the settings PUT changes the default voice for the next chapter
    c.put("/api/settings", json={"narrator": {"voice": "gazette"}})
    assert c.post(f"/api/chronicles/{gid}/write").json()["ok"]
    eng.step()
    assert len(chron.chapters) == 2 and chron.chapters[1].voice == "gazette"
    # a JSON body picks a voice and a focus for one chapter
    r = c.post(f"/api/chronicles/{gid}/write", json={"voice": "noir", "focus": "the roof"})
    assert r.status_code == 200 and r.json()["voice"] == "noir"
    eng.step()
    ch = chron.chapters[2]
    assert ch.voice == "noir" and "the roof" in ch.inputs["user_text"]
    assert llm.temperatures[-1] == 0.8
    assert c.post(f"/api/chronicles/{gid}/write", json={"voice": "nope"}).status_code == 422
    # per-chronicle settings: voice override and directive
    r = c.put(f"/api/chronicles/{gid}/settings", json={"voice": "saga", "directive": "Mind the animals."})
    assert r.json() == {"ok": True, "voice": "saga", "diarist": "", "directive": "Mind the animals."}
    assert chron.voice == "saga" and eng.store.load(gid).directive == "Mind the animals."
    assert c.get("/api/status").json()["voice"] == "saga" and c.get("/api/status").json()["default_voice"] == "gazette"
    assert c.post(f"/api/chronicles/{gid}/write").json()["ok"]
    eng.step()
    assert chron.chapters[3].voice == "saga" and "Mind the animals." in llm.calls[-1][0]["content"]
    assert c.put(f"/api/chronicles/{gid}/settings", json={"voice": "nope"}).status_code == 422
    assert c.put("/api/chronicles/nope/settings", json={"voice": "saga"}).status_code == 404
    c.put(f"/api/chronicles/{gid}/settings", json={"voice": ""})
    assert chron.voice == "" and c.get(f"/api/chronicles/{gid}").json()["voice"] == ""


def test_rewrite_keeps_versions(cfg, bridge, llm):
    eng = make_engine(cfg, bridge, llm)
    c = TestClient(create_app(eng))
    eng.step()
    gid = eng.watcher.chronicle.id
    assert c.post(f"/api/chronicles/{gid}/chapters/1/rewrite", json={"voice": "nope"}).status_code == 422
    assert c.post(f"/api/chronicles/{gid}/chapters/9/rewrite", json={"voice": "saga"}).status_code == 404
    assert c.post("/api/chronicles/nope/chapters/1/rewrite", json={"voice": "saga"}).status_code == 404
    r = c.post(f"/api/chronicles/{gid}/chapters/1/rewrite", json={"voice": "saga"})
    assert r.status_code == 200 and r.json() == {"ok": True, "k": 1, "voice": "saga"}
    eng.step()
    ch = c.get(f"/api/chronicles/{gid}").json()["chapters"][0]
    assert ch["voice"] == "saga" and len(ch["versions"]) == 1 and ch["versions"][0]["voice"] == "storyteller"
    assert llm.temperatures[-1] == 0.8
    assert len(c.get(f"/api/chronicles/{gid}").json()["chapters"]) == 1
    # a chronicle that is not the live game can still be rewritten (it is loaded from the store)
    bridge.new_game("second-seed", 777)
    eng.step()
    assert eng.watcher.chronicle.id == "second-seed-777"
    assert c.post(f"/api/chronicles/{gid}/chapters/1/rewrite", json={"voice": "noir"}).status_code == 200
    eng.step()
    ch = c.get(f"/api/chronicles/{gid}").json()["chapters"][0]
    assert ch["voice"] == "noir" and [v["voice"] for v in ch["versions"]] == ["storyteller", "saga"]


def test_export_has_people_and_days(cfg, bridge, llm):
    eng = make_engine(cfg, bridge, llm)
    c = TestClient(create_app(eng))
    eng.step()
    gid = eng.watcher.chronicle.id
    html = render_book(eng.store.load(gid), eng.store)
    assert "The people of Aswell" in html and "Kena" in html and "Caravan child, then bounty hunter." in html and "Abrasive" in html
    assert "<h1>Aswell</h1>" in html and "of the faction Anditeria, seed test-seed" in html
    assert "The Storyteller" in html
    assert 'id="days"' in html and "<figcaption>day 3</figcaption>" in html
    book = c.get(f"/api/chronicles/{gid}/book")
    assert book.status_code == 200 and "The people of Aswell" in book.text


def test_cli_voices(capsys):
    assert cli_main(["voices"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 9 and "chronicler" in out and "The Skald" in out
