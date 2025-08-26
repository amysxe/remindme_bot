# bot.py
import os
import logging
import uuid
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# -------- CONFIG ----------
BOT_TOKEN = os.getenv("TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ TOKEN not set. Please add it in Railway → Variables")

JKT = ZoneInfo("Asia/Jakarta")  # UTC+7

# in-memory stores
tasks = {}  # {user_id: [task1, task2, ...]}
pending_reminders = {}  # {uid: {"user_id":..., "task":..., "task_index":...}}

# scheduler & application placeholders
scheduler = AsyncIOScheduler()
application = None  # will be set in main()

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Hello {user.first_name}! 👋\n\n"
        "I’m your ToDo & Reminder Bot.\n\n"
        "Commands:\n"
        "➕ /add <task> - Add new task\n"
        "📋 /list - Show your tasks\n"
        "⏰ /remind <task_number> in <minutes>\n"
        "⏰ /remind <task_number> at <HH:MM> (UTC+7)\n"
    )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task = " ".join(context.args).strip()
    if not task:
        await update.message.reply_text("⚠️ Please provide a task. Example: `/add Buy milk`", parse_mode="Markdown")
        return

    tasks.setdefault(user_id, []).append(task)
    await update.message.reply_text(f"✅ Task added: {task}")


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = tasks.get(user_id, [])
    if not user_tasks:
        await update.message.reply_text("📭 Your todo list is empty.")
        return

    text = "📋 *Your Tasks:*\n"
    for i, task in enumerate(user_tasks, start=1):
        text += f"{i}. {task}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = tasks.get(user_id, [])
    if not user_tasks:
        await update.message.reply_text("⚠️ You don’t have any tasks yet.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage:\n/remind <task_number> in <minutes>\n/remind <task_number> at <HH:MM>\n"
            "Example: `/remind 1 in 10` or `/remind 2 at 14:30`"
        )
        return

    # parse task number
    try:
        task_index = int(context.args[0]) - 1
        task = user_tasks[task_index]
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid task number.")
        return

    mode = context.args[1].lower()
    if mode == "in":
        try:
            minutes = int(context.args[2])
            run_time = datetime.now(JKT) + timedelta(minutes=minutes)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: `/remind 1 in 10` (10 is minutes).")
            return
    elif mode == "at":
        try:
            time_str = context.args[2]
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(JKT)
            run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_time <= now:
                run_time += timedelta(days=1)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: `/remind 1 at 14:30` (HH:MM in UTC+7).")
            return
    else:
        await update.message.reply_text("⚠️ Use `in <minutes>` or `at <HH:MM>`.")
        return

    # schedule job (send_reminder will create its own uid)
    scheduler.add_job(send_reminder, "date", run_date=run_time, args=[user_id, task, task_index])
    await update.message.reply_text(f"⏰ Reminder set for task: {task}\nScheduled at: {run_time.isoformat()}")


# -------- Reminder sender & callbacks ----------
async def send_reminder(user_id: int, task: str, task_index: int):
    """This runs inside the event loop (AsyncIOScheduler)."""
    bot = application.bot  # use the application global
    try:
        chat = await bot.get_chat(user_id)
        name = getattr(chat, "first_name", None) or getattr(chat, "full_name", None) or getattr(chat, "username", None) or "there"
    except Exception:
        name = "there"

    uid = uuid.uuid4().hex
    pending_reminders[uid] = {"user_id": user_id, "task": task, "task_index": task_index}

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"done_{uid}"),
            InlineKeyboardButton("❌ No", callback_data=f"notdone_{uid}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await bot.send_message(
        chat_id=user_id,
        text=f"⏰ Reminder, {name}! You need to do:\n👉 {task}",
        reply_markup=reply_markup,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    uid = data[1]

    info = pending_reminders.get(uid)
    if not info:
        await query.edit_message_text("⚠️ This reminder is no longer available (maybe expired).")
        return

    user_id = info["user_id"]
    task = info["task"]
    task_index = info["task_index"]

    # DONE
    if action == "done":
        user_tasks = tasks.get(user_id, [])
        removed = False
        # try remove by exact value first
        if task in user_tasks:
            user_tasks.remove(task)
            removed = True
        else:
            # fallback to index
            if 0 <= task_index < len(user_tasks):
                user_tasks.pop(task_index)
                removed = True

        if removed:
            await query.edit_message_text(f"🎉 Great! Task completed and removed:\n👉 {task}")
        else:
            await query.edit_message_text("⚠️ Task not found (it may have been removed earlier).")

        pending_reminders.pop(uid, None)
        return

    # NOT DONE -> show snooze buttons
    if action == "notdone":
        keyboard = [
            [
                InlineKeyboardButton("5 min", callback_data=f"snooze_{uid}_5"),
                InlineKeyboardButton("10 min", callback_data=f"snooze_{uid}_10"),
                InlineKeyboardButton("30 min", callback_data=f"snooze_{uid}_30"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"⏳ Noted. When should I remind you again for:\n👉 {task}",
            reply_markup=reply_markup,
        )
        return

    # SNOOZE
    if action == "snooze":
        try:
            minutes = int(data[2])
        except (IndexError, ValueError):
            await query.edit_message_text("⚠️ Invalid snooze value.")
            pending_reminders.pop(uid, None)
            return

        run_time = datetime.now(JKT) + timedelta(minutes=minutes)
        # schedule a new reminder (this will create a new uid)
        scheduler.add_job(send_reminder, "date", run_date=run_time, args=[user_id, task, task_index])

        await query.edit_message_text(f"🔔 Okay! I’ll remind you again in {minutes} minutes:\n👉 {task}")
        pending_reminders.pop(uid, None)
        return


# -------- Main ----------
def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()

    # start scheduler only after the app event loop is ready
    async def on_startup(app):
        scheduler.start()
        logger.info("✅ Scheduler started")

    application.post_init = on_startup

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_task))
    application.add_handler(CommandHandler("list", list_tasks))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Bot is running...")
    application.run_polling()


if __name__ == "__main__":
    main()
