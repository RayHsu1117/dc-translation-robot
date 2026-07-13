# 安裝與測試指南

## 前置需求

- Python 3.11+
- Discord Bot Token（需要開啟 Message Content Intent）
- OpenAI API Key
- Discord 伺服器 ID（可多個）
- （選用）Docker

---

## 方法一：直接執行 Python

### 1. 安裝套件

```bash
pip install -r requirements.txt
```

### 2. 建立 `.env` 檔案

在專案根目錄建立 `.env`：

```env
DISCORD_BOT_TOKEN=你的_discord_bot_token
OPENAI_API_KEY=你的_openai_api_key
DISCORD_GUILD_IDS=你的伺服器ID1,你的伺服器ID2

# 以下為選填，僅列出常用項目，完整清單與預設值見 README.md 的環境變數表
OPENAI_MODEL=gpt-4o-mini
SHORT_MSG_MAX_CHARS=10
CACHE_TTL_SEC=604800
LIVE_MAX_CHARS=1500
LIVE_USER_PER_MIN=5
LIVE_CHANNEL_PER_MIN=20
USE_REDIS=false
LOG_LEVEL=INFO
```

> `DISCORD_GUILD_IDS` 可用逗號分隔多個伺服器 ID；只有一個伺服器也可以用舊名稱 `DISCORD_GUILD_ID`（兩者擇一即可，程式會自動相容）。

### 3. 啟動 Bot

```bash
python bot.py
```

直接執行時，SQLite 快取檔（`trans_cache.sqlite3`）與語言集檔（`channel_langs.json`）預設會寫在專案根目錄。

---

## 方法二：Docker

### 1. 建立 `.env` 檔案（同上）

### 2. 啟動

```bash
docker-compose up -d
```

`docker-compose.yml` 會建置 `Dockerfile`（非 root 使用者執行、內建 heartbeat HEALTHCHECK），並把 runtime 資料（SQLite 快取、語言集 JSON）掛載到 named volume `bot-data`（容器內路徑 `/data`），**不再**寫回專案目錄。

### 3. 查看日誌

```bash
docker-compose logs -f
```

### 4. 查看健康狀態（HEALTHCHECK）

```bash
docker inspect --format="{{.State.Health.Status}}" dc-gpt-translator
```

> 以上兩則 Docker 指令依官方文件語法撰寫，本機 Docker daemon 目前未啟動，未實際執行驗證，操作前請自行確認結果。

### 5. 停止

```bash
docker-compose down
```

> `docker-compose down` 不會刪除 named volume，`bot-data` 內的快取與語言集資料會保留到下次 `docker-compose up`。若要連資料一併清除，需另外加 `-v`（`docker-compose down -v`），會遺失語言集設定，請謹慎使用。

---

## 重要：從舊版（bind mount）升級的資料搬遷

舊版 `docker-compose.yml` 把 `trans_cache.sqlite3`、`channel_langs.json` 直接寫在專案目錄（bind mount）。這次改版後，這兩個檔案改存在 named volume `bot-data`（`/data`），**新建立的 volume 是空的**，語言集設定不會自動帶過去。

- `trans_cache.sqlite3`（快取）：可捨棄不管，容器啟動時會自動重新建立空快取，之後正常運作。
- `channel_langs.json`（各頻道語言集 / 即時翻譯開關）：**必須手動搬過去，否則所有頻道的語言集設定與即時翻譯開關會消失**，需重新用 `/set add`、`/live-translate-on` 逐一設定。

搬遷步驟（容器名稱依 `docker-compose.yml` 的 `container_name: dc-gpt-translator`）：

```bash
# 1. 先啟動一次，讓容器與 named volume 建立起來
docker-compose up -d

# 2. 立刻停止容器再複製——若容器還在跑，搬遷期間有人在 Discord 用
#    /set 等指令觸發存檔，會把剛複製進去的舊設定蓋掉
docker-compose stop
docker cp ./channel_langs.json dc-gpt-translator:/data/channel_langs.json

# 3. 重新啟動容器，讓 bot 重新讀取語言集檔案
docker-compose start
```

> 上述 `docker cp` / `docker-compose stop`/`start` 指令依官方文件語法撰寫，本機 Docker daemon 目前未啟動，未實際執行驗證。語言集檔案只在啟動時讀取一次（`main()` 裡的 `load_channel_state()`），複製檔案後務必重啟容器才會生效。

---

## Discord Bot 設定

### 建立 Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 新增 Application → 進入 **Bot** 頁面
3. 複製 **Token** → 填入 `.env` 的 `DISCORD_BOT_TOKEN`
4. 開啟以下權限：
   - **Message Content Intent**（必要，機器人需要讀取訊息內容才能翻譯）
   - **Server Members Intent**（選用，目前程式碼未使用，不開也可以正常運作）

### 取得伺服器 ID

1. Discord 開啟開發者模式：**設定 → 進階 → 開發者模式**
2. 右鍵點擊你的伺服器圖示 → **複製伺服器 ID**
3. 填入 `.env` 的 `DISCORD_GUILD_IDS`（多個伺服器用逗號分隔）

### 邀請 Bot 到伺服器

在 Developer Portal → **OAuth2** → **URL Generator**，勾選：

- Scopes：`bot`、`applications.commands`
- Bot Permissions：`Send Messages`、`Read Message History`、`Read Messages/View Channels`、`Add Reactions`

複製產生的 URL，在瀏覽器開啟並選擇伺服器邀請。

> `applications.commands` 是斜線指令（`/live-translate-on`、`/set` 等）的必要 scope。`Add Reactions` 是即時翻譯訊息過長時，機器人會在原訊息加上 📏 表情做提示，需要這個權限才能加表情。

---

## 斜線指令同步

Bot 啟動時（`setup_hook`，只在啟動時執行一次，不會隨 gateway 重連重複同步）會把指令同步到 `.env` 的 `DISCORD_GUILD_IDS` 列出的**每一個**伺服器，屬於 guild-scoped 指令，同步後立即生效。

同步後在 Discord 輸入 `/` 應能看到：

| 指令 | 說明 | 權限要求 |
|------|------|------|
| `/live-translate-on` | 開始即時翻譯 | 管理頻道 |
| `/live-translate-off` | 結束即時翻譯 | 任何人 |
| `/set add` | 新增語言到即時翻譯語言集 | 管理頻道 |
| `/set remove` | 從語言集移除語言 | 管理頻道 |
| `/set list` | 查看目前語言集與即時翻譯狀態 | 任何人 |

沒有右鍵選單（Apps）指令，隨選翻譯是「回覆訊息 + @mention 機器人」，不是斜線指令也不是右鍵選單。

> `/live-translate-on`、`/set add`、`/set remove` 在 Discord 介面上「可能」因伺服器的整合權限設定而對沒有「管理頻道」權限的成員隱藏，但這不是保證行為（管理員可自行調整整合設定）；即使成員看得到並送出指令，機器人的 handler 也會再檢查一次權限並回覆「需要「管理頻道」權限才能使用這個指令。」。

---

## 測試清單

以下每一步都對應 `bot.py` 目前的實際行為，可依序執行驗證。

**隨選翻譯**
- [ ] 在頻道傳送一句中文
- [ ] 回覆那則訊息，輸入 `@bot`（不加任何文字）
- [ ] 確認 Bot 回覆英文與韓文譯文（新頻道預設語言集只有「繁體中文」一種，不足兩種語言時會自動改用全部支援語言，所以不需要先 `/set add` 也能看到效果）
- [ ] 回覆同一則中文訊息，輸入 `@bot ko`，確認只回覆韓文譯文
- [ ] 單獨輸入 `@bot`（不回覆任何訊息），確認顯示指令說明訊息
- [ ] 私訊（DM）機器人並嘗試 @mention，確認收到「指令僅能在伺服器頻道中使用，不支援私訊。」

**語言集管理**
- [ ] 輸入 `/set list`，確認回覆目前語言集（預設應為「繁體中文」）與即時翻譯狀態（未開啟），且此回覆只有自己看得到
- [ ] 以沒有「管理頻道」權限的帳號輸入 `/set add`，確認收到權限不足訊息
- [ ] 以有「管理頻道」權限的帳號輸入 `/set add` 選擇 `en`，確認回覆已新增並顯示更新後語言集
- [ ] 再 `/set add` 選擇 `ko`，確認語言集變成三種語言
- [ ] 輸入 `/set remove` 移除 `en`，確認語言集剩兩種
- [ ] 持續移除到只剩一種語言時再嘗試移除，確認收到「語言集至少需要一種語言，無法移除。」

**即時翻譯**
- [ ] 確認目前頻道語言集至少有兩種語言（見上一節），否則即時翻譯不會有任何反應
- [ ] 以沒有「管理頻道」權限的帳號輸入 `/live-translate-on`，確認收到權限不足訊息
- [ ] 以有「管理頻道」權限的帳號輸入 `/live-translate-on`，確認 Bot 回覆「即時翻譯已開始」並列出目前語言集
- [ ] 傳送中文訊息，確認 Bot 自動回覆其餘語言的譯文
- [ ] 編輯剛剛那則中文訊息內容，確認 Bot 依新內容再送出一則新翻譯（舊的翻譯訊息不會被刪除或修改）
- [ ] 傳送純網址或純表情符號訊息，確認 Bot 不回應（`should_skip` 略過）
- [ ] 傳送超過 `LIVE_MAX_CHARS`（預設 1500 字）的長訊息，確認 Bot 不翻譯，改在該則訊息加上 📏 表情
- [ ] 在一分鐘內以同一帳號連續傳送超過 `LIVE_USER_PER_MIN`（預設 5）則訊息，確認超過門檻後 Bot 回覆一次「訊息太頻繁…」提示並略過翻譯（同一分鐘視窗內只會提示一次）
- [ ] 輸入 `/live-translate-off`（任何身分都可以），確認翻譯停止

**文字指令 fallback（@bot）**
- [ ] 輸入 `@bot /live-translate-on`，確認行為與 `/live-translate-on` 相同（含權限檢查）
- [ ] 輸入 `@bot /live-translate-off`，確認關閉即時翻譯
- [ ] 確認 `@bot /set add ...` 這類寫法不會被機器人解析為指令（`/set` 只有斜線指令版本），文字 @mention 只支援開關即時翻譯與隨選翻譯

**重啟持久化**
- [ ] 設定好語言集並開啟即時翻譯後，重啟 Bot 程序（或 `docker-compose restart`）
- [ ] 重啟後在該頻道傳訊息，確認即時翻譯狀態與語言集都還在（不需要重新 `/live-translate-on` 或 `/set add`）

**快取測試**
- [ ] 傳送相同的短訊息兩次（≤ `SHORT_MSG_MAX_CHARS`，預設 10 字），觀察第二次是否明顯較快（快取命中）
- [ ] 確認快取檔存在（直接執行：專案目錄下的 `trans_cache.sqlite3`；Docker：named volume `bot-data` 內的 `/data/trans_cache.sqlite3`，可用 `docker exec dc-gpt-translator ls /data` 確認，此指令未實際驗證）
- [ ] 傳送超過門檻字數的長訊息兩次，確認兩次都重新呼叫 GPT（長訊息目前不進快取）

### 日誌確認

正常啟動應依序看到（時間戳與實際伺服器 ID / bot 名稱會不同）：

```
2026-xx-xx | INFO | translator-bot | 已載入語言集設定，共 N 個頻道（即時翻譯開啟中：N 個）
2026-xx-xx | INFO | translator-bot | 使用 SQLite 快取：./trans_cache.sqlite3
2026-xx-xx | INFO | translator-bot | 斜線指令已同步到授權伺服器：{123456789}
2026-xx-xx | INFO | translator-bot | Logged in as YourBot#1234 (id=...)，授權伺服器：{123456789}
```

Docker 部署時第二行會顯示 `/data/trans_cache.sqlite3`。

---

## 新增支援語言

1. 在 `bot.py` 找到 `SUPPORTED_LANGS`，加入新語言：

```python
SUPPORTED_LANGS: Dict[str, str] = {
    "zh": "繁體中文",
    "en": "English",
    "ko": "한국어",
    "ja": "日本語",   # 新增這行
}
```

2. 在 `LANG_NAMES_EN` 加入對應的英文名（給 GPT 用）：

```python
LANG_NAMES_EN: Dict[str, str] = {
    "zh": "Traditional Chinese",
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",   # 新增這行
}
```

3. 重啟 Bot，`/set add`、`/set remove` 的語言選項（`_lang_choices`，在模組載入時依 `SUPPORTED_LANGS` 自動產生）與斜線指令同步都會自動更新，不需要額外程式碼。
