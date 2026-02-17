import logging
import re
import asyncio
from collections.abc import Callable, Awaitable
from openai import AsyncOpenAI
from bot.config import OPENAI_API_KEY, OPENAI_MODEL, CHUNK_SIZE

DEFAULT_CHUNK_SIZE = max(CHUNK_SIZE, 8000)
MAX_CONCURRENT = 10  # parallel OpenAI requests

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "Ты профессиональный переводчик книг. Переведи текст с английского на русский.\n"
    "Правила:\n"
    "1. Сохрани ВСЮ Markdown-разметку: заголовки (#), списки (-), таблицы (|), "
    "выделения (**bold**, *italic*), ссылки, блоки кода.\n"
    "2. Не добавляй ничего от себя.\n"
    "3. Переводи только текст, оставляя разметку и код нетронутыми.\n"
    "4. Имена собственные и технические термины транслитерируй или оставляй в оригинале "
    "по контексту.\n"
    "5. Сохраняй абзацы и переносы строк как в оригинале.\n"
    "6. Плейсхолдеры вида <<IMG_N>> — это изображения. Оставляй их БЕЗ ИЗМЕНЕНИЙ "
    "на тех же позициях в тексте."
)

# Pattern to match markdown images: ![alt text](path/to/image.png)
_IMG_RE = re.compile(r'!\[[^\]]*\]\([^)]+\)')


def _protect_images(text: str) -> tuple[str, list[str]]:
    """Replace image markdown with placeholders, return (cleaned_text, images)."""
    images: list[str] = []

    def _replace(m: re.Match) -> str:
        idx = len(images)
        images.append(m.group(0))
        return f"<<IMG_{idx}>>"

    cleaned = _IMG_RE.sub(_replace, text)
    return cleaned, images


def _restore_images(text: str, images: list[str]) -> str:
    """Restore image placeholders back to original markdown."""
    for idx, img_md in enumerate(images):
        placeholder = f"<<IMG_{idx}>>"
        text = text.replace(placeholder, img_md)
    return text


def split_into_chunks(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """Split markdown text into chunks, respecting section boundaries."""
    lines = text.split("\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        is_heading = line.startswith("#")

        if current_len + line_len > chunk_size and current_chunk:
            if is_heading:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = line_len
            else:
                current_chunk.append(line)
                current_len += line_len
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return [c for c in chunks if c.strip()]


async def translate_chunk(text: str) -> str:
    """Translate a single text chunk via OpenAI."""
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


async def translate_markdown(
    text: str,
    progress_callback: Callable[[int, int], Awaitable[None]] | None = None,
) -> str:
    """Translate full markdown document with parallel API calls.

    Images are protected from translation via placeholders and restored after.
    """
    # Protect images before chunking/translation
    protected_text, images = _protect_images(text)
    logger.info("Protected %d images from translation", len(images))

    chunks = split_into_chunks(protected_text)
    total = len(chunks)
    logger.info("Translating %d chunks (max %d parallel)", total, MAX_CONCURRENT)

    results: list[str] = [""] * total
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    done_count = 0
    lock = asyncio.Lock()

    async def _process(idx: int, chunk: str) -> None:
        nonlocal done_count
        async with sem:
            logger.info("Translating chunk %d/%d (%d chars)", idx + 1, total, len(chunk))
            results[idx] = await translate_chunk(chunk)
            async with lock:
                done_count += 1
                if progress_callback:
                    await progress_callback(done_count, total)

    await asyncio.gather(*[_process(i, c) for i, c in enumerate(chunks)])

    translated = "\n\n".join(results)

    # Restore images
    translated = _restore_images(translated, images)
    logger.info("Restored %d images after translation", len(images))

    return translated
