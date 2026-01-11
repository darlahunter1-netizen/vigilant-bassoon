import logging
import random
import sqlite3
import os
import asyncio
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask

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
    raise ValueError("TELEGRAM_BOT_TOKEN не найден!")

GROUP_CHAT_ID = -1003431090434
ADMIN_ID = 998091317

DB_FILE = "users.db"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Bot alive! 🚀", 200

# БД
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

def get_all_user_ids():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    return [row[0] for row in c.fetchall()]

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
    return a + b, f"{a} + {b} = ?"

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
            text=f"Для вступления в <b>{chat.title}</b> решите:\n\n<b>{question}</b>\n\n5 минут!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не отправлена капча {user.id}: {e}")

async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    if len(data) != 3 or data[0] != "captcha":
        return

    chosen = int(data[1])
    user_id = int(data[2])

    if user_id != query.from_user.id or user_id not in pending_requests:
        await query.edit_message_text("Ошибка / время истекло.")
        return

    info = pending_requests[user_id]

    if datetime.now() > info["expires"]:
        await query.edit_message_text("⏰ Время истекло.")
        del pending_requests[user_id]
        return

    if chosen == info["answer"]:
        add_user(user_id, query.from_user.username, query.from_user.full_name)

        text = (
            "🎉 <b>Поздравляем!</b>\n\n"
            "Заявка прошла капчу и <b>находится в обработке</b>!\n"
            "Мы проверим и добавим вас в ближайшее время 🚀\n"
            "Пока ждёте — держите мотивацию!"
        )

        photo = "https://assets.justinmind.com/wp-content/uploads/2024/10/progress-bar-ui-heading-768x492.png"

        await context.bot.send_photo(
            chat_id=user_id,
            photo=photo,
            caption=text,
            parse_mode="HTML"
        )

        await query.edit_message_text("✅ Пройдено! Ожидайте добавления.")
    else:
        await query.edit_message_text("❌ Неверно.")

    del pending_requests[user_id]

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user.id, update.effective_user.username, update.effective_user.full_name)
    await update.message.reply_text("Привет! Я бот группы. Подай заявку — пришлю капчу.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"Пользователей в БД: {get_users_count()}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast текст")
        return

    text = " ".join(context.args)
    users = get_all_user_ids()
    success = failed = 0

    await update.message.reply_text(f"Рассылка {len(users)} пользователям...")

    for uid in users:
        try:
            await context.bot.send_message(uid, text)
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(f"Готово! Успех: {success}, Ошибок: {failed}")

# Приложение
application = Application.builder().token(TOKEN).build()

application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(CallbackQueryHandler(captcha_callback, pattern="^captcha_"))
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CommandHandler("broadcast", broadcast))

init_db()

def run_polling():
    logger.info("Polling стартует в фоне...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Polling краш: {e}")
    finally:
        loop.run_until_complete(application.stop())
        loop.run_until_complete(application.shutdown())
        loop.close()
async def debug_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text if update.message else "не текст"
    await update.message.reply_text(f"Я тебя услышал! Получено: {text}\n\nРаботаю нормально.")

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, debug_echo))
if __name__ == "__main__":
    polling_thread = Thread(target=run_polling, daemon=True)
    polling_thread.start()

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask на {port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

