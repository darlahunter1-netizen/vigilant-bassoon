import logging
import random
import sqlite3
import os
import asyncio
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask, jsonify

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler, ChatJoinRequestHandler

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в Secrets!")

GROUP_CHAT_ID = -1003431090434
ADMIN_ID = 998091317
DB_FILE = "users.db"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Bot is running! 🚀"}), 200

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"Пользователей в базе: {get_users_count()}")

application.add_handler(CommandHandler("stats", stats))

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

pending_requests = {}

def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    return a + b, f"{a} + {b} = ?"

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    if not request or request.chat.id != GROUP_CHAT_ID:
        return
    answer, question = generate_captcha()
    options = [answer, answer + random.randint(1, 5), answer - random.randint(1, 5)]
    random.shuffle(options)
    keyboard = [[InlineKeyboardButton(str(opt), callback_data=f"captcha_{opt}_{request.from_user.id}")] for opt in options]
    expires = datetime.now() + timedelta(minutes=5)
    pending_requests[request.from_user.id] = {"expires": expires, "answer": answer, "chat_id": request.chat.id}
    try:
        await context.bot.send_message(
            request.from_user.id,
            f"Чтобы вступить в <b>{request.chat.title}</b>, решите задачу:\n\n<b>{question}</b>\n\nУ вас 5 минут.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка капчи: {e}")

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
        await query.edit_message_text("Время истекло.")
        del pending_requests[user_id]
        return
    if chosen == info["answer"]:
        add_user(user_id, query.from_user.username, query.from_user.full_name)
        text = "🎉 Заявка прошла проверку и находится в обработке! Скоро добавим в группу 🚀"
        await context.bot.send_photo(
            user_id,
            photo="https://i.imgur.com/0Z8Z8Z8.jpeg",
            caption=text,
            parse_mode="HTML"
        )
        await query.edit_message_text("✅ Пройдено!")
    else:
        await query.edit_message_text("❌ Неправильно.")
    del pending_requests[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user.id, update.effective_user.username, update.effective_user.full_name)
    await update.message.reply_text("Привет! Подай заявку на вступление — пришлю капчу.")

application = Application.builder().token(TOKEN).build()
application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(CallbackQueryHandler(captcha_callback, pattern="^captcha_"))
application.add_handler(CommandHandler("start", start))

init_db()

def run_polling():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Polling error: {e}")
    finally:
        loop.run_until_complete(application.stop())
        loop.run_until_complete(application.shutdown())
        loop.close()

if __name__ == "__main__":
    Thread(target=run_polling, daemon=True).start()
    port = int(os.getenv("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False)

