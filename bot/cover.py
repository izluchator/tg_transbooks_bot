"""Generate cover images for translated books."""

import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

COVER_WIDTH = 1600
COVER_HEIGHT = 2400
BOT_LINK = "t.me/tg_transbooks_bot"
BOT_HANDLE = "@tg_transbooks_bot"

# Try loading a good font, fall back to default
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]

_FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _find_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _draw_gradient(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Draw a dark blue to near-black vertical gradient."""
    for y in range(height):
        ratio = y / height
        r = int(15 * (1 - ratio))
        g = int(25 * (1 - ratio) + 10 * ratio)
        b = int(80 * (1 - ratio) + 20 * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _wrap_text(text: str, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = font.getbbox(test)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    return lines or [text]


def generate_cover(
    title: str,
    author: str,
    output_path: str | Path,
) -> Path:
    """Generate a styled cover image for EPUB with translated title and branding."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT))
    draw = ImageDraw.Draw(img)

    # Background gradient
    _draw_gradient(draw, COVER_WIDTH, COVER_HEIGHT)

    # Fonts
    title_font = _find_font(_FONT_PATHS, 90)
    author_font = _find_font(_FONT_PATHS_REGULAR, 48)
    brand_font = _find_font(_FONT_PATHS_REGULAR, 36)
    small_font = _find_font(_FONT_PATHS_REGULAR, 30)

    margin = 120
    usable_width = COVER_WIDTH - margin * 2

    # --- Title ---
    title_lines = _wrap_text(title.upper(), title_font, usable_width)
    y = 600
    for line in title_lines[:6]:  # max 6 lines
        bbox = title_font.getbbox(line)
        w = bbox[2] - bbox[0]
        x = (COVER_WIDTH - w) // 2
        draw.text((x, y), line, fill=(255, 255, 255), font=title_font)
        y += bbox[3] - bbox[1] + 20

    # --- Decorative line ---
    line_y = y + 50
    line_margin = 300
    draw.line(
        [(line_margin, line_y), (COVER_WIDTH - line_margin, line_y)],
        fill=(180, 180, 220),
        width=2,
    )

    # --- Author ---
    if author:
        author_y = line_y + 60
        bbox = author_font.getbbox(author)
        w = bbox[2] - bbox[0]
        x = (COVER_WIDTH - w) // 2
        draw.text((x, author_y), author, fill=(200, 200, 220), font=author_font)

    # --- Branding at bottom ---
    brand_y = COVER_HEIGHT - 300
    # Translated by line
    trans_text = "Переведено с помощью"
    bbox = brand_font.getbbox(trans_text)
    w = bbox[2] - bbox[0]
    draw.text(
        ((COVER_WIDTH - w) // 2, brand_y),
        trans_text,
        fill=(140, 140, 170),
        font=brand_font,
    )
    # Bot handle
    handle_y = brand_y + 55
    bbox = brand_font.getbbox(BOT_HANDLE)
    w = bbox[2] - bbox[0]
    draw.text(
        ((COVER_WIDTH - w) // 2, handle_y),
        BOT_HANDLE,
        fill=(100, 160, 255),
        font=brand_font,
    )
    # URL
    url_y = handle_y + 50
    bbox = small_font.getbbox(BOT_LINK)
    w = bbox[2] - bbox[0]
    draw.text(
        ((COVER_WIDTH - w) // 2, url_y),
        BOT_LINK,
        fill=(100, 120, 160),
        font=small_font,
    )

    img.save(str(output_path), "PNG", quality=95)
    logger.info("Cover generated: %s", output_path)
    return output_path
