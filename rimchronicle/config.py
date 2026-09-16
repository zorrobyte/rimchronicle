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
        "voice": "storyteller",        # see rimchronicle/voices.py; a chronicle can override it
        "custom_prompt": "",           # the system prompt used by the "custom" voice
        "directive": "",               # author directive appended to every chapter prompt
        "min_events": 4,
        "max_hours_between": 24,
        "min_real_seconds_between": 120,
        "opening_chapter": True,
        "base_width_cells": 50,
        "event_width_cells": 30,
        "moments_per_chapter": 3,      # camera moments attached to a chapter besides the wide shot
        "saga_every": 8,               # roll older chapter summaries into "the story so far" every N chapters
        "dossier_colonists": 12,       # colonists described in full in the prompt; the rest get one line
        "label_anchors": False,
        "grid": False,
    },
    "camera": {
        "moments": True,               # shoot the cell where a notable event happens, as it happens
        "follow_fight": True,          # extra frames on the hostiles while a raid is on the map
        "daily": True,                 # one wide shot of the base per in-game day (the time-lapse)
        "portraits": True,             # a close shot of each colonist once per in-game day
        "max_per_chapter": 8,          # moments between chapters, beyond which the camera rests
        "debounce_seconds": 10,        # real seconds between moments of the same kind
        "moment_width_cells": 24,
        "portrait_width_cells": 8,
        "frame_max_px": 1024,
    },
    "timeline": {"state_every_seconds": 60},
    "overseer": {"enabled": False, "url": "http://127.0.0.1:8770", "poll_seconds": 5},
    "web": {"host": "127.0.0.1", "port": 8771},
    "storage": {"dir": "chronicles"},
}

# Sections and keys the settings UI may change (everything else is file-only).
SETTINGS_KEYS: dict[str, tuple[str, ...]] = {
    "llm": ("base_url", "model", "api_key", "timeout_s", "max_tokens", "temperature", "disable_thinking"),
    "narrator": ("voice", "custom_prompt", "directive", "min_events", "max_hours_between", "min_real_seconds_between", "opening_chapter",
                 "base_width_cells", "event_width_cells", "moments_per_chapter", "saga_every", "dossier_colonists", "label_anchors", "grid"),
    "camera": ("moments", "follow_fight", "daily", "portraits", "max_per_chapter", "debounce_seconds", "moment_width_cells", "portrait_width_cells", "frame_max_px"),
    "overseer": ("enabled", "url", "poll_seconds"),
    "bridge": ("url", "poll_seconds", "state_refresh_seconds", "timeout_s"),
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


def filter_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keys the settings UI is allowed to change, coerced to the default's type."""
    out: dict[str, Any] = {}
    for section, keys in SETTINGS_KEYS.items():
        sub = patch.get(section)
        if not isinstance(sub, dict):
            continue
        for k in keys:
            if k not in sub:
                continue
            v = sub[k]
            ref = DEFAULTS[section].get(k)
            try:
                if isinstance(ref, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(ref, int) and not isinstance(ref, bool):
                    v = int(float(v))
                elif isinstance(ref, float):
                    v = float(v)
                elif isinstance(ref, str) or ref is None:
                    v = "" if v is None else str(v)
            except (TypeError, ValueError):
                continue
            out.setdefault(section, {})[k] = v
    return out


def save_settings(root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `patch` into config.local.yaml (created if missing) and return the new effective config."""
    p = root / "config.local.yaml"
    current: dict[str, Any] = {}
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            current = yaml.safe_load(fh) or {}
        if not isinstance(current, dict):
            current = {}
    merged = deep_merge(current, patch)
    tmp = p.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("# Private overrides (gitignored). Same structure as config.yaml; deep-merged on top of it.\n")
        fh.write("# The settings page of the reader writes this file.\n")
        yaml.safe_dump(merged, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(p)
    return load_config(root)
