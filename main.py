import logging
import random
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    MessageHandler,
    filters
)

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_CHAT_ID = -1003431090434          # ← обязательно правильный ID супергруппы!
ADMIN_ID = 998091317
DB_FILE = "users.db"

# ===================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Bot is running! 🚀"}), 200


def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# База данных
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
                 user_id INTEGER PRIMARY KEY,
                 username TEXT,
                 full_name TEXT,
                 joined_at TIMESTAMP
                 )""")
    conn.commit()
    conn.close()


def add_user(user_id: int, username: str, full_name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, datetime.now())
    )
    conn.commit()
    conn.close()


# Капча
pending_requests = {}


def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    correct = a + b
    question = f"{a} + {b} = ?"
    options = [correct]
    while len(options) < 3:
        wrong = correct + random.randint(-5, 5)
        if wrong != correct and wrong not in options:
            options.append(wrong)
    random.shuffle(options)
    return correct, question, options


async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    user = request.from_user
    chat = request.chat

    logger.info(f"Новая заявка на вступление | user: {user.id} | chat: {chat.id}")

    if chat.id != GROUP_CHAT_ID:
        logger.warning(f"Заявка НЕ в целевую группу! Ожидали {GROUP_CHAT_ID}")
        return

    correct, question, options = generate_captcha()

    keyboard = [
        [InlineKeyboardButton(str(opt), callback_data=f"captcha_{opt}_{user.id}")]
        for opt in options
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    expires = datetime.now() + timedelta(minutes=5)
    pending_requests[user.id] = {
        "expires": expires,
        "answer": correct,
        "chat_id": chat.id,
        "question": question
    }

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Чтобы вступить в <b>{chat.title}</b>, решите задачу:\n\n"
                 f"<b>{question}</b>\n\nУ вас 5 минут.",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        logger.info(f"Капча отправлена пользователю {user.id}")
    except Exception as e:
        logger.error(f"Не удалось отправить капчу пользователю {user.id}: {e}")
        try:
            await context.bot.decline_chat_join_request(
                chat_id=chat.id,
                user_id=user.id
            )
        except Exception as e2:
            logger.error(f"Не удалось отклонить заявку {user.id}: {e2}")


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    if len(data) != 3 or data[0] != "captcha":
        await query.edit_message_text("Некорректный запрос")
        return

    try:
        chosen = int(data[1])
        user_id = int(data[2])
    except ValueError:
        await query.edit_message_text("Ошибка обработки")
        return

    if user_id != query.from_user.id or user_id not in pending_requests:
        await query.edit_message_text("Эта капча не для вас или уже истекла")
        return

    info = pending_requests[user_id]

    if datetime.now() > info["expires"]:
        await query.edit_message_text("⏰ Время истекло")
        try:
            await context.bot.decline_chat_join_request(
                chat_id=info["chat_id"],
                user_id=user_id
            )
        except:
            pass
        del pending_requests[user_id]
        return

    if chosen == info["answer"]:
        add_user(user_id, query.from_user.username or "None", query.from_user.full_name)

        welcome_text = (
            "🎉 <b>Поздравляем!</b> 🎉\n\n"
            "Вы успешно прошли проверку!\n\n"
            "Ваша заявка находится в обработке. "
            "Скоро администратор добавит вас в группу ShortsBlast 🚀\n\n"
            "Пока ждёте — держите мотивацию! ✨"
        )

        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo="https://i.imgur.com/0Z8Z8Z8.jpeg",  # ← желательно заменить
                caption=welcome_text,
                parse_mode="HTML"
            )
            await query.edit_message_text("✅ Капча пройдена! Ожидайте добавления в группу.")
        except Exception as e:
            logger.error(f"Ошибка отправки приветствия {user_id}: {e}")
            await query.edit_message_text("✅ Капча пройдена! Ожидайте добавления.")
    else:
        try:
            await context.bot.decline_chat_join_request(
                chat_id=info["chat_id"],
                user_id=user_id
            )
            await query.edit_message_text("❌ Ответ неверный. Заявка отклонена.")
        except Exception as e:
            logger.error(f"Ошибка при отклонении {user_id}: {e}")
            await query.edit_message_text("❌ Неверно")

    del pending_requests[user_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username or "None", user.full_name)
    await update.message.reply_text(
        "Привет! Я бот для проверки вступления в группу.\n"
        "Просто подай заявку в группу — я пришлю тебе капчу в личку 😊"
    )


async def debug_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Текущий chat_id: <code>{chat_id}</code>\n\n"
        f"Ожидаемый GROUP_CHAT_ID: <code>{GROUP_CHAT_ID}</code>",
        parse_mode="HTML"
    )


def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    # Порядок важен!
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("id", debug_info))           # для диагностики
    application.add_handler(CallbackQueryHandler(captcha_callback, pattern="^captcha_"))
    application.add_handler(ChatJoinRequestHandler(handle_join_request))

    # Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Бот запускается...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=0.8,
        timeout=15
    )


if __name__ == "__main__":
    main()
