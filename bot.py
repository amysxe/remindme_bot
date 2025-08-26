# bot.py
import os
import logging
import uuid
import asyncio
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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


# -------- Bot Command Setup ----------
async def set_bot_commands(app):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("add", "Add a new task"),
        BotCommand("list", "Show your tasks"),
        BotCommand("remind", "Set a reminder for a task"),
        BotCommand("delete", "Delete a task"),
    ]
    await app.bot.set_my_commands(commands)


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
        "🗑️ /delete <task_number> - Delete a task"
    )


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    task = text[len("/add "):].strip() if text.lower().startswith("/add ") else " ".join(context.args).strip()

    if not task:
        await update.message.reply_text(
            "⚠️ Please provide a task. Example: `/add Buy milk`",
            parse_mode="Markdown"
        )
        return

    tasks.setdefault(user_id, []).append(task)
    await update.message.reply_text(f"✅ Task added: *__{task}__*", parse_mode="MarkdownV2")


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


async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = tasks.get(user_id, [])
    if not user_tasks:
        await update.message.reply_text("🗑️ You don’t have any tasks to delete.")
        return

    if len(context.args) == 0:
        await update.message.reply_text("⚠️ Please provide the task number. Example: /delete 2")
        return

    try:
        task_index = int(context.args[0]) - 1
        if task_index < 0 or task_index >= len(user_tasks):
            await update.message.reply_text("❌ Invalid task number.")
            return

        deleted_task = user_tasks.pop(task_index)
        await update.message.reply_text(f"🗑️ Deleted task: *__{deleted_task}__*", parse_mode="MarkdownV2")
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid task number. Example: /delete 2")


# -------- Remind command with task selection dropdown ----------
def schedule_reminder(run_time, user_id, task, task_index):
    # APScheduler is sync, wrap async in create_task
    scheduler.add_job(lambda: asyncio.create_task(send_reminder(user_id, task, task_index)), 'date', run_date=run_time)


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_tasks = tasks.get(user_id, [])
    if not user_tasks:
        await update.message.reply_text("⚠️ You don’t have any tasks yet.")
        return

    # If user only types /remind → show inline keyboard to select task
    if len(context.args) == 0:
        keyboard = [[InlineKeyboardButton(f"{i+1}. {t}", callback_data=f"select_{i}")] for i, t in enumerate(user_tasks)]
        await update.message.reply_text("Select a task to set a reminder:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # If user types /remind <task_number> in <minutes> or at <HH:MM>
    if len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ Usage:\n/remind <task_number> in <minutes>\n/remind <task_number> at <HH:MM>\n"
            "Example: `/remind 1 in 10` or `/remind 2 at 14:30`",
            parse_mode="MarkdownV2"
        )
        return

    try:
        task_index = int(context.args[0]) - 1
        task = user_tasks[task_index]
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid task number.")
        return

    mode = context.args[1].lower()
    now = datetime.now(JKT)
    if mode == "in":
        try:
            minutes = int(context.args[2])
            run_time = now + timedelta(minutes=minutes)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: `/remind 1 in 10` (10 is minutes).")
            return
    elif mode == "at":
        try:
            hour, minute = map(int, context.args[2].split(":"))
            run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_time <= now:
                run_time += timedelta(days=1)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: `/remind 1 at 14:30` (HH:MM in UTC+7).")
            return
    else:
        await update.message.reply_text("⚠️ Use `in <minutes>` or `at <HH:MM>`.")
        return

    formatted_time = run_time.strftime("%d %b %Y, %H:%M (UTC+7)")
    schedule_reminder(run_time, user_id, task, task_index)

    # Immediate feedback
    await update.message.reply_text(
        f"✅ Reminder set for task {task_index + 1}: *__{task}__*\n⏰ At {formatted_time}",
        parse_mode="MarkdownV2"
    )


# -------- Reminder sender & callbacks ----------
async def send_reminder(user_id: int, task: str, task_index: int):
    bot = application.bot
    try:
        chat = await bot.get_chat(user_id)
        name = getattr(chat, "first_name", None) or getattr(chat, "full_name", None) or getattr(chat, "username", None) or "there"
    except Exception:
        name = "there"

    uid = uuid.uuid4().hex
    pending_reminders[uid] = {"user_id": user_id, "task": task, "task_index": task_index}

    keyboard = [
        [
            InlineKeyboardButton("✅ Complete", callback_data=f"complete_{uid}"),
            InlineKeyboardButton("⏰ Later", callback_data=f"later_{uid}")
        ]
    ]
    await bot.send_message(
        chat_id=user_id,
        text=f"⏰ Reminder, *{name}*! You need to do __*{task}*__ now ⏳",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    uid = data[1]

    # SELECT task from dropdown → show how to set reminder next
    if action == "select":
        task_index = int(uid)
        user_id = query.from_user.id
        task = tasks[user_id][task_index]
        await query.edit_message_text(
            f"Selected task: *__{task}__*\n\nNow use `/remind {task_index+1} in <minutes>` or `/remind {task_index+1} at <HH:MM>` to set a reminder.",
            parse_mode="MarkdownV2"
        )
        return

    info = pending_reminders.get(uid)
    if not info:
        await query.edit_message_text("⚠️ This reminder is no longer available.")
        return

    user_id = info["user_id"]
    task = info["task"]
    task_index = info["task_index"]
    user_tasks = tasks.get(user_id, [])

    if action == "complete":
        removed = False
        if task in user_tasks:
            user_tasks.remove(task)
            removed = True
        elif 0 <= task_index < len(user_tasks):
            user_tasks.pop(task_index)
            removed = True

        if removed:
            await query.edit_message_text(f"✅ Task completed and removed: *__{task}__*", parse_mode="MarkdownV2")
        else:
            await query.edit_message_text("⚠️ Task not found.")

        pending_reminders.pop(uid, None)
        return

    if action == "later":
        keyboard = [
            [
                InlineKeyboardButton("5 min", callback_data=f"snooze_{uid}_5"),
                InlineKeyboardButton("10 min", callback_data=f"snooze_{uid}_10"),
                InlineKeyboardButton("30 min", callback_data=f"snooze_{uid}_30")
            ],
            [InlineKeyboardButton("⬅ Back", callback_data=f"back_{uid}")]
        ]
        await query.edit_message_text(
            f"⏰ How many minutes do you want to be reminded again for *__{task}__*?",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if action == "snooze":
        minutes = int(data[2])
        run_time = datetime.now(JKT) + timedelta(minutes=minutes)
        schedule_reminder(run_time, user_id, task, task_index)
        await query.edit_message_text(f"🔔 Okay! I’ll remind you again in {minutes} minutes:\n👉 {task}")
        pending_reminders.pop(uid, None)
        return

    if action == "back":
        keyboard = [
            [
                InlineKeyboardButton("✅ Complete", callback_data=f"complete_{uid}"),
                InlineKeyboardButton("⏰ Later", callback_data=f"later_{uid}")
            ]
        ]
        await query.edit_message_text(
            f"⏰ Reminder, *{query.from_user.first_name}*! You need to do __*{task}*__ now ⏳",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return


# -------- Main ----------
def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()

    # handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_task))
    application.add_handler(CommandHandler("list", list_tasks))
    application.add_handler(CommandHandler("delete", delete_task))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        scheduler.start()
        await set_bot_commands(app)
        logger.info("✅ Scheduler started and bot commands set")

    logger.info("🚀 Bot is running...")
    # pass post_init correctly
    application.run_polling(post_init=on_startup)

if __name__ == "__main__":
    main()
