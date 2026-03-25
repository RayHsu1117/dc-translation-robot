#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord 翻譯機器人
-------------------------------------------------
指令：
  回覆訊息 + @bot          — 隨選翻譯（自動偵測語言，翻成所有其他支援語言）
  回覆訊息 + @bot [語言碼]  — 隨選翻譯（指定目標語言，例如 @bot ko）
  /live-translate-on       — 開始即時翻譯
  /live-translate-off      — 關閉即時翻譯
  /set add [語言]          — 新增語言到即時翻譯集合
  /set remove [語言]       — 從集合中移除語言
  /set list                — 查看目前即時翻譯語言集合
  @bot（單獨呼叫）          — 顯示說明

語言設定：
  新增支援語言只需在 SUPPORTED_LANGS 加一行。

環境變數（.env）：
  DISCORD_BOT_TOKEN  — Discord Bot Token（必填）
  OPENAI_API_KEY     — OpenAI API Key（必填）
  OPENAI_MODEL       — 模型名稱（選填，預設 gpt-4o-mini）
  SHORT_MSG_MAX_CHARS— 快取短訊息門檻字數（選填，預設 10）
  USE_REDIS          — 是否使用 Redis 快取（選填，預設 false）
  REDIS_URL          — Redis 連線 URL（選填）
  SQLITE_DB          — SQLite 檔案路徑（選填，預設 ./trans_cache.sqlite3）
  LANGS_FILE         — 語言集 JSON 路徑（選填，預設 ./channel_langs.json）
  LOG_LEVEL          — 日誌等級（選填，預設 INFO）
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands, Message
from discord.ext import commands
from dotenv import load_dotenv
from openai import AsyncOpenAI
import aiosqlite

try:
    import redis.asyncio as aioredis  # type: ignore
except Exception:
    aioredis = None

# -----------------------------
# 基本設定
# -----------------------------
load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("translator-bot")

DISCORD_TOKEN  = os.environ.get("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not DISCORD_TOKEN:
    raise RuntimeError("環境變數 DISCORD_BOT_TOKEN 未設定")
if not OPENAI_API_KEY:
    raise RuntimeError("環境變數 OPENAI_API_KEY 未設定")

_guild_ids_env = os.environ.get("DISCORD_GUILD_IDS") or os.environ.get("DISCORD_GUILD_ID")
if not _guild_ids_env:
    raise RuntimeError("環境變數 DISCORD_GUILD_IDS 未設定")
ALLOWED_GUILD_IDS: set[int] = {int(gid.strip()) for gid in _guild_ids_env.split(",") if gid.strip()}

SHORT_MSG_MAX_CHARS = int(os.getenv("SHORT_MSG_MAX_CHARS", "10"))
CACHE_LONG_MESSAGES = False

USE_REDIS  = os.getenv("USE_REDIS", "false").lower() == "true"
REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SQLITE_DB  = os.getenv("SQLITE_DB", "./trans_cache.sqlite3")
LANGS_FILE = os.getenv("LANGS_FILE", "./channel_langs.json")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_RETRIES = 3

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# -------------------------------------------------------
# 語言設定（可擴充）
# 未來新增語言只需在這裡加一行：
#   "ja": "日本語"
# -------------------------------------------------------
SUPPORTED_LANGS: Dict[str, str] = {
    "zh": "繁體中文",
    "en": "English",
    "ko": "한국어",
}

LANG_NAMES_EN: Dict[str, str] = {
    "zh": "Traditional Chinese",
    "en": "English",
    "ko": "Korean",
}

SYSTEM_PROMPT = (
    "You are an expert, context-aware translator for casual chats. "
    "Translate the user's message following the instructions given. "
    "Be natural and idiomatic, preserve punctuation and simple formatting. "
    "Do not add explanations. Output only valid JSON."
)

# -----------------------------
# 工具：前處理
# -----------------------------
URL_PATTERN        = re.compile(r"^https?://", re.IGNORECASE)
ONLY_EMOJI_PATTERN = re.compile(r"^[\W_]+$", re.UNICODE)
WHITESPACE_PATTERN = re.compile(r"^\s*$")
EMOJI_PATTERN      = re.compile(
    "[\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


def should_skip(text: str) -> bool:
    stripped = text.strip()
    if WHITESPACE_PATTERN.match(text):
        return True
    if URL_PATTERN.match(stripped):
        return True
    if ONLY_EMOJI_PATTERN.match(stripped):
        return True
    return False


def extract_emojis(text: str) -> Tuple[List[str], str]:
    emojis = EMOJI_PATTERN.findall(text)
    cleaned = EMOJI_PATTERN.sub("<EMOJI>", text)
    return emojis, cleaned


def restore_emojis(text: str, emojis: List[str]) -> str:
    for emoji in emojis:
        text = text.replace("<EMOJI>", emoji, 1)
    return text


# -----------------------------
# 快取層：Redis 或 SQLite
# -----------------------------
class TranslationCache:
    def __init__(self):
        self.use_redis   = USE_REDIS and aioredis is not None
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
                logger.warning("Redis 初始化失敗，改用 SQLite：%s", e)
                self.use_redis = False

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
        norm = re.sub(r"\s+", " ", text.strip())
        return json.dumps({"t": norm, "p": policy}, ensure_ascii=False)

    async def get(self, text: str, policy: str) -> Optional[Dict[str, str]]:
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
                async with db.execute(
                    "SELECT result_json FROM translations WHERE key=?", (key,)
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        try:
                            return json.loads(row[0])
                        except Exception:
                            return None
        return None

    async def set(
        self,
        text: str,
        policy: str,
        result: Dict[str, str],
        ttl_sec: int = 7 * 24 * 3600,
    ):
        key     = self._make_key(text, policy)
        payload = json.dumps(result, ensure_ascii=False)
        now     = int(time.time())
        if self.use_redis and self.redis is not None:
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
async def translate_with_openai(
    text: str,
    lang_set: List[str],
    force_target: Optional[str] = None,
) -> Dict[str, str]:
    """
    force_target 指定時：直接翻成該語言。
    force_target 為 None 時：讓 GPT 自動偵測來源語言，翻成語言集內其他語言。
    """
    if force_target:
        target_name = f"{LANG_NAMES_EN.get(force_target, force_target)} (\"{force_target}\")"
        user_prompt = (
            f"Translate the following message into {target_name}. "
            f"Output ONLY valid JSON with exactly one key \"{force_target}\". "
            "Preserve all line breaks exactly as in the original.\n\n"
            f"Message:\n{text}"
        )
    else:
        lang_descs = ", ".join(
            f"{LANG_NAMES_EN.get(l, l)} (\"{l}\")" for l in lang_set
        )
        user_prompt = (
            f"Language set: {lang_descs}.\n"
            "Detect the language of the message. "
            "Translate it into all OTHER languages from the set (skip the source language). "
            "Output ONLY valid JSON using language codes as keys. "
            "Include only the translated languages (do not include the source language key). "
            "Preserve all line breaks exactly as in the original.\n\n"
            f"Message:\n{text}"
        )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await openai_client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
            )
            output = resp.choices[0].message.content.strip()

            if output.startswith("```"):
                output = re.sub(r"^```[^\n]*\n?", "", output)
                output = re.sub(r"\n?```$", "", output).strip()

            parsed = json.loads(output)
            return {k: v for k, v in parsed.items() if isinstance(v, str)}

        except json.JSONDecodeError:
            logger.warning("解析 GPT 回傳失敗（第 %d 次）：%s", attempt, output)
            return {}
        except Exception as e:
            logger.warning("OpenAI 呼叫失敗（第 %d 次）：%s", attempt, e)
            if attempt == MAX_RETRIES:
                raise
            await asyncio.sleep(1.5 * attempt)
    return {}


# -----------------------------
# 核心翻譯流程（含快取）
# -----------------------------
async def run_translation(
    text: str,
    lang_set: List[str],
    force_target: Optional[str] = None,
) -> Dict[str, str]:
    emojis, clean = extract_emojis(text)
    src_text = clean.strip() or text

    policy    = force_target if force_target else "->".join(sorted(lang_set))
    is_short  = len(src_text) <= SHORT_MSG_MAX_CHARS
    use_cache = is_short or CACHE_LONG_MESSAGES

    if use_cache:
        cached = await cache.get(src_text, policy)
        if cached:
            return {k: restore_emojis(v, emojis) for k, v in cached.items() if v}

    result = await translate_with_openai(src_text, lang_set, force_target=force_target)
    result = {k: restore_emojis(v, emojis) for k, v in result.items()}

    if use_cache:
        try:
            await cache.set(src_text, policy, result)
        except Exception as e:
            logger.warning("寫入快取失敗：%s", e)

    return result


# -----------------------------
# 即時翻譯狀態
# -----------------------------
channel_langs: Dict[int, set] = {}
live_channels: set[int] = set()


def load_channel_langs() -> Dict[int, set]:
    try:
        with open(LANGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): set(v) for k, v in data.items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("載入語言集失敗：%s", e)
        return {}


def save_channel_langs():
    try:
        data = {str(k): list(v) for k, v in channel_langs.items()}
        with open(LANGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("儲存語言集失敗：%s", e)


# -----------------------------
# Bot 設定
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
cache = TranslationCache()

_lang_choices = [
    app_commands.Choice(name=f"{v}（{k}）", value=k)
    for k, v in SUPPORTED_LANGS.items()
]


@bot.tree.command(name="live-translate-on", description="開始即時翻譯（依語言集自動翻譯每則訊息）")
async def cmd_live_translate(interaction: discord.Interaction):
    cid = interaction.channel_id
    if cid not in channel_langs:
        channel_langs[cid] = {"zh"}
        save_channel_langs()
    live_channels.add(cid)
    langs = channel_langs[cid]
    names = " / ".join(SUPPORTED_LANGS.get(l, l) for l in langs)
    await interaction.response.send_message(
        f"即時翻譯已開始。目前語言集：**{names}**\n"
        "用 `/set add` 加入更多語言，`/live-translate-off` 停止。"
    )


@bot.tree.command(name="live-translate-off", description="關閉即時翻譯")
async def cmd_live_translate_off(interaction: discord.Interaction):
    if interaction.channel_id in live_channels:
        live_channels.discard(interaction.channel_id)
        await interaction.response.send_message("即時翻譯已結束。")
    else:
        await interaction.response.send_message("此頻道目前沒有進行即時翻譯。", ephemeral=True)


class SetGroup(app_commands.Group, name="set", description="管理即時翻譯語言集合"):

    @app_commands.command(name="add", description="新增語言到即時翻譯語言集")
    @app_commands.describe(lang="要新增的語言")
    @app_commands.choices(lang=_lang_choices)
    async def set_add(self, interaction: discord.Interaction, lang: str):
        cid = interaction.channel_id
        if cid not in channel_langs:
            channel_langs[cid] = {"zh"}
        channel_langs[cid].add(lang)
        save_channel_langs()
        names = " / ".join(SUPPORTED_LANGS.get(l, l) for l in channel_langs[cid])
        await interaction.response.send_message(f"已新增 **{SUPPORTED_LANGS.get(lang, lang)}**。目前語言集：{names}")

    @app_commands.command(name="remove", description="從即時翻譯語言集移除語言")
    @app_commands.describe(lang="要移除的語言")
    @app_commands.choices(lang=_lang_choices)
    async def set_remove(self, interaction: discord.Interaction, lang: str):
        cid = interaction.channel_id
        if cid not in channel_langs:
            channel_langs[cid] = {"zh"}
        langs = channel_langs[cid]
        if lang not in langs:
            await interaction.response.send_message(
                f"**{SUPPORTED_LANGS.get(lang, lang)}** 不在目前語言集中。", ephemeral=True
            )
            return
        if len(langs) <= 1:
            await interaction.response.send_message(
                "語言集至少需要一種語言，無法移除。", ephemeral=True
            )
            return
        langs.discard(lang)
        save_channel_langs()
        names = " / ".join(SUPPORTED_LANGS.get(l, l) for l in langs)
        await interaction.response.send_message(f"已移除 **{SUPPORTED_LANGS.get(lang, lang)}**。目前語言集：{names}")

    @app_commands.command(name="list", description="查看目前即時翻譯語言集")
    async def set_list(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid not in channel_langs:
            channel_langs[cid] = {"zh"}
        langs = channel_langs[cid]
        names = " / ".join(SUPPORTED_LANGS.get(l, l) for l in langs)
        status = "開啟中" if cid in live_channels else "未開啟"
        await interaction.response.send_message(
            f"目前語言集：{names}（即時翻譯：{status}）", ephemeral=True
        )


bot.tree.add_command(SetGroup())


async def process_ondemand(message: Message, target: Optional[str] = None):
    """翻譯被回覆的訊息。target 為指定語言代碼，None 則依頻道語言集翻譯。"""
    try:
        target_msg = await message.channel.fetch_message(message.reference.message_id)
    except Exception:
        await message.reply("無法取得原始訊息。")
        return

    if should_skip(target_msg.content):
        await message.reply("此訊息無法翻譯。")
        return

    if target:
        lang_set = list(SUPPORTED_LANGS.keys())
        force_target = target
    else:
        lang_set = list(channel_langs.get(message.channel.id) or SUPPORTED_LANGS.keys())
        force_target = None

    try:
        result = await run_translation(target_msg.content, lang_set, force_target=force_target)
    except Exception as e:
        logger.error("隨選翻譯失敗：%s", e)
        await message.reply("翻譯失敗，請稍後再試。")
        return

    parts = list(result.values())
    if parts:
        await message.reply("\n\n".join(parts)[:1990])


# ------------------------------------------------
# on_message：@bot 文字指令 + 即時翻譯
# ------------------------------------------------
@bot.event
async def on_ready():
    for gid in ALLOWED_GUILD_IDS:
        guild = discord.Object(id=gid)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    logger.info("Logged in as %s (id=%s)，斜線指令已同步到授權伺服器：%s", bot.user, bot.user.id, ALLOWED_GUILD_IDS)

    for g in bot.guilds:
        if g.id not in ALLOWED_GUILD_IDS:
            logger.warning("非授權伺服器 %s (%s)，自動離開", g.name, g.id)
            await g.leave()


@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id not in ALLOWED_GUILD_IDS:
        logger.warning("拒絕加入非授權伺服器 %s (%s)，自動離開", guild.name, guild.id)
        await guild.leave()


@bot.event
async def on_message(message: Message):
    if message.author.bot:
        return
    try:
        if bot.user in message.mentions:
            await handle_mention_command(message)
            return
        if message.channel.id in live_channels:
            await process_live_message(message)
    except Exception as e:
        logger.exception("處理訊息時發生未處理例外：%s", e)


async def handle_mention_command(message: Message):
    clean = re.sub(r"<@!?\d+>", "", message.content).strip()

    if clean.startswith("/live-translate-off"):
        if message.channel.id in live_channels:
            live_channels.discard(message.channel.id)
            await message.channel.send("即時翻譯已結束。")
        else:
            await message.channel.send("此頻道目前沒有進行即時翻譯。")
        return

    if clean.startswith("/live-translate-on"):
        cid = message.channel.id
        if cid not in channel_langs:
            channel_langs[cid] = {"zh"}
            save_channel_langs()
        live_channels.add(cid)
        langs = channel_langs[cid]
        names = " / ".join(SUPPORTED_LANGS.get(l, l) for l in langs)
        await message.channel.send(
            f"即時翻譯已開始。目前語言集：**{names}**\n"
            "用 `/set add` 加入更多語言，`/live-translate-off` 停止。"
        )
        return

    if message.reference and message.reference.message_id:
        target = clean if clean in SUPPORTED_LANGS else None
        await process_ondemand(message, target)
        return

    await message.channel.send(
        "**翻譯機器人指令說明**\n\n"
        "**隨選翻譯**\n"
        "→ 回覆訊息 + `@bot` — 翻譯被回覆的訊息（自動偵測語言）\n"
        "→ 回覆訊息 + `@bot ko` — 指定翻成韓文（可換其他語言代碼）\n\n"
        "**即時翻譯**\n"
        "→ `/live-translate-on` — 開始（頻道內每則訊息自動翻譯）\n"
        "→ `/set add` — 新增語言到集合\n"
        "→ `/set remove` — 移除語言\n"
        "→ `/set list` — 查看目前語言集合\n"
        "→ `/live-translate-off` — 結束（任何人都可以停止）\n\n"
        f"目前支援語言：{', '.join(f'{v}（{k}）' for k, v in SUPPORTED_LANGS.items())}"
    )


async def process_live_message(message: Message):
    if should_skip(message.content):
        return

    lang_set = list(channel_langs.get(message.channel.id, set()))
    if len(lang_set) < 2:
        return

    try:
        result = await run_translation(message.content, lang_set)
    except Exception as e:
        logger.error("即時翻譯失敗：%s", e)
        return

    parts = list(result.values())
    if parts:
        await message.reply("\n\n".join(parts)[:1990])


# -----------------------------
# 入口
# -----------------------------
async def main():
    global channel_langs
    channel_langs = load_channel_langs()
    logger.info("已載入語言集設定，共 %d 個頻道", len(channel_langs))
    await cache.init()
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot 已停止")