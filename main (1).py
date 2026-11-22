"""
Milliy TV kanali va izohlaridan kodni olib, @Milliydaman_bot ga yuboruvchi userbot.
Kod raqam yoki o'zbekcha so'z bilan (xato yozilgan bo'lsa ham) yozilishi mumkin.
Tushunolmagan xabarlarni Groq AI ga beradi (GROQ_API_KEY bo'lsa).
"""
import os
import re
import time
from collections import deque
from difflib import get_close_matches

import httpx
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

# ====== SOZLAMALAR ======
API_ID = int(os.getenv("API_ID", "123456"))
API_HASH = os.getenv("API_HASH", "your_api_hash")
SESSION_STRING = os.getenv("SESSION_STRING", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")          # console.groq.com dan (bepul)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

TARGET_BOT = "@Milliydaman_bot"
SOURCE_USERNAMES = ["Milliytivi_2026"]   # kanal (t.me/Milliytivi_2026)
SOURCE_TITLES = ["milliy tv"]            # nomida shu bor chatlar ("Milliy TV" izohlar)
MIN_LEN = int(os.getenv("MIN_LEN", "4"))  # kod uzunligi: kamida
MAX_LEN = int(os.getenv("MAX_LEN", "6"))  # kod uzunligi: ko'pi bilan
DEDUP = True                              # bir xil kodni qayta yubormaslik
DEBUG = os.getenv("DEBUG", "1") == "1"    # manba chatlardagi xabarlarni logga yozadi
# ========================

UNITS = {"nol": 0, "nul": 0, "bir": 1, "ikki": 2, "uch": 3, "tort": 4,
         "besh": 5, "olti": 6, "yetti": 7, "sakkiz": 8, "toqqiz": 9}
TENS = {"on": 10, "yigirma": 20, "ottiz": 30, "qirq": 40, "ellik": 50,
        "oltmish": 60, "yetmish": 70, "sakson": 80, "toqson": 90}
CANON = {**UNITS, **TENS}

# keng tarqalgan xato yozilishlar
ALIASES = {
    "iki": "ikki", "turt": "tort", "tuort": "tort", "bes": "besh", "olty": "olti",
    "yeti": "yetti", "sakiz": "sakkiz", "sakkis": "sakkiz",
    "tuqqiz": "toqqiz", "toqiz": "toqqiz", "tuqiz": "toqqiz",
    "toqquz": "toqqiz", "tuqquz": "toqqiz",
    "uttiz": "ottiz", "otiz": "ottiz", "utiz": "ottiz",
    "yigirmi": "yigirma", "igirma": "yigirma", "kirq": "qirq", "elik": "ellik",
    "oltmis": "oltmish", "yetmis": "yetmish", "saksan": "sakson",
    "tuqson": "toqson", "toqsan": "toqson", "tuqsan": "toqson",
}

TOKEN_RE = re.compile(r"\d+|[a-z]+")
GAP_OK = re.compile(r"^[\s,\-]*$")


def normalize(text: str) -> str:
    text = text.lower()
    for ch in ("’", "ʻ", "ʼ", "`", "‘", "'", "´"):
        text = text.replace(ch, "")          # to'rt -> tort, o'n -> on
    for ch in ("ş", "ṣ", "ś", "w"):
        text = text.replace(ch, "sh")        # ş / w  ->  sh   (beş, bew -> besh)
    return text


def to_word(tok: str):
    if tok in CANON:
        return tok
    if tok in ALIASES:
        return ALIASES[tok]
    if len(tok) >= 5:
        m = get_close_matches(tok, CANON.keys(), n=1, cutoff=0.8)
        if m:
            return m[0]
    return None


def words_to_str(words):
    """'toqson besh ottiz toqqiz' -> '9539'; 'bir ikki uch' -> '123'"""
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in TENS:
            v = TENS[w]
            if i + 1 < len(words) and words[i + 1] in UNITS and UNITS[words[i + 1]] != 0:
                v += UNITS[words[i + 1]]
                i += 1
            out.append(str(v))
        else:
            out.append(str(UNITS[w]))
        i += 1
    return "".join(out)


def run_to_code(run):
    out, buf = [], []
    for item in run:
        if item.isdigit():
            if buf:
                out.append(words_to_str(buf))
                buf = []
            out.append(item)
        else:
            buf.append(item)
    if buf:
        out.append(words_to_str(buf))
    return "".join(out)


def extract_code(text: str):
    text = normalize(text)
    runs, cur, last_end = [], [], None
    for m in TOKEN_RE.finditer(text):
        tok = m.group()
        item = tok if tok.isdigit() else to_word(tok)
        if item is None:
            if cur:
                runs.append(cur)
            cur, last_end = [], None
            continue
        if cur and not GAP_OK.match(text[last_end:m.start()]):
            runs.append(cur)
            cur = []
        cur.append(item)
        last_end = m.end()
    if cur:
        runs.append(cur)

    best = None
    for run in runs:
        code = run_to_code(run)
        if MIN_LEN <= len(code) <= MAX_LEN and (best is None or len(code) > len(best)):
            best = code
    return best


def maybe_code_text(text: str) -> bool:
    """Groq'ga faqat kodga o'xshash qisqa xabarlarni beramiz."""
    if len(text) > 80:
        return False
    t = normalize(text)
    if any(c.isdigit() for c in t):
        return True
    return any(get_close_matches(tok, CANON.keys(), n=1, cutoff=0.7)
               for tok in re.findall(r"[a-z]{3,}", t))


GROQ_SYSTEM = (
    "You extract a numeric code from Uzbek chat messages. The code has "
    f"{MIN_LEN}-{MAX_LEN} digits and may be written with digits and/or Uzbek number "
    "words, often misspelled (e.g. 'uttiz' = o'ttiz = 30, 'tuqqiz' = to'qqiz = 9; "
    "the letters 'w' or 'ş' may replace 'sh', so 'bew' = 'besh' = 5). Digits are read "
    "one by one or in pairs: '953 toqqiz' -> 9539; "
    "'to'qson besh o'ttiz to'qqiz' -> 95 39 -> 9539; 'bir ikki uch tort' -> 1234. "
    "Reply with ONLY the digits of the code, or NONE if the message has no code."
)
_last_groq = 0.0


async def groq_extract(text: str):
    global _last_groq
    if not GROQ_API_KEY:
        return None
    now = time.monotonic()
    if now - _last_groq < 1.0:      # Groq limitidan oshmaslik uchun
        return None
    _last_groq = now
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL, "temperature": 0, "max_tokens": 20,
                    "messages": [{"role": "system", "content": GROQ_SYSTEM},
                                 {"role": "user", "content": text}],
                },
            )
        r.raise_for_status()
        ans = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("Groq xatosi:", repr(e))
        return None
    if re.fullmatch(r"\d+", ans) and MIN_LEN <= len(ans) <= MAX_LEN:
        return ans
    return None


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
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = await client(GetFullChannelRequest(ent))
            if full.full_chat.linked_chat_id:
                source_ids.add(full.full_chat.linked_chat_id)
                print(f"Izohlar guruhi: {full.full_chat.linked_chat_id}")
        except Exception as e:
            print("Izohlar guruhini topib bo'lmadi:", e)

    async for d in client.iter_dialogs():
        name = (d.name or "").lower()
        if any(t in name for t in SOURCE_TITLES):
            source_ids.add(d.entity.id)
            print(f"Chat: {d.name} ({d.entity.id})")


@client.on(events.NewMessage)
async def handler(event):
    if event.chat_id is None:
        return
    real_id, _ = utils.resolve_id(event.chat_id)
    if real_id not in source_ids:
        return
    text = event.raw_text or ""
    code = extract_code(text)
    via = "oddiy"
    if not code and maybe_code_text(text):
        code = await groq_extract(text)
        via = "groq"
    if DEBUG:
        print(f"[xabar] chat={real_id} kod={code} ({via}) matn={text[:60]!r}")
    if not code:
        return
    if DEDUP and code in recent:
        print(f"O'tkazib yuborildi (takror): {code}")
        return
    try:
        await client.send_message(TARGET_BOT, code)
    except Exception as e:
        print("Botga yuborishda xato:", repr(e))
        return
    recent.append(code)
    print(f"Yuborildi: {code}  <-  {text!r}")


async def main():
    await client.start()
    await resolve_sources()
    print("Bot ishga tushdi...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    client.loop.run_until_complete(main())
