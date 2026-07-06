"""Render a tmux text snapshot into a Feishu-friendly PNG image.

The renderer intentionally depends only on Pillow plus fonts that are either
bundled with the CLI or already present on the machine. Terminal column
placement stays separate from glyph availability: text is positioned on a
monospace grid, and wide CJK glyphs use a CJK fallback font when available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
import math
import os
from pathlib import Path
import re
import unicodedata


ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)

FONT_ENV = "INTERN_SCREENSHOT_FONT"
CJK_FONT_ENV = "INTERN_SCREENSHOT_CJK_FONT"
TITLE_FONT_ENV = "INTERN_SCREENSHOT_TITLE_FONT"

CLI_ROOT = Path(__file__).resolve().parents[2]
RESOURCE_FONT_DIR = CLI_ROOT / "resources" / "fonts"
BUNDLED_CJK_FONT = RESOURCE_FONT_DIR / "NotoSansSC-Regular.otf"

MONO_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansMonoCJKsc-Regular.otf",
)

CJK_FONT_CANDIDATES = (
    str(BUNDLED_CJK_FONT),
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansMonoCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
)

TITLE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


@dataclass(frozen=True)
class ScreenshotRenderOptions:
    max_columns: int = 160
    max_lines: int = 80
    font_size: int = 16
    title_font_size: int = 16
    padding_x: int = 22
    padding_y: int = 18
    title_height: int = 50
    min_width: int = 720
    max_width: int = 1920


def _first_existing_font(env_name: str, candidates: tuple[str, ...]) -> str:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        raise RuntimeError(f"{env_name} points to missing font: {configured}")
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise RuntimeError(
        "no usable screenshot font found; checked: " + ", ".join(candidates)
    )


def _first_existing_font_optional(
    env_name: str,
    candidates: tuple[str, ...],
) -> str | None:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        raise RuntimeError(f"{env_name} points to missing font: {configured}")
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def resolve_cjk_font_path() -> str | None:
    """Return the CJK fallback font path that screenshot rendering will use."""
    return _first_existing_font_optional(CJK_FONT_ENV, CJK_FONT_CANDIDATES)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def sanitize_terminal_text(text: str) -> str:
    text = strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category.startswith("C") and ch != "\u200d":
            continue
        out.append(ch)
    return "".join(out).expandtabs(4)


def display_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            width += 2
        else:
            width += 1
    return width


def _truncate_to_columns(line: str, max_columns: int) -> tuple[str, bool]:
    if display_width(line) <= max_columns:
        return line, False
    limit = max(1, max_columns - 1)
    width = 0
    out = []
    for ch in line:
        ch_width = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        )
        if width + ch_width > limit:
            break
        out.append(ch)
        width += ch_width
    return "".join(out) + "…", True


def prepare_terminal_lines(
    snapshot: str,
    *,
    max_columns: int,
    max_lines: int,
) -> tuple[list[str], dict[str, int | bool]]:
    cleaned = sanitize_terminal_text(snapshot).rstrip("\n")
    raw_lines = cleaned.splitlines() or ["(tmux pane is empty)"]
    total_lines = len(raw_lines)
    omitted_lines = max(0, total_lines - max_lines)
    if omitted_lines:
        raw_lines = raw_lines[-max_lines:]
    rendered = []
    truncated_lines = 0
    for line in raw_lines:
        clipped, truncated = _truncate_to_columns(line.rstrip(), max_columns)
        rendered.append(clipped)
        if truncated:
            truncated_lines += 1
    return rendered, {
        "total_lines": total_lines,
        "omitted_lines": omitted_lines,
        "truncated_lines": truncated_lines,
    }


def _is_wide_glyph(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("F", "W")


def _draw_text_by_terminal_columns(
    draw,
    xy,
    text,
    *,
    font,
    cjk_font,
    fill,
    cell_width,
):
    x0, y = xy
    col = 0
    for ch in text:
        draw_font = cjk_font if cjk_font is not None and _is_wide_glyph(ch) else font
        if unicodedata.combining(ch):
            draw.text((x0 + col * cell_width, y), ch, font=draw_font, fill=fill)
            continue
        draw.text((x0 + col * cell_width, y), ch, font=draw_font, fill=fill)
        col += 2 if _is_wide_glyph(ch) else 1


def _truncate_to_pixel_width(text: str, *, font, max_width: int) -> str:
    if max_width <= 0 or font.getlength(text) <= max_width:
        return text
    marker = "…"
    marker_width = font.getlength(marker)
    if marker_width > max_width:
        return ""

    lo = 0
    hi = len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.getlength(text[:mid]) + marker_width <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + marker


def render_tmux_screenshot_png(
    *,
    intern_name: str,
    project: str | None,
    snapshot: str,
    captured_at: datetime | None = None,
    options: ScreenshotRenderOptions | None = None,
) -> bytes:
    """Return PNG bytes for the supplied tmux snapshot.

    Raises RuntimeError when Pillow or fonts are unavailable. Callers should
    surface that error to the supervisor instead of silently hiding it.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # pragma: no cover - depends on runtime image
        raise RuntimeError(f"Pillow unavailable for screenshot rendering: {exc}") from exc

    opts = options or ScreenshotRenderOptions()
    font_path = _first_existing_font(FONT_ENV, MONO_FONT_CANDIDATES)
    cjk_font_path = resolve_cjk_font_path()
    title_font_path = _first_existing_font(TITLE_FONT_ENV, TITLE_FONT_CANDIDATES)
    font = ImageFont.truetype(font_path, opts.font_size)
    cjk_font = ImageFont.truetype(cjk_font_path, opts.font_size) if cjk_font_path else None
    title_font = ImageFont.truetype(title_font_path, opts.title_font_size)

    lines, meta = prepare_terminal_lines(
        snapshot,
        max_columns=opts.max_columns,
        max_lines=opts.max_lines,
    )
    max_line_width = max(display_width(line) for line in lines) if lines else 1
    cell_width = max(8, math.ceil(font.getlength("M")))
    font_bbox = font.getbbox("Mg")
    text_height = math.ceil(font_bbox[3] - font_bbox[1])
    if cjk_font is not None:
        cjk_bbox = cjk_font.getbbox("中文")
        text_height = max(text_height, math.ceil(cjk_bbox[3] - cjk_bbox[1]))
    line_height = max(opts.font_size + 7, text_height + 6)
    gutter_digits = len(str(meta["total_lines"]))
    gutter_width = max(38, gutter_digits * cell_width + 18)

    content_width = gutter_width + max_line_width * cell_width + opts.padding_x * 2
    width = min(opts.max_width, max(opts.min_width, content_width))
    content_height = len(lines) * line_height + opts.padding_y * 2
    if meta["omitted_lines"]:
        content_height += line_height
    height = opts.title_height + content_height

    img = Image.new("RGB", (width, height), "#0f172a")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=18, fill="#020617")
    draw.rounded_rectangle([0, 0, width - 1, opts.title_height], radius=18, fill="#111827")
    draw.rectangle([0, opts.title_height - 18, width - 1, opts.title_height], fill="#111827")
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=18, outline="#334155", width=2)

    dot_y = 22
    for i, color in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        x = 22 + i * 18
        draw.ellipse([x, dot_y - 6, x + 12, dot_y + 6], fill=color)

    scope = f"{intern_name} / {project}" if project else intern_name
    ts = captured_at or datetime.now(timezone.utc)
    title = (
        f"tmux screenshot: {scope}  •  "
        f"{meta['total_lines']} lines  •  {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    if meta["omitted_lines"] or meta["truncated_lines"]:
        title += f"  •  cropped {meta['omitted_lines']} lines, {meta['truncated_lines']} wide lines"
    title = _truncate_to_pixel_width(
        title,
        font=title_font,
        max_width=width - 86 - opts.padding_x,
    )
    draw.text((86, 15), title, font=title_font, fill="#e5e7eb")

    body_top = opts.title_height + opts.padding_y
    line_no_start = int(meta["omitted_lines"]) + 1
    if meta["omitted_lines"]:
        msg = f"… omitted {meta['omitted_lines']} earlier lines …"
        draw.text((opts.padding_x, body_top), msg, font=font, fill="#94a3b8")
        body_top += line_height

    text_x = opts.padding_x + gutter_width
    for idx, line in enumerate(lines):
        y = body_top + idx * line_height
        line_no = str(line_no_start + idx).rjust(gutter_digits)
        draw.text((opts.padding_x, y), line_no, font=font, fill="#64748b")
        _draw_text_by_terminal_columns(
            draw,
            (text_x, y),
            line,
            font=font,
            cjk_font=cjk_font,
            fill="#e5e7eb",
            cell_width=cell_width,
        )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
