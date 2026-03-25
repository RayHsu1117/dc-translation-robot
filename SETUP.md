# 安裝與測試指南

## 前置需求

- Python 3.11+
- Discord Bot Token（需要開啟 Message Content Intent）
- OpenAI API Key
- Discord 伺服器 ID
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
DISCORD_GUILD_ID=你的伺服器ID

# 以下為選填
OPENAI_MODEL=gpt-4o-mini
SHORT_MSG_MAX_CHARS=10
USE_REDIS=false
LOG_LEVEL=INFO
```

### 3. 啟動 Bot

```bash
python bot.py
```

---

## 方法二：Docker

### 1. 建立 `.env` 檔案（同上）

### 2. 啟動

```bash
docker-compose up -d
```

### 3. 查看日誌

```bash
docker-compose logs -f
```

### 4. 停止

```bash
docker-compose down
```

---

## Discord Bot 設定

### 建立 Bot

1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 新增 Application → 進入 **Bot** 頁面
3. 複製 **Token** → 填入 `.env` 的 `DISCORD_BOT_TOKEN`
4. 開啟以下權限：
   - **Message Content Intent**（必要）
   - **Server Members Intent**（選用）

### 取得伺服器 ID

1. Discord 開啟開發者模式：**設定 → 進階 → 開發者模式**
2. 右鍵點擊你的伺服器圖示 → **複製伺服器 ID**
3. 填入 `.env` 的 `DISCORD_GUILD_ID`

### 邀請 Bot 到伺服器

在 Developer Portal → **OAuth2** → **URL Generator**，勾選：

- Scopes：`bot`、`applications.commands`
- Bot Permissions：`Send Messages`、`Read Message History`、`Read Messages/View Channels`

複製產生的 URL，在瀏覽器開啟並選擇伺服器邀請。

> `applications.commands` 是斜線指令（`/live-translate` 等）的必要 scope。

---

## 斜線指令同步

Bot 啟動時會自動將指令同步到 `DISCORD_GUILD_ID` 指定的伺服器，**即時生效**。

同步後在 Discord 輸入 `/` 應能看到：

| 指令 | 說明 |
|------|------|
| `/live-translate` | 開始即時翻譯 |
| `/live-translate-off` | 結束即時翻譯 |
| 右鍵訊息 → Apps → Translate | 隨選翻譯 |

---

## 測試清單

**隨選翻譯**
- [ ] 在頻道傳送一句中文
- [ ] 回覆那則訊息，輸入 `@bot /translate`
- [ ] 確認 Bot 回覆韓文與英文譯文
- [ ] 右鍵點擊任意訊息 → Apps → Translate，確認出現翻譯

**即時翻譯**
- [ ] 輸入 `/live-translate`，確認彈出語言選項
- [ ] 選擇語言後確認 Bot 發出「即時翻譯已開始」訊息
- [ ] 傳送中文訊息，確認 Bot 自動回覆韓文
- [ ] 傳送韓文訊息，確認 Bot 自動回覆中文
- [ ] 傳送英文訊息，確認 Bot 不回應（非語言對，自動跳過）
- [ ] 輸入 `/live-translate-off`，確認翻譯停止

**文字指令 fallback（@bot）**
- [ ] 輸入 `@bot /live-translate`，確認開啟即時翻譯
- [ ] 輸入 `@bot /live-translate-off`，確認關閉
- [ ] 單獨輸入 `@bot`，確認顯示說明訊息

**快取測試**
- [ ] 傳送相同短訊息兩次，觀察第二次是否明顯較快（快取命中）
- [ ] 確認 `trans_cache.sqlite3` 在專案目錄內產生

### 日誌確認

正常啟動應看到：

```
2024-xx-xx | INFO | translator-bot | 使用 SQLite 快取：./trans_cache.sqlite3
2024-xx-xx | INFO | translator-bot | Logged in as YourBot#1234 (id=...)，斜線指令已同步到伺服器 123456789
```

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

3. 重啟 Bot，斜線指令的語言選項會自動更新。
