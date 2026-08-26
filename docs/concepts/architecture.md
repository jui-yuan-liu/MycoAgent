# 系統架構

說明執行時角色、資料放哪、派工流程與 HTTP 表面。概念總覽見 [功能說明](overview.md)。

## 執行時角色

```
                    ┌─────────────────────┐
                    │  Cluster Manager    │
                    │  群組政策、通訊錄    │
                    │  SQLite 或 Postgres │
                    └──────────┬──────────┘
           註冊／心跳／catalog │
         ┌─────────────────────┴─────────────────────┐
         ▼                                           ▼
┌─────────────────┐                       ┌─────────────────┐
│ Host 行程       │                       │ Host 行程       │
│ Agent A（父）   │  assign_subtask       │ Agent B（子）   │
│ JobMemory       │ ───────────────────►  │ 執行器+工作區   │
│                 │ ◄───────────────────  │ 無任務樹        │
└────────┬────────┘  result+artifact_ids  └────────┬────────┘
         │                                         │
         │              ┌──────────┐               │
         └─────────────►│ MinIO/S3 │◄──────────────┘
           下載引用     │ 產物本體 │    上傳後刪工作區
                        └──────────┘
```

| 角色 | 職責 | 狀態放哪 |
| --- | --- | --- |
| **Cluster Manager** | 建組、註冊、心跳、目錄、核准 | SQLite（預設 `mycoagent.db`）或 `--db postgres://…` |
| **Host** | 一個 OS 行程，可掛多個 agent | 行程內 |
| **Agent** | 目錄上一列：信箱、skills、models、idle／busy | 身份在 Manager；忙碌在心跳 |
| **父 agent** | 誰 `POST /jobs` 誰就是該次父；保存 JobMemory | 該 agent 記憶體；可選 `--job-db` SQLite |
| **子 agent** | 收 `assign_subtask`，跑執行器，上傳產物，回報 | 執行期暫存目錄，結束刪除 |
| **產物庫** | blob | MinIO／S3；未設 env 則行程內假 S3 |

Manager **看不見** job 內容。兄弟 **不互連**、不交換 mailbox URL。不能派給**同一個 `agent_id`**；同機不同 agent 可以互派。

## 目錄裡的「node」

HTTP 與儲存表名仍是 `nodes`，語意是 **agent**。多 agent Host 的信箱為 `{advertise}/agents/{agent_id}`；單一 `--name` 時信箱就是 advertise URL。

每列包含：名稱、群組、mailbox_url、機器規格、models、skills、tools、status、membership。

Catalog 篩選：同組、已核准、可選 idle、skills、tools、排除某個 id、可選 model 名稱、最低 context_window、最低 memory_mb。派工在符合者裡取最少 in-flight（以父 JobMemory 計 ASSIGNED／RUNNING），同分再 round-robin。

## 群組政策（摘要）

- `join_mode=auto`：註冊即核准。`manual`：先 `pending`，管理員核准才進目錄、才能當父。
- `allow_register` 非空：僅列出的**名稱**能註冊。
- `allow_parent` 非空：僅列出的名稱或 id 能 `POST /jobs`。
- 可選共享 Bearer（`MYCOAGENT_TOKEN`／`--token`）；未設則開放（本機 MVP）。

操作步驟見 [自訂群組](../guides/custom-groups.md)。

## 執行時流程

### 1. 啟動與入組

1. `python -m mycoagent manager` 建立 FastAPI，可 `--bootstrap-group default`。
2. `python -m mycoagent node …` 起 Host，為每個 agent `POST /nodes/register`。
3. 每個 agent 背景心跳（預設約 5 秒），只帶 idle／busy 與可選 tools／models。
4. Manager 逾時（預設 15 秒）沒心跳則標 `offline`。預設 catalog 只顯示 idle。

`join_mode=manual` 時新 agent 為 `pending`，直到 `ctl approve`。

### 2. 發起任務（成為父）

對某 agent 的 `POST /jobs`：

1. 檢查 membership 為 approved，且通過 `allow_parent`。
2. 建立只存在此 agent 的 JobMemory。
3. **子任務來源：** 請求帶了 `subtasks` → 照單派；沒帶且有 LLM planner → 查 catalog 切分；沒帶也沒 planner → 本機跑執行器，寫 `local_result`。
4. 有子任務時：查 catalog、`POST {mailbox}/mailbox` 送 `assign_subtask`（含 `parent_mailbox_url`、payload、`artifact_ids`）。
5. 沒有符合的同伴：該子任務 `failed`。

子 agent 對同一 `job_id` 做 `GET /jobs` 或 `forward` 都是 **404**。

### 3. 子 agent 執行

```
assign_subtask
    → 若正在跑 child：進 in-process 佇列（滿了才 409）
    → 標 busy → 建暫存工作區
    → 若有 artifact_ids：從 MinIO 下載
    → executor.run
    → 上傳工作區檔案 → 刪工作區
    → POST 父信箱 subtask_result（失敗再試一次；摘要 + artifact_ids）
    → 標 idle
```

### 4. 父收結果與 Forward

全部 completed → job completed；任一 failed → job failed。

只有持有該 JobMemory 的父可以 `POST /jobs/{id}/forward`：可帶某筆子任務的結果文字與 artifact_ids；排除自己與來源子；可指定 `target_node_id`。孫任務必須再由最上層父 forward。

### 5. 產物鍵

`{group}/{job_id}/{subtask_id}/{filename}`。Manager 不存 blob。

## HTTP 表面（摘要）

**Manager（預設 :8080）**

- `GET /health`
- `POST/GET/PATCH/DELETE /groups`、`GET /groups/{name}`
- `POST /groups/{name}/approve/{id}`、`…/deny/{id}`
- `POST /nodes/register`、`POST /nodes/{id}/heartbeat`、`GET /nodes/{id}`
- `GET /catalog?group=&idle_only=&skills=&tools=&exclude_node_id=&model=&min_context_window=&min_memory_mb=`

**單一 agent Host（advertise 即信箱）**

- `GET /health`、`GET /child`
- `POST/GET /jobs`、`GET /jobs/{id}`、`POST /jobs/{id}/forward`
- `POST /mailbox`
- `POST /configure`

**多 agent Host**

- `GET /agents`；上述路徑掛在 `/agents/{id}/…`（含 `POST /agents/{id}/configure`）

## 附錄：主要模組（貢獻者）

| 路徑 | 用途 |
| --- | --- |
| [`src/mycoagent/manager/`](../../src/mycoagent/manager/api.py) | FastAPI Manager；SQLite／Postgres store |
| [`src/mycoagent/node/runtime.py`](../../src/mycoagent/node/runtime.py) | Host／Agent：註冊、派工、信箱 |
| [`src/mycoagent/node/jobs.py`](../../src/mycoagent/node/jobs.py) | 父專用 JobStore |
| [`src/mycoagent/node/executor.py`](../../src/mycoagent/node/executor.py) | Echo、內建 tool loop |
| [`src/mycoagent/node/opencode.py`](../../src/mycoagent/node/opencode.py) | `opencode run` |
| [`src/mycoagent/node/local_config.py`](../../src/mycoagent/node/local_config.py) | `.mycoagent/agents.yaml` 與 init |
| [`src/mycoagent/artifacts.py`](../../src/mycoagent/artifacts.py) | S3／假 S3 |
| [`src/mycoagent/models.py`](../../src/mycoagent/models.py) | 協定型別 |

## 下一步

- [範圍與限制](limitations.md)
- [CLI 參考](../reference/cli.md)
- [部署](../guides/deploy.md)
