# dc-gpt-translator

Discord 翻譯機器人，使用 GPT API 進行多語言翻譯。語言偵測與翻譯全部由 GPT 一次完成。

## 功能

### 隨選翻譯

回覆某則訊息並 @bot，機器人翻譯被回覆的訊息。

| 操作 | 說明 |
| --- | --- |
| 回覆訊息 + `@bot` | 自動偵測語言，翻成頻道語言集內其他語言；若頻道語言集不足兩種語言（例如剛啟用、還沒 `/set add` 過），自動改用全部支援語言 |
| 回覆訊息 + `@bot ko` | 指定翻成韓文，不受頻道語言集限制（可換其他語言代碼：`zh`、`en`、`ko`） |
| 單獨 `@bot`（不回覆任何訊息） | 顯示指令說明 |

> @mention 指令僅能在伺服器頻道使用，不支援私訊（DM）。

### 即時翻譯模式

開啟後，頻道內每則訊息（含編輯後的訊息）都會依照該頻道的語言集自動翻譯，不需手動呼叫機器人。**語言集需至少兩種語言才會真正翻譯**——新頻道預設語言集只有「繁體中文」一種，記得先用 `/set add` 加入至少第二種語言。

| 指令 | 說明 |
| --- | --- |
| `/live-translate-on` | 開始即時翻譯（需要「管理頻道」權限） |
| `/live-translate-off` | 結束即時翻譯（任何人都可以） |

`@bot /live-translate-on`、`@bot /live-translate-off` 也可以用文字 @mention 的方式呼叫（伺服器頻道內、權限要求相同），但 `/set` 系列指令僅提供斜線指令，沒有文字 fallback。

即時翻譯內建成本防護：訊息過長或觸發限流時會略過並提示（見下方環境變數）。

### 語言集管理

每個頻道有獨立的語言集，設定與即時翻譯開關狀態會持久化到檔案，重啟機器人也會保留。

| 指令 | 說明 |
| --- | --- |
| `/set add [語言]` | 新增語言到語言集（需要「管理頻道」權限） |
| `/set remove [語言]` | 從語言集移除語言，至少需保留一種語言（需要「管理頻道」權限） |
| `/set list` | 查看目前語言集與即時翻譯狀態（任何人都可以，回覆僅自己看得到） |

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

> 新增語言需在 `bot.py` 的 `SUPPORTED_LANGS` 和 `LANG_NAMES_EN` 各加一行（步驟見 [SETUP.md](SETUP.md#新增支援語言)），斜線指令的語言選單會自動更新。目前沒有透過環境變數或指令新增語言的方式。

## 技術架構

- **Discord 框架**：discord.py（`discord.Client` + `app_commands.CommandTree`）
- **翻譯引擎**：OpenAI API（預設 `gpt-4o-mini`，`AsyncOpenAI`，逐次呼叫皆有明確 timeout 與重試）
- **語言偵測**：由 GPT 自動判斷，無需額外套件
- **快取**：SQLite（預設）或 Redis；TTL 由 `CACHE_TTL_SEC` 控制（預設 604800 秒 = 7 天）；只有短訊息（≤ `SHORT_MSG_MAX_CHARS`）會進快取
- **語言集 / 即時翻譯狀態持久化**：`LANGS_FILE`（預設 `./channel_langs.json`；Docker 部署預設改為 `/data/channel_langs.json`，存於 named volume），重啟後自動載入，即時翻譯的開關狀態也會一併還原
- **成本防護**：即時翻譯有訊息長度上限與每人 / 每頻道限流（見環境變數表）
- **安全性**：非授權伺服器加入時自動離開、指令僅同步到 `DISCORD_GUILD_IDS` 指定的伺服器、翻譯 prompt 以 delimiter 明確標示使用者輸入為資料而非指令
- **容器化**：Docker（非 root 使用者執行、heartbeat HEALTHCHECK）+ docker-compose（runtime 資料存放於 named volume `bot-data`，不再 bind mount 到 repo 目錄）

## 環境變數

| 變數名稱 | 必填 | 預設值 | 說明 |
| --- | --- | --- | --- |
| `DISCORD_BOT_TOKEN` | ✅ | — | Discord Bot Token |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API Key |
| `DISCORD_GUILD_IDS` | ✅ | — | 授權伺服器 ID，逗號分隔，可多個（相容舊別名 `DISCORD_GUILD_ID`，兩者擇一設定即可） |
| `OPENAI_MODEL` | | `gpt-4o-mini` | 使用的 GPT 模型 |
| `OPENAI_MAX_TOKENS` | | `2000` | 單次翻譯回應的 token 上限 |
| `SHORT_MSG_MAX_CHARS` | | `10` | 快取短訊息門檻（字數，處理過提及/表情符號 token 化後的字數） |
| `CACHE_TTL_SEC` | | `604800`（7 天） | 快取存活秒數 |
| `LIVE_MAX_CHARS` | | `1500` | 即時翻譯單則訊息長度上限，超過就跳過並標記 📏 表情 |
| `LIVE_USER_PER_MIN` | | `5` | 即時翻譯每人每頻道每分鐘則數上限 |
| `LIVE_CHANNEL_PER_MIN` | | `20` | 即時翻譯每頻道每分鐘則數上限 |
| `USE_REDIS` | | `false` | 是否使用 Redis 做快取（初始化失敗會自動退回 SQLite） |
| `REDIS_URL` | | `redis://localhost:6379/0` | Redis 連線 URL |
| `SQLITE_DB` | | `./trans_cache.sqlite3` | SQLite 快取檔案路徑（docker-compose 預設覆寫為 `/data/trans_cache.sqlite3`） |
| `LANGS_FILE` | | `./channel_langs.json` | 語言集 / 即時翻譯狀態 JSON 路徑（docker-compose 預設覆寫為 `/data/channel_langs.json`） |
| `HEARTBEAT_FILE` | | 系統暫存目錄下的 `dc-translator-heartbeat` | 心跳檔案路徑（Docker 映像內固定覆寫為 `/tmp/heartbeat`，供 HEALTHCHECK 使用） |
| `LOG_LEVEL` | | `INFO` | 日誌等級 |

詳細安裝與測試步驟請見 [SETUP.md](SETUP.md)。

## 限制

目前程式碼**不**具備以下能力，使用前請留意：

- **沒有花費上限**：`LIVE_MAX_CHARS` / `LIVE_USER_PER_MIN` / `LIVE_CHANNEL_PER_MIN` / `OPENAI_MAX_TOKENS` 只限制訊息長度與呼叫頻率，不追蹤或限制實際 OpenAI 花費金額（無論每人、每頻道或每月）。
- **Prompt injection 僅緩解、未完全阻絕**：使用者訊息會包在 delimiter 內並在 system prompt 明確聲明「這是資料不是指令」，但無法保證在所有情況下都不受惡意訊息內容影響翻譯輸出。
- **訊息編輯會重新送出新翻譯，不會取代舊翻譯**：即時翻譯頻道中若原訊息被編輯，機器人會依新內容再翻譯一次並發新訊息，先前送出的翻譯不會被編輯或刪除。
- **快取只對短訊息生效**：只有長度 ≤ `SHORT_MSG_MAX_CHARS`（預設 10 字）的訊息會進快取；是否快取長訊息目前寫死在程式碼中（`CACHE_LONG_MESSAGES = False`），沒有對應環境變數可調整。
- **Redis 路徑預設未使用**：`USE_REDIS` 預設 `false`，`docker-compose.yml` 也沒有內建 Redis 服務；要用 Redis 需自行部署並設定 `REDIS_URL`。
- **支援語言寫死在程式碼**：`SUPPORTED_LANGS` / `LANG_NAMES_EN` 需改 `bot.py` 並重啟才能新增語言，無法用環境變數或指令動態新增。
- **只處理訊息文字內容**：不解析附件、embed、貼圖、語音等其他訊息類型。
- **@mention 指令不支援私訊**：`@bot ...` 只能在伺服器頻道中使用。
