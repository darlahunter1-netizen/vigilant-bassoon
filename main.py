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
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")

GROUP_CHAT_ID = -1003431090434      # ← свой ID группы
ADMIN_ID = 998091317                # ← твой ID (для возможного расширения)

DB_FILE = "users.db"

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== FLASK для keep-alive ====================
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Bot is running! 🚀"}), 200

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== БАЗА ДАННЫХ ====================
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
pending_requests = {}

def generate_captcha():
    a = random.randint(3, 12)
    b = random.randint(3, 12)
    answer = a + b
    question = f"{a} + {b} = ?"
    return a, b, answer, question

# ==================== ОБРАБОТЧИКИ ====================
async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    request = update.chat_join_request
    if not request:
        return

    chat = request.chat
    user = request.from_user

    if chat.id != GROUP_CHAT_ID:
        return

    a, b, answer, question = generate_captcha()

    # 3 варианта ответа — правильный и два неправильных
    wrong1 = answer + random.randint(2, 7)
    wrong2 = answer - random.randint(2, 7)
    options = [answer, wrong1, wrong2]
    random.shuffle(options)

    keyboard = [
        [InlineKeyboardButton(str(opt), callback_data=f"cap_{opt}_{user.id}")]
        for opt in options
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    expires = datetime.now() + timedelta(minutes=5)
    pending_requests[user.id] = {
        "expires": expires,
        "answer": answer,
        "chat_id": chat.id,
        "question": question
    }

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                f"👋 Добро пожаловать в заявку на вступление в <b>{chat.title}</b>!\n\n"
                f"Чтобы подтвердить, что вы не робот, решите простую задачу:\n\n"
                f"<b>{question}</b>\n\n"
                f"⏰ У вас 5 минут"
            ),
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        logger.info(f"Отправлена капча пользователю {user.id}")
    except Exception as e:
        logger.error(f"Не удалось отправить капчу {user.id}: {e}")
        await context.bot.decline_chat_join_request(chat_id=chat.id, user_id=user.id)


async def captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, chosen_str, user_id_str = query.data.split("_")
        chosen = int(chosen_str)
        user_id = int(user_id_str)
    except:
        await query.edit_message_text("Некорректные данные кнопки")
        return

    if user_id != query.from_user.id:
        await query.edit_message_text("Эта капча не для вас")
        return

    if user_id not in pending_requests:
        await query.edit_message_text("Капча уже завершена или истекла")
        return

    info = pending_requests[user_id]

    if datetime.now() > info["expires"]:
        await query.edit_message_text("⏰ Время вышло!")
        await context.bot.decline_chat_join_request(
            chat_id=info["chat_id"], user_id=user_id
        )
        del pending_requests[user_id]
        return

    if chosen == info["answer"]:
        add_user(
            user_id,
            query.from_user.username,
            query.from_user.full_name
        )

        welcome_text = (
            "🎉 <b>Поздравляем!</b> 🎉\n\n"
            "Вы успешно прошли проверку!\n\n"
            "Ваша заявка принята и находится на финальной модерации.\n"
            "Оставайтесь на связи — скоро увидимся в группе! 🚀\n\n"
            "Спасибо за терпение ❤️"
        )

        # Можно поменять на свою картинку
        photo_url = "https://i.imgur.com/8vY8YxL.jpeg"

        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=photo_url,
                caption=welcome_text,
                parse_mode="HTML"
            )
        except:
            await context.bot.send_message(
                chat_id=user_id,
                text=welcome_text,
                parse_mode="HTML"
            )

        await query.edit_message_text("✅ Отлично! Заявка принята, скоро добавим в группу!")
    else:
        await query.edit_message_text("❌ Неверный ответ. Попробуйте снова подать заявку.")
        await context.bot.decline_chat_join_request(
            chat_id=info["chat_id"], user_id=user_id
        )

    del pending_requests[user_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.full_name)
    
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Я бот-охранник группы.\n"
        "Если хочешь вступить — просто подай заявку, я отправлю тебе капчу 😊"
    )


async def debug_echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Временный обработчик для отладки"""
    text = update.message.text
    await update.message.reply_text(f"Я получил: {text}\n\nВсё работает!")


# ==================== ЗАПУСК ====================
def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    # Основные обработчики
    application.add_handler(ChatJoinRequestHandler(handle_join_request))
    application.add_handler(CallbackQueryHandler(captcha_callback, pattern="^cap_"))

    # Команды
    application.add_handler(CommandHandler("start", start))

    # Для отладки (можно закомментировать позже)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, debug_echo))

    # Запуск Flask в отдельном потоке (для Replit / Render)
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    print("Bot starting...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=0.8,
        timeout=15
    )


if __name__ == "__main__":
    main()
