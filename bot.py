import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- CONFIG ---
BOT_TOKEN = os.getenv("TOKEN")  # set this in Railway → Variables
todos = {}  # simple in-memory store: {user_id: [task1, task2]}

# --- LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- SCHEDULER ---
scheduler = AsyncIOScheduler()

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    await update.message.reply_text(
        "👋 Hi! I’m your Todo Bot.\n\n"
        "Commands:\n"
        "/add <task> → add a todo\n"
        "/list → show your todos\n"
        "/remind <minutes> <task> → set a reminder"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a task"""
    user_id = update.message.from_user.id
    task = " ".join(context.args)
    if not task:
        return await update.message.reply_text("❌ Usage: /add Buy milk")

    todos.setdefault(user_id, []).append(task)
    await update.message.reply_text(f"✅ Added: {task}")

async def list_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks"""
    user_id = update.message.from_user.id
    tasks = todos.get(user_id, [])
    if not tasks:
        await update.message.reply_text("📭 Your todo list is empty.")
    else:
        msg = "\n".join([f"{i+1}. {t}" for i, t in enumerate(tasks)])
        await update.message.reply_text(f"📝 Your todos:\n{msg}")

async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a reminder"""
    user_id = update.message.from_user.id
    if len(context.args) < 2:
        return await update.message.reply_text("❌ Usage: /remind <minutes> <task>")

    try:
        minutes = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("❌ Minutes must be a number")

    task = " ".join(context.args[1:])
    run_time = datetime.now() + timedelta(minutes=minutes)

    scheduler.add_job(send_reminder, "date", run_date=run_time, args=[context, user_id, task])
    await update.message.reply_text(f"⏰ Reminder set in {minutes} min: {task}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id, task):
    """Send reminder"""
    await context.bot.send_message(chat_id=user_id, text=f"🔔 Reminder: {task}")

# --- MAIN ---
def main():
    if not BOT_TOKEN:
        raise ValueError("❌ TOKEN not set. Please add it in Railway → Variables")

    app = Application.builder().token(BOT_TOKEN).build()
    scheduler.start()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_todos))
    app.add_handler(CommandHandler("remind", remind))

    logger.info("🚀 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
