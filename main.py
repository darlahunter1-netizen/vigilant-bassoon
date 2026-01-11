import logging
import random
import sqlite3
import asyncio
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler, ChatJoinRequestHandler

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # ← Добавь в Secrets!

GROUP_CHAT_ID = -1003431090434  # Твой ID группы

ADMIN_ID = 998091317  # Твой ID

DB_FILE = "users.db"
# ===================================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Bot is running! 🚀"}), 200

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

application = Application.builder().token(TOKEN).build()

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
    c.execute("INSERT OR REPLACE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
              (user_id, username, full_name, datetime.now()))
    conn.commit()
    conn.close()

# Капча
pending_requests = {}

def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    return a, b, a + b, f"{a} + {b} = ?"

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    user = request.from_user
    chat = request.chat

    if chat.id != GROUP_CHAT_ID:
        return

    a, b, answer, question = generate_captcha()
    options = [answer, answer + random.randint(1, 5), answer - random.randint(1, 5)]
    random.shuffle(options)

    keyboard = [[InlineKeyboardButton(str(opt), callback_data=f"captcha_{opt}_{user.id}")] for opt in options]
    reply_markup = InlineKeyboardMarkup(keyboard)

    expires = datetime.now() + timedelta(minutes=5)
    pending_requests[user.id] = {"expires": expires, "answer": answer, "chat_id": chat.id}

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=f"Чтобы вступить в <b>{chat.title}</b>, решите задачу:\n\n<b>{question}</b>\n\nУ вас 5 минут.",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки капчи {user.id}: {e}")
        await context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)

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
        await context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user_id)
        return

    user = query.from_user
    if chosen == info["answer"]:
        # НЕ одобряем автоматически — просто приветствие
        add_user(user.id, user.username or "None", user.full_name)

        # Красивое приветственное сообщение с картинкой
        welcome_text = (
            "🎉 <b>Поздравляем!</b> 🎉\n\n"
            "Ваша заявка успешно прошла проверку и <b>находится в обработке</b>!\n\n"
            "Мы проверим её в ближайшее время и добавим вас в эксклюзивное сообщество ShortsBlast 🚀\n"
            "Пока ждёшь — держи мотивацию и полезные советы по взлёту Shorts 👇\n\n"
            "Оставайся на связи!"
        )

        # Красивая картинка (замени ссылку на свою, если хочешь)
        photo_url = "https://i.imgur.com/0Z8Z8Z8.jpeg"  # Пример — космическая мотивация, найди свою

        await context.bot.send_photo(
            chat_id=user.id,
            photo=photo_url,
            caption=welcome_text,
            parse_mode="HTML"
        )

        await query.edit_message_text("✅ Капча пройдена! Ожидай добавления в группу.")
    else:
        await context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user.id)
        await query.edit_message_text("❌ Неправильно.")

    del pending_requests[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username or "None", user.full_name)
    await update.message.reply_text("Привет! Теперь я могу писать тебе персональные сообщения.")

# Регистрация обработчиков
application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^captcha_"))
application.add_handler(CommandHandler("start", start))

init_db()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    # Flask в фоне
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Polling
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=10
    )
