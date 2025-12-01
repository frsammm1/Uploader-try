import os
import re
import asyncio
import aiohttp
import aiofiles
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
from typing import Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

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
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3600)) as response:
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
                                        f"Progress: {percent:.1f}%\n"
                                        f"Size: {downloaded/(1024*1024):.1f}MB"
                                    )
                                except:
                                    pass
                    
                    return str(filepath)
        return None
    except Exception as e:
        logger.error(f"PDF download error: {e}")
        return None


async def download_m3u8(url: str, quality: str, filename: str, progress_msg: Message, user_id: int) -> Optional[str]:
    try:
        output_path = DOWNLOAD_DIR / filename
        
        ydl_opts = {
            'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]',
            'outtmpl': str(output_path.with_suffix('')),
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'concurrent_fragment_downloads': 4,
            'buffersize': 1024 * 256,
            'http_chunk_size': 1024 * 1024,
            'retries': 10,
            'fragment_retries': 10,
            'nocheckcertificate': True,
        }
        
        last_percent = [0]
        
        def progress_hook(d):
            if not active_downloads.get(user_id, False):
                raise Exception("Download cancelled")
            
            if d['status'] == 'downloading':
                try:
                    percent = d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100
                    if int(percent) - last_percent[0] >= 5:
                        last_percent[0] = int(percent)
                        asyncio.create_task(
                            progress_msg.edit_text(
                                f"📥 Downloading video...\n"
                                f"Progress: {percent:.1f}%\n"
                                f"Speed: {d.get('speed', 0)/(1024*1024):.2f} MB/s"
                            )
                        )
                except:
                    pass
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await asyncio.get_event_loop().run_in_executor(None, ydl.download, [url])
        
        for ext in ['.mp4', '.mkv', '.webm']:
            possible_path = output_path.with_suffix(ext)
            if possible_path.exists():
                return str(possible_path)
        
        return str(output_path.with_suffix('.mp4')) if output_path.with_suffix('.mp4').exists() else None
        
    except Exception as e:
        logger.error(f"M3U8 download error: {e}")
        return None


@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "🎬 **M3U8 Video Downloader Bot**\n\n"
        "📝 Send me a TXT/HTML file with M3U8 and PDF links\n"
        "🎯 Select quality: 360p, 480p, 720p, 1080p\n"
        "📥 I'll download and send everything!"
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
                InlineKeyboardButton("720p", callback_data="quality_720p"),
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
        await callback.answer("❌ Session expired! Send file again.", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    file_path = user_data[user_id]['file_path']
    active_downloads[user_id] = True
    
    stop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ Stop", callback_data="stop_download")]
    ])
    
    await callback.message.edit_text(
        f"🚀 Starting downloads ({quality})...\nTotal: {len(items)}",
        reply_markup=stop_keyboard
    )
    
    success_count = 0
    failed_count = 0
    
    for idx, item in enumerate(items, 1):
        if not active_downloads.get(user_id, False):
            await callback.message.edit_text("⛔ Stopped by user!")
            break
        
        progress_msg = await callback.message.reply_text(
            f"📦 {idx}/{len(items)}: {item['title'][:50]}..."
        )
        
        try:
            if item['type'] == 'video':
                quality_value = QUALITY_MAP[quality]
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                filename = f"{safe_filename}.mp4"
                
                video_path = await download_m3u8(item['url'], quality_value, filename, progress_msg, user_id)
                
                if video_path and active_downloads.get(user_id, False):
                    await progress_msg.edit_text("📤 Uploading...")
                    await callback.message.reply_video(
                        video_path,
                        caption=f"🎬 {item['title']}\n📊 {quality}",
                        supports_streaming=True
                    )
                    os.remove(video_path)
                    await progress_msg.delete()
                    success_count += 1
                else:
                    await progress_msg.edit_text(f"❌ Failed")
                    failed_count += 1
                    
            elif item['type'] == 'pdf':
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                filename = f"{safe_filename}.pdf"
                
                pdf_path = await download_pdf(item['url'], filename, progress_msg, user_id)
                
                if pdf_path and active_downloads.get(user_id, False):
                    await progress_msg.edit_text("📤 Uploading...")
                    await callback.message.reply_document(pdf_path, caption=f"📄 {item['title']}")
                    os.remove(pdf_path)
                    await progress_msg.delete()
                    success_count += 1
                else:
                    await progress_msg.edit_text(f"❌ Failed")
                    failed_count += 1
        
        except Exception as e:
            logger.error(f"Error {idx}: {e}")
            await progress_msg.edit_text(f"❌ Error")
            failed_count += 1
        
        await asyncio.sleep(1)
    
    try:
        os.remove(file_path)
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


if __name__ == "__main__":
    logger.info("🚀 Bot starting...")
    app.run()
