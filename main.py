import logging
import random
import sqlite3
import os
from datetime import datetime, timedelta

from flask import Flask, request, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, CallbackQueryHandler, ChatJoinRequestHandler

# ==================== НАСТРОЙКИ ====================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")  # ← добавь в Secrets!

GROUP_CHAT_ID = -1003431090434          # ← замени на ID своей группы

ADMIN_ID = 998091317  # ← ЗАМЕНИ на свой Telegram ID

WEBHOOK_SECRET = "supersecret1234567890"  # ← можно оставить или изменить (длинный случайный)

DB_FILE = "users.db"
# ===================================================

app = Flask(__name__)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация Telegram Application
application = None
if TOKEN:
    application = Application.builder().token(TOKEN).build()
    # Обязательная инициализация и запуск (фиксит RuntimeError)
    application.initialize()
    application.start()
    logger.info("Telegram bot initialized and started")
else:
    logger.warning("TELEGRAM_BOT_TOKEN not found in Secrets")

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

def get_all_user_ids():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_users_count():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    count = c.fetchone()[0]
    conn.close()
    return count

# Капча
pending_requests = {}

def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    return a, b, a + b, f"{a} + {b} = ?"

def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        context.bot.send_message(
            chat_id=user.id,
            text=f"Чтобы вступить в <b>{chat.title}</b>, решите задачу:\n\n<b>{question}</b>\n\nУ вас 5 минут.",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки капчи: {e}")
        context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)

def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    data = query.data.split("_")
    if len(data) != 3 or data[0] != "captcha":
        return

    chosen = int(data[1])
    user_id = int(data[2])

    if user_id != query.from_user.id or user_id not in pending_requests:
        query.edit_message_text("Ошибка или время вышло.")
        return

    info = pending_requests[user_id]
    if datetime.now() > info["expires"]:
        query.edit_message_text("⏰ Время истекло.")
        del pending_requests[user_id]
        context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user_id)
        return

    user = query.from_user
    if chosen == info["answer"]:
        context.bot.approve_chat_join_request(chat_id=info["chat_id"], user_id=user.id)
        add_user(user.id, user.username or "None", user.full_name)

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Нажми /start", url=f"t.me/{context.bot.get_me().username}")]])
        query.edit_message_text("✅ Пройдено! Вы в группе.\n\nНажмите /start у бота.", reply_markup=keyboard)
    else:
        context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user.id)
        query.edit_message_text("❌ Неправильно.")

    del pending_requests[user_id]

def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username or "None", user.full_name)
    update.message.reply_text("Привет! Теперь я могу писать тебе.")

def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    update.message.reply_text(f"Собрано пользователей: {get_users_count()}")

def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        update.message.reply_text("Использование: /broadcast текст")
        return

    text = " ".join(context.args)
    user_ids = get_all_user_ids()
    success = failed = 0
    for uid in user_ids:
        try:
            context.bot.send_message(uid, text)
            success += 1
        except:
            failed += 1
    update.message.reply_text(f"Рассылка: {success} ок, {failed} ошибок")

# Регистрация обработчиков
if application:
    application.add_handler(ChatJoinRequestHandler(handle_join_request))
    application.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^captcha_"))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))

    init_db()

# ==================== WEBHOOK ====================
@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if application is None:
        return "Telegram отключён - нет токена", 503

    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)

    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return "OK"

@app.route("/")
def index():
    if application is None:
        return "Сервер работает, но Telegram отключён (добавь TELEGRAM_BOT_TOKEN в Secrets)"
    return "Бот работает!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)