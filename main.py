"""
Milliy TV kanali va izohlaridan kodni olib, @Milliydaman_bot ga yuboruvchi userbot.

O'rnatish:   pip install telethon
Ishga tushirish:   python kod_bot.py
Birinchi marta telefon raqam va Telegram kodi so'raladi.
"""
import os
import re
from collections import deque
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ====== SOZLAMALAR ======
# Railway'da Variables bo'limiga yozing; kompyuterda esa shu yerga to'g'ridan-to'g'ri yozsa ham bo'ladi
API_ID = int(os.getenv("API_ID", "123456"))
API_HASH = os.getenv("API_HASH", "your_api_hash")
SESSION_STRING = os.getenv("SESSION_STRING", "")  # gen_session.py beradi

TARGET_BOT = "@Milliydaman_bot"    # kod yuboriladigan bot
SOURCE_USERNAMES = ["Milliytivi_2026"]   # kanal (t.me/Milliytivi_2026)
SOURCE_TITLES = ["Milliy TV"]            # izohlar (muhokama) chati nomi
MIN_LEN = 3                        # kod kamida nechta raqamdan iborat
DEDUP = True                       # bir xil kodni ketma-ket qayta yubormaslik
# ========================

DIGITS = {
    "nol": 0, "nul": 0, "bir": 1, "ikki": 2, "uch": 3, "tort": 4,
    "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8, "toqqiz": 9,
}
TENS = {
    "on": 10, "yigirma": 20, "ottiz": 30, "qirq": 40, "ellik": 50,
    "oltmish": 60, "yetmish": 70, "sakson": 80, "toqson": 90,
}
BIG = {"yuz": 100, "ming": 1000}
ALL_WORDS = {**DIGITS, **TENS, **BIG}

# "125tort", "125 to'rt", "bir ikki uch", "yuz yigirma besh" kabilarni ushlaydi
_words = "|".join(sorted(ALL_WORDS, key=len, reverse=True))
TOKEN = rf"(?:\d+|(?<![a-z])(?:{_words})(?![a-z]))"
PATTERN = re.compile(rf"{TOKEN}(?:\s*{TOKEN})*")
TOKEN_RE = re.compile(rf"\d+|{_words}")


def normalize(text: str) -> str:
    text = text.lower()
    for ch in ("’", "ʻ", "ʼ", "`", "‘", "'"):
        text = text.replace(ch, "")      # to'rt -> tort, o'n -> on
    return text


def words_to_str(words):
    """So'zlar ketma-ketligini raqamga aylantiradi.
    Faqat bir xonali so'zlar bo'lsa: 'bir ikki uch' -> '123'.
    O'nlik/yuz/ming bo'lsa: 'yuz yigirma besh' -> '125'."""
    if not any(w in TENS or w in BIG for w in words):
        return "".join(str(DIGITS[w]) for w in words)
    total = cur = 0
    for w in words:
        if w in DIGITS or w in TENS:
            cur += ALL_WORDS[w]
        elif w == "yuz":
            cur = (cur or 1) * 100
        elif w == "ming":
            total += (cur or 1) * 1000
            cur = 0
    return str(total + cur)


def run_to_code(run: str) -> str:
    out, buf = [], []
    for t in TOKEN_RE.findall(run):
        if t.isdigit():
            if buf:
                out.append(words_to_str(buf)); buf = []
            out.append(t)
        else:
            buf.append(t)
    if buf:
        out.append(words_to_str(buf))
    return "".join(out)


def extract_code(text: str):
    text = normalize(text)
    best = None
    for m in PATTERN.finditer(text):
        digits = run_to_code(m.group())
        if len(digits) >= MIN_LEN and (best is None or len(digits) > len(best)):
            best = digits
    return best


client = TelegramClient(
    StringSession(SESSION_STRING) if SESSION_STRING else "kod_session",
    API_ID, API_HASH,
)
source_ids = set()
recent = deque(maxlen=20)


async def resolve_sources():
    for username in SOURCE_USERNAMES:
        ent = await client.get_entity(username)
        source_ids.add(ent.id)
        print(f"Kanal: {getattr(ent, 'title', username)} ({ent.id})")
        # kanalga ulangan izohlar (muhokama) guruhini ham qo'shish
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(ent))
            if full.full_chat.linked_chat_id:
                source_ids.add(full.full_chat.linked_chat_id)
                print(f"Izohlar guruhi: {full.full_chat.linked_chat_id}")
        except Exception as e:
            print("Izohlar guruhini topib bo'lmadi:", e)

    # nomi bo'yicha qidirish ("Milliy TV")
    async for d in client.iter_dialogs():
        if d.name in SOURCE_TITLES:
            source_ids.add(d.entity.id)
            print(f"Chat: {d.name} ({d.entity.id})")


@client.on(events.NewMessage)
async def handler(event):
    if event.chat_id is None:
        return
    # chat_id ba'zan -100 prefiksi bilan keladi, shuning uchun peer ID bilan solishtiramiz
    from telethon import utils
    real_id, _ = utils.resolve_id(event.chat_id)
    if real_id not in source_ids:
        return
    code = extract_code(event.raw_text or "")
    if not code:
        return
    if DEDUP and code in recent:
        return
    recent.append(code)
    await client.send_message(TARGET_BOT, code)
    print(f"Yuborildi: {code}  <-  {event.raw_text!r}")


async def main():
    await client.start()
    await resolve_sources()
    print("Bot ishga tushdi...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
