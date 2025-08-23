# 🤖 Discord 翻譯機器人

一個專為多語言 Discord 群組設計的 **自動翻譯 Bot**。  
支援 **中英文互譯**，並可擴充到其他語言。  
採用分層架構設計，降低 API 成本並確保可維護性。  

---

## ✨ 功能特色
- 🔄 自動翻譯 Discord 訊息  
- 🧹 過濾不需要翻譯的內容（中文 ↔ 中文 / 英文 ↔ 英文直接跳過）  
- ⚡ 短訊息翻譯後 **快取結果**（減少重複花費 GPT Token）  
- 🔌 可自由切換翻譯模型（GPT-4o-mini / GPT-4o / HuggingFace 等）  
- 📦 支援 Docker 部署  

---

## 🏗 分層架構

### 1. Client Layer (Discord Bot Layer)
- 使用 **discord.py** 監聽訊息
- 判斷是否需要翻譯
- 呼叫 Translation Service API
- 回傳翻譯結果到 Discord 頻道  

### 2. Preprocessing Layer (Message Filter & Router)
- 判斷訊息語言（例如用 `langdetect`）  
- 規則：
  - 中文 ↔ 中文：不翻譯  
  - 英文 ↔ 英文：不翻譯  
  - 其他情況：丟到 Translation Service  
- 短訊息（例如 10 字以內） → **直接丟給 GPT** 翻譯  
  - 翻譯結果會存到快取（例如 Redis / SQLite）  
  - 下次相同訊息出現 → 直接取快取，不再耗 GPT Token  

### 3. Translation Service Layer
- 封裝 API 呼叫，提供 `/translate` 介面  
- 模型選擇邏輯：
  - 中文 ↔ 英文：`GPT-4o-mini`（便宜快速）  
  - 其他語言：`GPT-4o`（翻譯準確度高）  

### 4. Caching Layer
- 短訊息翻譯後快取結果  
- 可使用：
  - **Redis**（推薦，快取過期時間可控）  
  - **SQLite**（簡單好用，適合小型部署）  

### 5. Infrastructure Layer
- 運行環境：
  - 本地端（開發測試）  
  - Docker（部署到雲端）  
- 功能：
  - 背景常駐服務  
  - API Key 環境變數管理  
  - 簡單監控（logging，錯誤回報到 Discord 管理員）  

---

## 📊 架構流程圖

```mermaid
flowchart TD

    A[Discord User Message] --> B[Client Layer: Bot]
    B --> C[Preprocessing Layer]

    C -->|中文 ↔ 中文 / 英文 ↔ 英文| G[跳過翻譯]
    C -->|短訊息 (<=10字)| D[Check Cache]

    D -->|Hit| F[Return Cached Result]
    D -->|Miss| E[Translation Service: GPT]

    C -->|長訊息| E[Translation Service]

    E --> H[Save to Cache]
    H --> F[Bot Sends Translation]
