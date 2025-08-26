import os
import pytz
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIG ---
BOT_TOKEN = os.getenv("TOKEN")  # Your bot token from @BotFather
tasks = {}  # {user_id: [task1, task2, ...]}
scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Jakarta"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Start ---
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


# --- Add Task ---
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task = " ".join(context.args)
    if not task:
        await update.message.reply_text("⚠️ Please provide a task. Example: `/add Buy milk`", parse_mode="Markdown")
        return

    tasks.setdefault(user_id, []).append(task)
    await update.message.reply_text(f"✅ Task added: {task}")


# --- List Tasks ---
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in tasks or not tasks[user_id]:
        await update.message.reply_text("📭 Your todo list is empty.")
        return

    text = "📋 *Your Tasks:*\n"
    for i, task in enumerate(tasks[user_id], start=1):
        text += f"{i}. {task}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# --- Reminder ---
async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in tasks or not tasks[user_id]:
        await update.message.reply_text("⚠️ You don’t have any tasks yet.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ Usage:\n/remind <task_number> in <minutes>\n/remind <task_number> at <HH:MM>"
        )
        return

    try:
        task_index = int(context.args[0]) - 1
        task = tasks[user_id][task_index]
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Invalid task number.")
        return

    if context.args[1] == "in":
        try:
            minutes = int(context.args[2])
            run_time = datetime.now(pytz.timezone("Asia/Jakarta")) + timedelta(minutes=minutes)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: /remind 1 in 10")
            return
    elif context.args[1] == "at":
        try:
            time_str = context.args[2]
            hour, minute = map(int, time_str.split(":"))
            now = datetime.now(pytz.timezone("Asia/Jakarta"))
            run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_time < now:
                run_time += timedelta(days=1)  # schedule for tomorrow if time already passed
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Example: /remind 1 at 14:30")
            return
    else:
        await update.message.reply_text("⚠️ Use `in <minutes>` or `at <HH:MM>`.", parse_mode="Markdown")
        return

    scheduler.add_job(
        send_reminder,
        "date",
        run_date=run_time,
        args=[context, user_id, task, task_index],
    )
    await update.message.reply_text(f"⏰ Reminder set for task: {task}")


# --- Reminder Message ---
async def send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id, task, task_index):
    chat = await context.bot.get_chat(user_id)
    name = chat.first_name or chat.full_name or "there"

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data=f"done_{user_id}_{task_index}"),
            InlineKeyboardButton("❌ No", callback_data=f"notdone_{user_id}_{task_index}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=user_id,
        text=f"⏰ Reminder, {name}! You need to do:\n👉 {task}",
        reply_markup=reply_markup,
    )


# --- Handle Button Clicks ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action, user_id, task_index = data[0], int(data[1]), int(data[2])

    if action == "done":
        if user_id in tasks and 0 <= task_index < len(tasks[user_id]):
            task = tasks[user_id].pop(task_index)
            await query.edit_message_text(f"🎉 Great job! Task completed and removed:\n👉 {task}")
        else:
            await query.edit_message_text("⚠️ Task not found.")
    elif action == "notdone":
        keyboard = [
            [InlineKeyboardButton("5 min", callback_data=f"snooze_{user_id}_{task_index}_5")],
            [InlineKeyboardButton("10 min", callback_data=f"snooze_{user_id}_{task_index}_10")],
            [InlineKeyboardButton("30 min", callback_data=f"snooze_{user_id}_{task_index}_30")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⏳ How many more minutes do you want to be reminded again?",
            reply_markup=reply_markup,
        )
    elif action == "snooze":
        minutes = int(data[3])
        task = tasks[user_id][task_index]
        run_time = datetime.now(pytz.timezone("Asia/Jakarta")) + timedelta(minutes=minutes)
        scheduler.add_job(
            send_reminder,
            "date",
            run_date=run_time,
            args=[context, user_id, task, task_index],
        )
        await query.edit_message_text(f"🔔 Snoozed for {minutes} minutes.")


# --- MAIN ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    scheduler.start()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_task))
    application.add_handler(CommandHandler("list", list_tasks))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling()


if __name__ == "__main__":
    main()
