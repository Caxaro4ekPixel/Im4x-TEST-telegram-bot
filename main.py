import asyncio
import os
import shutil

import requests
import yt_dlp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv(".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
VAST_API_KEY = os.getenv("VAST_API_KEY")
INSTANCE_ID = os.getenv("INSTANCE_ID")

user_data: dict[int, dict] = {}

MODELS = {
    "v1143": "model_mel_band_roformer_ep_3005_sdr_11.4360.ckpt",
    "karaoke": "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt",
    "duality": "mel_band_roformer_kim_instvoc_duality_v2.ckpt",
    "dereverb": "bs_roformer_dereverb_anvuew_sdr_10.6667.ckpt"
}

FORMATS = {
    "aac": {"ext": "m4a", "codec": "-c:a aac -b:a 192k"},
    "flac": {"ext": "flac", "codec": "-c:a flac"},
    "alac": {"ext": "m4a", "codec": "-c:a alac"}
}

router = Router()


def stop_server() -> None:
    if not VAST_API_KEY or not INSTANCE_ID:
        return
    url = f"https://console.vast.ai/api/v0/instances/{INSTANCE_ID}/"
    headers = {"Authorization": f"Bearer {VAST_API_KEY}"}
    requests.put(url, headers=headers, json={"state": "stopped"})


def _action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔥 ViperX", callback_data="sep_v1143"),
                InlineKeyboardButton(text="🎤 Karaoke", callback_data="sep_karaoke"),
            ],
            [
                InlineKeyboardButton(text="🎧 Duality V2", callback_data="sep_duality"),
                InlineKeyboardButton(text="🔇 Dereverb", callback_data="sep_dereverb"),
            ],
            [
                InlineKeyboardButton(text="🎞 Reaper", callback_data="vid_reaper"),
                InlineKeyboardButton(text="📉 50MB", callback_data="vid_50mb"),
            ],
        ]
    )


@router.message(F.text | F.audio | F.video | F.document)
async def handle_any(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    data: dict = {"chat_id": chat_id}

    if message.text:
        if "magnet:?" in message.text or "http" in message.text:
            data["link"] = message.text
        else:
            return
    elif message.document and message.document.file_name and message.document.file_name.endswith(
            ".torrent"
    ):
        data["file_id"] = message.document.file_id
        data["is_torrent"] = True
    else:
        f_obj = message.audio or message.video or message.document
        if not f_obj:
            return
        data["file_id"] = f_obj.file_id

    user_data[chat_id] = data
    await message.reply("Выберите действие:", reply_markup=_action_keyboard())


@router.callback_query()
async def callback_query_handler(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    if not callback.message:
        return
    chat_id = callback.message.chat.id
    if chat_id not in user_data:
        return

    if callback.data and callback.data.startswith("sep_"):
        user_data[chat_id]["model"] = callback.data.split("_")[1]
        fmt_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="AAC", callback_data="fmt_aac"),
                    InlineKeyboardButton(text="FLAC", callback_data="fmt_flac"),
                    InlineKeyboardButton(text="ALAC (Mac only)", callback_data="fmt_alac"),
                ],
            ]
        )
        await callback.message.edit_text("Выберите формат:", reply_markup=fmt_kb)
        return

    if not callback.data:
        return
    mode = callback.data.split("_")[1]
    await callback.message.edit_text("🚀 Обработка запущена...")

    target = "input_file"
    try:
        if "link" in user_data[chat_id]:
            link = user_data[chat_id]["link"]
            if "youtube" in link or "youtu.be" in link:
                with yt_dlp.YoutubeDL({"format": "bestaudio", "outtmpl": target}) as ydl:
                    ydl.download([link])
            elif "magnet:?" in link:
                os.system(f"aria2c --seed-time=0 '{link}'")
                files = [
                    os.path.join(r, f)
                    for r, _, fs in os.walk(".")
                    for f in fs
                    if f != "bot.py"
                ]
                shutil.move(max(files, key=os.path.getsize), target)
        elif "file_id" in user_data[chat_id]:
            tg_file = await bot.get_file(user_data[chat_id]["file_id"])
            await bot.download(tg_file, destination=target)
            if user_data[chat_id].get("is_torrent"):
                os.system(f"aria2c --seed-time=0 '{target}'")
                files = [
                    os.path.join(r, f)
                    for r, _, fs in os.walk(".")
                    for f in fs
                    if f not in ("bot.py", target)
                ]
                shutil.move(max(files, key=os.path.getsize), target)

        if "model" in user_data[chat_id]:
            os.system(
                f"audio-separator {target} --model_filename "
                f"{MODELS[user_data[chat_id]['model']]} --output_format flac"
            )
            fmt = FORMATS[mode]
            for f in os.listdir("."):
                if f.endswith(".flac") and f != target:
                    out = f"Result_{f.split('_')[-1]}.{fmt['ext']}"
                    os.system(f"ffmpeg -y -i '{f}' {fmt['codec']} '{out}'")
                    await bot.send_document(chat_id, FSInputFile(out))
        else:
            out_vid = "output.mp4"
            scale = "-vf scale=-2:720" if mode == "reaper" else "-fs 49M"
            os.system(f"ffmpeg -y -i {target} {scale} -c:v libx264 -preset veryfast {out_vid}")
            await bot.send_document(chat_id, FSInputFile(out_vid))

    except Exception as e:
        await bot.send_message(chat_id, f"❌ Error: {e}")

    stop_server()


async def main() -> None:
    api_base = os.getenv("TELEGRAM_BOT_API_URL", "http://localhost:8081")
    local_server = TelegramAPIServer.from_base(api_base)
    session = AiohttpSession(api=local_server)
    dp = Dispatcher()
    dp.include_router(router)
    async with Bot(token=BOT_TOKEN, session=session) as bot:
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
