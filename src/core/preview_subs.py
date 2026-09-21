"""Compose softsubs onto preview frames (text ASS/SRT + bitmap PGS/DVD)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from av.subtitles.subtitle import AssSubtitle, BitmapSubtitle, SubtitleSet
from PIL import Image, ImageDraw, ImageFont

from src.core.logging_setup import get_logger

logger = get_logger("preview_subs")

_AV_TIME_BASE = 1_000_000.0  # PyAV SubtitleSet.pts is in av.time_base


@dataclass
class ActiveSubtitle:
    """One currently visible subtitle event."""

    end: float
    kind: str  # "text" | "bitmap"
    text: str = ""
    image: Image.Image | None = None
    x: int = 0
    y: int = 0
    src_w: int = 0
    src_h: int = 0


def subtitle_set_times(subset: SubtitleSet) -> tuple[float, float]:
    """Return (start_sec, end_sec) for a decoded SubtitleSet."""
    pts = float(subset.pts or 0) / _AV_TIME_BASE
    start = pts + float(subset.start_display_time or 0) / 1000.0
    end = pts + float(subset.end_display_time or 0) / 1000.0
    if end <= start:
        end = start + 2.0
    return start, end


def actives_from_subset(
    subset: SubtitleSet,
    *,
    src_w: int,
    src_h: int,
) -> list[ActiveSubtitle]:
    """Convert a SubtitleSet into overlay payloads (empty list = clear display)."""
    start, end = subtitle_set_times(subset)
    if not subset.rects:
        return []
    out: list[ActiveSubtitle] = []
    for rect in subset.rects:
        if isinstance(rect, AssSubtitle):
            dialogue = rect.dialogue or b""
            if isinstance(dialogue, bytes):
                text = dialogue.decode("utf-8", errors="replace").strip()
            else:
                text = str(dialogue).strip()
            if not text:
                continue
            out.append(
                ActiveSubtitle(
                    end=end,
                    kind="text",
                    text=text,
                    src_w=src_w,
                    src_h=src_h,
                )
            )
        elif isinstance(rect, BitmapSubtitle):
            img = bitmap_subtitle_to_rgba(rect)
            if img is None:
                continue
            out.append(
                ActiveSubtitle(
                    end=end,
                    kind="bitmap",
                    image=img,
                    x=int(rect.x),
                    y=int(rect.y),
                    src_w=src_w,
                    src_h=src_h,
                )
            )
    # Empty rects after a PGS clear still produce end time — treat as clear.
    if not out and subset.rects:
        return []
    # Stamp start unused; caller replaces active list when a new set arrives.
    _ = start
    return out


def bitmap_subtitle_to_rgba(bmp: BitmapSubtitle) -> Image.Image | None:
    """Convert a PyAV BitmapSubtitle (PGS / DVD / DVB) to an RGBA PIL image."""
    w, h = int(bmp.width), int(bmp.height)
    if w <= 0 or h <= 0 or not bmp.planes:
        return None
    try:
        idx = np.frombuffer(memoryview(bmp.planes[0]), dtype=np.uint8, count=w * h)
        idx = idx.reshape((h, w))
    except Exception:
        logger.debug("Could not read bitmap plane 0 (%sx%s)", w, h, exc_info=True)
        return None

    palette = _read_palette(bmp)
    rgba = palette[idx]
    # Index 0 is conventionally transparent for many PGS/DVD streams; also
    # respect fully transparent palette entries.
    return Image.fromarray(rgba, mode="RGBA")


def _read_palette(bmp: BitmapSubtitle) -> np.ndarray:
    """Return shape (256, 4) uint8 RGBA palette."""
    pal = np.zeros((256, 4), dtype=np.uint8)
    # Default: 0 transparent, others opaque white (readable if palette missing).
    pal[1:, :3] = 255
    pal[1:, 3] = 255

    if len(bmp.planes) >= 2:
        try:
            raw = memoryview(bmp.planes[1])
            n = min(len(raw), 1024)
            arr = np.frombuffer(raw[:n], dtype=np.uint8)
            colors = n // 4
            if colors > 0:
                chunk = arr[: colors * 4].reshape((colors, 4))
                pal[:colors] = chunk
        except Exception:
            logger.debug("Could not read bitmap palette plane", exc_info=True)
    return pal


def expire_actives(actives: list[ActiveSubtitle], t: float) -> list[ActiveSubtitle]:
    return [a for a in actives if a.end > t + 1e-3]


def composite_subtitles(
    frame: Image.Image,
    actives: list[ActiveSubtitle],
) -> Image.Image:
    """Alpha-composite active subtitle overlays onto *frame* (RGB or RGBA)."""
    if not actives:
        return frame
    base = frame.convert("RGBA")
    fw, fh = base.size
    for active in actives:
        if active.kind == "text" and active.text:
            _draw_text_overlay(base, active.text)
        elif active.kind == "bitmap" and active.image is not None:
            overlay = active.image
            src_w = active.src_w or fw
            src_h = active.src_h or fh
            scale_x = fw / float(src_w) if src_w else 1.0
            scale_y = fh / float(src_h) if src_h else 1.0
            ow = max(1, int(round(overlay.width * scale_x)))
            oh = max(1, int(round(overlay.height * scale_y)))
            if (ow, oh) != overlay.size:
                overlay = overlay.resize((ow, oh), Image.Resampling.BILINEAR)
            x = int(round(active.x * scale_x))
            y = int(round(active.y * scale_y))
            # Clip paste box
            if x >= fw or y >= fh or x + ow <= 0 or y + oh <= 0:
                continue
            base.alpha_composite(overlay, dest=(x, y))
    return base.convert(frame.mode) if frame.mode != "RGBA" else base


def _draw_text_overlay(base: Image.Image, text: str) -> None:
    """Simple bottom-centered softsub (not full libass)."""
    draw = ImageDraw.Draw(base)
    fw, fh = base.size
    font_size = max(14, min(36, fh // 16))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Soft wrap long lines
    lines = _wrap_text(text, font, draw, max_width=int(fw * 0.9))
    line_heights: list[int] = []
    line_widths: list[int] = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    block_h = sum(line_heights) + max(0, len(lines) - 1) * 4
    y = fh - block_h - max(12, fh // 24)
    for line, lw, lh in zip(lines, line_widths, line_heights):
        x = (fw - lw) // 2
        # Outline for contrast on light/dark video
        for dx, dy in (
            (-2, 0),
            (2, 0),
            (0, -2),
            (0, 2),
            (-1, -1),
            (1, -1),
            (-1, 1),
            (1, 1),
        ):
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += lh + 4


def _wrap_text(
    text: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    max_width: int,
) -> list[str]:
    raw_lines = text.replace("\r", "").split("\n")
    out: list[str] = []
    for raw in raw_lines:
        words = raw.split(" ")
        if not words:
            out.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = trial
            else:
                out.append(current)
                current = word
        out.append(current)
    return out or [text]
