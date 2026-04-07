# line-dify-bridge

LINE Bot 與 Dify 的橋接服務，並將驗證資料、互動狀態與對話紀錄同步到 Google Sheets。

目前包含兩個 Flask 服務：
- Alex Bot（A/B/C/D 組）：server.py
- Aria Bot（E/F/G/H 組）：server-aria.py

## 1. 專案用途

此服務主要負責：
- 接收 LINE webhook 訊息
- 驗證受試者（手機末 5 碼）
- 依分組呼叫不同 Dify App
- 寫入 Google Sheets（驗證、每日互動、對話紀錄）
- 在指定天數觸發衝突腳本（目前為 Day 7）

## 2. 專案結構

- README.md：專案說明
- requirements.txt：Python 套件
- server.py：Alex Bot 服務（A/B/C/D）
- server-aria.py：Aria Bot 服務（E/F/G/H）

## 3. 環境需求

- Python 3.10+
- 可連外網路（LINE API、Dify API、OpenAI API、Google Sheets API）

安裝套件：

```bash
pip install -r requirements.txt
```

## 4. 環境變數

### 共用

- SHEETS_API_URL：Google Sheets Apps Script Web API URL
- OPENAI_API_KEY：情緒判斷用（可選；未提供時使用關鍵字 fallback）
- JOB_SECRET：Cron Job 驗證密鑰（X-Job-Secret header）
- PORT：服務埠號（預設 10000）
- STATE_DB_PATH：本地 SQLite 狀態檔路徑（可選）

### Alex Bot（server.py）

- DIFY_KEY_A
- DIFY_KEY_B
- DIFY_KEY_C
- DIFY_KEY_D
- LINE_CHANNEL_ACCESS_TOKEN
- NUDGE_MESSAGE：Daily Nudge 推播內容（預設：嗨！今天還好嗎？有什麼想聊的嗎？）

### Aria Bot（server-aria.py）

- DIFY_KEY_E
- DIFY_KEY_F
- DIFY_KEY_G
- DIFY_KEY_H
- LINE_CHANNEL_ACCESS_TOKEN_ARIA
- NUDGE_MESSAGE_ARIA：Aria 的 Daily Nudge 推播內容

## 5. 啟動方式

### 本機開發

啟動 Alex：

```bash
python server.py
```

啟動 Aria：

```bash
python server-aria.py
```

### Gunicorn（部署）

啟動 Alex：

```bash
gunicorn -w 1 -b 0.0.0.0:${PORT:-10000} server:app
```

啟動 Aria：

```bash
gunicorn -w 1 -b 0.0.0.0:${PORT:-10000} server-aria:app
```

注意：server-aria.py 檔名含有連字號，實際部署時建議改名為 server_aria.py，避免 WSGI import 問題。

## 6. HTTP 路由

- GET /：健康檢查
- GET /webhook：webhook readiness
- POST /webhook：LINE 事件處理主入口
- POST /jobs/daily-nudge：Cron Job — 每日推播（今日未互動的用戶）
- POST /jobs/d7-trigger：Cron Job — Day 7 推播引導句（D7_SETUP_MESSAGES）

## 7. 主要流程

1. 收到 LINE text message
2. 若未驗證，要求輸入手機末 5 碼
3. 用 code 向 Sheets 查詢並綁定 LINE user_id
4. 已驗證使用者進入 Dify 對話流程
5. 每次 user/ai 訊息寫入 Conversation_Logs
6. 每次互動更新 last_interaction 與 is_first_today
7. 每日 21:00（TW）Cron Job 對今日未互動用戶主動推播 Daily Nudge
8. Day 7 18:00（TW）Cron Job 推播 D7 引導句（提高用戶發話機率）
9. Day 7 收到用戶第一則訊息時，進入衝突腳本流程（無論有無先收到引導句）

## 8. Day 7 衝突腳本流程

- 觸發條件：
	- current_day == 7
	- d7_triggered == False
	- Day 7 收到第一則訊息即觸發（不需先收到引導句）

- 情緒判斷：
	- 優先 OpenAI API（gpt-4o-mini，temperature=0）
	- 失敗則 fallback 關鍵字法
	- 結果：Positive / Negative / Neutral

- 回覆邏輯：
	- 第 1 輪：依組別 × 情緒選衝突語句（D7_TRIGGERS）
	- 第 2 輪：固定腳本（D7_SCRIPTS[group][2]）
	- 第 3 輪：依使用者反應分類（合作/拒絕/質疑/中性）選分支腳本
	- 之後恢復一般 Dify 對話

## 9. 測試指令（透過 LINE 訊息）

- RESET
	- 清除 user_id 綁定與本地快取狀態

- TESTDAY n
	- 將 first_interaction 回推成第 n 天（例如 TESTDAY 7）
	- 並重置 D7 觸發狀態

- TEST_D7
	- 強制進入衝突觸發流程（便於腳本測試）

## 10. Alex 與 Aria 的差異

- 分組不同：Alex 用 A/B/C/D；Aria 用 E/F/G/H
- Token 不同：Alex 用 LINE_CHANNEL_ACCESS_TOKEN；Aria 用 LINE_CHANNEL_ACCESS_TOKEN_ARIA
- Aria 有防呆：只接受 E/F/G/H 代碼，避免加入錯 Bot
- Aria 的 D7 腳本會把 E/F/G/H 映射到 A/B/C/D 腳本集

## 11. 已知限制

- Render 上的 SQLite **重啟後會清空**
	- `d7_turn` 已同步寫入 Sheets（AA 欄），重啟後可自動還原
	- `conversation_id`（Dify 記憶）重啟後遺失，Dify 會開新對話
	- 多實例部署時狀態不共享（需 Redis 才能完全解）
- 目前未做 LINE 簽章驗證（X-Line-Signature）
- timeout 與錯誤重試策略較基礎，尖峰流量下有風險

## 12. 建議後續優化

- 將記憶體狀態移到 Redis
- 加入 LINE webhook 簽章驗證
- 加入結構化 logging 與 request trace id
- 補齊自動化測試（單元測試 + webhook 整合測試）