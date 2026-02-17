import logging
import re
from pathlib import Path
import markdown as md_lib
from weasyprint import HTML
from ebooklib import epub
from bot.config import TEMPLATES_DIR

logger = logging.getLogger(__name__)

BOT_LINK = "https://t.me/tg_transbooks_bot"
BOT_HANDLE = "@tg_transbooks_bot"


def _load_css() -> str:
    css_path = TEMPLATES_DIR / "book.css"
    if css_path.exists():
        return css_path.read_text(encoding="utf-8")
    return ""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <style>{css}</style>
</head>
<body>
{content}
</body>
</html>"""


def _md_to_html(md_text: str) -> str:
    return md_lib.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc"],
    )


# ---------------------------------------------------------------------------
# Title page & colophon HTML
# ---------------------------------------------------------------------------

def _title_page_html(title: str, author: str) -> str:
    author_block = f'<p class="title-author">{author}</p>' if author else ""
    return (
        '<div class="title-page">'
        f'<h1 class="title-main">{title}</h1>'
        f'{author_block}'
        '<div class="title-separator"></div>'
        '<p class="title-translated">Переведено с помощью</p>'
        f'<p class="title-bot"><a href="{BOT_LINK}">{BOT_HANDLE}</a></p>'
        '</div>'
    )


def _colophon_html() -> str:
    return (
        '<div class="colophon">'
        '<div class="colophon-content">'
        '<p class="colophon-heading">📖 О переводе</p>'
        '<p>Этот перевод выполнен автоматически с помощью AI.</p>'
        '<p class="colophon-separator"></p>'
        '<p>Телеграм-бот для перевода книг:</p>'
        f'<p class="colophon-bot"><a href="{BOT_LINK}">{BOT_HANDLE}</a></p>'
        f'<p class="colophon-link">{BOT_LINK}</p>'
        '</div>'
        '</div>'
    )


def _cover_page_html(cover_image_path: Path) -> str:
    abs_path = str(cover_image_path.resolve())
    return (
        '<div class="cover-page">'
        f'<img src="{abs_path}" alt="Cover" class="cover-image"/>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def markdown_to_pdf(
    md_text: str,
    output_path: str | Path,
    image_dir: Path | None = None,
    cover_image_path: Path | None = None,
    title: str = "",
    author: str = "",
) -> Path:
    """Convert Markdown text to a styled PDF file with cover, title page, and colophon."""
    output_path = Path(output_path)
    logger.info("Generating PDF: %s", output_path)

    html_content = _md_to_html(md_text)
    css = _load_css()

    # Build full HTML with optional cover + title page + colophon
    parts: list[str] = []

    if cover_image_path and cover_image_path.exists():
        parts.append(_cover_page_html(cover_image_path))

    if title:
        parts.append(_title_page_html(title, author))

    parts.append(html_content)
    parts.append(_colophon_html())

    combined = "\n".join(parts)
    full_html = HTML_TEMPLATE.format(css=css, content=combined)

    base_url = str(image_dir.resolve()) + "/" if image_dir else None

    weasy_doc = HTML(string=full_html, base_url=base_url).render()

    # Set PDF metadata
    metadata = weasy_doc.metadata
    if title:
        metadata.title = title
    if author:
        metadata.authors = [author]
    metadata.generator = f"TransBooks Bot ({BOT_HANDLE})"

    weasy_doc.write_pdf(str(output_path))

    logger.info("PDF generated: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


# ---------------------------------------------------------------------------
# EPUB generation
# ---------------------------------------------------------------------------

def markdown_to_epub(
    md_text: str,
    output_path: str | Path,
    title: str = "Перевод",
    author: str = "",
    image_dir: Path | None = None,
    cover_image_path: Path | None = None,
) -> Path:
    """Convert Markdown text to an EPUB file with cover, metadata, and colophon."""
    output_path = Path(output_path)
    logger.info("Generating EPUB: %s", output_path)

    book = epub.EpubBook()
    book.set_identifier("transbooks-bot-translation")
    book.set_title(title)
    book.set_language("ru")
    book.add_author(author or "TransBooks Bot")

    # Publisher & description metadata
    book.add_metadata("DC", "publisher", f"TransBooks Bot ({BOT_HANDLE})")
    book.add_metadata(
        "DC", "description",
        f"Перевод выполнен с помощью {BOT_HANDLE} — {BOT_LINK}",
    )

    css = _load_css()
    style = epub.EpubItem(
        uid="style", file_name="style/book.css",
        media_type="text/css", content=css.encode("utf-8"),
    )
    book.add_item(style)

    # Set cover image
    if cover_image_path and cover_image_path.exists():
        cover_data = cover_image_path.read_bytes()
        book.set_cover("cover.png", cover_data, create_page=True)
        logger.info("EPUB cover set from: %s", cover_image_path)

    # Collect and embed content images
    image_items: dict[str, str] = {}
    if image_dir and image_dir.exists():
        for img_file in sorted(image_dir.iterdir()):
            if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"):
                epub_img_path = f"images/{img_file.name}"
                media_type = _guess_media_type(img_file.suffix)
                img_item = epub.EpubItem(
                    uid=f"img_{img_file.stem}",
                    file_name=epub_img_path,
                    media_type=media_type,
                    content=img_file.read_bytes(),
                )
                book.add_item(img_item)
                image_items[str(img_file.resolve())] = epub_img_path
                logger.info("Added EPUB image: %s", epub_img_path)

    if image_items:
        md_text = _rewrite_paths_for_epub(md_text, image_items)

    # Split markdown into chapters
    chapters = _split_into_chapters(md_text)
    spine = ["nav"]
    toc = []

    for i, (ch_title, ch_md) in enumerate(chapters):
        html_content = _md_to_html(ch_md)
        ch = epub.EpubHtml(
            title=ch_title, file_name=f"chapter_{i}.xhtml", lang="ru",
        )
        ch.content = f"<html><body>{html_content}</body></html>"
        ch.add_item(style)
        book.add_item(ch)
        spine.append(ch)
        toc.append(ch)

    # Colophon chapter
    colophon_ch = epub.EpubHtml(
        title="О переводе", file_name="colophon.xhtml", lang="ru",
    )
    colophon_ch.content = f"<html><body>{_colophon_html()}</body></html>"
    colophon_ch.add_item(style)
    book.add_item(colophon_ch)
    spine.append(colophon_ch)

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(output_path), book)

    logger.info("EPUB generated: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


def _rewrite_paths_for_epub(md_text: str, image_items: dict[str, str]) -> str:
    """Replace absolute image paths with EPUB-relative paths."""
    for abs_path, epub_path in image_items.items():
        md_text = md_text.replace(abs_path, epub_path)
    return md_text


def _guess_media_type(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }.get(suffix.lower(), "image/png")


def _split_into_chapters(md_text: str) -> list[tuple[str, str]]:
    """Split markdown by H1/H2 headings into (title, content) pairs."""
    lines = md_text.split("\n")
    chapters: list[tuple[str, str]] = []
    current_title = "Начало"
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{1,2}\s+", line):
            if current_lines:
                chapters.append((current_title, "\n".join(current_lines)))
            current_title = re.sub(r"^#{1,2}\s+", "", line).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        chapters.append((current_title, "\n".join(current_lines)))

    return chapters if chapters else [("Перевод", md_text)]
