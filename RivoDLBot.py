import os
import json
import logging
import asyncio
import time
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAX_FILE_SIZE = 1000 * 1024 * 1024
COOLDOWN_SECONDS = 2
DATA_FILE = "bot_data.json"

user_cooldowns = {}
bot_start_time = datetime.now()


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "total_downloads": 0}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


bot_data = load_data()


class UserManager:

    @staticmethod
    def register_user(user):
        uid = str(user.id)
        if uid not in bot_data["users"]:
            bot_data["users"][uid] = {
                "username": user.username,
                "join_date": datetime.now().isoformat(),
                "downloads": 0
            }
            save_data(bot_data)

    @staticmethod
    def add_download(user_id):
        uid = str(user_id)
        bot_data["users"][uid]["downloads"] += 1
        bot_data["total_downloads"] += 1
        save_data(bot_data)


class DownloadManager:

    def __init__(self):
        self.opts = {
            "format": "best[filesize<1000M]/best",
            "quiet": True
        }

    async def download(self, url, folder, mp3=False):

        ydl_opts = self.opts.copy()

        if mp3:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3"
                }]
            })

        ydl_opts["outtmpl"] = f"{folder}/%(title)s.%(ext)s"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:

                info = ydl.extract_info(url, download=False)

                if info.get("filesize") and info["filesize"] > MAX_FILE_SIZE:
                    return None, "too_large"

                ydl.download([url])

                files = list(Path(folder).glob("*"))
                if files:
                    return str(files[0]), None

        except Exception as e:
            logger.error(e)

        return None, "error"


download_manager = DownloadManager()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    UserManager.register_user(user)

    text = (
        "🎬 *Universal Media Downloader*\n\n"
        "Send me a video link from:\n"
        "YouTube / Instagram / TikTok / Twitter / Facebook\n\n"
        "Or use /mp3 to convert to audio"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def mp3_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["mp3"] = True

    await update.message.reply_text(
        "🎵 MP3 mode enabled.\nSend video link.",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user
    uid = user.id

    UserManager.register_user(user)

    if not update.message.text:
        return

    url = update.message.text.strip()

    now = time.time()

    if uid in user_cooldowns:
        if now - user_cooldowns[uid] < COOLDOWN_SECONDS:
            await update.message.reply_text("⏳ Wait a moment.")
            return

    user_cooldowns[uid] = now

    temp_dir = tempfile.mkdtemp()

    try:

        msg = await update.message.reply_text("⏳ Processing...")

        mp3 = context.user_data.get("mp3", False)

        file_path, error = await download_manager.download(url, temp_dir, mp3)

        if error:
            await msg.edit_text("❌ Download failed.")
            return

        if file_path:

            UserManager.add_download(uid)

            await msg.delete()

            size = os.path.getsize(file_path) / (1024 * 1024)

            with open(file_path, "rb") as f:

                if mp3:

                    await update.message.reply_audio(
                        audio=f,
                        caption=f"🎵 MP3 Ready\n📏 {size:.1f}MB"
                    )

                else:

                    await update.message.reply_document(
                        document=f,
                        caption=f"📥 Downloaded\n📏 {size:.1f}MB"
                    )

            footer = (
                f"📊 Total downloads: {bot_data['total_downloads']}\n"
                f"Developer: @NEOBLADE70"
            )

            await update.message.reply_text(footer)

            context.user_data["mp3"] = False

    except Exception as e:

        logger.error(e)

        await update.message.reply_text("❌ Error occurred.")

    finally:

        try:
            shutil.rmtree(temp_dir)
        except:
            pass


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    users = len(bot_data["users"])
    downloads = bot_data["total_downloads"]

    uptime = datetime.now() - bot_start_time

    text = (
        f"📊 Bot Stats\n\n"
        f"Users: {users}\n"
        f"Downloads: {downloads}\n"
        f"Uptime: {uptime}"
    )

    await update.message.reply_text(text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.error(msg="Exception:", exc_info=context.error)


def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mp3", mp3_mode))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
