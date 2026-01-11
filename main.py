import logging
import random
import sqlite3
import os
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask, jsonify

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
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден! Добавь в Secrets → TELEGRAM_BOT_TOKEN")

GROUP_CHAT_ID = -1003431090434   # ← ← ← ТОЧНО ПРОВЕРЬ! Должен начинаться с -100...

DB_FILE = "users.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask для keep-alive (Replit/Render/etc.)
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok"}), 200

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== БД ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            joined_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str | None, full_name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
        (user_id, username, full_name, datetime.now())
    )
    conn.commit()
    conn.close()

# ==================== КАПЧА ====================
pending = {}  # user_id -> {"answer": int, "expires": datetime, "chat_id": int}

def make_captcha():
    a = random.randint(2, 15)
    b = random.randint(2, 15)
    ans = a + b
    return f"{a} + {b} = ?", ans

# ==================== ХЕНДЛЕРЫ ====================
async def join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    if not req:
        return

    user = req.from_user
    chat = req.chat

    logger.info(f"ЗАЯВКА НА ВСТУПЛЕНИЕ! Пользователь: {user.id} ({user.username}) в чат {chat.id}")

    # Проверка на нужный чат (на всякий случай)
    if chat.id != GROUP_CHAT_ID:
        logger.warning(f"Заявка в НЕ ту группу: {chat.id}")
        return

    question, correct_answer = make_captcha()

    variants = [correct_answer]
    while len(variants) < 3:
        wrong = correct_answer + random.randint(-7, 7)
        if wrong != correct_answer and wrong not in variants:
            variants.append(wrong)
    random.shuffle(variants)

    keyboard = [[InlineKeyboardButton(str(v), callback_data=f"captcha_{v}_{user.id}")] for v in variants]

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Привет! Чтобы попасть в группу, реши задачку:\n\n<b>{question}</b>\n\nУ тебя 5 минут ⏳",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        pending[user.id] = {
            "answer": correct_answer,
            "expires": datetime.now() + timedelta(minutes=5),
            "chat_id": chat.id
        }
        logger.info(f"Капча отправлена {user.id}")
    except Exception as e:
        logger.error(f"Не смог отправить капчу {user.id}: {e}")


async def captcha_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, answer_str, uid_str = query.data.split("_")
        chosen = int(answer_str)
        uid = int(uid_str)
    except:
        await query.edit_message_text("Ошибка данных кнопки")
        return

    if query.from_user.id != uid:
        await query.edit_message_text("Это не твоя капча :)")
        return

    if uid not in pending:
        await query.edit_message_text("Капча уже обработана или истекла")
        return

    data = pending[uid]

    if datetime.now() > data["expires"]:
        await query.edit_message_text("Время вышло ⏰")
        try:
            await context.bot.decline_chat_join_request(chat_id=data["chat_id"], user_id=uid)
        except:
            pass
        del pending[uid]
        return

    if chosen == data["answer"]:
        add_user(uid, query.from_user.username, query.from_user.full_name)

        text = (
            "✅ **Отлично!** Ты прошёл проверку!\n\n"
            "Заявка принята, скоро тебя добавят в группу 🚀\n"
            "Спасибо за терпение ❤️"
        )

        await context.bot.send_message(uid, text, parse_mode="Markdown")

        await query.edit_message_text("Правильно! Ожидай приглашения :)")
    else:
        await query.edit_message_text("❌ Неправильно. Попробуй подать заявку заново.")
        try:
            await context.bot.decline_chat_join_request(chat_id=data["chat_id"], user_id=uid)
        except:
            pass

    del pending[uid]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот с капчей для группы.\n\nПросто подай заявку на вступление — я отправлю тебе задачку 😊"
    )


async def debug_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Я живой! Получил сообщение.")


# ==================== ЗАПУСК ====================
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # Самое главное — порядок важен!
    app.add_handler(ChatJoinRequestHandler(join_request_handler))
    app.add_handler(CallbackQueryHandler(captcha_callback_handler, pattern="^captcha_"))

    app.add_handler(CommandHandler("start", start))

    # Для теста
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, debug_echo))

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logger.info("Бот запускается...")
    print("Бот запускается... Проверь права в группе!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=0.7,
        timeout=12
    )


if __name__ == "__main__":
    main()
