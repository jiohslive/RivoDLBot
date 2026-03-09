import os
import yt_dlp
import asyncio
import time
import shutil

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

MAX_TELEGRAM_SIZE = 50 * 1024 * 1024


# -------------------------
# UTIL
# -------------------------

def cleanup():
    for file in os.listdir(DOWNLOAD_FOLDER):
        path = os.path.join(DOWNLOAD_FOLDER, file)
        try:
            os.remove(path)
        except:
            pass


def format_size(size):
    return f"{size / (1024*1024):.1f} MB"


# -------------------------
# PROGRESS BAR
# -------------------------

async def progress(current, total, message):
    percent = current / total * 100

    bar_length = 20
    filled = int(bar_length * percent / 100)

    bar = "█" * filled + "░" * (bar_length - filled)

    try:
        await message.edit_text(
            f"⬇ Downloading...\n\n"
            f"[{bar}] {percent:.1f}%"
        )
    except:
        pass


# -------------------------
# START
# -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "╔════════════════════╗\n"
        "🚀 *Universal Downloader*\n"
        "╚════════════════════╝\n\n"
        "Send any video link to download."
    )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# -------------------------
# ADMIN
# -------------------------

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    files = len(os.listdir(DOWNLOAD_FOLDER))

    await update.message.reply_text(
        f"📊 Bot Stats\n\n"
        f"Files in cache: {files}"
    )


async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    cleanup()

    await update.message.reply_text("🧹 Files cleaned.")


# -------------------------
# DOWNLOAD
# -------------------------

async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text

    msg = await update.message.reply_text("🔎 Processing link...")

    try:

        ydl_opts = {
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            'format': 'best',
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:

            info = ydl.extract_info(url, download=True)

            file_path = ydl.prepare_filename(info)

        size = os.path.getsize(file_path)

        if size > MAX_TELEGRAM_SIZE:

            await msg.edit_text(
                f"⚠ File too large\n\n"
                f"Size: {format_size(size)}\n"
                f"Telegram limit: 50MB"
            )

            return

        await msg.edit_text("📤 Uploading...")

        with open(file_path, "rb") as f:

            await update.message.reply_document(
                document=f,
                supports_streaming=True,
                caption="✅ Download complete"
            )

        os.remove(file_path)

    except Exception as e:

        await msg.edit_text(f"❌ Error\n{str(e)}")


# -------------------------
# MAIN
# -------------------------

def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clean", clean))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download))

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
