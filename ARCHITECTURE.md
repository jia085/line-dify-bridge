# CRP Prototype — 系統運作邏輯文件

> 最後更新：2026-04-07

---

## 一、專案概覽

這是一個以 **LINE Bot** 為介面的心理學實驗平台，模擬「AI 交友對象」陪伴受試者進行為期 14 天的研究。
受試者透過 LINE 與 AI 互動，研究團隊透過 Google Sheets 追蹤所有狀態與對話記錄。

### 核心元件

| 元件 | 角色 |
|------|------|
| **LINE** | 受試者端通訊介面 |
| **Render（Flask）** | 主後端，Alex（server.py）+ Aria（server-aria.py）|
| **Dify** | AI 對話引擎（帶記憶、依組別設定人格）|
| **Google Apps Script** | Sheets REST API 橋接層 |
| **Google Sheets** | 受試者資料庫 + 對話紀錄 |
| **SQLite** | Render 本地暫存狀態（重啟後清空，由 Sheets 同步） |
| **OpenAI GPT-4o-mini** | D7 情緒偵測 |
| **cron-job.org** | 定時觸發 daily nudge + D7 引導句 |

---

## 二、分組設計

| Bot | 組別 | 人格類型 | Dify App | LINE Channel |
|-----|------|---------|----------|-------------|
| **Alex** | A | 協作型 | DIFY_KEY_A | LINE_CHANNEL_ACCESS_TOKEN |
| **Alex** | B | 攻擊型 | DIFY_KEY_B | 同上 |
| **Alex** | C | 遷就型 | DIFY_KEY_C | 同上 |
| **Alex** | D | 迴避型 | DIFY_KEY_D | 同上 |
| **Aria** | E | 協作型 | DIFY_KEY_E | LINE_CHANNEL_ACCESS_TOKEN_ARIA |
| **Aria** | F | 攻擊型 | DIFY_KEY_F | 同上 |
| **Aria** | G | 遷就型 | DIFY_KEY_G | 同上 |
| **Aria** | H | 迴避型 | DIFY_KEY_H | 同上 |

> Aria 與 Alex 邏輯一致，組別 E→A、F→B、G→C、H→D 做腳本映射（`D7_GROUP_MAPPING`）。

---

## 三、Google Sheets 欄位對照

受試者資料存於 **Participants** 工作表，關鍵欄位（0-based index → 1-based column）：

| Index | Column | 欄位名稱 | 說明 |
|-------|--------|---------|------|
| 4 | E (col 5) | Line_User_ID | 驗證後寫入 |
| 5 | F (col 6) | Group | A～H |
| 8 | I (col 9) | Current_Day | 由 Apps Script 計算（今日 − First_Interaction + 1）|
| 9 | J (col 10) | First_Interaction | 驗證時寫入 |
| 15 | P (col 16) | D7_Triggered | D7 衝突已觸發（TRUE/FALSE）|
| 24 | Y (col 25) | Last_Nudge_At | Daily Nudge 最後推播日期 |
| 26 | AA (col 27) | D7_Turn | D7 對話輪數（0＝未開始，2/3/4＝進行中；Render 恢復用）|

---

## 四、受試者完整生命週期

```
受試者加 LINE Bot 好友
         │
         ▼
   輸入手機末 5 碼
         │
   Apps Script 查詢 Sheets
         │
    ┌────┴────┐
    │ 找到    │ 找不到
    │         ▼
    │   ❌ 錯誤提示
    ▼
驗證成功 → 寫入 Line_User_ID + First_Interaction
         │
         ▼
   Onboarding 歡迎訊息（依組別）
         │
         ▼
  ========= Day 1～6 =========
  │  正常 Dify 對話（依組別人格）│
  │  update_last_interaction()    │
  │  每日 21:00 cron：daily nudge │
  ===========================
         │
         ▼
  ========= Day 7 ============
  │  18:00 cron：/jobs/d7-trigger │
  │  → 推播 D7_SETUP_MESSAGES     │
  │    （引導句）                  │
  │                               │
  │  用戶傳任何訊息                │
  │  → GPT-4o-mini 偵測情緒       │
  │  → 發衝突語句（D7_TRIGGERS）  │
  │  → D7_Turn 設為 2             │
  │                               │
  │  Turn 2：固定腳本（D7_SCRIPTS）│
  │  Turn 3：依反應類型分支腳本    │
  │  Turn 4+：清除 D7，恢復正常對話│
  ===========================
         │
         ▼
  ========= Day 8～14 =========
  │  正常 Dify 對話（繼續）       │
  ===========================
```

---

## 五、Webhook 訊息處理流程（詳細）

```
POST /webhook
     │
     ▼
handle_message_event(event)
     │
     ├─ [RESET 指令]
     │    清除 Sheets + SQLite → 回覆重置完成
     │
     ├─ get_user_data_by_user_id(user_id)  ← 只呼叫一次！
     │
     ├─ [TESTDAY N 指令]（測試用）
     │    設定 First_Interaction = 今天 - (N-1) 天
     │    清除 clear_d7_turn()
     │
     ├─ [TEST_D7 指令]（測試用）
     │    強制觸發 D7 → d7_turn = 2
     │
     ├─ D7 Recovery（Render 重啟保護）
     │    if SQLite d7_turn == 0 AND Sheets D7_Turn > 0:
     │        還原 d7_turn 到 SQLite
     │
     ├─ [D7 進行中 turn > 0]
     │    turn 2 → D7_SCRIPTS[group][2]（固定腳本）
     │    turn 3 → detect_user_response_type() → 分支腳本
     │    turn 4+ → clear_d7_turn()，落入正常對話
     │    每輪都呼叫 Dify（維護記憶），但不用其回應
     │
     ├─ [未驗證使用者]
     │    5 碼數字 → 查詢 Sheets → 驗證 → Onboarding
     │    其他 → 提示輸入代碼
     │
     └─ [已驗證，正常流程]
          ├─ d7_setup 過期清除（current_day != 7 時自動重置）
          │
          ├─ [Day 7 且尚未觸發衝突]
          │    → trigger_d7()（GPT 情緒偵測 → 衝突句）
          │    → D7_Turn = 2
          │
          └─ [正常對話]
               → call_dify() → 回覆
               → update_last_interaction()
               → log_conversation()
```

---

## 六、D7 衝突機制詳解

### 6.1 觸發條件

- `current_day == 7`（由 Sheets 計算）
- `d7_triggered == False`（Sheets 欄位，確保每人只觸發一次）
- 只要 Day 7 收到第一則訊息，**無論有無先看到引導句**，立即觸發

### 6.2 引導句（Cron Push，每日 18:00 TW = 10:00 UTC）

- 目的：提高 Day 7 用戶主動發話機率（非觸發必要條件）
- 若用戶 Day 7 沒回覆引導句，下次傳訊時仍會觸發衝突
- `d7_setup = 1` 只是「引導句已發出」的紀錄，避免重複推播

### 6.3 情緒偵測（GPT-4o-mini）

```
用戶訊息
    → OpenAI API（temperature=0）
    → Positive / Negative / Neutral
    → D7_TRIGGERS[group][emotion]
    →（Fallback：關鍵字偵測）
```

### 6.4 D7 三輪腳本流程

```
Turn 1（觸發）：衝突句（D7_TRIGGERS）→ D7_Turn = 2
Turn 2：固定回應（D7_SCRIPTS[group][2]）→ D7_Turn = 3
Turn 3：依反應分支
    用戶說「好/可以/願意...」→ 3_cooperative
    用戶說「不要/不想/算了...」→ 3_refuse
    用戶問「為什麼/怎麼...」→ 3_question
    其他 → 3_neutral
    → D7_Turn = 4
Turn 4+：clear_d7_turn()，恢復正常 Dify 對話
```

### 6.5 Render 重啟後恢復

- `set_d7_turn()` 每次寫入 SQLite 時，同步 POST 到 Sheets（AA 欄）
- Webhook 入口判斷：SQLite d7_turn=0 但 Sheets D7_Turn>0 → 從 Sheets 還原

---

## 七、Cron Jobs

| Job | 時間（UTC）| 時間（TW）| Endpoint | 說明 |
|-----|-----------|-----------|----------|------|
| Daily Nudge（Alex）| 13:00 | 21:00 | `POST /jobs/daily-nudge` | 今日未互動且未推播的用戶 |
| Daily Nudge（Aria）| 13:00 | 21:00 | `POST /jobs/daily-nudge` | 同上，Aria 服務 |
| D7 引導句（Alex）| 10:00 | 18:00 | `POST /jobs/d7-trigger` | Day 7 且未觸發的用戶 |
| D7 引導句（Aria）| 10:00 | 18:00 | `POST /jobs/d7-trigger` | 同上，Aria 服務 |

所有 Cron Job 需附帶 Header：`X-Job-Secret: <JOB_SECRET>`

---

## 八、Google Apps Script API 端點

Apps Script 部署為 Web App（GET + POST），提供以下操作：

### GET 查詢

| 參數 | 說明 |
|------|------|
| `?code=XXXXX` | 以手機碼查詢受試者（驗證用）|
| `?user_id=UXXXXX` | 以 LINE User ID 查詢（返回 d7_turn、current_day 等）|
| `?action=get_active_users` | 返回所有已驗證用戶陣列（含 current_day、d7_triggered）|

### POST 操作

| JSON 欄位 | 說明 |
|-----------|------|
| `code + user_id + first_interaction` | 驗證成功，寫入 User ID 與起始日期 |
| `clear_user_id: true + user_id` | RESET，清除 User ID 及相關欄位 |
| `user_id + last_interaction + is_first_today` | 更新最後互動時間 |
| `user_id + last_nudge_date` | 更新 Daily Nudge 推播日期 |
| `user_id + d7_trigger: true + emotion + trigger_sentence` | 標記 D7 已觸發，寫入情緒與觸發句 |
| `user_id + d7_turn: N` | 更新 D7_Turn（AA 欄）|
| `user_id + testday: true + first_interaction + reset_d7` | 測試用：重設日期與 D7 狀態 |
| `log_conversation: true + ...` | 寫入對話記錄到 Conversation_Logs 工作表 |

---

## 九、SQLite 狀態表（`bot_state`）

```sql
CREATE TABLE bot_state (
    user_id              TEXT PRIMARY KEY,
    conversation_id      TEXT,       -- Dify 對話 ID（帶記憶用）
    d7_turn              INTEGER DEFAULT 0,  -- D7 輪次（0=未開始）
    d7_setup             INTEGER DEFAULT 0,  -- 引導句已推播（1=已發）
    last_interaction_date TEXT              -- 當日是否已互動（防重複更新 Sheets）
)
```

> **注意**：Render 服務重啟或部署時 SQLite 會清空。
> `d7_turn` 透過 Sheets AA 欄同步，重啟後可恢復。
> `conversation_id` 重啟後遺失 → Dify 新開對話（記憶中斷，但功能不受影響）。

---

## 十、環境變數清單

### Alex Bot（server.py）

```
DIFY_KEY_A / B / C / D
LINE_CHANNEL_ACCESS_TOKEN
SHEETS_API_URL
OPENAI_API_KEY（可選，缺少時用關鍵字 fallback）
JOB_SECRET
NUDGE_MESSAGE（預設：嗨！今天還好嗎？有什麼想聊的嗎？）
STATE_DB_PATH（預設：state_alex.db）
PORT（預設：10000）
```

### Aria Bot（server-aria.py）

```
DIFY_KEY_E / F / G / H
LINE_CHANNEL_ACCESS_TOKEN_ARIA
SHEETS_API_URL（同 Alex）
OPENAI_API_KEY（同 Alex）
JOB_SECRET（同 Alex）
NUDGE_MESSAGE_ARIA
STATE_DB_PATH（預設：state_aria.db）
PORT（預設：10000）
```

---

## 十一、測試指令（LINE 對話框輸入）

| 指令 | 說明 |
|------|------|
| `RESET` | 清除所有狀態，重新驗證 |
| `TESTDAY 7` | 模擬目前為第 7 天（也可設其他數字）|
| `TEST_D7` | 強制觸發 D7 衝突（不需 Day 7）|

---

## 十二、已知限制與注意事項

1. **Dify 記憶在 Render 重啟後中斷**：`conversation_id` 存於 SQLite，重啟後遺失，Dify 會開新對話。目前無持久化方案。

2. **Sheets API 失敗時的後備行為**：若 `get_user_data_by_user_id()` 失敗，用戶會被視為「未驗證」並要求重新輸入代碼。

3. **Race condition（極低概率）**：若用戶在 D7 觸發瞬間同時發送兩條訊息，可能觸發兩次 `trigger_d7()`。目前未加鎖，實際研究環境下概率極低。

4. **d7_setup 僅存於 SQLite**：引導句紀錄不持久化到 Sheets，Render 重啟後 `d7_setup` 歸零，可能在同一天重複推播引導句。可接受（最多推播兩次）。

5. **Apps Script 每次部署需建立新版本**：修改 Apps Script 後必須「Deploy > Manage deployments > 建立新版本」，否則變更不生效。
