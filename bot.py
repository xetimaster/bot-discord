# bot.py
import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
import yt_dlp
import discord

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("insta-mirror")

# Environment variables
TOKEN = os.environ.get("TOKEN")  # Discord bot token
# If you want to restrict uploads to a specific channel uncomment:
# TARGET_CHANNEL_ID = int(os.environ.get("TARGET_CHANNEL_ID", "0"))
MAX_UPLOAD_MB = int(os.environ.get("DISCORD_MAX_UPLOAD_MB", "8"))  # default 8MB free tier

if not TOKEN:
    log.critical("TOKEN environment variable is required.")
    raise SystemExit("No TOKEN provided")

# Regex to match Instagram post / reel / tv / p links
INSTAGRAM_REGEX = re.compile(
    r"(https?://(?:www\.)?instagram\.com/(?:reel|p|tv|stories)/[A-Za-z0-9_\-/?=&%\.]+)",
    re.IGNORECASE,
)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

ydl_opts_common = {
    "quiet": True,
    "no_warnings": True,
    "retries": 2,
    # will be overridden per-download 'outtmpl'
}

def download_media(url: str, dest_folder: Path) -> Path:
    """
    Download best video/format or image for the given Instagram URL to dest_folder.
    Returns the Path to the downloaded file.
    """
    outtmpl = str(dest_folder / "media.%(ext)s")
    opts = dict(ydl_opts_common)
    opts.update({
        "outtmpl": outtmpl,
        # prefer mp4/ best mp4; for images ext will be jpg/png
        "format": "mp4/bestvideo+bestaudio/best",
        "noplaylist": True,
        "ignoreerrors": False,
        "nooverwrites": False,
    })
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Determine filename
        if info is None:
            raise RuntimeError("yt-dlp failed to extract info")
        # yt-dlp returns 'requested_formats' or 'url' fields; find filename used
        filename = ydl.prepare_filename(info)
        # If the file includes an extension replaced by outtmpl, adjust:
        # ydl.prepare_filename can return something like ...media.mp4 or ...media.jpg
        path = Path(filename)
        if not path.exists():
            # try to find any file in dest folder starting with "media."
            matches = list(dest_folder.glob("media.*"))
            if matches:
                return matches[0]
            raise FileNotFoundError(f"Downloaded file not found for {url}")
        return path

async def safe_send_file(channel: discord.abc.Messageable, file_path: Path, filename: str = None):
    """Send file, handling file-size errors from Discord library."""
    filesize = file_path.stat().st_size
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if filesize > max_bytes:
        # too big to upload to Discord directly
        raise ValueError(f"File {file_path.name} is {filesize} bytes, exceeds {MAX_UPLOAD_MB} MB limit.")
    # send
    await channel.send(file=discord.File(fp=str(file_path), filename=(filename or file_path.name)))

@client.event
async def on_ready():
    log.info(f"Logged in as {client.user} (id: {client.user.id})")

@client.event
async def on_message(message: discord.Message):
    # ignore bot's own messages
    if message.author == client.user:
        return

    # optional: only respond in a specific channel
    # if TARGET_CHANNEL_ID and message.channel.id != TARGET_CHANNEL_ID:
    #     return

    # find instagram link
    match = INSTAGRAM_REGEX.search(message.content)
    if not match:
        return

    url = match.group(1)
    log.info(f"Detected Instagram URL from {message.author} in #{message.channel}: {url}")

    # acknowledge
    try:
        ack = await message.channel.send(f"🔎 Downloading Instagram media from the link...")
    except Exception:
        ack = None

    # create temporary dir for download
    with tempfile.TemporaryDirectory() as tdir:
        tpath = Path(tdir)
        try:
            downloaded = await asyncio.get_event_loop().run_in_executor(None, download_media, url, tpath)
            log.info(f"Downloaded to {downloaded}")

            # check size
            try:
                await safe_send_file(message.channel, downloaded)
            except ValueError as e:
                # file too big (or other size error)
                log.warning(str(e))
                # send fallback message with original URL and info
                await message.channel.send(
                    f"⚠️ Downloaded file is larger than {MAX_UPLOAD_MB} MB; I cannot upload it directly. "
                    f"You can download it manually here: {url}"
                )
                # do NOT delete original link in this case (safer)
                if ack:
                    await ack.delete()
                return

            # if upload succeeded, delete original message
            try:
                await message.delete()
                log.info("Original message deleted.")
            except discord.Forbidden:
                await message.channel.send("⚠️ I don't have permission to delete messages. Please give me Manage Messages permission.")
            except Exception as e:
                log.exception("Failed to delete original message: %s", e)

            # remove acknowledgment
            if ack:
                try:
                    await ack.delete()
                except Exception:
                    pass

        except Exception as exc:
            log.exception("Error handling Instagram URL")
            if ack:
                try:
                    await ack.edit(content=f"❌ Failed to download or upload: {exc}")
                except Exception:
                    pass
            else:
                await message.channel.send(f"❌ Failed to handle Instagram link: {exc}")

def main():
    client.run(TOKEN)

if __name__ == "__main__":
    main()

