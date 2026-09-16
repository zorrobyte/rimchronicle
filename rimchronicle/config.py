"""Configuration: config.yaml (generic, shipped) deep-merged with config.local.yaml (private, gitignored)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "bridge": {"url": "http://127.0.0.1:8765", "poll_seconds": 2, "state_refresh_seconds": 20, "timeout_s": 15},
    "llm": {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "Qwen/Qwen3-VL-32B",
        "api_key": "not-needed",
        "timeout_s": 240,
        "max_tokens": 1200,
        "temperature": 0.7,
        "disable_thinking": True,
    },
    "narrator": {
        "min_events": 4,
        "max_hours_between": 24,
        "min_real_seconds_between": 120,
        "opening_chapter": True,
        "base_width_cells": 50,
        "event_width_cells": 30,
        "label_anchors": False,
        "grid": False,
    },
    "overseer": {"enabled": True, "url": "http://127.0.0.1:8770", "poll_seconds": 5},
    "web": {"host": "127.0.0.1", "port": 8771},
    "storage": {"dir": "chronicles"},
}


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def project_root() -> Path:
    env = os.environ.get("RIMCHRONICLE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def load_config(root: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    cfg = copy.deepcopy(DEFAULTS)
    for name in ("config.yaml", "config.local.yaml"):
        p = root / name
        if p.exists():
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{p} must contain a mapping at the top level")
            cfg = deep_merge(cfg, data)
    store = Path(cfg["storage"]["dir"])
    if not store.is_absolute():
        store = root / store
    cfg["storage"]["path"] = store
    cfg["root"] = root
    return cfg
