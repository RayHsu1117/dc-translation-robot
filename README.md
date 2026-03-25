# dc-gpt-translator

Discord 翻譯機器人，使用 GPT API 進行多語言翻譯。語言偵測與翻譯全部由 GPT 一次完成。

## 功能

### 隨選翻譯

回覆某則訊息並 @bot，機器人自動翻譯被回覆的訊息。

| 操作 | 說明 |
| --- | --- |
| 回覆訊息 + `@bot` | 自動偵測語言，翻成頻道語言集內其他所有語言 |
| 回覆訊息 + `@bot ko` | 指定翻成韓文（可換其他語言代碼） |

### 即時翻譯模式

開啟後，頻道內每則訊息都會自動翻譯，不需手動呼叫機器人。

| 指令 | 說明 |
| --- | --- |
| `/live-translate-on` | 開始即時翻譯 |
| `/live-translate-off` | 結束即時翻譯（任何人都能停止） |

### 語言集管理

每個頻道有獨立的語言集，設定後重啟機器人也會保留。

| 指令 | 說明 |
| --- | --- |
| `/set add [語言]` | 新增語言到語言集 |
| `/set remove [語言]` | 從語言集移除語言 |
| `/set list` | 查看目前語言集與即時翻譯狀態 |

## 翻譯邏輯

GPT 自動判斷訊息的來源語言，翻譯成語言集內所有**其他**語言。

範例（語言集：繁體中文 + 한국어 + English）：

- 傳送中文 → 輸出韓文 + 英文
- 傳送韓文 → 輸出中文 + 英文
- 傳送英文 → 輸出中文 + 韓文

## 支援語言

| 代碼 | 語言 |
| --- | --- |
| `zh` | 繁體中文 |
| `en` | English |
| `ko` | 한국어 |

> 新增語言只需在 `bot.py` 的 `SUPPORTED_LANGS` 和 `LANG_NAMES_EN` 各加一行。

## 技術架構

- **Discord 框架**：discord.py（`commands.Bot` + `app_commands`）
- **翻譯引擎**：OpenAI API（預設 `gpt-4o-mini`，AsyncOpenAI）
- **語言偵測**：由 GPT 自動判斷，無需額外套件
- **快取**：SQLite（預設）或 Redis，TTL 7 天
- **語言集持久化**：`channel_langs.json`，重啟後自動載入
- **容器化**：Docker + docker-compose

## 環境變數

| 變數名稱 | 必填 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | ✅ | — | Discord Bot Token |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API Key |
| `DISCORD_GUILD_IDS` | ✅ | — | 授權伺服器 ID（多個用逗號分隔） |
| `OPENAI_MODEL` | | `gpt-4o-mini` | 使用的 GPT 模型 |
| `SHORT_MSG_MAX_CHARS` | | `10` | 快取短訊息門檻（字數） |
| `USE_REDIS` | | `false` | 改用 Redis 做快取 |
| `REDIS_URL` | | `redis://localhost:6379/0` | Redis 連線 URL |
| `SQLITE_DB` | | `./trans_cache.sqlite3` | SQLite 檔案路徑 |
| `LANGS_FILE` | | `./channel_langs.json` | 語言集設定檔路徑 |
| `LOG_LEVEL` | | `INFO` | 日誌等級 |

詳細安裝與測試步驟請見 [SETUP.md](SETUP.md)。
