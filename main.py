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
    MessageHandler,
    filters
)

# ===================== НАСТРОЙКИ =====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в Secrets!")

GROUP_CHAT_ID = -1003431090434          # ← измени на свой
ADMIN_ID = 998091317                    # ← твой ID

DB_FILE = "users.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask для Replit (health check)
app = Flask(__name__)

@app.route("/")
def health():
    return "Bot is alive", 200

# ===================== БАЗА ДАННЫХ =====================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TIMESTAMP
            )
        """)

def add_user(user_id: int, username: str | None, full_name: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, username, full_name, datetime.now())
        )

def get_users_count():
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def get_all_user_ids():
    with sqlite3.connect(DB_FILE) as conn:
        return [row[0] for row in conn.execute("SELECT user_id FROM users")]

# ===================== КАПЧА =====================
pending = {}  # user_id → {"answer": int, "expires": datetime, "chat_id": int}

def make_captcha():
    a = random.randint(1, 12)
    b = random.randint(1, 12)
    ans = a + b
    return ans, f"{a} + {b} = ?"

# ===================== ОБРАБОТЧИКИ =====================
async def join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    if not req or req.chat.id != GROUP_CHAT_ID:
        return

    user = req.from_user
    answer, question = make_captcha()

    opts = [answer, answer + random.randint(-5, 5), answer + random.randint(-5, 5)]
    random.shuffle(opts)

    kb = [[InlineKeyboardButton(str(x), callback_data=f"cap_{x}_{user.id}")] for x in opts]

    pending[user.id] = {
        "answer": answer,
        "expires": datetime.now() + timedelta(minutes=5),
        "chat_id": req.chat.id
    }

    try:
        await context.bot.send_message(
            user.id,
            f"Чтобы вступить в группу, реши:\n\n<b>{question}</b>\n\nУ тебя 5 минут ⏳",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить капчу {user.id}: {e}")

async def captcha_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        _, val_str, uid_str = q.data.split("_")
        val = int(val_str)
        uid = int(uid_str)
    except:
        await q.edit_message_text("Некорректная кнопка")
        return

    if uid != q.from_user.id or uid not in pending:
        await q.edit_message_text("Капча не твоя или истекла")
        return

    data = pending[uid]

    if datetime.now() > data["expires"]:
        await q.edit_message_text("Время вышло ⏰")
        del pending[uid]
        return

    if val == data["answer"]:
        add_user(uid, q.from_user.username, q.from_user.full_name)

        text = (
            "🎉 <b>Отлично!</b>\n\n"
            "Ты прошёл проверку. Заявка <b>находится в обработке</b>.\n"
            "Мы проверим её в ближайшее время и добавим тебя в группу 🚀\n"
            "Спасибо за терпение!"
        )

        photo = "https://i.imgur.com/0Z8Z8Z8.jpeg"  # ← можно заменить

        await context.bot.send_photo(uid, photo, caption=text, parse_mode="HTML")
        await q.edit_message_text("✅ Пройдено! Жди добавления.")
    else:
        await q.edit_message_text("❌ Неверный ответ.")

    del pending[uid]

async def start(update: Update, _):
    u = update.effective_user
    add_user(u.id, u.username, u.full_name)
    await update.message.reply_text("Привет! Я бот группы. Подай заявку на вступление — пришлю капчу.")

async def stats(update: Update, _):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(f"Пользователей в базе: {get_users_count()}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not context.args:
        await update.message.reply_text("Использование: /broadcast текст сообщения")
        return

    text = " ".join(context.args)
    users = get_all_user_ids()
    ok = fail = 0

    await update.message.reply_text(f"Рассылаю {len(users)} пользователям...")

    for uid in users:
        try:
            await context.bot.send_message(uid, text)
            ok += 1
        except:
            fail += 1
        await asyncio.sleep(0.04)

    await update.message.reply_text(f"Готово!\nУспешно: {ok}\nОшибок: {fail}")

# ===================== ЗАПУСК =====================
application = Application.builder().token(TOKEN).build()

application.add_handler(ChatJoinRequestHandler(join_handler))
application.add_handler(CallbackQueryHandler(captcha_handler, pattern="^cap_"))
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("stats", stats))
application.add_handler(CommandHandler("broadcast", broadcast))

init_db()

def polling_task():
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
    Thread(target=polling_task, daemon=True).start()

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting Flask on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
