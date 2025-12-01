import os
import re
import asyncio
import aiohttp
import aiofiles
import ssl
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
from typing import Dict, Optional
import logging
import glob
from aiohttp import web
import threading

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))

app = Client("m3u8_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_data: Dict[int, dict] = {}
active_downloads: Dict[int, bool] = {}

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

QUALITY_MAP = {
    "360p": "360",
    "480p": "480", 
    "720p": "720",
    "1080p": "1080",
}

# Health check web server
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    """Start web server for health checks"""
    app_web = web.Application()
    app_web.router.add_get("/", health_check)
    app_web.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Health check server started on port {PORT}")


def parse_content(text: str) -> list:
    lines = text.strip().split('\n')
    items = []
    
    for line in lines:
        if ':' in line:
            parts = line.split(':', 1)
            title = parts[0].strip()
            url = parts[1].strip()
            
            if '.m3u8' in url:
                items.append({'title': title, 'url': url, 'type': 'video'})
            elif '.pdf' in url:
                items.append({'title': title, 'url': url, 'type': 'pdf'})
    
    return items


async def download_pdf(url: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    try:
        filepath = DOWNLOAD_DIR / filename
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context, limit=10)
        timeout = aiohttp.ClientTimeout(total=3600)
        
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    
                    async with aiofiles.open(filepath, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            if not active_downloads.get(user_id, False):
                                if filepath.exists():
                                    os.remove(filepath)
                                return None
                            
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            if downloaded % (1024 * 1024) < 8192:
                                percent = (downloaded / total_size * 100) if total_size > 0 else 0
                                try:
                                    await progress_msg.edit_text(
                                        f"📥 Downloading PDF...\n"
                                        f"Progress: {percent:.1f}%"
                                    )
                                except:
                                    pass
                    
                    return str(filepath)
        return None
    except Exception as e:
        logger.error(f"PDF download error: {e}")
        return None


def download_video_sync(url: str, quality: str, output_path: str, user_id: int) -> bool:
    """Synchronous download to avoid event loop issues"""
    try:
        ydl_opts = {
            'format': f'best[height<={quality}]/best',
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            
            'quiet': True,
            'no_warnings': True,
            
            # SSL
            'nocheckcertificate': True,
            
            # Headers
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            },
            
            # Conservative settings
            'concurrent_fragment_downloads': 2,
            'retries': 10,
            'fragment_retries': 10,
            'skip_unavailable_fragments': True,
            
            # Small buffers
            'buffersize': 1024 * 64,
            'http_chunk_size': 1024 * 256,
            
            # No re-encoding
            'postprocessor_args': {
                'ffmpeg': ['-c', 'copy', '-movflags', '+faststart']
            },
            
            'geo_bypass': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if not active_downloads.get(user_id, False):
                return False
            ydl.download([url])
        
        return True
        
    except Exception as e:
        logger.error(f"Sync download error: {e}")
        return False


async def download_m3u8(url: str, quality: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    temp_filename = f"temp_{user_id}_{filename.replace('.mp4', '')}"
    output_path = str(DOWNLOAD_DIR / temp_filename)
    
    try:
        await progress_msg.edit_text("📥 Starting download...\nPlease wait...")
        
        # Run download in thread pool to avoid event loop blocking
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            download_video_sync,
            url,
            quality,
            output_path,
            user_id
        )
        
        if not success or not active_downloads.get(user_id, False):
            return None
        
        await asyncio.sleep(2)
        
        # Find output file
        possible_files = []
        
        for ext in ['.mp4', '.mkv', '.webm', '.ts']:
            p = Path(output_path + ext)
            if p.exists():
                possible_files.append(p)
        
        for pattern in [f"{output_path}.f*.mp4", f"{output_path}*.mp4"]:
            for file in glob.glob(pattern):
                possible_files.append(Path(file))
        
        for file in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
            if file.is_file() and file.stat().st_size > 1024:
                possible_files.append(file)
        
        if not possible_files:
            logger.error(f"No output file found")
            return None
        
        output_file = max(possible_files, key=lambda p: p.stat().st_size)
        logger.info(f"Found file: {output_file} ({output_file.stat().st_size / 1024 / 1024:.2f}MB)")
        
        final_path = DOWNLOAD_DIR / filename
        if output_file != final_path:
            os.rename(output_file, final_path)
        else:
            final_path = output_file
        
        if final_path.exists() and final_path.stat().st_size > 1024:
            return str(final_path)
        else:
            return None
        
    except Exception as e:
        logger.error(f"M3U8 download error: {e}", exc_info=True)
        
        # Cleanup
        for file in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
            try:
                os.remove(file)
            except:
                pass
        
        return None


@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "🎬 **M3U8 Video Downloader Bot**\n\n"
        "📝 Send TXT/HTML file with M3U8 and PDF links\n"
        "🎯 Quality: 360p, 480p, 720p, 1080p\n\n"
        "**Format:**\n"
        "`[Title] Name : https://url.com/video.m3u8`\n\n"
        "⚠️ **Recommended: 720p for free tier**"
    )


@app.on_message(filters.document)
async def handle_document(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name
    
    if not (file_name.endswith('.txt') or file_name.endswith('.html')):
        await message.reply_text("❌ Send TXT or HTML file only!")
        return
    
    status_msg = await message.reply_text("📥 Processing file...")
    
    try:
        file_path = await message.download(file_name=f"{DOWNLOAD_DIR}/{user_id}_{file_name}")
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        items = parse_content(content)
        
        if not items:
            await status_msg.edit_text("❌ No M3U8 or PDF links found!")
            os.remove(file_path)
            return
        
        video_count = sum(1 for item in items if item['type'] == 'video')
        pdf_count = sum(1 for item in items if item['type'] == 'pdf')
        
        user_data[user_id] = {'items': items, 'file_path': file_path}
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("360p", callback_data="quality_360p"),
                InlineKeyboardButton("480p", callback_data="quality_480p")
            ],
            [
                InlineKeyboardButton("720p ⭐", callback_data="quality_720p"),
                InlineKeyboardButton("1080p", callback_data="quality_1080p")
            ]
        ])
        
        await status_msg.edit_text(
            f"✅ Found:\n🎬 Videos: {video_count}\n📄 PDFs: {pdf_count}\n\n📊 Select quality:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")


@app.on_callback_query(filters.regex(r"^quality_"))
async def quality_callback(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    quality = callback.data.split("_")[1]
    
    if user_id not in user_data:
        await callback.answer("❌ Session expired!", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    file_path = user_data[user_id]['file_path']
    active_downloads[user_id] = True
    
    stop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ Stop", callback_data="stop_download")]
    ])
    
    await callback.message.edit_text(
        f"🚀 Starting ({quality})...\nTotal: {len(items)}",
        reply_markup=stop_keyboard
    )
    
    success_count = 0
    failed_count = 0
    
    for idx, item in enumerate(items, 1):
        if not active_downloads.get(user_id, False):
            await callback.message.reply_text("⛔ Stopped!")
            break
        
        progress_msg = await callback.message.reply_text(
            f"📦 [{idx}/{len(items)}] {item['title'][:40]}..."
        )
        
        try:
            if item['type'] == 'video':
                quality_value = QUALITY_MAP[quality]
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:30]
                filename = f"{safe_filename}_{idx}.mp4"
                
                video_path = await download_m3u8(item['url'], quality_value, filename, progress_msg, user_id)
                
                if video_path and active_downloads.get(user_id, False):
                    if os.path.exists(video_path):
                        file_size = os.path.getsize(video_path) / (1024 * 1024)
                        
                        await progress_msg.edit_text("📤 Uploading...")
                        
                        await callback.message.reply_video(
                            video_path,
                            caption=f"🎬 {item['title']}\n📊 {quality} | 💾 {file_size:.1f}MB",
                            supports_streaming=True
                        )
                        
                        os.remove(video_path)
                        await progress_msg.delete()
                        success_count += 1
                    else:
                        await progress_msg.edit_text("❌ Failed")
                        failed_count += 1
                else:
                    await progress_msg.edit_text("❌ Failed")
                    failed_count += 1
                    
            elif item['type'] == 'pdf':
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                filename = f"{safe_filename}.pdf"
                
                pdf_path = await download_pdf(item['url'], filename, progress_msg, user_id)
                
                if pdf_path and active_downloads.get(user_id, False):
                    if os.path.exists(pdf_path):
                        await progress_msg.edit_text("📤 Uploading...")
                        
                        await callback.message.reply_document(
                            pdf_path, 
                            caption=f"📄 {item['title']}"
                        )
                        
                        os.remove(pdf_path)
                        await progress_msg.delete()
                        success_count += 1
                    else:
                        await progress_msg.edit_text("❌ Failed")
                        failed_count += 1
                else:
                    await progress_msg.edit_text("❌ Failed")
                    failed_count += 1
        
        except Exception as e:
            logger.error(f"Error {idx}: {e}")
            try:
                await progress_msg.edit_text("❌ Error")
            except:
                pass
            failed_count += 1
        
        await asyncio.sleep(2)
    
    # Cleanup
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass
    
    for temp_file in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
        try:
            os.remove(temp_file)
        except:
            pass
    
    if user_id in user_data:
        del user_data[user_id]
    if user_id in active_downloads:
        del active_downloads[user_id]
    
    await callback.message.reply_text(
        f"✅ Complete!\n✔️ Success: {success_count}\n❌ Failed: {failed_count}"
    )


@app.on_callback_query(filters.regex("^stop_download$"))
async def stop_download(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    active_downloads[user_id] = False
    await callback.answer("⛔ Stopping...", show_alert=True)


@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    active_downloads[user_id] = False
    await message.reply_text("⛔ Cancelled!")


@app.on_message(filters.command("clean"))
async def clean_command(client: Client, message: Message):
    try:
        count = 0
        for file in DOWNLOAD_DIR.glob("*"):
            if file.is_file():
                os.remove(file)
                count += 1
        await message.reply_text(f"🧹 Cleaned {count} files!")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def main():
    """Main async function"""
    # Start web server in background
    asyncio.create_task(start_web_server())
    
    # Start bot
    await app.start()
    logger.info("✅ Bot started successfully!")
    
    # Keep running
    await asyncio.Event().wait()


if __name__ == "__main__":
    logger.info("🚀 Starting M3U8 Downloader Bot...")
    logger.info(f"Download directory: {DOWNLOAD_DIR.absolute()}")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
