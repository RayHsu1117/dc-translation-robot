# -*- coding: utf-8 -*-
"""bot.py 單元測試（stdlib unittest）。

重要：所有測試只使用 temp 目錄，絕不碰 repo 內的
trans_cache.sqlite3 / channel_langs.json（那是正在運行的生產資料）。
"""

import asyncio
import glob
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

# ------------------------------------------------------------------
# 必須在 import bot 之前設好假環境變數，並把所有資料檔導向 temp 目錄
# （bot.py import 時會 load_dotenv()，但 load_dotenv 不覆寫既有環境變數）
# ------------------------------------------------------------------
_TMPDIR = tempfile.mkdtemp(prefix="dc-gpt-translator-test-")
os.environ["DISCORD_BOT_TOKEN"] = "test-token"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["DISCORD_GUILD_IDS"] = "123456789"
os.environ["USE_REDIS"] = "false"
os.environ["SQLITE_DB"] = os.path.join(_TMPDIR, "cache.sqlite3")
os.environ["LANGS_FILE"] = os.path.join(_TMPDIR, "langs.json")
os.environ["HEARTBEAT_FILE"] = os.path.join(_TMPDIR, "heartbeat")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402


class ChannelStateTest(unittest.TestCase):
    """S5（atomic 寫入 + 壞檔保留）與 R9（live 狀態持久化 + 舊格式遷移）。"""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="langs-test-", dir=_TMPDIR)
        self._orig_langs_file = bot.LANGS_FILE
        self._orig_channel_langs = bot.channel_langs
        self._orig_live_channels = bot.live_channels
        bot.LANGS_FILE = os.path.join(self._dir, "langs.json")

    def tearDown(self):
        bot.LANGS_FILE = self._orig_langs_file
        bot.channel_langs = self._orig_channel_langs
        bot.live_channels = self._orig_live_channels

    def test_save_load_roundtrip(self):
        bot.channel_langs = {1: {"zh", "en"}, 2: {"ko"}}
        bot.live_channels = {1}
        bot.save_channel_state()

        # atomic 寫入：不留 .tmp 殘檔
        self.assertFalse(os.path.exists(bot.LANGS_FILE + ".tmp"))

        # 磁碟上是新格式
        with open(bot.LANGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw["1"], {"langs": ["en", "zh"], "live": True})
        self.assertEqual(raw["2"], {"langs": ["ko"], "live": False})

        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {1: {"zh", "en"}, 2: {"ko"}})
        self.assertEqual(live, {1})

    def test_load_missing_file(self):
        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {})
        self.assertEqual(live, set())

    def test_load_corrupt_file_is_preserved(self):
        garbage = "{ this is not json"
        with open(bot.LANGS_FILE, "w", encoding="utf-8") as f:
            f.write(garbage)

        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {})
        self.assertEqual(live, set())

        # 壞檔被改名保留，內容原封不動
        corrupts = glob.glob(bot.LANGS_FILE + ".corrupt-*")
        self.assertEqual(len(corrupts), 1)
        with open(corrupts[0], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), garbage)
        self.assertFalse(os.path.exists(bot.LANGS_FILE))

    def test_load_wrong_toplevel_type_treated_as_corrupt(self):
        with open(bot.LANGS_FILE, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {})
        self.assertEqual(live, set())
        self.assertEqual(len(glob.glob(bot.LANGS_FILE + ".corrupt-*")), 1)

    def _write(self, obj):
        with open(bot.LANGS_FILE, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_malformed_entries_skipped_never_crash(self):
        """N1：合法 JSON 但內容格式錯誤 → 逐筆略過，絕不 raise。"""
        cases = [
            {"abc": ["zh"]},                        # 非數字 key
            {"1": 5},                               # 數值
            {"1": None},                            # null
            {"1": "zh"},                            # 字串（不能被拆成 {'z','h'}）
            {"1": {"langs": "zh", "live": True}},   # 新格式但 langs 非 list
            {"1": ["zh", 5]},                       # list 但含非字串
        ]
        for data in cases:
            with self.subTest(data=data):
                self._write(data)
                langs, live = bot.load_channel_state()  # 不得 raise
                self.assertEqual(langs, {})
                self.assertEqual(live, set())
                # 檔案本身是合法 JSON → skip-and-log，不隔離
                self.assertTrue(os.path.exists(bot.LANGS_FILE))

    def test_string_value_never_split_into_chars(self):
        """N1：{"1": "zh"} 絕不能變成語言集 {'z','h'}。"""
        self._write({"1": "zh"})
        langs, _ = bot.load_channel_state()
        self.assertEqual(langs, {})
        self.assertNotIn(1, langs)

    def test_mixed_good_and_bad_entries(self):
        """N1：壞的略過、好的保留。"""
        self._write({
            "1": ["zh", "en"],
            "2": "bad",
            "abc": ["ko"],
            "3": {"langs": ["ko"], "live": True},
        })
        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {1: {"zh", "en"}, 3: {"ko"}})
        self.assertEqual(live, {3})

    def test_old_schema_migration(self):
        # 舊格式：{"cid": ["zh", "ko"]} → 透明遷移，live 預設關閉
        with open(bot.LANGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"42": ["zh", "ko"]}, f)
        langs, live = bot.load_channel_state()
        self.assertEqual(langs, {42: {"zh", "ko"}})
        self.assertEqual(live, set())

        # 存回去之後升級為新格式
        bot.channel_langs = langs
        bot.live_channels = live
        bot.save_channel_state()
        with open(bot.LANGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw["42"], {"langs": ["ko", "zh"], "live": False})


class TokenExtractionTest(unittest.TestCase):
    """R8：編號 token 的抽取與還原。"""

    def test_basic_emoji_roundtrip(self):
        text = "hello 😀 world"
        items, cleaned = bot.extract_tokens(text)
        self.assertEqual(items, ["😀"])
        self.assertEqual(cleaned, "hello ⟦E1⟧ world")
        self.assertEqual(bot.restore_tokens(cleaned, items), text)

    def test_zwj_sequence_kept_whole(self):
        # 家庭 emoji（ZWJ 組合序列）必須當成單一 token
        family = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"
        text = f"我們 {family} 全家"
        items, cleaned = bot.extract_tokens(text)
        self.assertEqual(items, [family])
        self.assertEqual(cleaned, "我們 ⟦E1⟧ 全家")
        self.assertEqual(bot.restore_tokens(cleaned, items), text)

    def test_skin_tone_modifier_kept_whole(self):
        thumbs = "\U0001F44D\U0001F3FD"  # 👍🏽
        items, cleaned = bot.extract_tokens(f"good {thumbs}")
        self.assertEqual(items, [thumbs])

    def test_custom_emoji_and_mentions_protected(self):
        text = "<:smile:12345> hi <a:run:99> <@111> <@!222> <@&333> <#444>"
        items, cleaned = bot.extract_tokens(text)
        self.assertEqual(
            items,
            ["<:smile:12345>", "<a:run:99>", "<@111>", "<@!222>", "<@&333>", "<#444>"],
        )
        # cleaned 內不應再有任何 Discord 特殊片段
        self.assertNotIn("<:", cleaned)
        self.assertNotIn("<@", cleaned)
        self.assertNotIn("<#", cleaned)
        self.assertEqual(bot.restore_tokens(cleaned, items), text)

    def test_literal_token_lookalike_no_collision(self):
        # 訊息本身就含字面 ⟦E1⟧ 也不能錯位
        text = "fake ⟦E1⟧ real 😀"
        items, cleaned = bot.extract_tokens(text)
        self.assertEqual(items, ["⟦E1⟧", "😀"])
        self.assertEqual(cleaned, "fake ⟦E1⟧ real ⟦E2⟧")
        self.assertEqual(bot.restore_tokens(cleaned, items), text)

    def test_missing_token_appended_at_end(self):
        # GPT 把 token 弄丟 → 內容附回文末，不丟例外
        restored = bot.restore_tokens("翻譯結果沒有 token", ["😀"])
        self.assertTrue(restored.endswith("😀"))

    def test_unknown_token_dropped_and_missing_appended(self):
        # GPT 自己生出 ⟦E9⟧ → 移除；原本的 😀 沒被用到 → 附回文末
        restored = bot.restore_tokens("x ⟦E9⟧ y", ["😀"])
        self.assertNotIn("⟦E9⟧", restored)
        self.assertIn("😀", restored)


class FilterTranslationKeysTest(unittest.TestCase):
    """S4：回傳 key 白名單過濾。"""

    def test_whitelist_and_str_values(self):
        parsed = {
            "zh": "嗨",
            "ko": "안녕",
            "xx": "不在白名單",
            "en": 123,          # 非字串 value
            "evil": {"a": 1},
        }
        self.assertEqual(
            bot.filter_translation_keys(parsed), {"zh": "嗨", "ko": "안녕"}
        )

    def test_non_dict_returns_empty(self):
        self.assertEqual(bot.filter_translation_keys(["zh"]), {})
        self.assertEqual(bot.filter_translation_keys("zh"), {})
        self.assertEqual(bot.filter_translation_keys(None), {})


class CacheKeyTest(unittest.TestCase):
    """R14：快取 key 含 model 名。"""

    def test_key_contains_model_name(self):
        key = bot.TranslationCache._make_key("hello world", "en->zh")
        self.assertIn(bot.MODEL_NAME, key)
        self.assertIn("en->zh", key)

    def test_whitespace_normalized(self):
        self.assertEqual(
            bot.TranslationCache._make_key("a   b\n c", "p"),
            bot.TranslationCache._make_key("a b c", "p"),
        )


class SlidingWindowLimiterTest(unittest.TestCase):
    """S2：滑動視窗限流器。"""

    def test_allow_then_deny_then_recover(self):
        lim = bot.SlidingWindowLimiter(2, window_sec=60.0)
        self.assertTrue(lim.allow("k", now=0.0))
        self.assertTrue(lim.allow("k", now=1.0))
        self.assertFalse(lim.allow("k", now=2.0))   # 超過 2 則/分鐘
        self.assertFalse(lim.allow("k", now=59.0))
        self.assertTrue(lim.allow("k", now=61.0))   # 視窗滑過去後恢復

    def test_keys_are_independent(self):
        lim = bot.SlidingWindowLimiter(1, window_sec=60.0)
        self.assertTrue(lim.allow("a", now=0.0))
        self.assertTrue(lim.allow("b", now=0.0))
        self.assertFalse(lim.allow("a", now=1.0))

    def test_should_notify_once_per_window(self):
        lim = bot.SlidingWindowLimiter(1, window_sec=60.0)
        self.assertTrue(lim.should_notify("k", now=0.0))
        self.assertFalse(lim.should_notify("k", now=30.0))
        self.assertTrue(lim.should_notify("k", now=60.0))

    def test_prune_removes_stale_keys(self):
        """N3：視窗滑空的 key 要被清掉，不能無限成長。"""
        lim = bot.SlidingWindowLimiter(2, window_sec=60.0)
        lim.allow("stale", now=0.0)
        lim.should_notify("stale", now=0.0)
        lim.allow("fresh", now=100.0)
        removed = lim.prune(now=100.0)
        self.assertEqual(removed, 1)
        self.assertNotIn("stale", lim._events)
        self.assertNotIn("stale", lim._last_notice)
        self.assertIn("fresh", lim._events)


class SplitMessageTest(unittest.TestCase):
    """R4：長訊息分段。"""

    def test_empty_and_short(self):
        self.assertEqual(bot.split_message(""), [])
        self.assertEqual(bot.split_message("hi"), ["hi"])
        self.assertEqual(bot.split_message("a" * 2000), ["a" * 2000])

    def test_split_on_newline_boundaries(self):
        line = "a" * 900
        text = "\n".join([line] * 5)  # 4504 字元
        chunks = bot.split_message(text)
        self.assertTrue(all(len(c) <= 2000 for c in chunks))
        # 只在換行處切：接回去要等於原文
        self.assertEqual("\n".join(chunks), text)
        # 每段都是完整的行組成
        for c in chunks:
            for seg in c.split("\n"):
                self.assertEqual(seg, line)

    def test_hard_split_without_newline(self):
        text = "a" * 4500
        chunks = bot.split_message(text)
        self.assertEqual([len(c) for c in chunks], [2000, 2000, 500])
        self.assertEqual("".join(chunks), text)


class CacheTTLTest(unittest.TestCase):
    """R2：SQLite 快取 TTL（讀取檢查 + 過期清理），全部在 temp 目錄執行。"""

    def setUp(self):
        self._orig_ttl = bot.CACHE_TTL_SEC
        self.cache = bot.TranslationCache()
        self.cache.use_redis = False
        self.cache.sqlite_path = os.path.join(
            tempfile.mkdtemp(prefix="cache-test-", dir=_TMPDIR), "ttl.sqlite3"
        )

    def tearDown(self):
        bot.CACHE_TTL_SEC = self._orig_ttl

    def test_ttl_hit_expire_and_cleanup(self):
        async def scenario():
            await self.cache.init()
            await self.cache.set("hello", "p", {"zh": "嗨"})

            # 未過期 → 命中
            self.assertEqual(await self.cache.get("hello", "p"), {"zh": "嗨"})

            # TTL 設為 -1 → 一切都算過期 → 未命中
            bot.CACHE_TTL_SEC = -1
            self.assertIsNone(await self.cache.get("hello", "p"))

            # 清理後即使 TTL 恢復也拿不到（列已刪除）
            await self.cache.cleanup_expired()
            bot.CACHE_TTL_SEC = self._orig_ttl
            self.assertIsNone(await self.cache.get("hello", "p"))

        asyncio.run(scenario())


class TokenOnlyMessageTest(unittest.TestCase):
    """N4：整則訊息只剩受保護片段 → 跳過翻譯，絕不把原文送給 GPT。"""

    def test_should_skip_token_only_messages(self):
        self.assertTrue(bot.should_skip("<:smile:123>"))
        self.assertTrue(bot.should_skip("<@111> <#222>"))
        self.assertTrue(bot.should_skip("<a:run:99> 😀"))
        self.assertFalse(bot.should_skip("hi <:smile:123>"))

    def test_run_translation_skips_token_only_text(self):
        # 不需要 mock：run_translation 必須在碰快取 / OpenAI 之前就直接回空
        result = asyncio.run(bot.run_translation("<:smile:123>", ["zh", "en"]))
        self.assertEqual(result, {})
        result = asyncio.run(bot.run_translation("<@111> ⟦E1⟧", ["zh", "en"]))
        self.assertEqual(result, {})


class _FakeTyping:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


def _fake_message(target_content, channel_id=999, author_id=5):
    channel = SimpleNamespace(
        id=channel_id,
        fetch_message=mock.AsyncMock(
            return_value=SimpleNamespace(content=target_content)
        ),
        typing=lambda: _FakeTyping(),
        send=mock.AsyncMock(),
    )
    return SimpleNamespace(
        channel=channel,
        reference=SimpleNamespace(message_id=1),
        author=SimpleNamespace(id=author_id),
        reply=mock.AsyncMock(),
    )


class OnDemandGuardTest(unittest.TestCase):
    """S2（隨選路徑）：長度上限與限流，拒絕時要回話；不會打到 OpenAI。"""

    def setUp(self):
        self._orig_limiter = bot.ondemand_limiter
        self._patcher = mock.patch.object(
            bot, "run_translation", new=mock.AsyncMock(return_value={"en": "hi"})
        )
        self.run_translation = self._patcher.start()

    def tearDown(self):
        bot.ondemand_limiter = self._orig_limiter
        self._patcher.stop()

    def test_length_cap_refused_with_reply(self):
        bot.ondemand_limiter = bot.SlidingWindowLimiter(5)
        msg = _fake_message("a" * (bot.LIVE_MAX_CHARS + 1))
        asyncio.run(bot.process_ondemand(msg))
        msg.reply.assert_awaited()
        self.assertIn("過長", msg.reply.await_args.args[0])
        self.run_translation.assert_not_awaited()

    def test_rate_limit_refused_with_reply(self):
        bot.ondemand_limiter = bot.SlidingWindowLimiter(0)  # 一律拒絕
        msg = _fake_message("hello world")
        asyncio.run(bot.process_ondemand(msg))
        msg.reply.assert_awaited()
        self.assertIn("頻繁", msg.reply.await_args.args[0])
        self.run_translation.assert_not_awaited()

    def test_allowed_request_translates_and_replies(self):
        bot.ondemand_limiter = bot.SlidingWindowLimiter(5)
        msg = _fake_message("hello world")
        asyncio.run(bot.process_ondemand(msg))
        self.run_translation.assert_awaited()
        self.assertEqual(msg.reply.await_args.args[0], "hi")


if __name__ == "__main__":
    unittest.main()
