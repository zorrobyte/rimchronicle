"""Image handling: JPEG encoding for storage, data URLs for the narrator, and an optional light overlay.

The overlay is a trimmed-down cousin of a Set-of-Mark annotator: no numbered marks, only a faint
10-cell grid and anchor name labels. Both are off by default because a clean frame reads best in a book.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def overlay(png: bytes, cx: int, cz: int, cells_wide: float, anchors: list[dict[str, Any]] | None = None, grid: bool = False) -> bytes:
    """Return PNG bytes with a faint 10-cell grid and/or anchor labels drawn on top."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    wpx, hpx = img.size
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cell = wpx / cells_wide

    def px(x: float) -> float:
        return wpx / 2 + (x - (cx + 0.5)) * cell

    def py(z: float) -> float:
        return hpx / 2 - (z - (cz + 0.5)) * cell

    if grid:
        x0 = int(cx - cells_wide / 2) - 1
        x1 = int(cx + cells_wide / 2) + 2
        z0 = int(cz - cells_wide * hpx / wpx / 2) - 1
        z1 = int(cz + cells_wide * hpx / wpx / 2) + 2
        for x in range(x0, x1):
            if x % 10 == 0:
                d.line([(px(x), 0), (px(x), hpx)], fill=(255, 255, 255, 40), width=1)
        for z in range(z0, z1):
            if z % 10 == 0:
                d.line([(0, py(z)), (wpx, py(z))], fill=(255, 255, 255, 40), width=1)
    f = _font(max(11, int(cell * 0.9)))
    for a in anchors or []:
        rect = a.get("rect") or {}
        mn, mx = rect.get("min"), rect.get("max")
        name = a.get("name")
        if not (mn and mx and name):
            continue
        try:
            d.rectangle([px(mn[0]), py(mx[1] + 1), px(mx[0] + 1), py(mn[1])], outline=(255, 255, 255, 110), width=1)
            tw = d.textlength(name, font=f)
            tx, ty = px(mn[0]) + 3, py(mx[1] + 1) + 2
            d.rectangle([tx - 2, ty - 1, tx + tw + 2, ty + f.size + 1], fill=(0, 0, 0, 120))
            d.text((tx, ty), name, fill=(255, 255, 255, 230), font=f)
        except Exception:  # noqa: BLE001
            continue
    out = Image.alpha_composite(img, layer).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def to_jpeg(data: bytes, quality: int = 85, max_width: int | None = None) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max_width and img.width > max_width:
        h = round(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def data_url(jpeg: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(jpeg).decode("ascii")


def tiny_png(width: int = 64, height: int = 48, color: tuple[int, int, int] = (70, 90, 60)) -> bytes:
    """A small generated PNG, used by the tests' fake bridge."""
    img = Image.new("RGB", (width, height), color)
    d = ImageDraw.Draw(img)
    d.rectangle([width // 4, height // 4, 3 * width // 4, 3 * height // 4], fill=(120, 100, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
