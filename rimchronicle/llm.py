"""OpenAI-compatible chat client with vision. The narrator sends text plus image_url data URLs."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("rimchronicle.llm")


@dataclass
class Reply:
    content: str = ""
    reasoning: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0


class ChatLike(Protocol):
    def chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None, temperature: float | None = None) -> Reply: ...


class LLM:
    def __init__(self, cfg: dict[str, Any]):
        from openai import OpenAI  # imported lazily so the tests never need network-capable clients

        self.model = cfg["model"]
        self.max_tokens = int(cfg.get("max_tokens", 1200))
        self.temperature = float(cfg.get("temperature", 0.7))
        self.disable_thinking = bool(cfg.get("disable_thinking", True))
        self.client = OpenAI(base_url=cfg["base_url"], api_key=cfg.get("api_key") or "not-needed", timeout=float(cfg.get("timeout_s", 240)), max_retries=0)

    def chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None, temperature: float | None = None) -> Reply:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature if temperature is None else float(temperature),
        }
        if self.disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        t0 = time.time()
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        raw = msg.model_dump()
        reply = Reply(content=msg.content or "", elapsed=time.time() - t0)
        reply.reasoning = raw.get("reasoning_content") or raw.get("reasoning") or ""
        if resp.usage:
            reply.usage = resp.usage.model_dump()
        return reply


def image_part(url: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}}


def text_part(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
