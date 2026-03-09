import os
import json
import logging
import asyncio
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
import tempfile
import requests
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode, ChatMemberStatus
import yt_dlp

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration (ENV variables)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MAX_FILE_SIZE = 1000 * 1024 * 1024  # 1000MB
COOLDOWN_SECONDS = 2
DATA_FILE = "bot_data.json"

# Global variables
user_cooldowns = {}
maintenance_mode = False
waiting_for_input = {}  # Track users waiting for input
bot_start_time = datetime.now()
user_temp_data = {}  # Store temporary user data

# Load/Save data functions
def load_data():
    """Load data from JSON file"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Ensure all required keys exist
                if "users" not in data:
                    data["users"] = {}
                if "total_downloads" not in data:
                    data["total_downloads"] = 0
                if "force_channels" not in data:
                    data["force_channels"] = []
                if "banner_url" not in data:
                    data["banner_url"] = None
                if "banner_file_id" not in data:
                    data["banner_file_id"] = None
                if "maintenance_mode" not in data:
                    data["maintenance_mode"] = False
                return data
        except Exception as e:
            logger.error(f"Error loading data: {e}")
            return {"users": {}, "total_downloads": 0, "force_channels": [], "banner_url": None, "banner_file_id": None, "maintenance_mode": False}
    return {"users": {}, "total_downloads": 0, "force_channels": [], "banner_url": None, "banner_file_id": None, "maintenance_mode": False}

def save_data(data):
    """Save data to JSON file"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Initialize data
bot_data = load_data()
force_channels = bot_data.get("force_channels", [])
BANNER_URL = bot_data.get("banner_url")
BANNER_FILE_ID = bot_data.get("banner_file_id")
maintenance_mode = bot_data.get("maintenance_mode", False)

class UserManager:
    @staticmethod
    def register_user(user_id: int, username: str = None, full_name: str = None):
        """Register or update user in database"""
        user_id = str(user_id)
        if user_id not in bot_data["users"]:
            bot_data["users"][user_id] = {
                "username": username,
                "full_name": full_name,
                "join_date": datetime.now().isoformat(),
                "total_downloads": 0,
                "last_active": datetime.now().isoformat(),
                "verified": False  # Track if user has verified subscription
            }
        else:
            bot_data["users"][user_id]["last_active"] = datetime.now().isoformat()
            if username:
                bot_data["users"][user_id]["username"] = username
            if full_name:
                bot_data["users"][user_id]["full_name"] = full_name
        save_data(bot_data)
    
    @staticmethod
    def increment_downloads(user_id: int):
        """Increment user download count"""
        user_id = str(user_id)
        if user_id in bot_data["users"]:
            bot_data["users"][user_id]["total_downloads"] += 1
            bot_data["total_downloads"] += 1
            save_data(bot_data)

    @staticmethod
    def set_verified(user_id: int, verified: bool = True):
        """Set user verification status"""
        user_id = str(user_id)
        if user_id in bot_data["users"]:
            bot_data["users"][user_id]["verified"] = verified
            save_data(bot_data)

    @staticmethod
    def is_verified(user_id: int) -> bool:
        """Check if user is verified"""
        user_id = str(user_id)
        return bot_data["users"].get(user_id, {}).get("verified", False)

    @staticmethod
    def get_stats():
        """Get bot statistics"""
        total_users = len(bot_data["users"])
        total_downloads = bot_data["total_downloads"]
        
        # Calculate active users today
        today = datetime.now().date()
        active_today = 0
        for user in bot_data["users"].values():
            last_active = datetime.fromisoformat(user["last_active"]).date()
            if last_active == today:
                active_today += 1
        
        # Calculate bot uptime
        uptime = datetime.now() - bot_start_time
        days = uptime.days
        hours = uptime.seconds // 3600
        minutes = (uptime.seconds // 60) % 60
        
        return total_users, total_downloads, active_today, f"{days}d {hours}h {minutes}m"

class DownloadManager:
    def __init__(self):
        self.ydl_opts = {
            'format': 'best[filesize<1000M]/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'force_generic_extractor': False,
        }
        
    async def download_video(self, url: str, download_dir: str, extract_audio: bool = False) -> tuple:
        """Download video from URL using yt-dlp"""
        try:
            ydl_opts = self.ydl_opts.copy()
            
            if extract_audio:
                ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': f'{download_dir}/%(title)s.%(ext)s',
                })
            else:
                ydl_opts.update({
                    'outtmpl': f'{download_dir}/%(title)s.%(ext)s',
                })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    # Extract info first
                    info = ydl.extract_info(url, download=False)
                    
                    # Get video title
                    title = info.get('title', 'video')
                    
                    # Check file size if available
                    if 'filesize' in info and info['filesize'] and info['filesize'] > MAX_FILE_SIZE:
                        return None, "file_too_large", title
                    
                    # Download the file
                    ydl.download([url])
                    
                    # Find downloaded file
                    files = list(Path(download_dir).glob('*'))
                    if files:
                        return str(files[0]), None, title
                    
                except Exception as e:
                    logger.error(f"Download error: {str(e)}")
                    error_str = str(e).lower()
                    if "private" in error_str or "private video" in error_str:
                        return None, "private", None
                    elif "unsupported" in error_str:
                        return None, "unsupported", None
                    elif "copyright" in error_str:
                        return None, "copyright", None
                    elif "age" in error_str or "18+" in error_str:
                        return None, "age_restricted", None
                    else:
                        return None, "unknown", None
            
            return None, "unknown", None
            
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return None, "unknown", None

# Initialize managers
user_manager = UserManager()
download_manager = DownloadManager()

async def extract_channel_info(text: str) -> tuple:
    """Extract channel username or invite link from text"""
    text = text.strip()
    
    # Check if it's an invite link
    if "t.me/+" in text or "telegram.me/+" in text:
        # Extract invite hash
        parsed = urlparse(text)
        path = parsed.path
        if "/+" in path:
            invite_hash = path.split("/+")[-1]
            return "private", invite_hash
    
    # Check if it's a public channel link
    elif "t.me/" in text or "telegram.me/" in text:
        parsed = urlparse(text)
        path = parsed.path.strip('/')
        if path and not path.startswith('+'):
            return "public", path
    
    # Check if it's just a username
    elif text.startswith('@'):
        return "public", text[1:]
    elif text and not text.startswith('+'):
        return "public", text
    
    return None, None

async def get_subscription_keyboard():
    """Get inline keyboard for force subscription"""
    keyboard = []
    for channel in force_channels:
        channel_type = channel.get('type', 'public')
        if channel_type == 'public':
            keyboard.append([InlineKeyboardButton(f"📢 Join @{channel['identifier']}", url=f"https://t.me/{channel['identifier']}")])
        else:
            keyboard.append([InlineKeyboardButton(f"📢 Join Private Channel", url=f"https://t.me/+{channel['invite_hash']}")])
    
    keyboard.append([InlineKeyboardButton("✅ Verify Join", callback_data="verify_subscription")])
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_subscription")])
    return InlineKeyboardMarkup(keyboard)

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined all required channels"""
    if not force_channels:
        return True
    
    # If user is already verified, return True
    if user_manager.is_verified(user_id):
        return True
    
    all_joined = True
    for channel in force_channels:
        try:
            channel_type = channel.get('type', 'public')
            
            if channel_type == 'public':
                # Check public channel membership
                chat = await context.bot.get_chat(f"@{channel['identifier']}")
                try:
                    member = await context.bot.get_chat_member(chat_id=chat.id, user_id=user_id)
                    if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                        all_joined = False
                        break
                except Exception as e:
                    logger.error(f"Error checking membership: {e}")
                    all_joined = False
                    break
            else:
                # For private channels, we rely on user's verification
                # You can implement a more sophisticated check if needed
                pass
                
        except Exception as e:
            logger.error(f"Error checking subscription for {channel}: {str(e)}")
            continue
    
    # If all joined, set user as verified
    if all_joined and force_channels:
        user_manager.set_verified(user_id, True)
    
    return all_joined

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_manager.register_user(user.id, user.username, user.full_name)
    
    # Clear any waiting states
    if user.id in waiting_for_input:
        del waiting_for_input[user.id]
    
    # Check force subscription
    if force_channels and not await check_subscription(user.id, context):
        await update.message.reply_text(
            "⚠️ *Access Denied!*\n\nYou must join the following channels to use this bot:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await get_subscription_keyboard()
        )
        return
    
    # Reset verification if no channels
    if not force_channels:
        user_manager.set_verified(user.id, False)
    
    # Create main menu keyboard
    keyboard = [
        [InlineKeyboardButton("🎬 YouTube", callback_data="platform_youtube"),
         InlineKeyboardButton("📸 Instagram", callback_data="platform_instagram")],
        [InlineKeyboardButton("🎵 TikTok", callback_data="platform_tiktok"),
         InlineKeyboardButton("🐦 Twitter/X", callback_data="platform_twitter")],
        [InlineKeyboardButton("📘 Facebook", callback_data="platform_facebook"),
         InlineKeyboardButton("🎧 MP3 Converter", callback_data="mp3_mode")],
        [InlineKeyboardButton("📊 My Stats", callback_data="user_stats"),
         InlineKeyboardButton("ℹ️ About", callback_data="about")]
    ]
    
    # Add admin button for admin
    if user.id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    caption = (
        "🎯 *Universal Media Downloader* 🚀\n\n"
        "📥 *Download videos from:*\n"
        "• YouTube • Instagram • TikTok\n"
        "• Twitter/X • Facebook\n\n"
        "⚡ *Fast & Reliable*\n"
        "💯 *Completely Free*\n"
        "🔒 *No Login Required*\n\n"
        f"👨‍💻 *Developer:* @RivoBots"
    )
    
    try:
        if BANNER_FILE_ID:
            await update.message.reply_photo(
                photo=BANNER_FILE_ID,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        elif BANNER_URL:
            await update.message.reply_photo(
                photo=BANNER_URL,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending start message: {e}")
        await update.message.reply_text(
            caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_manager.register_user(user.id, user.username, user.full_name)
    
    # Check if it's admin callback
    if query.data.startswith("admin_"):
        if user.id != ADMIN_ID:
            await query.message.reply_text("⛔ *Unauthorized!*", parse_mode=ParseMode.MARKDOWN)
            return
        await admin_callback(update, context)
        return
    
    # Check force subscription for non-admin users
    if force_channels and not await check_subscription(user.id, context):
        await query.message.reply_text(
            "⚠️ *Access Denied!*\n\nYou must join the required channels first!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await get_subscription_keyboard()
        )
        return
    
    if query.data == "verify_subscription" or query.data == "refresh_subscription":
        if await check_subscription(user.id, context):
            await query.message.delete()
            await start(update, context)
        else:
            await query.message.reply_text(
                "❌ *Not Joined Yet!*\n\nPlease join all channels and try again.\n"
                "After joining, click Verify Join button.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=await get_subscription_keyboard()
            )
        return
    
    if query.data == "user_stats":
        user_id = str(user.id)
        if user_id in bot_data["users"]:
            user_info = bot_data["users"][user_id]
            join_date = datetime.fromisoformat(user_info["join_date"]).strftime("%Y-%m-%d")
            stats_text = (
                f"📊 *Your Statistics*\n\n"
                f"📅 *Joined:* {join_date}\n"
                f"📥 *Downloads:* {user_info['total_downloads']}\n"
                f"🆔 *User ID:* `{user.id}`\n"
                f"👤 *Username:* @{user.username or 'None'}\n"
                f"✅ *Verified:* {'Yes' if user_info.get('verified', False) else 'No'}"
            )
            await query.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        return
    
    if query.data == "about":
        total_users, total_downloads, active_today, uptime = user_manager.get_stats()
        about_text = (
            f"ℹ️ *About Bot*\n\n"
            f"🤖 *Name:* Universal Media Downloader\n"
            f"👨‍💻 *Developer:* @RivoBots\n"
            f"📊 *Version:* 3.0 Ultimate\n\n"
            f"📈 *Global Stats:*\n"
            f"• Total Users: {total_users}\n"
            f"• Total Downloads: {total_downloads}\n"
            f"• Active Today: {active_today}\n"
            f"• Uptime: {uptime}\n\n"
            f"⚡ *Powered by:* yt-dlp & python-telegram-bot"
        )
        await query.message.reply_text(about_text, parse_mode=ParseMode.MARKDOWN)
        return
    
    if query.data == "mp3_mode":
        context.user_data['mp3_mode'] = True
        await query.message.reply_text(
            "🎵 *MP3 Mode Activated!*\n\n"
            "Send me any video link and I'll convert it to MP3 audio.\n"
            "Supported platforms: YouTube, Instagram, TikTok, Twitter, Facebook",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if query.data.startswith("platform_"):
        platform = query.data.replace("platform_", "").upper()
        context.user_data['platform'] = platform
        context.user_data['mp3_mode'] = False
        await query.message.reply_text(
            f"📥 *{platform} Downloader Selected*\n\n"
            "Please send me the video link:",
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user messages"""
    user = update.effective_user
    user_id = user.id
    
    # Check if user is in input mode
    if user_id in waiting_for_input:
        await handle_admin_input(update, context)
        return
    
    # Check maintenance mode
    if maintenance_mode and user_id != ADMIN_ID:
        await update.message.reply_text(
            "🔧 *Bot is under maintenance.*\nPlease try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check force subscription
    if force_channels and not await check_subscription(user_id, context):
        await update.message.reply_text(
            "⚠️ *Access Denied!*\n\nYou must join the required channels to use this bot.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=await get_subscription_keyboard()
        )
        return
    
    # Check if message contains URL
    if not (update.message.text and any(ext in update.message.text.lower() for ext in ['.com', '.org', '.net', 'http', 'www', 'youtu', 'instagram', 'tiktok', 'twitter', 'facebook'])):
        await update.message.reply_text(
            "❌ *Please send a valid URL!*",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Cooldown check
    current_time = time.time()
    if user_id in user_cooldowns:
        time_diff = current_time - user_cooldowns[user_id]
        if time_diff < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - time_diff)
            await update.message.reply_text(f"⏳ Please wait {remaining} seconds...")
            return
    
    url = update.message.text.strip()
    user_cooldowns[user_id] = current_time
    
    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Send processing message
        processing_msg = await update.message.reply_text(
            "⏳ *Processing...*\nThis may take a few moments.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Download video/audio
        extract_audio = context.user_data.get('mp3_mode', False)
        file_path, error, title = await download_manager.download_video(url, temp_dir, extract_audio)
        
        if error:
            error_messages = {
                "file_too_large": "❌ *File too large!*\nMaximum size is 1GB.",
                "private": "❌ *Cannot download private videos.*",
                "unsupported": "❌ *Unsupported URL or platform.*",
                "copyright": "❌ *Copyright protected content.*",
                "age_restricted": "❌ *Age-restricted content.*",
                "unknown": "❌ *Download failed.*\nPlease check the URL and try again."
            }
            await processing_msg.edit_text(
                error_messages.get(error, "❌ *Download failed.*"),
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        if file_path:
            # Increment download count
            user_manager.increment_downloads(user_id)
            
            # Send the file
            await processing_msg.delete()

file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
file_name = os.path.basename(file_path)

with open(file_path, 'rb') as f:
    if extract_audio:
        await update.message.reply_audio(
            audio=f,
            title=title[:50] + "..." if len(title) > 50 else title,
            performer="Universal Downloader",
            caption=f"🎵 *Converted to MP3*\n📏 Size: {file_size:.1f}MB",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_document(
            document=f,
            caption=f"📥 *Download Successful!*\n📏 Size: {file_size:.1f}MB",
            parse_mode=ParseMode.MARKDOWN
        )
                         
           # Send stylish footer
                footer = (
                    f"╔════════════════════════════╗\n"
                    f"   © 2026 Universal Media Downloader\n"
                    f"   👨‍💻 Developer: @RivoBots\n"
                    f"   📊 Download #{bot_data['total_downloads']:,}\n"
                    f"   ⏱️ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"╚════════════════════════════╝"
                )
                await update.message.reply_text(f"`{footer}`", parse_mode=ParseMode.MARKDOWN)
            
            # Reset MP3 mode
            context.user_data['mp3_mode'] = False
    
    except Exception as e:
        logger.error(f"Error in handle_message: {str(e)}")
        await update.message.reply_text(
            "❌ *An error occurred.*\nPlease try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    finally:
        # Clean up temp directory
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ *Unauthorized Access!*\nThis command is for admins only.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    global maintenance_mode
    
    # Get fresh stats
    total_users, total_downloads, active_today, uptime = user_manager.get_stats()
    
    # Create admin keyboard
    keyboard = [
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats")
        ],
        [
            InlineKeyboardButton("🔧 Maintenance", callback_data="admin_maintenance"),
            InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")
        ],
        [
            InlineKeyboardButton("➖ Remove Channel", callback_data="admin_remove_channel"),
            InlineKeyboardButton("📋 Channels List", callback_data="admin_channels_list")
        ],
        [
            InlineKeyboardButton("👥 Users List", callback_data="admin_users_list"),
            InlineKeyboardButton("🖼️ Set Banner", callback_data="admin_set_banner")
        ],
        [
            InlineKeyboardButton("🔄 Reset Verifications", callback_data="admin_reset_verifications"),
            InlineKeyboardButton("📊 Export Users", callback_data="admin_export_users")
        ],
        [
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Create channel list text
    channels_text = ""
    if force_channels:
        for i, channel in enumerate(force_channels, 1):
            ch_type = channel.get('type', 'public')
            if ch_type == 'public':
                channels_text += f"{i}. @{channel['identifier']} (Public)\n"
            else:
                channels_text += f"{i}. Private Channel (Hash: {channel['invite_hash'][:8]}...)\n"
    else:
        channels_text = "No channels configured"
    
    # Count verified users
    verified_users = sum(1 for u in bot_data["users"].values() if u.get("verified", False))
    
    status_text = (
        f"👑 *Admin Panel*\n\n"
        f"📊 *Statistics:*\n"
        f"• Users: {total_users:,}\n"
        f"• Verified: {verified_users:,}\n"
        f"• Downloads: {total_downloads:,}\n"
        f"• Active Today: {active_today}\n"
        f"• Uptime: {uptime}\n\n"
        f"🔧 *Settings:*\n"
        f"• Maintenance: {'🔴 ON' if maintenance_mode else '🟢 OFF'}\n"
        f"• Force Channels: {len(force_channels)}\n"
        f"• Banner: {'✅ Set' if BANNER_FILE_ID or BANNER_URL else '❌ Not Set'}\n\n"
        f"📢 *Force Channels:*\n{channels_text}"
    )
    
    # Check if called from callback or command
    if update.callback_query:
        await update.callback_query.message.edit_text(
            status_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            status_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin button callbacks"""
    query = update.callback_query
    
    global maintenance_mode, force_channels, BANNER_FILE_ID, BANNER_URL, bot_data
    
    if query.data == "back_to_main":
        await query.message.delete()
        await start(update, context)
        return
    
    if query.data == "admin_stats":
        total_users, total_downloads, active_today, uptime = user_manager.get_stats()
        verified_users = sum(1 for u in bot_data["users"].values() if u.get("verified", False))
        
        # Get top users
        top_users = []
        for uid, uinfo in sorted(bot_data["users"].items(), key=lambda x: x[1].get('total_downloads', 0), reverse=True)[:5]:
            username = uinfo.get('username', 'No username')
            downloads = uinfo.get('total_downloads', 0)
            verified = "✅" if uinfo.get("verified", False) else "❌"
            top_users.append(f"• @{username}: {downloads} downloads {verified}")
        
        top_users_text = "\n".join(top_users) if top_users else "No data yet"
        
        stats_text = (
            f"📊 *Detailed Statistics*\n\n"
            f"👥 *Total Users:* {total_users:,}\n"
            f"✅ *Verified Users:* {verified_users:,}\n"
            f"📥 *Total Downloads:* {total_downloads:,}\n"
            f"📅 *Active Today:* {active_today}\n"
            f"⏱️ *Uptime:* {uptime}\n\n"
            f"🏆 *Top Users:*\n{top_users_text}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await query.message.edit_text(
            stats_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_maintenance":
        maintenance_mode = not maintenance_mode
        bot_data["maintenance_mode"] = maintenance_mode
        save_data(bot_data)
        status = "enabled" if maintenance_mode else "disabled"
        await query.answer(f"Maintenance mode {status}!")
        await admin_panel(update, context)
    
    elif query.data == "admin_add_channel":
        waiting_for_input[update.effective_user.id] = 'add_channel'
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")]]
        await query.message.edit_text(
            "📢 *Add Force Channel*\n\n"
            "Send me the channel *link* or *username*:\n\n"
            "• Public channel: `@username` or `https://t.me/username`\n"
            "• Private channel: `https://t.me/+invitehash`\n\n"
            "⚠️ *Important:* I must be an admin in the channel!\n"
            "Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_remove_channel":
        if not force_channels:
            await query.answer("No channels to remove!")
            return
        
        keyboard = []
        for i, channel in enumerate(force_channels):
            ch_type = channel.get('type', 'public')
            if ch_type == 'public':
                display_name = f"@{channel['identifier']}"
            else:
                display_name = f"Private ({channel['invite_hash'][:8]}...)"
            
            keyboard.append([InlineKeyboardButton(f"❌ Remove {display_name}", callback_data=f"remove_channel_{i}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        
        await query.message.edit_text(
            "📢 *Remove Force Channel*\n\nSelect channel to remove:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data.startswith("remove_channel_"):
        index = int(query.data.replace("remove_channel_", ""))
        if 0 <= index < len(force_channels):
            removed = force_channels.pop(index)
            bot_data["force_channels"] = force_channels
            save_data(bot_data)
            
            # Reset all user verifications
            for user_id in bot_data["users"]:
                bot_data["users"][user_id]["verified"] = False
            save_data(bot_data)
            
            ch_type = removed.get('type', 'public')
            if ch_type == 'public':
                display = f"@{removed['identifier']}"
            else:
                display = "Private Channel"
            
            await query.answer(f"Removed {display}!")
            await admin_panel(update, context)
    
    elif query.data == "admin_channels_list":
        if not force_channels:
            await query.answer("No channels configured!")
            return
        
        channels_text = "📢 *Force Channels List*\n\n"
        for i, channel in enumerate(force_channels, 1):
            ch_type = channel.get('type', 'public')
            added_date = channel.get('added_date', 'Unknown')
            
            if ch_type == 'public':
                channels_text += f"{i}. Public: @{channel['identifier']}\n"
            else:
                channels_text += f"{i}. Private: `{channel['invite_hash']}`\n"
            channels_text += f"   Added: {added_date}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        await query.message.edit_text(
            channels_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_users_list":
        total_users = len(bot_data["users"])
        verified_users = sum(1 for u in bot_data["users"].values() if u.get("verified", False))
        
        users_text = f"👥 *Users List (Total: {total_users}, Verified: {verified_users})*\n\n"
        
        # Show last 10 active users
        users_text += "*Last 10 Active Users:*\n"
        sorted_users = sorted(
            bot_data["users"].items(),
            key=lambda x: datetime.fromisoformat(x[1]['last_active']),
            reverse=True
        )[:10]
        
        for uid, uinfo in sorted_users:
            username = uinfo.get('username', 'No username')
            full_name = uinfo.get('full_name', 'Unknown')[:20]
            last_active = datetime.fromisoformat(uinfo['last_active']).strftime("%Y-%m-%d %H:%M")
            downloads = uinfo.get('total_downloads', 0)
            verified = "✅" if uinfo.get("verified", False) else "❌"
            users_text += f"• {verified} @{username} - {downloads} dl\n  {full_name}\n  Last: {last_active}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]
        
        # Split message if too long
        if len(users_text) > 4000:
            users_text = users_text[:4000] + "...\n\n(Truncated)"
        
        await query.message.edit_text(
            users_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_set_banner":
        waiting_for_input[update.effective_user.id] = 'set_banner'
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")]]
        await query.message.edit_text(
            "🖼️ *Set Banner Image*\n\n"
            "Send me the new banner image or image URL:\n"
            "• Send a photo directly\n"
            "• Or send an image URL\n\n"
            "Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_reset_verifications":
        # Reset all user verifications
        for user_id in bot_data["users"]:
            bot_data["users"][user_id]["verified"] = False
        save_data(bot_data)
        
        await query.answer("All verifications reset!")
        await admin_panel(update, context)
    
    elif query.data == "admin_export_users":
        # Create CSV export
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'Username', 'Full Name', 'Join Date', 'Last Active', 'Downloads', 'Verified'])
        
        for uid, uinfo in bot_data["users"].items():
            writer.writerow([
                uid,
                uinfo.get('username', ''),
                uinfo.get('full_name', ''),
                uinfo.get('join_date', ''),
                uinfo.get('last_active', ''),
                uinfo.get('total_downloads', 0),
                uinfo.get('verified', False)
            ])
        
        # Send as file
        await query.message.reply_document(
            document=io.BytesIO(output.getvalue().encode()),
            filename=f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            caption="📊 Users Export"
        )
        
        await query.answer("Export sent!")
    
    elif query.data == "admin_broadcast":
        waiting_for_input[update.effective_user.id] = 'broadcast'
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")]]
        await query.message.edit_text(
            "📢 *Broadcast Message*\n\n"
            "Send me the message to broadcast to all users:\n"
            "• You can send text, photo, video, document, audio\n"
            "• HTML formatting is supported\n\n"
            f"Total users: {len(bot_data['users']):,}\n\n"
            "Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_panel":
        await admin_panel(update, context)

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin input for various actions"""
    user_id = update.effective_user.id
    action = waiting_for_input.get(user_id)
    
    if not action:
        return
    
    global BANNER_FILE_ID, BANNER_URL, bot_data, force_channels
    
    if action == 'add_channel':
        text = update.message.text or update.message.caption or ""
        
        # Extract channel info
        ch_type, identifier = await extract_channel_info(text)
        
        if not ch_type:
            await update.message.reply_text(
                "❌ *Invalid channel format!*\n\n"
                "Please send a valid channel link or username.\n"
                "Example: `@channel` or `https://t.me/+invite`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if channel already exists
        exists = False
        for channel in force_channels:
            if ch_type == 'public' and channel.get('type') == 'public' and channel.get('identifier') == identifier:
                exists = True
                break
            elif ch_type == 'private' and channel.get('type') == 'private' and channel.get('invite_hash') == identifier:
                exists = True
                break
        
        if exists:
            await update.message.reply_text(
                f"❌ *Channel already exists!*",
                parse_mode=ParseMode.MARKDOWN
            )
            del waiting_for_input[user_id]
            await start(update, context)
            return
        
        # Try to verify bot is admin
        try:
            if ch_type == 'public':
                chat = await context.bot.get_chat(f"@{identifier}")
                try:
                    bot_member = await context.bot.get_chat_member(chat_id=chat.id, user_id=context.bot.id)
                    
                    if bot_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                        channel_data = {
                            'type': 'public',
                            'identifier': identifier,
                            'added_date': datetime.now().strftime("%Y-%m-%d %H:%M")
                        }
                        force_channels.append(channel_data)
                        bot_data["force_channels"] = force_channels
                        save_data(bot_data)
                        
                        await update.message.reply_text(
                            f"✅ *Channel @{identifier} added successfully!*\n\n"
                            f"Users will now need to join this channel to use the bot.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    else:
                        await update.message.reply_text(
                            f"❌ *I'm not an admin in @{identifier}!*\n\n"
                            f"Please make me admin first, then try again.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                except Exception as e:
                    await update.message.reply_text(
                        f"❌ *Error accessing channel!*\n\n"
                        f"Make sure I am added as admin to @{identifier}\n"
                        f"Error: {str(e)[:100]}",
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                # For private channels, just add with invite hash
                channel_data = {
                    'type': 'private',
                    'invite_hash': identifier,
                    'added_date': datetime.now().strftime("%Y-%m-%d %H:%M")
                }
                force_channels.append(channel_data)
                bot_data["force_channels"] = force_channels
                save_data(bot_data)
                
                await update.message.reply_text(
                    f"✅ *Private channel added successfully!*\n\n"
                    f"Note: Users will need the invite link to join.\n"
                    f"Make sure the bot is admin in the channel.",
                    parse_mode=ParseMode.MARKDOWN
                )
                
        except Exception as e:
            logger.error(f"Error adding channel: {e}")
            await update.message.reply_text(
                f"❌ *Error adding channel!*\n\n"
                f"Make sure:\n"
                f"1. Channel exists\n"
                f"2. I am admin in the channel\n"
                f"3. Channel is accessible\n\n"
                f"Error: {str(e)[:100]}",
                parse_mode=ParseMode.MARKDOWN
            )
        
        del waiting_for_input[user_id]
        await start(update, context)
    
    elif action == 'set_banner':
        # Handle banner image
        if update.message.photo:
            # Get the largest photo
            photo = update.message.photo[-1]
            BANNER_FILE_ID = photo.file_id
            bot_data["banner_file_id"] = BANNER_FILE_ID
            bot_data["banner_url"] = None
            save_data(bot_data)
            
            await update.message.reply_text(
                "✅ *Banner image updated successfully!*",
                parse_mode=ParseMode.MARKDOWN
            )
        
        elif update.message.text and update.message.text.startswith(('http://', 'https://')):
            # Handle URL
            BANNER_URL = update.message.text
            bot_data["banner_url"] = BANNER_URL
            bot_data["banner_file_id"] = None
            save_data(bot_data)
            
            await update.message.reply_text(
                "✅ *Banner URL updated successfully!*",
                parse_mode=ParseMode.MARKDOWN
            )
        
        else:
            await update.message.reply_text(
                "❌ *Invalid input!*\n\nPlease send a photo or valid image URL.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        del waiting_for_input[user_id]
        await start(update, context)
    
    elif action == 'broadcast':
        # Send processing message
        processing = await update.message.reply_text(
            "📤 *Broadcasting...*",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Prepare message
        message = update.message
        success = 0
        failed = 0
        
        users_to_send = [int(uid) for uid in bot_data["users"].keys()]
        total = len(users_to_send)
        
        await processing.edit_text(f"📤 *Broadcasting...*\nProgress: 0/{total}", parse_mode=ParseMode.MARKDOWN)
        
        for i, uid in enumerate(users_to_send, 1):
            try:
                if message.photo:
                    await message.copy(
                        chat_id=uid,
                        caption=message.caption_html if message.caption else None,
                        parse_mode=ParseMode.HTML
                    )
                elif message.video:
                    await message.copy(
                        chat_id=uid,
                        caption=message.caption_html if message.caption else None,
                        parse_mode=ParseMode.HTML
                    )
                elif message.document:
                    await message.copy(
                        chat_id=uid,
                        caption=message.caption_html if message.caption else None,
                        parse_mode=ParseMode.HTML
                    )
                elif message.text:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=message.text_html if message.text_html else message.text,
                        parse_mode=ParseMode.HTML if message.text_html else None
                    )
                elif message.audio:
                    await message.copy(
                        chat_id=uid,
                        caption=message.caption_html if message.caption else None,
                        parse_mode=ParseMode.HTML
                    )
                elif message.voice:
                    await message.copy(
                        chat_id=uid,
                        caption=message.caption_html if message.caption else None,
                        parse_mode=ParseMode.HTML
                    )
                
                success += 1
                
                # Update progress every 10 messages
                if i % 10 == 0 or i == total:
                    await processing.edit_text(
                        f"📤 *Broadcasting...*\nProgress: {i}/{total}",
                        parse_mode=ParseMode.MARKDOWN
                    )
                
                await asyncio.sleep(0.05)  # Small delay to avoid flood limits
                
            except Exception as e:
                logger.error(f"Broadcast failed for {uid}: {str(e)}")
                failed += 1
        
        await processing.delete()
        
        result_text = (
            f"📊 *Broadcast Complete*\n\n"
            f"✅ *Success:* {success:,}\n"
            f"❌ *Failed:* {failed:,}\n"
            f"📢 *Total Users:* {total:,}"
        )
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
        
        del waiting_for_input[user_id]
        await start(update, context)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    user_id = update.effective_user.id
    
    if user_id in waiting_for_input:
        del waiting_for_input[user_id]
        await update.message.reply_text(
            "✅ *Operation cancelled!*",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "❌ *No operation to cancel.*",
            parse_mode=ParseMode.MARKDOWN
        )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ *An error occurred.*\nPlease try again later.",
                parse_mode=ParseMode.MARKDOWN
            )
    except:
        pass

def main():
    """Main function to run the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("cancel", cancel_command))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Message handlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_message
    ))
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE,
        handle_message
    ))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    logger.info("🚀 Bot is starting...")
    print("""
    ╔════════════════════════════════════╗
    ║  Universal Media Downloader Bot    ║
    ║  Developer: @RivoBots              ║
    ║  Version: 3.0 Ultimate             ║
    ╚════════════════════════════════════╝
    """)
    print("🤖 Bot is running... Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
