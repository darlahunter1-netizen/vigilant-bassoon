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
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в Secrets!")

GROUP_CHAT_ID = -1003431090434  # ← свой ID группы
ADMIN_ID = 998091317            # ← свой ID

DB_FILE = "users.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK (главный процесс) ====================
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "Bot is alive! 🚀", 200

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

# ==================== КАПЧА ====================
pending_requests = {}

def generate_captcha():
    a = random.randint(1, 10)
    b = random.randint(1, 10)
    answer = a + b
    question = f"{a} + {b} = ?"
    return question, answer

# ==================== ОБРАБОТЧИКИ ====================
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    user = request.from_user
    chat = request.chat

    if chat.id != GROUP_CHAT_ID:
        return

    question, answer = generate_captcha()
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
        await context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user_id)
        del pending_requests[user_id]
        return

    if chosen == info["answer"]:
        add_user(user_id, query.from_user.username, query.from_user.full_name)

        welcome_text = (
            "🎉 <b>Поздравляем!</b> 🎉\n\n"
            "Ваша заявка успешно прошла проверку и <b>находится в обработке</b>!\n\n"
            "Мы проверим её в ближайшее время и добавим вас в сообщество 🚀\n"
            "Пока ждёте — держите мотивацию!"
        )

        # Картинка для ожидания (progress/motivation)
        photo_url = "https://assets.justinmind.com/wp-content/uploads/2024/10/progress-bar-ui-heading-768x492.png"

        await context.bot.send_photo(
            chat_id=user_id,
            photo=photo_url,
            caption=welcome_text,
            parse_mode="HTML"
        )

        await query.edit_message_text("✅ Капча пройдена! Заявка в обработке.")
    else:
        await context.bot.decline_chat_join_request(chat_id=info["chat_id"], user_id=user_id)
        await query.edit_message_text("❌ Неправильно.")

    del pending_requests[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.full_name)
    await update.message.reply_text("Привет! Теперь я могу писать тебе персональные сообщения.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"Собрано пользователей: {get_users_count()}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /broadcast Текст рассылки")
        return

    text = " ".join(context.args)
    user_ids = get_all_user_ids()
    success = failed = 0

    await update.message.reply_text(f"Рассылка {len(user_ids)} пользователям...")

    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            success += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)  # Анти-флуд

    await update.message.reply_text(f"Рассылка завершена!\nУспешно: {success}\nНе удалось: {failed}")

# ==================== ГЛОБАЛЬНОЕ ПРИЛОЖЕНИЕ ====================
application = Application.builder().token(TOKEN).build()

application.add_handler(ChatJoinRequestHandler(handle_join_request))
application.add_handler(CallbackQueryHandler(captcha_callback, pattern=r"^captcha_"))
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CommandHandler("broadcast", broadcast))

init_db()

# ==================== ПОЛЛИНГ В ФОНЕ ====================
def run_polling():
    logger.info("Запуск Telegram polling в фоновом потоке с новым event loop")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        loop.run_forever()
    except Exception as e:
        logger.error(f"Polling упал: {e}")
    finally:
        loop.run_until_complete(application.stop())
        loop.run_until_complete(application.shutdown())
        loop.close()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    polling_thread = Thread(target=run_polling, daemon=True)
    polling_thread.start()

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Flask стартует на порту {port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
