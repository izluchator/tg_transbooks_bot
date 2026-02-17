import logging
import math
import uuid
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    LabeledPrice,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
    Application,
)

from bot.config import MAX_FILE_SIZE_MB, TMP_DIR, ADMIN_USERNAME, STARS_PER_50_PAGES
from bot.database import (
    get_or_create_user, get_balance, add_stars, gift_stars,
    spend_stars, set_format, get_format, get_all_users,
    get_user_by_username, get_stats, init_db,
)
from bot.extractor import extract_to_markdown, count_pages, extract_cover_image, extract_metadata
from bot.translator import translate_markdown, translate_chunk
from bot.generator import markdown_to_pdf, markdown_to_epub
from bot.cover import generate_cover

logger = logging.getLogger(__name__)

active_jobs: dict[int, bool] = {}

# Star packages available for purchase
STAR_PACKAGES = [
    (10, "⭐ 10 звёзд — ~25 стр"),
    (50, "⭐ 50 звёзд — ~125 стр"),
    (150, "⭐ 150 звёзд — ~375 стр"),
    (500, "⭐ 500 звёзд — ~1250 стр"),
]


def _calc_cost(pages: int) -> int:
    """Calculate star cost for given page count."""
    return max(1, math.ceil(pages / 50 * STARS_PER_50_PAGES))


def _is_admin(user) -> bool:
    return (user.username or "").lower() == ADMIN_USERNAME.lower()


# --- Keyboards ---

def _main_kb(balance: int, fmt: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    fmt_label = fmt.upper()
    rows = [
        [
            InlineKeyboardButton(f"💫 Купить звёзды", callback_data="buy"),
            InlineKeyboardButton(f"💰 Баланс: {balance} ⭐", callback_data="balance"),
        ],
        [
            InlineKeyboardButton(f"📁 Формат: {fmt_label}", callback_data="show_format"),
            InlineKeyboardButton("❓ Помощь", callback_data="help"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("🔧 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def _buy_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"pack_{amount}")]
        for amount, label in STAR_PACKAGES
    ]
    buttons.append([InlineKeyboardButton("← Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)


def _format_kb(current: str) -> InlineKeyboardMarkup:
    pdf_l = "📄 PDF ✅" if current == "pdf" else "📄 PDF"
    epub_l = "📱 EPUB ✅" if current == "epub" else "📱 EPUB"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(pdf_l, callback_data="fmt_pdf"),
            InlineKeyboardButton(epub_l, callback_data="fmt_epub"),
        ],
        [InlineKeyboardButton("← Назад", callback_data="back_main")],
    ])


def _confirm_kb(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Перевести", callback_data=f"confirm_{job_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_job"),
        ]
    ])


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 Отменить перевод", callback_data="cancel")]
    ])


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Начислить звёзды", callback_data="admin_gift")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("← Назад", callback_data="back_main")],
    ])


# --- Welcome text ---

def _welcome_text(balance: int) -> str:
    return (
        "📖 *Переводчик книг EN→RU*\n\n"
        "Отправь PDF или EPUB на английском — получи перевод на русский.\n\n"
        f"💫 *Баланс:* {balance} ⭐\n"
        f"💰 *Цена:* {STARS_PER_50_PAGES} ⭐ ≈ 50 страниц"
    )


# --- Command handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    u = await get_or_create_user(user.id, user.username or user.first_name)
    balance = u["balance"]
    fmt = u["format"]
    await update.message.reply_text(
        _welcome_text(balance),
        parse_mode="Markdown",
        reply_markup=_main_kb(balance, fmt, _is_admin(user)),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user = update.effective_user
    u = await get_or_create_user(user.id, user.username or "")
    await update.message.reply_text(
        "📖 *Переводчик книг EN→RU*\n\n"
        "*Как пользоваться:*\n"
        "1. Купи звёзды кнопкой 💫\n"
        "2. Отправь PDF или EPUB файл\n"
        "3. Подтверди перевод\n"
        "4. Получи переведённый файл\n\n"
        f"💰 {STARS_PER_50_PAGES} ⭐ ≈ 50 страниц\n"
        f"📏 Макс. размер: {MAX_FILE_SIZE_MB} МБ",
        parse_mode="Markdown",
        reply_markup=_main_kb(u["balance"], u["format"], _is_admin(user)),
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    if user_id in active_jobs:
        active_jobs[user_id] = True
        await update.message.reply_text("🛑 Отменяю перевод...")
    else:
        await update.message.reply_text("ℹ️ Нет активного перевода.")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not _is_admin(update.effective_user):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text("🔧 *Админ-панель*", parse_mode="Markdown", reply_markup=_admin_kb())


# --- Callback query handler ---

# Pending jobs: job_id -> {user_id, input_path, pages, cost, filename, status_msg}
pending_jobs: dict[str, dict] = {}


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()
    user = update.effective_user
    user_id = user.id
    data = query.data

    u = await get_or_create_user(user_id, user.username or "")

    # --- Main menu ---
    if data == "back_main":
        u = await get_or_create_user(user_id, user.username or "")
        await query.edit_message_text(
            _welcome_text(u["balance"]),
            parse_mode="Markdown",
            reply_markup=_main_kb(u["balance"], u["format"], _is_admin(user)),
        )

    elif data == "balance":
        balance = await get_balance(user_id)
        await query.edit_message_text(
            f"💰 *Твой баланс:* {balance} ⭐\n\n"
            f"Этого хватит на ~{balance * 50 // max(STARS_PER_50_PAGES, 1)} страниц.",
            parse_mode="Markdown",
            reply_markup=_main_kb(balance, u["format"], _is_admin(user)),
        )

    # --- Buy stars ---
    elif data == "buy":
        await query.edit_message_text(
            "💫 *Купить звёзды*\n\nВыбери пакет:",
            parse_mode="Markdown",
            reply_markup=_buy_kb(),
        )

    elif data.startswith("pack_"):
        amount = int(data.split("_")[1])
        pkg = next((l for a, l in STAR_PACKAGES if a == amount), f"{amount} ⭐")
        await context.bot.send_invoice(
            chat_id=user_id,
            title=f"{amount} звёзд",
            description=f"Пополнение баланса переводчика на {amount} ⭐",
            payload=f"stars_{amount}_{user_id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"{amount} звёзд", amount=amount)],
            provider_token="",
        )

    # --- Format ---
    elif data == "show_format":
        await query.edit_message_text(
            "Выбери формат вывода:",
            reply_markup=_format_kb(u["format"]),
        )

    elif data == "fmt_pdf":
        await set_format(user_id, "pdf")
        await query.edit_message_text(
            "✅ Формат: *PDF*",
            parse_mode="Markdown",
            reply_markup=_main_kb(await get_balance(user_id), "pdf", _is_admin(user)),
        )

    elif data == "fmt_epub":
        await set_format(user_id, "epub")
        await query.edit_message_text(
            "✅ Формат: *EPUB*",
            parse_mode="Markdown",
            reply_markup=_main_kb(await get_balance(user_id), "epub", _is_admin(user)),
        )

    # --- Help ---
    elif data == "help":
        await query.edit_message_text(
            "📖 *Переводчик книг EN→RU*\n\n"
            "Принимаю: PDF, EPUB\n"
            "Выдаю: PDF или EPUB\n\n"
            "*Как пользоваться:*\n"
            "1. Купи звёзды 💫\n"
            "2. Отправь файл (PDF/EPUB)\n"
            "3. Подтверди и получи перевод\n\n"
            f"💰 {STARS_PER_50_PAGES} ⭐ ≈ 50 страниц\n"
            f"📏 Макс: {MAX_FILE_SIZE_MB} МБ",
            parse_mode="Markdown",
            reply_markup=_main_kb(u["balance"], u["format"], _is_admin(user)),
        )

    # --- Cancel ---
    elif data == "cancel":
        if user_id in active_jobs:
            active_jobs[user_id] = True
            await query.edit_message_text("🛑 Отменяю перевод...")
        else:
            await query.edit_message_text("ℹ️ Нет активного перевода.")

    elif data == "cancel_job":
        # Cancel pending unconfirmed job
        to_remove = [jid for jid, j in pending_jobs.items() if j["user_id"] == user_id]
        for jid in to_remove:
            _cleanup(Path(pending_jobs[jid]["input_path"]).parent)
            del pending_jobs[jid]
        await query.edit_message_text("❌ Отменено.")

    # --- Confirm translation ---
    elif data.startswith("confirm_"):
        job_id = data.split("_", 1)[1]
        job = pending_jobs.pop(job_id, None)
        if not job:
            await query.edit_message_text("⚠️ Задача не найдена или уже выполнена.")
            return
        # Run translation in background
        context.application.create_task(
            _run_translation(user, job, query.message, context)
        )

    # --- Admin ---
    elif data == "admin" and _is_admin(user):
        await query.edit_message_text("🔧 *Админ-панель*", parse_mode="Markdown", reply_markup=_admin_kb())

    elif data == "admin_stats" and _is_admin(user):
        stats = await get_stats()
        await query.edit_message_text(
            "📊 *Статистика*\n\n"
            f"👥 Пользователей: {stats['users']}\n"
            f"📖 Переводов: {stats['translations']}\n"
            f"💰 Куплено ⭐: {stats['stars_bought']}\n"
            f"💸 Потрачено ⭐: {stats['stars_spent']}\n"
            f"🎁 Подарено ⭐: {stats['stars_gifted']}",
            parse_mode="Markdown",
            reply_markup=_admin_kb(),
        )

    elif data == "admin_users" and _is_admin(user):
        users = await get_all_users()
        if not users:
            text = "👥 Пользователей нет."
        else:
            lines = []
            for u in users[:20]:
                uname = f"@{u['username']}" if u['username'] else f"id:{u['tg_id']}"
                lines.append(f"• {uname} — {u['balance']} ⭐")
            text = "👥 *Пользователи:*\n\n" + "\n".join(lines)
            if len(users) > 20:
                text += f"\n\n_...и ещё {len(users) - 20}_"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_admin_kb())

    elif data == "admin_gift" and _is_admin(user):
        context.user_data["awaiting_gift"] = True
        await query.edit_message_text(
            "💰 *Начислить звёзды*\n\n"
            "Отправь сообщение в формате:\n"
            "`username количество`\n\n"
            "Например: `ivan 50`",
            parse_mode="Markdown",
        )


# --- Payment handlers ---

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    payment = update.message.successful_payment
    if not payment:
        return

    user = update.effective_user
    payload = payment.invoice_payload
    # Parse: stars_{amount}_{user_id}
    parts = payload.split("_")
    amount = int(parts[1])

    new_balance = await add_stars(user.id, amount, f"telegram payment {payment.telegram_payment_charge_id}")
    u = await get_or_create_user(user.id, user.username or "")

    await update.message.reply_text(
        f"✅ *Оплата прошла!*\n\n"
        f"Зачислено: {amount} ⭐\n"
        f"💰 Баланс: {new_balance} ⭐",
        parse_mode="Markdown",
        reply_markup=_main_kb(new_balance, u["format"], _is_admin(user)),
    )


# --- Document handler ---

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    u = await get_or_create_user(user.id, user.username or user.first_name)

    document = update.message.document
    if not document:
        return

    mime = document.mime_type or ""
    filename = document.file_name or "file"
    ext = Path(filename).suffix.lower()

    allowed_mimes = {"application/pdf", "application/epub+zip"}
    allowed_exts = {".pdf", ".epub"}

    if mime not in allowed_mimes and ext not in allowed_exts:
        await update.message.reply_text("⚠️ Принимаю только PDF и EPUB файлы.")
        return

    file_size_mb = (document.file_size or 0) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        await update.message.reply_text(
            f"⚠️ Файл слишком большой ({file_size_mb:.1f} МБ). Макс: {MAX_FILE_SIZE_MB} МБ."
        )
        return

    job_id = uuid.uuid4().hex[:8]
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    input_file = job_dir / filename

    status_msg = await update.message.reply_text("⏳ Анализирую файл...")

    try:
        tg_file = await document.get_file()
        await tg_file.download_to_drive(str(input_file))

        pages = count_pages(input_file)
        cost = _calc_cost(pages)
        balance = await get_balance(user.id)

        if balance < cost:
            deficit = cost - balance
            await status_msg.edit_text(
                f"📄 *{filename}*\n"
                f"📏 Страниц: ~{pages}\n"
                f"💰 Стоимость: {cost} ⭐\n"
                f"❌ Не хватает: {deficit} ⭐\n\n"
                f"Пополни баланс кнопкой 💫 Купить звёзды",
                parse_mode="Markdown",
                reply_markup=_buy_kb(),
            )
            _cleanup(job_dir)
            return

        pending_jobs[job_id] = {
            "user_id": user.id,
            "input_path": str(input_file),
            "filename": filename,
            "pages": pages,
            "cost": cost,
        }

        await status_msg.edit_text(
            f"📄 *{filename}*\n"
            f"📏 Страниц: ~{pages}\n"
            f"💰 Стоимость: {cost} ⭐\n"
            f"💫 Баланс: {balance} ⭐ → {balance - cost} ⭐\n\n"
            "Перевести?",
            parse_mode="Markdown",
            reply_markup=_confirm_kb(job_id),
        )

    except Exception as e:
        logger.exception("Error analyzing file for user %s", user.id)
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        _cleanup(job_dir)


async def _run_translation(user, job: dict, message, context) -> None:
    """Run the translation job (called as background task)."""
    user_id = user.id
    input_path = Path(job["input_path"])
    job_dir = input_path.parent
    filename = job["filename"]
    cost = job["cost"]

    fmt = await get_format(user_id)
    stem = Path(filename).stem
    output_file = job_dir / f"RU_{stem}.{fmt}"
    is_pdf = input_path.suffix.lower() == ".pdf"

    try:
        status_msg = await message.edit_text(
            "📄 Извлекаю текст и изображения...",
            reply_markup=_cancel_kb(),
        )

        # --- Extract metadata and cover ---
        meta = extract_metadata(input_path)
        orig_title = meta["title"]
        orig_author = meta["author"]

        cover_path: Path | None = None
        if is_pdf:
            cover_path = extract_cover_image(input_path, job_dir / "cover.png")

        image_dir = job_dir / "images"
        md_text = extract_to_markdown(
            input_path,
            image_dir=image_dir,
            skip_first_page=(is_pdf and cover_path is not None),
        )

        if not md_text.strip():
            await status_msg.edit_text(
                "❌ Не удалось извлечь текст. Возможно, файл содержит только изображения."
            )
            return

        # --- Translate title ---
        await status_msg.edit_text(
            "🏷 Перевожу заголовок...",
            reply_markup=_cancel_kb(),
        )
        try:
            translated_title = await translate_chunk(orig_title)
            translated_title = translated_title.strip().strip('"').strip("'")
        except Exception:
            translated_title = orig_title
        logger.info("Title: %r -> %r", orig_title, translated_title)

        # --- Translate content ---
        char_count = len(md_text)
        await status_msg.edit_text(
            f"🌐 Перевожу ({char_count:,} символов)... 0%",
            reply_markup=_cancel_kb(),
        )

        active_jobs[user_id] = False

        async def on_progress(done: int, total: int) -> None:
            if active_jobs.get(user_id):
                raise RuntimeError("Перевод отменён")
            pct = int(done / total * 100)
            try:
                await status_msg.edit_text(
                    f"🌐 Перевожу... {pct}% ({done}/{total})",
                    reply_markup=_cancel_kb(),
                )
            except Exception:
                pass

        try:
            translated_md = await translate_markdown(md_text, progress_callback=on_progress)
        finally:
            active_jobs.pop(user_id, None)

        # Deduct stars only on success
        new_balance = await spend_stars(user_id, cost, filename)

        await status_msg.edit_text(f"📑 Собираю {fmt.upper()}...")

        img_dir = image_dir if image_dir.exists() and any(image_dir.iterdir()) else None

        if fmt == "epub":
            # Generate styled cover for EPUB
            epub_cover = generate_cover(
                title=translated_title,
                author=orig_author,
                output_path=job_dir / "epub_cover.png",
            )
            markdown_to_epub(
                translated_md,
                output_file,
                title=translated_title,
                author=orig_author,
                image_dir=img_dir,
                cover_image_path=epub_cover,
            )
        else:
            markdown_to_pdf(
                translated_md,
                output_file,
                image_dir=img_dir,
                cover_image_path=cover_path,
                title=translated_title,
                author=orig_author,
            )

        await status_msg.edit_text("📤 Отправляю...")

        u = await get_or_create_user(user_id, user.username or "")
        with open(output_file, "rb") as f:
            await message.reply_document(
                document=f,
                filename=output_file.name,
                caption=f"✅ Перевод готов! Списано {cost} ⭐, баланс: {new_balance} ⭐",
                reply_markup=_main_kb(new_balance, u["format"], _is_admin(user)),
            )

        await status_msg.delete()

    except RuntimeError as e:
        if "отменён" in str(e).lower():
            await status_msg.edit_text("🛑 Перевод отменён. Звёзды не списаны.")
        else:
            await status_msg.edit_text(f"❌ Ошибка: {e}")
    except Exception as e:
        logger.exception("Error translating for user %s", user_id)
        try:
            await status_msg.edit_text(f"❌ Ошибка: {e}")
        except Exception:
            pass
    finally:
        _cleanup(job_dir)


# --- Text handler (for admin gift + fallback) ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user = update.effective_user

    # Admin gift flow
    if _is_admin(user) and context.user_data.get("awaiting_gift"):
        context.user_data["awaiting_gift"] = False
        text = update.message.text.strip()
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text(
                "⚠️ Формат: `username количество`\nНапример: `ivan 50`",
                parse_mode="Markdown",
            )
            return
        username, amount_str = parts
        username = username.lstrip("@")
        try:
            amount = int(amount_str)
        except ValueError:
            await update.message.reply_text("⚠️ Количество должно быть числом.")
            return

        target = await get_user_by_username(username)
        if not target:
            await update.message.reply_text(
                f"⚠️ Пользователь @{username} не найден. "
                "Он должен сначала написать боту /start",
                reply_markup=_admin_kb(),
            )
            return

        new_bal = await gift_stars(target["tg_id"], amount, user.username or "admin")
        await update.message.reply_text(
            f"✅ Начислено {amount} ⭐ пользователю @{username}\n"
            f"Баланс: {new_bal} ⭐",
            reply_markup=_admin_kb(),
        )
        # Notify recipient
        try:
            await context.bot.send_message(
                target["tg_id"],
                f"🎁 Вам начислено {amount} ⭐ от администратора!\n"
                f"💰 Баланс: {new_bal} ⭐",
            )
        except Exception:
            pass
        return

    # Default: prompt to send a file
    u = await get_or_create_user(user.id, user.username or "")
    await update.message.reply_text(
        "📎 Отправь PDF или EPUB файл для перевода.",
        reply_markup=_main_kb(u["balance"], u["format"], _is_admin(user)),
    )


def _cleanup(job_dir: Path) -> None:
    import shutil
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except Exception as e:
        logger.warning("Cleanup failed for %s: %s", job_dir, e)


async def _post_init(app: Application) -> None:
    await init_db()
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("help", "Помощь — PDF и EPUB перевод"),
        BotCommand("cancel", "Отменить перевод"),
        BotCommand("admin", "Админ-панель"),
    ])


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.post_init = _post_init
