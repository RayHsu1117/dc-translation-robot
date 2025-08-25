#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord 翻譯機器人（分層架構 + 短訊息快取）
-------------------------------------------------
功能：
- 中→英、英→中；其他語言 → 同時輸出中+英
- 短訊息也會翻譯，但先查快取（命中則不呼叫 GPT）
- 分層：Client / Preprocess / Decision / Translation / Cache

使用方式：
1) 安裝需求：
   pip install discord.py langdetect openai python-dotenv aiosqlite
   （如要用 Redis 當快取，另外安裝：pip install redis asyncio-redis aioredis）

2) 設定環境變數（.env）：
   DISCORD_TOKEN=...  # 你的 Discord Bot Token
   OPENAI_API_KEY=... # 你的 OpenAI API Key
   SHORT_MSG_MAX_CHARS=10        # 可選，預設 10
   USE_REDIS=false               # 可選，true/false
   REDIS_URL=redis://localhost:6379/0  # 可選

3) 執行：
   python discord_translator_bot.py

備註：
- 預設使用 SQLite 做快取；若環境變數 USE_REDIS=true 且 REDIS_URL 可用，則改用 Redis。
- 若你只想快取短訊息，可以維持目前邏輯；如果也想快取長訊息，把下面的 CACHE_LONG_MESSAGES 改為 True。
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

import discord
from discord import Message
from langdetect import DetectorFactory, detect, detect_langs
from dotenv import load_dotenv

# OpenAI Python SDK v1.x
from openai import OpenAI

# SQLite 快取
import aiosqlite

# 可選：Redis 快取（若要使用，請確保已安裝 aioredis 或 redis asyncio 版本）
try:
    import redis.asyncio as aioredis  # type: ignore
except Exception:  # pragma: no cover
    aioredis = None  # 允許沒有安裝 redis 也能運作（改用 SQLite）

# -----------------------------
# 基本設定
# -----------------------------
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("translator-bot")

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("環境變數 DISCORD_TOKEN 未設定")
if not OPENAI_API_KEY:
    raise RuntimeError("環境變數 OPENAI_API_KEY 未設定")

SHORT_MSG_MAX_CHARS = int(os.getenv("SHORT_MSG_MAX_CHARS", "10"))
CACHE_LONG_MESSAGES = False  # 如需連長訊息也快取，改 True

USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SQLITE_DB = os.getenv("SQLITE_DB", "./trans_cache.sqlite3")

# 讓 langdetect 可重現
DetectorFactory.seed = 42

# OpenAI Client
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# -----------------------------
# 型別 / 常數
# -----------------------------
Lang = Literal["zh", "en", "other", "unknown"]

SYSTEM_PROMPT = (
    "You are an expert, context-aware translator for casual chats. "
    "Translate the user's message following the requested target language(s). "
    "Be natural and idiomatic, keep emojis, preserve punctuation and simple formatting. "
    "Do not add explanations."
)

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_RETRIES = 3

# -----------------------------
# 工具：語言偵測與前處理
# -----------------------------
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
ONLY_EMOJI_PATTERN = re.compile(r"^[\W_]+$", re.UNICODE)
WHITESPACE_PATTERN = re.compile(r"^\s*$")


def normalize_lang(code: str) -> Lang:
    if not code:
        return "unknown"
    c = code.lower()
    if c.startswith("zh"):
        return "zh"
    if c.startswith("en"):
        return "en"
    # 其他語言
    return "other"


def detect_lang(text: str) -> Lang:
    try:
        langs = detect_langs(text)  # 回傳多個語言及機率
        if not langs:
            return "unknown"
        best = langs[0]
        return normalize_lang(best.lang)
    except Exception:
        return "unknown"


def should_skip(text: str) -> bool:
    stripped = text.strip()

    # 跳過空白
    if WHITESPACE_PATTERN.match(text):
        return True

    # 跳過純網址
    if URL_PATTERN.match(stripped):
        return True

    # 跳過純 emoji 或符號
    if ONLY_EMOJI_PATTERN.match(stripped):
        return True

    return False

import re

def extract_emojis(text: str):
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002700-\U000027BF"  # Dingbats
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U00002600-\U000026FF"  # Misc symbols
        "]+", flags=re.UNICODE
    )
    emojis = emoji_pattern.findall(text)
    text_wo_emoji = emoji_pattern.sub("<EMOJI>", text)
    return emojis, text_wo_emoji

def restore_emojis(translated_text: str, emojis: list):
    for emoji in emojis:
        translated_text = translated_text.replace("<EMOJI>", emoji, 1)
    return translated_text

# -----------------------------
# 快取層：Redis 或 SQLite
# -----------------------------
class TranslationCache:
    """抽象化翻譯結果快取：key 由 (text, policy) 組成。
    policy 例如："zh->en", "en->zh", "other->zh+en"
    值為 JSON 字串，包含 {"en": str | None, "zh": str | None}
    """

    def __init__(self):
        self.use_redis = USE_REDIS and aioredis is not None
        self.redis: Optional[aioredis.Redis] = None
        self.sqlite_path = SQLITE_DB

    async def init(self):
        if self.use_redis:
            try:
                self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
                await self.redis.ping()
                logger.info("使用 Redis 快取：%s", REDIS_URL)
                return
            except Exception as e:
                logger.warning("Redis 初始化失敗，改用 SQLite。原因：%s", e)
                self.use_redis = False

        # SQLite 後備
        async with aiosqlite.connect(self.sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    result_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            await db.commit()
        logger.info("使用 SQLite 快取：%s", self.sqlite_path)

    @staticmethod
    def _make_key(text: str, policy: str) -> str:
        # 壓縮空白，避免同一句不同空白導致快取未命中
        norm = re.sub(r"\s+", " ", text.strip())
        return json.dumps({"t": norm, "p": policy}, ensure_ascii=False)

    async def get(self, text: str, policy: str) -> Optional[Dict[str, Optional[str]]]:
        if isinstance(text, list):
            text = "\n".join(text) 
        key = self._make_key(text, policy)
        if self.use_redis and self.redis is not None:
            val = await self.redis.get(key)
            if val:
                try:
                    return json.loads(val)
                except Exception:
                    return None
        else:
            async with aiosqlite.connect(self.sqlite_path) as db:
                async with db.execute("SELECT result_json FROM translations WHERE key=?", (key,)) as cur:
                    row = await cur.fetchone()
                    if row:
                        try:
                            return json.loads(row[0])
                        except Exception:
                            return None
        return None

    async def set(self, text: str, policy: str, result: Dict[str, Optional[str]], ttl_sec: int = 7 * 24 * 3600):
        key = self._make_key(text, policy)
        payload = json.dumps(result, ensure_ascii=False)
        now = int(time.time())
        if self.use_redis and self.redis is not None:
            # 設定過期時間，預設 7 天
            await self.redis.set(key, payload, ex=ttl_sec)
        else:
            async with aiosqlite.connect(self.sqlite_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO translations(key, result_json, created_at) VALUES(?,?,?)",
                    (key, payload, now),
                )
                await db.commit()

# -----------------------------
# 翻譯層：OpenAI API
# -----------------------------
@dataclass
class TranslationTask:
    src_lang: Lang
    targets: List[Lang]  # 例如 ["en"] 或 ["zh", "en"]


async def translate_with_openai(text: str, task) -> Dict[str, Optional[str]]:
    """呼叫 OpenAI 進行翻譯，強制 JSON 格式輸出，完整保留換行"""
    target_desc = ", ".join([{"en": "English", "zh": "Chinese"}.get(t, t) for t in task.targets])

    user_prompt = (
        f"Translate the following message into {target_desc}. "
        "Output ONLY in JSON with keys 'en' and 'zh'. "
        "Preserve all line breaks exactly as in the original.\n\n"
        f"Message:\n{text}\n\n"
        "Example:\n"
        "{\n"
        "  \"en\": \"<English translation with same line breaks>\",\n"
        "  \"zh\": \"<Chinese translation with same line breaks>\"\n"
        "}"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = openai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )

            output = resp.choices[0].message.content.strip()

            # 嘗試解析 JSON
            try:
                parsed = json.loads(output)
                return {
                    "zh": parsed.get("zh"),
                    "en": parsed.get("en")
                }
            except json.JSONDecodeError:
                logger.warning("解析 GPT 回傳失敗，原始輸出：%s", output)
                return {"zh": None, "en": output}

        except Exception as e:
            logger.warning("OpenAI 呼叫失敗（第 %d 次）：%s", attempt, e)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(1.5 * attempt)


# -----------------------------
# 翻譯決策層
# -----------------------------
def build_policy(src: Lang) -> Tuple[TranslationTask, str]:
    """根據來源語言回傳翻譯任務與 policy key。
    zh -> en ; en -> zh ; other/unknown -> zh + en
    """
    if src == "zh":
        task = TranslationTask(src_lang=src, targets=["en"])
        return task, "zh->en"
    if src == "en":
        task = TranslationTask(src_lang=src, targets=["zh"])
        return task, "en->zh"
    task = TranslationTask(src_lang=src, targets=["zh", "en"])  # other/unknown
    return task, "other->zh+en"


# -----------------------------
# Client Layer: Discord Bot
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

cache = TranslationCache()


async def process_message(message: Message):
    if message.author.bot:
        return

    content = message.content

    # 1) 前置過濾
    if should_skip(content):
        return

    # 🔥 分離 emoji
    clean_text, emojis = extract_emojis(content)

    # 2) 語言偵測
    src_lang = detect_lang(clean_text if clean_text else content)

    # 3) 決策 / Policy
    task, policy = build_policy(src_lang)

    # 4) 快取策略
    is_short = len(clean_text) <= SHORT_MSG_MAX_CHARS
    use_cache = is_short or CACHE_LONG_MESSAGES

    if use_cache:
        cached = await cache.get(clean_text, policy)
        if cached:
            # 翻譯結果加回 emoji
            cached["zh"] = restore_emojis(cached.get("zh") or "", emojis)
            cached["en"] = restore_emojis(cached.get("en") or "", emojis)
            await send_translation(message, content, cached)
            return

    # 5) 翻譯
    try:
        result = await translate_with_openai(clean_text, task)
    except Exception as e:
        logger.error("翻譯失敗：%s", e)
        return

    # 翻譯後加回 emoji
    result["zh"] = restore_emojis(result.get("zh") or "", emojis)
    result["en"] = restore_emojis(result.get("en") or "", emojis)

    # 6) 寫入快取（依策略）
    if use_cache:
        try:
            await cache.set(clean_text, policy, result)
        except Exception as e:
            logger.warning("寫入快取失敗：%s", e)

    # 7) 回覆
    await send_translation(message, content, result)



async def send_translation(message: Message, original: str, result: Dict[str, Optional[str]]):
    """將翻譯結果送回 Discord。可依偏好調整格式。"""
    zh_part = result.get("zh")
    en_part = result.get("en")

    # 動態組裝輸出
    lines: List[str] = []
    lines.append("**原文**:\n" + original)
    if zh_part:
        lines.append("**中文**:\n" + zh_part)
    if en_part:
        lines.append("**English**:\n" + en_part)

    text = "\n\n".join(lines)

    # 避免超過 Discord 單則訊息長度（約 2000 字元）
    if len(text) > 1900:
        # 若太長，僅回傳翻譯，不附原文
        alt_lines: List[str] = []
        if zh_part:
            alt_lines.append("**中文**:\n" + zh_part)
        if en_part:
            alt_lines.append("**English**:\n" + en_part)
        text = "\n\n".join(alt_lines)[:1990]

    await message.channel.send(text)


@client.event
async def on_ready():
    logger.info("Logged in as %s (id=%s)", client.user, client.user.id)


@client.event
async def on_message(message: Message):
    try:
        await process_message(message)
    except Exception as e:
        logger.exception("處理訊息時發生未處理例外：%s", e)


async def main():
    await cache.init()
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot 已停止")
