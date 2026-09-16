from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from rimchronicle.engine import Engine
from rimchronicle.export import export_book, render_book
from rimchronicle.overseer import Overseer
from rimchronicle.web import create_app


def make_engine(cfg, bridge, llm):
    ov = Overseer("http://127.0.0.1:1", enabled=False)
    return Engine(cfg, bridge=bridge, llm=llm, overseer=ov)


def test_export_produces_html_with_inline_images(cfg, bridge, llm):
    bridge.add("incident", "Flashstorm", cell=[30, 30], data={"def": "Flashstorm"})
    eng = make_engine(cfg, bridge, llm)
    ch = eng.write_now()
    assert ch is not None
    gid = eng.watcher.chronicle.id
    out = export_book(eng.store, gid)
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "https://" not in html and "http://" not in html  # the downloaded book works offline
    assert html.count("data:image/jpeg;base64,") == 3  # two chapter pictures plus the day's wide shot in the "Days" contact sheet
    assert ch.title in html and "State of the colony" in html
    # the inline image really is the JPEG we stored
    b64 = html.split("data:image/jpeg;base64,")[1].split('"')[0]
    assert base64.b64decode(b64)[:3] == b"\xff\xd8\xff"
    assert render_book(eng.store.load(gid), eng.store) == html.replace(html[html.index("Exported "):html.index("Exported ") + 27], html[html.index("Exported "):html.index("Exported ") + 27])


def test_web_api_and_page(cfg, bridge, llm):
    eng = make_engine(cfg, bridge, llm)
    app = create_app(eng)
    c = TestClient(app)
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert c.get("/static/app.css").headers["cache-control"] == "no-cache"
    assert "RimChronicle" in c.get("/").text
    assert c.get("/api/chronicles").json() == []
    eng.step()  # warms the watcher; opening chapter is due and written in-loop
    lib = c.get("/api/chronicles").json()
    assert len(lib) == 1 and lib[0]["live"] and lib[0]["chapters"] == 1 and lib[0]["cover"].endswith("-base.jpg")
    gid = lib[0]["id"]
    full = c.get(f"/api/chronicles/{gid}").json()
    assert full["chapters"][0]["title"].startswith("Chapter 1") and full["live"]
    cover = lib[0]["cover"]
    img = c.get(f"/api/chronicles/{gid}/frames/{cover}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/chronicles/{gid}/frames/../chronicle.json").status_code in (404, 422)
    assert c.get(f"/api/chronicles/{gid}/images/../chronicle.json").status_code in (404, 422)
    book = c.get(f"/api/chronicles/{gid}/book")
    assert book.status_code == 200 and "data:image/jpeg;base64," in book.text
    assert c.get("/api/chronicles/nope").status_code == 404
    st = c.get("/api/status").json()
    assert st["online"] and st["chronicle"] == gid
    # write-now goes through the loop on the next step
    r = c.post(f"/api/chronicles/{gid}/write")
    assert r.status_code == 200 and r.json()["ok"]
    eng.step()
    assert len(c.get(f"/api/chronicles/{gid}").json()["chapters"]) == 2
    assert c.post("/api/chronicles/other/write").status_code == 409
