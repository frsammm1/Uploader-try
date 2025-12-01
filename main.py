import os
import re
import asyncio
import aiohttp
import aiofiles
import ssl
from pathlib import Path
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
from typing import Dict, Optional
import logging
import glob
from aiohttp import web
import time
from PIL import Image
import subprocess

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
download_progress: Dict[int, dict] = {}

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

QUALITY_MAP = {
    "360p": "360",
    "480p": "480", 
    "720p": "720",
    "1080p": "1080",
}

# Web server for health check
web_app = web.Application()

async def health_check(request):
    return web.Response(text="OK")

web_app.router.add_get("/", health_check)
web_app.router.add_get("/health", health_check)


def parse_content(text: str) -> list:
    lines = text.strip().split('\n')
    items = []
    
    for line in lines:
        if ':' in line and ('http://' in line or 'https://' in line):
            parts = line.split(':', 1)
            if len(parts) == 2:
                title = parts[0].strip()
                url = parts[1].strip()
                
                if '.m3u8' in url:
                    items.append({'title': title, 'url': url, 'type': 'video'})
                elif '.pdf' in url:
                    items.append({'title': title, 'url': url, 'type': 'pdf'})
    
    return items


def get_video_info(filepath: str) -> dict:
    """Get video duration and dimensions"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-show_entries', 'stream=width,height',
            '-of', 'default=noprint_wrappers=1',
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        info = {'duration': 0, 'width': 1280, 'height': 720}
        
        for line in result.stdout.split('\n'):
            if 'duration=' in line:
                try:
                    info['duration'] = int(float(line.split('=')[1]))
                except:
                    pass
            elif 'width=' in line:
                try:
                    info['width'] = int(line.split('=')[1])
                except:
                    pass
            elif 'height=' in line:
                try:
                    info['height'] = int(line.split('=')[1])
                except:
                    pass
        
        return info
    except Exception as e:
        logger.error(f"FFprobe error: {e}")
        return {'duration': 0, 'width': 1280, 'height': 720}


def generate_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Generate thumbnail from video"""
    try:
        cmd = [
            'ffmpeg', '-i', video_path,
            '-ss', '00:00:01',
            '-vframes', '1',
            '-vf', 'scale=320:180',
            thumb_path,
            '-y'
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        
        if os.path.exists(thumb_path):
            return True
        return False
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return False


async def progress_bar(current, total, start_time, text=""):
    """Progress bar callback for uploads"""
    now = time.time()
    elapsed = now - start_time
    
    if elapsed == 0:
        return
    
    percentage = current * 100 / total
    speed = current / elapsed
    eta = (total - current) / speed if speed > 0 else 0
    
    completed = int(percentage / 5)
    remaining = 20 - completed
    bar = "█" * completed + "░" * remaining
    
    return (
        f"{text}\n\n"
        f"Progress: {percentage:.1f}%\n"
        f"[{bar}]\n"
        f"Done: {current/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB\n"
        f"Speed: {speed/(1024*1024):.2f} MB/s\n"
        f"ETA: {int(eta)}s"
    )


async def download_pdf(url: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    try:
        filepath = DOWNLOAD_DIR / filename
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            headers = {'User-Agent': 'Mozilla/5.0'}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=1800)) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    start_time = time.time()
                    last_update = 0
                    
                    async with aiofiles.open(filepath, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            if not active_downloads.get(user_id, False):
                                if filepath.exists():
                                    os.remove(filepath)
                                return None
                            
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            # Update every 1MB
                            if downloaded - last_update >= 1024 * 1024:
                                last_update = downloaded
                                try:
                                    percent = (downloaded / total_size * 100) if total_size > 0 else 0
                                    speed = downloaded / (time.time() - start_time)
                                    
                                    await progress_msg.edit_text(
                                        f"📥 Downloading PDF...\n\n"
                                        f"Progress: {percent:.1f}%\n"
                                        f"Size: {downloaded/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB\n"
                                        f"Speed: {speed/(1024*1024):.2f} MB/s"
                                    )
                                except:
                                    pass
                    
                    return str(filepath)
        return None
    except Exception as e:
        logger.error(f"PDF error: {e}")
        return None


def download_video_sync(url: str, quality: str, output_path: str, user_id: int) -> bool:
    try:
        def progress_hook(d):
            if not active_downloads.get(user_id, False):
                raise Exception("Cancelled")
            
            if d['status'] == 'downloading':
                try:
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    speed = d.get('speed', 0) or 0
                    
                    if total > 0:
                        percent = (downloaded / total) * 100
                        eta = d.get('eta', 0)
                        
                        download_progress[user_id] = {
                            'percent': percent,
                            'downloaded': downloaded,
                            'total': total,
                            'speed': speed,
                            'eta': eta
                        }
                except:
                    pass
        
        ydl_opts = {
            'format': f'best[height<={quality}]/best',
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'http_headers': {'User-Agent': 'Mozilla/5.0'},
            'concurrent_fragment_downloads': 2,
            'retries': 10,
            'fragment_retries': 10,
            'skip_unavailable_fragments': True,
            'buffersize': 65536,
            'http_chunk_size': 262144,
            'postprocessor_args': {'ffmpeg': ['-c', 'copy']},
            'progress_hooks': [progress_hook],
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if not active_downloads.get(user_id, False):
                return False
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False


async def update_progress(progress_msg: Message, user_id: int):
    """Update progress message periodically"""
    last_percent = 0
    
    while active_downloads.get(user_id, False) and user_id in download_progress:
        try:
            prog = download_progress[user_id]
            percent = prog.get('percent', 0)
            
            # Update every 5%
            if int(percent) - last_percent >= 5:
                last_percent = int(percent)
                
                downloaded = prog.get('downloaded', 0)
                total = prog.get('total', 0)
                speed = prog.get('speed', 0)
                eta = prog.get('eta', 0)
                
                # Progress bar
                completed = int(percent / 5)
                bar = "█" * completed + "░" * (20 - completed)
                
                await progress_msg.edit_text(
                    f"📥 Downloading video...\n\n"
                    f"[{bar}] {percent:.1f}%\n\n"
                    f"Downloaded: {downloaded/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB\n"
                    f"Speed: {speed/(1024*1024):.2f} MB/s\n"
                    f"ETA: {int(eta)}s remaining"
                )
        except Exception as e:
            logger.error(f"Progress update error: {e}")
        
        await asyncio.sleep(2)


async def download_m3u8(url: str, quality: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    temp_name = f"temp_{user_id}_{filename.replace('.mp4', '')}"
    output_path = str(DOWNLOAD_DIR / temp_name)
    
    try:
        download_progress[user_id] = {'percent': 0, 'downloaded': 0, 'total': 0, 'speed': 0}
        
        await progress_msg.edit_text("📥 Starting download...")
        
        # Start progress updater
        progress_task = asyncio.create_task(update_progress(progress_msg, user_id))
        
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, download_video_sync, url, quality, output_path, user_id)
        
        # Stop progress updater
        if user_id in download_progress:
            del download_progress[user_id]
        
        try:
            progress_task.cancel()
        except:
            pass
        
        if not success:
            return None
        
        await progress_msg.edit_text("🔄 Processing video...")
        await asyncio.sleep(1)
        
        # Find file
        possible = []
        for ext in ['.mp4', '.mkv', '.webm']:
            p = Path(output_path + ext)
            if p.exists():
                possible.append(p)
        
        for file in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
            if file.is_file() and file.stat().st_size > 1024:
                possible.append(file)
        
        if not possible:
            return None
        
        output_file = max(possible, key=lambda p: p.stat().st_size)
        final_path = DOWNLOAD_DIR / filename
        
        if output_file != final_path:
            os.rename(output_file, final_path)
        else:
            final_path = output_file
        
        if final_path.exists() and final_path.stat().st_size > 1024:
            return str(final_path)
        return None
        
    except Exception as e:
        logger.error(f"M3U8 error: {e}")
        if user_id in download_progress:
            del download_progress[user_id]
        return None


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🎬 **M3U8 Downloader Bot v6.1**\n\n"
        "📝 Send TXT/HTML file with links\n"
        "🎯 Quality: 360p-1080p\n\n"
        "**New Features:**\n"
        "✨ Video thumbnails\n"
        "✨ Duration display\n"
        "✨ Progress bar\n"
        "✨ Speed & ETA\n\n"
        "Format: `[Title] : https://url.m3u8`"
    )


@app.on_message(filters.document)
async def handle_doc(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name
    
    if not (file_name.endswith('.txt') or file_name.endswith('.html')):
        await message.reply_text("❌ Send TXT/HTML only!")
        return
    
    status = await message.reply_text("📥 Processing...")
    
    try:
        file_path = await message.download(file_name=f"{DOWNLOAD_DIR}/{user_id}_{file_name}")
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        items = parse_content(content)
        
        if not items:
            await status.edit_text("❌ No links found!")
            os.remove(file_path)
            return
        
        v_count = sum(1 for i in items if i['type'] == 'video')
        p_count = sum(1 for i in items if i['type'] == 'pdf')
        
        user_data[user_id] = {'items': items, 'file_path': file_path}
        
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("360p", callback_data="q_360p"),
                InlineKeyboardButton("480p", callback_data="q_480p")
            ],
            [
                InlineKeyboardButton("720p ⭐", callback_data="q_720p"),
                InlineKeyboardButton("1080p", callback_data="q_1080p")
            ]
        ])
        
        await status.edit_text(
            f"✅ Found:\n🎬 Videos: {v_count}\n📄 PDFs: {p_count}\n\nSelect quality:",
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Doc error: {e}")
        await status.edit_text(f"❌ Error: {str(e)[:100]}")


@app.on_callback_query(filters.regex(r"^q_"))
async def quality_cb(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    quality = callback.data.split("_")[1]
    
    if user_id not in user_data:
        await callback.answer("❌ Expired!", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    file_path = user_data[user_id]['file_path']
    active_downloads[user_id] = True
    
    stop_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="stop")]])
    
    await callback.message.edit_text(f"🚀 Starting ({quality})...\nTotal: {len(items)}", reply_markup=stop_kb)
    
    success = 0
    failed = 0
    
    for idx, item in enumerate(items, 1):
        if not active_downloads.get(user_id, False):
            await callback.message.reply_text("⛔ Stopped!")
            break
        
        prog = await callback.message.reply_text(f"📦 [{idx}/{len(items)}] {item['title'][:40]}...")
        
        try:
            if item['type'] == 'video':
                q_val = QUALITY_MAP[quality]
                safe = re.sub(r'[^\w\s-]', '', item['title'])[:30]
                fname = f"{safe}_{idx}.mp4"
                
                vpath = await download_m3u8(item['url'], q_val, fname, prog, user_id)
                
                if vpath and active_downloads.get(user_id, False) and os.path.exists(vpath):
                    fsize = os.path.getsize(vpath) / (1024 * 1024)
                    
                    # Get video info
                    await prog.edit_text("🎬 Getting video info...")
                    video_info = get_video_info(vpath)
                    
                    # Generate thumbnail
                    thumb_path = str(DOWNLOAD_DIR / f"thumb_{user_id}_{idx}.jpg")
                    await prog.edit_text("📸 Generating thumbnail...")
                    has_thumb = generate_thumbnail(vpath, thumb_path)
                    
                    # Upload with progress
                    await prog.edit_text("📤 Uploading video...")
                    start_time = time.time()
                    
                    await callback.message.reply_video(
                        vpath,
                        caption=f"🎬 {item['title']}\n📊 {quality} | 💾 {fsize:.1f}MB",
                        supports_streaming=True,
                        duration=video_info['duration'],
                        width=video_info['width'],
                        height=video_info['height'],
                        thumb=thumb_path if has_thumb else None,
                        progress=lambda c, t: asyncio.create_task(
                            prog.edit_text(
                                asyncio.run(progress_bar(c, t, start_time, "📤 Uploading video..."))
                            ) if int((c/t)*100) % 10 == 0 else asyncio.sleep(0)
                        )
                    )
                    
                    # Cleanup
                    os.remove(vpath)
                    if has_thumb:
                        try:
                            os.remove(thumb_path)
                        except:
                            pass
                    
                    await prog.delete()
                    success += 1
                else:
                    await prog.edit_text("❌ Failed")
                    failed += 1
                    
            elif item['type'] == 'pdf':
                safe = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                fname = f"{safe}.pdf"
                
                ppath = await download_pdf(item['url'], fname, prog, user_id)
                
                if ppath and active_downloads.get(user_id, False) and os.path.exists(ppath):
                    await prog.edit_text("📤 Uploading PDF...")
                    await callback.message.reply_document(ppath, caption=f"📄 {item['title']}")
                    os.remove(ppath)
                    await prog.delete()
                    success += 1
                else:
                    await prog.edit_text("❌ Failed")
                    failed += 1
        
        except Exception as e:
            logger.error(f"Item {idx} error: {e}")
            try:
                await prog.edit_text("❌ Error")
            except:
                pass
            failed += 1
        
        await asyncio.sleep(2)
    
    # Cleanup
    try:
        os.remove(file_path)
    except:
        pass
    
    for tf in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
        try:
            os.remove(tf)
        except:
            pass
    
    for tf in DOWNLOAD_DIR.glob(f"thumb_{user_id}_*"):
        try:
            os.remove(tf)
        except:
            pass
    
    if user_id in user_data:
        del user_data[user_id]
    if user_id in active_downloads:
        del active_downloads[user_id]
    
    await callback.message.reply_text(f"✅ Done!\n✔️ Success: {success}\n❌ Failed: {failed}")


@app.on_callback_query(filters.regex("^stop$"))
async def stop_cb(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    active_downloads[user_id] = False
    await callback.answer("⛔ Stopping...", show_alert=True)


@app.on_message(filters.command("cancel"))
async def cancel_cmd(client: Client, message: Message):
    active_downloads[message.from_user.id] = False
    await message.reply_text("⛔ Cancelled!")


async def main():
    # Start web server
    runner = web.AppRunner(web_app)
  
