import logging
from pathlib import Path
import pymupdf4llm
import fitz
from ebooklib import epub
import html2text

logger = logging.getLogger(__name__)

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = False
_h2t.body_width = 0


def extract_cover_image(file_path: str | Path, output_path: str | Path) -> Path | None:
    """Render first page of PDF as high-res PNG for use as cover."""
    file_path = Path(file_path)
    output_path = Path(output_path)

    if file_path.suffix.lower() != ".pdf":
        return None

    try:
        doc = fitz.open(str(file_path))
        if len(doc) == 0:
            doc.close()
            return None
        page = doc[0]
        pix = page.get_pixmap(dpi=300)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(output_path))
        doc.close()
        logger.info("Cover extracted: %s (%dx%d)", output_path, pix.width, pix.height)
        return output_path
    except Exception as e:
        logger.warning("Failed to extract cover: %s", e)
        return None


def extract_metadata(file_path: str | Path) -> dict[str, str]:
    """Extract title and author from PDF or EPUB metadata."""
    file_path = Path(file_path)
    ext = file_path.suffix.lower()
    meta = {"title": "", "author": ""}

    try:
        if ext == ".pdf":
            doc = fitz.open(str(file_path))
            pdf_meta = doc.metadata or {}
            meta["title"] = pdf_meta.get("title", "") or ""
            meta["author"] = pdf_meta.get("author", "") or ""
            doc.close()
        elif ext == ".epub":
            book = epub.read_epub(str(file_path), options={"ignore_ncx": True})
            title_list = book.get_metadata("DC", "title")
            if title_list:
                meta["title"] = title_list[0][0]
            creator_list = book.get_metadata("DC", "creator")
            if creator_list:
                meta["author"] = creator_list[0][0]
    except Exception as e:
        logger.warning("Failed to extract metadata: %s", e)

    # Fallback: use filename as title
    if not meta["title"]:
        meta["title"] = file_path.stem.replace("_", " ").replace("-", " ")

    logger.info("Metadata: title=%r, author=%r", meta["title"], meta["author"])
    return meta


def extract_to_markdown(
    file_path: str | Path,
    image_dir: Path | None = None,
    skip_first_page: bool = False,
) -> str:
    """Extract content to Markdown from PDF or EPUB, saving images to image_dir."""
    file_path = Path(file_path)
    ext = file_path.suffix.lower()

    if image_dir:
        image_dir.mkdir(parents=True, exist_ok=True)

    if ext == ".pdf":
        return _extract_pdf(file_path, image_dir, skip_first_page=skip_first_page)
    elif ext == ".epub":
        return _extract_epub(file_path, image_dir)
    else:
        raise ValueError(f"Unsupported format: {ext}")


def count_pages(file_path: str | Path) -> int:
    """Count pages in PDF or estimated pages in EPUB."""
    file_path = Path(file_path)
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        doc = fitz.open(str(file_path))
        count = len(doc)
        doc.close()
        return count
    elif ext == ".epub":
        book = epub.read_epub(str(file_path), options={"ignore_ncx": True})
        total_chars = 0
        for item in book.get_items_of_type(9):
            content = item.get_content().decode("utf-8", errors="ignore")
            total_chars += len(content)
        # ~2000 chars per page estimate for EPUB
        return max(1, total_chars // 2000)
    return 1


def _extract_pdf(
    pdf_path: Path,
    image_dir: Path | None = None,
    skip_first_page: bool = False,
) -> str:
    logger.info("Extracting PDF: %s (skip_first_page=%s)", pdf_path, skip_first_page)

    kwargs: dict = {
        "show_progress": False,
        "page_chunks": False,
        "dpi": 150,
    }

    if skip_first_page:
        doc = fitz.open(str(pdf_path))
        total = len(doc)
        doc.close()
        if total > 1:
            kwargs["pages"] = list(range(1, total))

    if image_dir:
        kwargs["write_images"] = True
        kwargs["image_path"] = str(image_dir)
        kwargs["image_format"] = "png"
        kwargs["image_size_limit"] = 0.03
        logger.info("Saving images to: %s", image_dir)

    md_text = pymupdf4llm.to_markdown(str(pdf_path), **kwargs)

    if image_dir:
        md_text = _normalize_image_paths(md_text, image_dir)

    logger.info("PDF extraction complete: %d characters", len(md_text))
    return md_text


def _extract_epub(epub_path: Path, image_dir: Path | None = None) -> str:
    logger.info("Extracting EPUB: %s", epub_path)
    book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})

    # Extract images from EPUB
    image_map: dict[str, str] = {}
    if image_dir:
        image_dir.mkdir(parents=True, exist_ok=True)
        for item in book.get_items():
            if item.get_type() == 3:  # ITEM_IMAGE
                img_name = Path(item.get_name()).name
                img_path = image_dir / img_name
                img_path.write_bytes(item.get_content())
                # Map original EPUB path to local path
                image_map[item.get_name()] = str(img_path)
                logger.info("Extracted EPUB image: %s", img_name)

    parts: list[str] = []
    for item in book.get_items_of_type(9):
        html_content = item.get_content().decode("utf-8", errors="ignore")

        # Rewrite image src paths to local extracted files
        if image_dir:
            html_content = _rewrite_epub_image_paths(html_content, image_map, image_dir)

        md = _h2t.handle(html_content).strip()
        if md:
            parts.append(md)

    md_text = "\n\n---\n\n".join(parts)
    logger.info("EPUB extraction complete: %d characters", len(md_text))
    return md_text


def _normalize_image_paths(md_text: str, image_dir: Path) -> str:
    """Ensure image paths in markdown are absolute for WeasyPrint/EPUB generation."""
    import re
    abs_dir = str(image_dir.resolve())

    def _fix_path(m: re.Match) -> str:
        alt = m.group(1)
        path = m.group(2)
        # If already absolute, leave it
        if Path(path).is_absolute():
            return f"![{alt}]({path})"
        # Make absolute
        abs_path = str((image_dir / Path(path).name).resolve())
        return f"![{alt}]({abs_path})"

    return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', _fix_path, md_text)


def _rewrite_epub_image_paths(
    html_content: str,
    image_map: dict[str, str],
    image_dir: Path,
) -> str:
    """Replace EPUB image references with local file paths."""
    import re
    for epub_path, local_path in image_map.items():
        # Match various src patterns: relative, with ../, just filename
        epub_name = Path(epub_path).name
        html_content = re.sub(
            rf'(src=["\'])([^"\']*{re.escape(epub_name)})',
            rf'\g<1>{local_path}',
            html_content,
        )
    return html_content
