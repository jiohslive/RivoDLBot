import os
import yt_dlp
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

queue = asyncio.Queue()

# -----------------
# START
# -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🚀 Universal Downloader\n\n"
        "Send link from:\n"
        "YouTube / Instagram / TikTok / Facebook"
    )

    await update.message.reply_text(text)

# -----------------
# PROGRESS
# -----------------

def progress_hook(d):

    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%')
        print("Downloading:", percent)

# -----------------
# DOWNLOAD
# -----------------

async def process_download(url, message):

    ydl_opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "progress_hooks": [progress_hook],
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        info = ydl.extract_info(url, download=True)

        file_path = ydl.prepare_filename(info)

    size = os.path.getsize(file_path)

    if size > 50 * 1024 * 1024:

        await message.reply_text(
            f"⚠ File too large ({size/1024/1024:.1f}MB)\n"
            "Telegram bot limit = 50MB"
        )

        return

    with open(file_path, "rb") as f:

        await message.reply_document(
            document=f,
            caption="✅ Download complete"
        )

    os.remove(file_path)

# -----------------
# MESSAGE HANDLER
# -----------------

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text

    msg = await update.message.reply_text("⏳ Added to queue...")

    await queue.put((url, msg))

# -----------------
# QUEUE WORKER
# -----------------

async def worker():

    while True:

        url, msg = await queue.get()

        try:
            await process_download(url, msg)
        except Exception as e:
            await msg.reply_text(f"❌ Error: {e}")

        queue.task_done()

# -----------------
# MAIN
# -----------------

async def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    asyncio.create_task(worker())

    print("Bot running...")

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
