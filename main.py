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
import subprocess
import mimetypes

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

# Supported file types
SUPPORTED_TYPES = {
    'video': ['.m3u8', '.mpd', '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.ts'],
    'image': ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'],
    'document': ['.pdf', '.doc', '.docx', '.txt', '.zip', '.rar']
}

# Web server
from aiohttp import web
web_app = web.Application()

async def health_check(request):
    return web.Response(text="OK")

web_app.router.add_get("/", health_check)
web_app.router.add_get("/health", health_check)


def get_file_type(url: str) -> str:
    """Determine file type from URL"""
    url_lower = url.lower()
    
    for ftype, extensions in SUPPORTED_TYPES.items():
        if any(ext in url_lower for ext in extensions):
            return ftype
    
    return 'unknown'


def parse_content(text: str) -> list:
    """Parse content and identify all supported file types"""
    lines = text.strip().split('\n')
    items = []
    
    for line in lines:
        if ':' in line and ('http://' in line or 'https://' in line):
            parts = line.split(':', 1)
            if len(parts) == 2:
                title = parts[0].strip()
                url = parts[1].strip()
                
                file_type = get_file_type(url)
                
                if file_type != 'unknown':
                    items.append({
                        'title': title, 
                        'url': url, 
                        'type': file_type
                    })
    
    return items


def get_video_info(filepath: str) -> dict:
    """Get video duration and dimensions"""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        import json
        data = json.loads(result.stdout)
        
        duration = int(float(data.get('format', {}).get('duration', 0)))
        
        video_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), {})
        width = video_stream.get('width', 1280)
        height = video_stream.get('height', 720)
        
        return {'duration': duration, 'width': width, 'height': height}
    except Exception as e:
        logger.error(f"FFprobe error: {e}")
        return {'duration': 0, 'width': 1280, 'height': 720}


def generate_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Generate thumbnail from video"""
    try:
        cmd = [
            'ffmpeg', '-i', video_path,
            '-ss', '00:00:02',
            '-vframes', '1',
            '-vf', 'scale=320:180',
            '-q:v', '2',
            thumb_path,
            '-y'
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 1024:
            return True
        return False
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return False


async def download_file(url: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    """Universal file downloader for images, PDFs, and other documents"""
    try:
        filepath = DOWNLOAD_DIR / filename
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context, limit=20)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            headers = {'User-Agent': 'Mozilla/5.0'}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=1800)) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    start_time = asyncio.get_event_loop().time()
                    last_update = 0
                    
                    async with aiofiles.open(filepath, 'wb') as f:
                        async for chunk in response.content.iter_chunked(16384):
                            if not active_downloads.get(user_id, False):
                                if filepath.exists():
                                    os.remove(filepath)
                                return None
                            
                            await f.write(chunk)
                            downloaded += len(chunk)
                            
                            if downloaded - last_update >= 1024 * 1024:
                                last_update = downloaded
                                try:
                                    percent = (downloaded / total_size * 100) if total_size > 0 else 0
                                    elapsed = asyncio.get_event_loop().time() - start_time
                                    speed = downloaded / elapsed if elapsed > 0 else 0
                                    
                                    await progress_msg.edit_text(
                                        f"📥 Downloading...\n\n"
                                        f"Progress: {percent:.1f}%\n"
                                        f"Size: {downloaded/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB\n"
                                        f"Speed: {speed/(1024*1024):.2f} MB/s"
                                    )
                                except:
                                    pass
                    
                    return str(filepath)
        return None
    except Exception as e:
        logger.error(f"File download error: {e}")
        return None


def download_video_sync(url: str, quality: str, output_path: str, user_id: int) -> bool:
    """Download video using yt-dlp (supports m3u8, mpd, mp4, etc.)"""
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
            'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            'concurrent_fragment_downloads': 4,
            'retries': 15,
            'fragment_retries': 15,
            'skip_unavailable_fragments': True,
            'buffersize': 131072,
            'http_chunk_size': 524288,
            'postprocessor_args': {'ffmpeg': ['-c', 'copy', '-movflags', '+faststart']},
            'progress_hooks': [progress_hook],
            'extractor_retries': 5,
            'file_access_retries': 5,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if not active_downloads.get(user_id, False):
                return False
            ydl.download([url])
        return True
    except Exception as e:
        logger.error(f"Video download error: {e}")
        return False


async def update_progress(progress_msg: Message, user_id: int):
    """Update download progress periodically"""
    last_percent = 0
    
    while active_downloads.get(user_id, False) and user_id in download_progress:
        try:
            prog = download_progress[user_id]
            percent = prog.get('percent', 0)
            
            if int(percent) - last_percent >= 5:
                last_percent = int(percent)
                
                downloaded = prog.get('downloaded', 0)
                total = prog.get('total', 0)
                speed = prog.get('speed', 0)
                eta = prog.get('eta', 0)
                
                completed = int(percent / 5)
                bar = "█" * completed + "░" * (20 - completed)
                
                await progress_msg.edit_text(
                    f"📥 Downloading video...\n\n"
                    f"[{bar}] {percent:.1f}%\n\n"
                    f"Downloaded: {downloaded/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB\n"
                    f"Speed: {speed/(1024*1024):.2f} MB/s\n"
                    f"ETA: {int(eta)}s"
                )
        except Exception as e:
            logger.error(f"Progress update error: {e}")
        
        await asyncio.sleep(2)


async def download_video(url: str, quality: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    """Download video with progress tracking"""
    temp_name = f"temp_{user_id}_{filename.replace('.mp4', '')}"
    output_path = str(DOWNLOAD_DIR / temp_name)
    
    try:
        download_progress[user_id] = {'percent': 0}
        
        await progress_msg.edit_text("📥 Starting download...")
        
        progress_task = asyncio.create_task(update_progress(progress_msg, user_id))
        
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, download_video_sync, url, quality, output_path, user_id)
        
        if user_id in download_progress:
            del download_progress[user_id]
        
        try:
            progress_task.cancel()
        except:
            pass
        
        if not success:
            return None
        
        await progress_msg.edit_text("🔄 Processing...")
        await asyncio.sleep(1)
        
        # Find output file
        possible = []
        for ext in ['.mp4', '.mkv', '.webm', '.ts']:
            p = Path(output_path + ext)
            if p.exists() and p.stat().st_size > 10240:
                possible.append(p)
        
        for file in DOWNLOAD_DIR.glob(f"temp_{user_id}_*"):
            if file.is_file() and file.stat().st_size > 10240:
                possible.append(file)
        
        if not possible:
            logger.error(f"No output file found for {output_path}")
            return None
        
        output_file = max(possible, key=lambda p: p.stat().st_size)
        final_path = DOWNLOAD_DIR / filename
        
        if output_file != final_path:
            os.rename(output_file, final_path)
        else:
            final_path = output_file
        
        if final_path.exists() and final_path.stat().st_size > 10240:
            return str(final_path)
        return None
        
    except Exception as e:
        logger.error(f"Video download error: {e}")
        if user_id in download_progress:
            del download_progress[user_id]
        return None


@app.on_message(filters.command("start"))
async def start_cmd(client: Client, message: Message):
    await message.reply_text(
        "🎬 **Advanced M3U8 Downloader Bot v7.0**\n\n"
        "✨ **Enhanced Features:**\n"
        "📊 Range selection\n"
        "🔢 Serial numbered uploads\n"
        "⚡ Multi-format support\n"
        "🎥 Video: M3U8, MPD, MP4, MKV\n"
        "🖼️ Images: PNG, JPG, GIF\n"
        "📄 Documents: PDF, DOC, TXT\n"
        "🖼️ Auto thumbnails\n"
        "⏱️ Duration tracking\n"
        "📈 Real-time progress\n\n"
        "📝 Send TXT/HTML file to start!"
    )


@app.on_message(filters.document)
async def handle_doc(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name
    
    if not (file_name.endswith('.txt') or file_name.endswith('.html')):
        await message.reply_text("❌ Send TXT/HTML only!")
        return
    
    status = await message.reply_text("📥 Processing file...")
    
    try:
        file_path = await message.download(file_name=f"{DOWNLOAD_DIR}/{user_id}_{file_name}")
        
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
        
        items = parse_content(content)
        
        if not items:
            await status.edit_text("❌ No supported links found!")
            os.remove(file_path)
            return
        
        # Count by type
        type_counts = {}
        for item in items:
            ftype = item['type']
            type_counts[ftype] = type_counts.get(ftype, 0) + 1
        
        user_data[user_id] = {'items': items, 'file_path': file_path}
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Select Range", callback_data="select_range")],
            [InlineKeyboardButton("⬇️ Download All", callback_data="download_all")]
        ])
        
        type_info = "\n".join([f"{'🎬' if t == 'video' else '🖼️' if t == 'image' else '📄'} {t.title()}s: {c}" 
                                for t, c in type_counts.items()])
        
        await status.edit_text(
            f"✅ **Content Detected:**\n\n"
            f"{type_info}\n"
            f"📦 Total: {len(items)}\n\n"
            f"Choose an option:",
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Document processing error: {e}")
        await status.edit_text(f"❌ Error: {str(e)[:100]}")


@app.on_callback_query(filters.regex(r"^(select_range|download_all)$"))
async def range_select(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data
    
    if user_id not in user_data:
        await callback.answer("❌ Session expired!", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    
    if action == "download_all":
        user_data[user_id]['range'] = (1, len(items))
        
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
        
        await callback.message.edit_text(
            f"📦 Downloading all {len(items)} items\n\n"
            f"🎬 Select video quality:\n"
            f"(Images & documents will download automatically)",
            reply_markup=kb
        )
    else:
        await callback.message.edit_text(
            f"📊 **Range Selection**\n\n"
            f"Total items: {len(items)}\n\n"
            f"Send range in format:\n"
            f"`start-end` (e.g., `1-10`)\n"
            f"or `start` (e.g., `5` for item 5 only)\n\n"
            f"**Examples:**\n"
            f"• `1-50` → Downloads items 1 to 50\n"
            f"• `10-20` → Downloads items 10 to 20\n"
            f"• `15` → Downloads only item 15"
        )


@app.on_message(filters.text & filters.private)
async def handle_range(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_data:
        return
    
    if 'range' in user_data[user_id]:
        return
    
    text = message.text.strip()
    items = user_data[user_id]['items']
    
    try:
        if '-' in text:
            start, end = map(int, text.split('-'))
        else:
            start = end = int(text)
        
        if start < 1 or end > len(items) or start > end:
            await message.reply_text(
                f"❌ Invalid range!\n\n"
                f"Valid range: 1-{len(items)}\n"
                f"Please try again."
            )
            return
        
        user_data[user_id]['range'] = (start, end)
        
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
        
        count = end - start + 1
        await message.reply_text(
            f"✅ Range set: {start}-{end}\n"
            f"📦 Will download {count} item(s)\n\n"
            f"🎬 Select video quality:",
            reply_markup=kb
        )
        
    except Exception as e:
        await message.reply_text(
            f"❌ Invalid format!\n\n"
            f"Use: `start-end` or `number`\n"
            f"Example: `1-10` or `5`"
        )


@app.on_callback_query(filters.regex(r"^q_"))
async def quality_cb(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    quality = callback.data.split("_")[1]
    
    if user_id not in user_data or 'range' not in user_data[user_id]:
        await callback.answer("❌ Session expired!", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    file_path = user_data[user_id]['file_path']
    start, end = user_data[user_id]['range']
    
    selected_items = items[start-1:end]
    active_downloads[user_id] = True
    
    stop_kb = InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="stop")]])
    
    await callback.message.edit_text(
        f"🚀 **Starting batch download**\n\n"
        f"Quality: {quality}\n"
        f"Range: {start}-{end}\n"
        f"Total: {len(selected_items)} items\n\n"
        f"⏳ Processing...",
        reply_markup=stop_kb
    )
    
    success = 0
    failed = 0
    
    for idx, item in enumerate(selected_items, start):
        if not active_downloads.get(user_id, False):
            await callback.message.reply_text("⛔ Download stopped by user!")
            break
        
        # Serial number in progress
        prog = await callback.message.reply_text(
            f"📦 **Item {idx}/{end}**\n"
            f"📝 {item['title'][:50]}..."
        )
        
        try:
            # Create caption with serial number
            serial_caption = f"{idx}. {item['title']}"
            
            if item['type'] == 'video':
                q_val = QUALITY_MAP[quality]
                safe = re.sub(r'[^\w\s-]', '', item['title'])[:30]
                fname = f"{safe}_{idx}.mp4"
                
                vpath = await download_video(item['url'], q_val, fname, prog, user_id)
                
                if vpath and active_downloads.get(user_id, False) and os.path.exists(vpath):
                    fsize = os.path.getsize(vpath) / (1024 * 1024)
                    
                    await prog.edit_text("🎬 Processing video...")
                    video_info = get_video_info(vpath)
                    
                    thumb_path = str(DOWNLOAD_DIR / f"thumb_{user_id}_{idx}.jpg")
                    has_thumb = generate_thumbnail(vpath, thumb_path)
                    
                    await prog.edit_text("📤 Uploading...")
                    
                    await callback.message.reply_video(
                        vpath,
                        caption=f"🎬 {serial_caption}\n📊 {quality} | 💾 {fsize:.1f}MB",
                        supports_streaming=True,
                        duration=video_info['duration'],
                        width=video_info['width'],
                        height=video_info['height'],
                        thumb=thumb_path if has_thumb else None
                    )
                    
                    os.remove(vpath)
                    if has_thumb:
                        try:
                            os.remove(thumb_path)
                        except:
                            pass
                    
                    await prog.delete()
                    success += 1
                else:
                    # Fallback: send link if download fails
                    await callback.message.reply_text(
                        f"❌ Download failed for:\n\n{serial_caption}\n\n🔗 Link: {item['url']}"
                    )
                    await prog.delete()
                    failed += 1
                    
            elif item['type'] == 'image':
                safe = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                ext = os.path.splitext(item['url'])[1] or '.jpg'
                fname = f"{safe}_{idx}{ext}"
                
                ipath = await download_file(item['url'], fname, prog, user_id)
                
                if ipath and active_downloads.get(user_id, False) and os.path.exists(ipath):
                    await prog.edit_text("📤 Uploading image...")
                    await callback.message.reply_photo(
                        ipath, 
                        caption=f"🖼️ {serial_caption}"
                    )
                    os.remove(ipath)
                    await prog.delete()
                    success += 1
                else:
                    await callback.message.reply_text(
                        f"❌ Download failed for:\n\n{serial_caption}\n\n🔗 Link: {item['url']}"
                    )
                    await prog.delete()
                    failed += 1
                    
            elif item['type'] == 'document':
                safe = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                ext = os.path.splitext(item['url'])[1] or '.pdf'
                fname = f"{safe}_{idx}{ext}"
                
                dpath = await download_file(item['url'], fname, prog, user_id)
                
                if dpath and active_downloads.get(user_id, False) and os.path.exists(dpath):
                    await prog.edit_text("📤 Uploading document...")
                    await callback.message.reply_document(
                        dpath, 
                        caption=f"📄 {serial_caption}"
                    )
                    os.remove(dpath)
                    await prog.delete()
                    success += 1
                else:
                    await callback.message.reply_text(
                        f"❌ Download failed for:\n\n{serial_caption}\n\n🔗 Link: {item['url']}"
                    )
                    await prog.delete()
                    failed += 1
        
        except Exception as e:
            logger.error(f"Item {idx} error: {e}")
            try:
                await prog.edit_text(f"❌ Error occurred")
                await callback.message.reply_text(
                    f"❌ Failed to process:\n\n{idx}. {item['title']}\n\n🔗 Link: {item['url']}"
                )
            except:
                pass
            failed += 1
        
        await asyncio.sleep(1)
    
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
    
    await callback.message.reply_text(
        f"✅ **Batch Complete!**\n\n"
        f"✔️ Success: {success}\n"
        f"❌ Failed: {failed}\n"
        f"📊 Total: {len(selected_items)}\n\n"
        f"🎯 Range was: {start}-{end}"
    )


@app.on_callback_query(filters.regex("^stop$"))
async def stop_cb(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    active_downloads[user_id] = False
    await callback.answer("⛔ Stopping downloads...", show_alert=True)


@app.on_message(filters.command("cancel"))
async def cancel_cmd(client: Client, message: Message):
    active_downloads[message.from_user.id] = False
    await message.reply_text("⛔ All downloads cancelled!")


async def main():
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Web server started on port {PORT}")
    
    await app.start()
    logger.info("✅ Bot v7.0 started successfully!")
    
    await idle()


if __name__ == "__main__":
    logger.info("🚀 Starting Advanced M3U8 Bot v7.0...")
    app.run(main())
