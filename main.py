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
        
        # Create SSL context that doesn't verify certificates
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=3600)) as response:
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
        output_path = DOWNLOAD_DIR / filename.replace('.mp4', '')
        
        # Advanced yt-dlp options with SSL fix
        ydl_opts = {
            'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best',
            'outtmpl': str(output_path),
            'merge_output_format': 'mp4',
            'quiet': False,
            'no_warnings': False,
            'verbose': True,
            
            # SSL and connection fixes
            'nocheckcertificate': True,
            'no_check_certificate': True,
            'prefer_insecure': True,
            
            # Headers
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            },
            
            # Download settings
            'concurrent_fragment_downloads': 5,
            'retries': 15,
            'fragment_retries': 15,
            'skip_unavailable_fragments': True,
            'keep_fragments': False,
            
            # Buffer settings
            'buffersize': 1024 * 512,
            'http_chunk_size': 1024 * 1024 * 2,
            
            # FFmpeg settings
            'postprocessor_args': {
                'ffmpeg': ['-c', 'copy']
            },
            
            # Geo bypass
            'geo_bypass': True,
            'geo_bypass_country': 'IN',
        }
        
        last_status = {'percent': 0, 'speed': 0}
        
        def progress_hook(d):
            if not active_downloads.get(user_id, False):
                raise Exception("Download cancelled")
            
            if d['status'] == 'downloading':
                try:
                    total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    downloaded = d.get('downloaded_bytes', 0)
                    
                    if total > 0:
                        percent = (downloaded / total) * 100
                        speed = d.get('speed', 0) or 0
                        
                        # Update every 5%
                        if int(percent) - last_status['percent'] >= 5:
                            last_status['percent'] = int(percent)
                            last_status['speed'] = speed
                            
                            asyncio.create_task(
                                progress_msg.edit_text(
                                    f"📥 Downloading video...\n"
                                    f"Progress: {percent:.1f}%\n"
                                    f"Downloaded: {downloaded/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB\n"
                                    f"Speed: {speed/(1024*1024):.2f} MB/s"
                                )
                            )
                except Exception as e:
                    logger.error(f"Progress hook error: {e}")
            
            elif d['status'] == 'finished':
                asyncio.create_task(progress_msg.edit_text("🔄 Converting to MP4..."))
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        # Download with yt-dlp
        logger.info(f"Starting download for: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(None, ydl.extract_info, url, True)
            logger.info(f"Download info: {info.get('title', 'Unknown')}")
        
        # Find output file
        for ext in ['.mp4', '.mkv', '.webm', '.ts']:
            possible_path = Path(str(output_path) + ext)
            if possible_path.exists():
                logger.info(f"Found file: {possible_path}")
                
                # If not MP4, rename to MP4
                if ext != '.mp4':
                    mp4_path = possible_path.with_suffix('.mp4')
                    os.rename(possible_path, mp4_path)
                    return str(mp4_path)
                
                return str(possible_path)
        
        # Try without extension
        if Path(str(output_path) + '.mp4').exists():
            return str(output_path) + '.mp4'
        
        logger.error(f"No output file found for: {output_path}")
        return None
        
    except Exception as e:
        logger.error(f"M3U8 download error: {e}", exc_info=True)
        try:
            await progress_msg.edit_text(f"❌ Download failed: {str(e)[:100]}")
        except:
            pass
        return None


@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    await message.reply_text(
        "🎬 **M3U8 Video Downloader Bot**\n\n"
        "📝 Send me a TXT/HTML file with M3U8 and PDF links\n"
        "🎯 Select quality: 360p, 480p, 720p, 1080p\n"
        "📥 I'll download and send everything!\n\n"
        "Format:\n"
        "`[Title] Name : https://url.com/video.m3u8`\n"
        "`[Title] PDF : https://url.com/file.pdf`"
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
        await callback.answer("❌ Session expired! Send file again.", show_alert=True)
        return
    
    items = user_data[user_id]['items']
    file_path = user_data[user_id]['file_path']
    active_downloads[user_id] = True
    
    stop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ Stop Download", callback_data="stop_download")]
    ])
    
    await callback.message.edit_text(
        f"🚀 Starting downloads ({quality})...\nTotal items: {len(items)}",
        reply_markup=stop_keyboard
    )
    
    success_count = 0
    failed_count = 0
    
    for idx, item in enumerate(items, 1):
        if not active_downloads.get(user_id, False):
            await callback.message.reply_text("⛔ Stopped by user!")
            break
        
        progress_msg = await callback.message.reply_text(
            f"📦 [{idx}/{len(items)}] {item['title'][:50]}..."
        )
        
        try:
            if item['type'] == 'video':
                quality_value = QUALITY_MAP[quality]
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                filename = f"{safe_filename}_{quality}.mp4"
                
                logger.info(f"Downloading video {idx}: {item['url']}")
                video_path = await download_m3u8(item['url'], quality_value, filename, progress_msg, user_id)
                
                if video_path and active_downloads.get(user_id, False):
                    if os.path.exists(video_path):
                        file_size = os.path.getsize(video_path) / (1024 * 1024)
                        logger.info(f"Video downloaded: {video_path}, Size: {file_size:.2f}MB")
                        
                        await progress_msg.edit_text("📤 Uploading video...")
                        
                        await callback.message.reply_video(
                            video_path,
                            caption=f"🎬 {item['title']}\n📊 Quality: {quality}\n💾 Size: {file_size:.1f}MB",
                            supports_streaming=True,
                            width=1280,
                            height=720
                        )
                        
                        os.remove(video_path)
                        await progress_msg.delete()
                        success_count += 1
                    else:
                        logger.error(f"Video file not found: {video_path}")
                        await progress_msg.edit_text(f"❌ File not found")
                        failed_count += 1
                else:
                    await progress_msg.edit_text(f"❌ Download failed")
                    failed_count += 1
                    
            elif item['type'] == 'pdf':
                safe_filename = re.sub(r'[^\w\s-]', '', item['title'])[:50]
                filename = f"{safe_filename}.pdf"
                
                logger.info(f"Downloading PDF {idx}: {item['url']}")
                pdf_path = await download_pdf(item['url'], filename, progress_msg, user_id)
                
                if pdf_path and active_downloads.get(user_id, False):
                    if os.path.exists(pdf_path):
                        await progress_msg.edit_text("📤 Uploading PDF...")
                        
                        await callback.message.reply_document(
                            pdf_path, 
                            caption=f"📄 {item['title']}"
                        )
                        
                        os.remove(pdf_path)
                        await progress_msg.delete()
                        success_count += 1
                    else:
                        await progress_msg.edit_text(f"❌ File not found")
                        failed_count += 1
                else:
                    await progress_msg.edit_text(f"❌ Download failed")
                    failed_count += 1
        
        except Exception as e:
            logger.error(f"Error processing item {idx}: {e}", exc_info=True)
            try:
                await progress_msg.edit_text(f"❌ Error: {str(e)[:50]}")
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
    
    if user_id in user_data:
        del user_data[user_id]
    if user_id in active_downloads:
        del active_downloads[user_id]
    
    await callback.message.reply_text(
        f"✅ **Complete!**\n\n"
        f"✔️ Success: {success_count}\n"
        f"❌ Failed: {failed_count}\n"
        f"📊 Total: {len(items)}"
    )


@app.on_callback_query(filters.regex("^stop_download$"))
async def stop_download(client: Client, callback: CallbackQuery):
    user_id = callback.from_user.id
    active_downloads[user_id] = False
    await callback.answer("⛔ Stopping downloads...", show_alert=True)


@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    active_downloads[user_id] = False
    await message.reply_text("⛔ Downloads cancelled!")


if __name__ == "__main__":
    logger.info("🚀 Bot starting...")
    app.run()
