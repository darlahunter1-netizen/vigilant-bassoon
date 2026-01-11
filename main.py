import logging
import random
import sqlite3
import os
import asyncio
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
)

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в Secrets!")

GROUP_CHAT_ID = -1003431090434   # ← свой ID группы
ADMIN_ID = 998091317             # ← свой ID

DB_FILE = "users.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK ====================
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Bot is running! 🚀"}), 200

# ==================== БАЗА ДАННЫХ ====================
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

def add_user(user_id: int, username: str | None, full_name: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
              (user_id, username, full_name, datetime.now()))
    conn.commit()
    conn.close()

# ==================== КАПЧА ====================
pending_requests = {}

def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    return a + b, f"{a} + {b} = ?"

# ==================== ОБРАБОТЧИКИ ====================
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    user = request.from_user
    chat = request.chat

    if chat.id != GROUP_CHAT_ID:
        return

    answer, question = generate_captcha()
    options = [answer, answer + random.randint(1, 5), answer - random.randint(1, 5)]
    random.shuffle(options)

    keyboard = [[InlineKeyboardButton(str(opt), callback_data=f"captcha_{opt}_{user.id}")] for opt in options]

    expires = datetime.now() + timedelta(minutes=5)
    pending_requests[user.id] = {"expires": expires, "answer": answer, "chat_id": chat.id}

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Чтобы вступить в <b>{chat.title}</b>, решите задачу:\n\n<b>{question}</b>\n\nУ вас 5 минут.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки капчи {user.id}: {e}")

async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    if len(data) != 3 or data[0] != "captcha":
        return

    chosen = int(data[1])
    user_id = int(data[2])

    if user_id != query.from_user.id or user_id not in pending_requests:
        await query.edit_message_text("Ошибка или время истекло.")
        return

    info = pending_requests[user_id]

    if datetime.now() > info["expires"]:
        await query.edit_message_text("⏰ Время истекло.")
        del pending_requests[user_id]
        return

    if chosen == info["answer"]:
        add_user(user_id, query.from_user.username, query.from_user.full_name)

        welcome_text = (
            "🎉 <b>Поздравляем!</b> 🎉\n\n"
            "Ваша заявка успешно прошла проверку и <b>находится в обработке</b>!\n\n"
            "Мы проверим её в ближайшее время и добавим вас в эксклюзивное сообщество ShortsBlast 🚀\n"
            "Пока ждёшь — держи мотивацию!"
        )

        photo_url = "https://i.imgur.com/0Z8Z8Z8.jpeg"  # ← можно заменить

        await context.bot.send_photo(
            chat_id=user_id,
            photo=photo_url,
            caption=welcome_text,
            parse_mode="HTML"
        )

        await query.edit_message_text("✅ Капча пройдена! Ожидай добавления.")
    else:
        await query.edit_message_text("❌ Неправильно.")

    del pending_requests[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.full_name)
    await update.message.reply_text("Привет! Теперь я могу писать тебе персональные сообщения.")

# ==================== ПРИЛОЖЕНИЕ ====================
application = Application.builder().token(TOKEN).build()

application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(CallbackQueryHandler(captcha_callback, pattern="^captcha_"))
application.add_handler(CommandHandler("start", start))

init_db()

# ==================== ПОЛЛИНГ В ФОНЕ ====================
def run_polling():
    logger.info("Telegram polling запускается в фоновом потоке")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Polling ошибка: {e}")
    finally:
        loop.run_until_complete(application.stop())
        loop.run_until_complete(application.shutdown())
        loop.close()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    # Polling в фоне
    Thread(target=run_polling, daemon=True).start()

    # Flask — основной процесс
    port = int(os.getenv("PORT", 8080))
    logger.info(f"Flask запускается на порту {port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
