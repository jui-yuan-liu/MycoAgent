# Agent 註冊指南

新 agent 怎麼加入群組、要帶哪些資料、以及怎麼連自己的 LLM。註冊與 OpenAI-compatible 呼叫**已經實作**；目錄裡的 `--models` 只是標籤，不會自動連 Ollama。

## 註冊有沒有實作？

有。`python -m mycoagent node`（或 Compose 裡的 node 服務）啟動時會對 Manager 做 `POST /nodes/register`，之後心跳。群組必須**先存在**（Manager `--bootstrap-group default` 或 `ctl groups-create`）。

- `join_mode=auto`（預設 `default`）：註冊即核准，立刻進 `ctl catalog`。
- `manual`：先 `pending`，管理員 `ctl approve <群組> <agent_id>` 後才進目錄、才能當父。
- `allow_register` 非空：只有名單裡的**名稱**能註冊。

## 需要哪些內容

| 項目 | 必填 | 說明 |
| --- | --- | --- |
| Manager URL | 是 | `--manager` 或 `MYCOAGENT_MANAGER` |
| 群組 | 是 | `--group`，必須已建立 |
| 顯示名稱 | 是 | `--name` 或 `--agent name=` |
| 信箱 URL | 是 | `--advertise`：其他 Host 要連得上。本機可用 `http://127.0.0.1:埠`；Docker 容器互連用 `http://node-c:9003`；跨機不要留迴環位址 |
| skills／tools | 否 | 手寫 `--skills`／`--tools`，或檔案／目錄 `--skills-file`／`--tools-file`／`--capabilities-file` |
| models | 否 | `--models llama3:local:8192` 寫進目錄，**不是** LLM 連線 |
| 機器／OS | 自動 | CPU、記憶體、是否容器等 |

## 怎麼操作

先確認 Manager 與群組：

```bash
python -m mycoagent ctl groups
python -m mycoagent ctl group default
```

**單一 agent（新開一個 Host 行程）：**

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 \
  --group default \
  --name gamma \
  --port 9003 \
  --advertise http://127.0.0.1:9003 \
  --skills coding \
  --tools shell \
  --models llama3:local:8192
```

不必手打清單時，把本機已有的 skills／tools 路徑傳進去（JSON 陣列、一行一個名字、或 Cursor／OpenCode 那種「資料夾裡有 `SKILL.md`」）：

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 --group default --name gamma \
  --port 9003 --advertise http://127.0.0.1:9003 \
  --skills-file ~/.cursor/skills \
  --tools-file ./tools.json
```

`--capabilities-file` 可一次帶 JSON：`{"skills":["coding"],"tools":["shell"]}`，或帶一個同時含 `skills/`、`tools/` 子目錄的資料夾。可與手寫 `--skills`／`--tools` 合併（檔案裡的名稱接在後面，重複的會去掉）。`--agent` 可用 `skills_file=`／`tools_file=`／`capabilities_file=`（路徑不要含逗號）。

**同一 Host 再掛一個身份：** `--agent` 可重複（信箱在 `{advertise}/agents/{id}`）。

```bash
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --port 9000 \
  --agent name=alpha,skills=coding,tools=shell \
  --agent name=gamma,skills=coding,tools=shell,llm_url=http://127.0.0.1:11434,llm_model=llama3
```

**Docker：** 在 `docker-compose.yml` 加一個類似 `node-b` 的 service（換 `--name`、`--port`、`--advertise`）。主機打 `http://127.0.0.1:新埠`。

**manual 群組：**

```bash
python -m mycoagent ctl groups-create locked --join-mode manual
# 起 node --group locked 後
python -m mycoagent ctl group locked          # 看 pending_ids
python -m mycoagent ctl approve locked <agent_id>
```

確認：`ctl catalog default` 看得到新名稱且 `idle`。預設每次啟動仍是新的 agent id；要重啟沿用同一列請加 `--id-file ./gamma.id`（或 `MYCOAGENT_ID_FILE`／`MYCOAGENT_AGENT_ID`）。`--agent` 可用 `id=` 或 `id_file=`。

## 新 agent 怎麼跟自己的 LLM 溝通？有實作嗎？

**有。** 子任務進內建執行器時，Host 用 httpx 打 **OpenAI-compatible** `POST {base_url}/chat/completions`（工具呼叫在工作區內跑 shell／讀寫檔）。不是 A2A，也不把 OpenCode 當 Manager。

兩層不要混：

1. **目錄標籤** `--models name:source[:context]`：給父／planner 看「宣稱有什麼模型」，不會開連線。
2. **真正連線**
   - **整台 Host 一組（最簡單）：** 環境變數 `MYCOAGENT_LLM_BASE_URL`（必填才會走 LLM）、可選 `MYCOAGENT_LLM_API_KEY`、`MYCOAGENT_LLM_MODEL`（預設 `gpt-4o-mini`）。沒設 URL 且 `--executor auto` → Echo。`--executor opencode` → 本機 `opencode run`。
   - **同一個 Host 裡每個 agent 不同 LLM：** `--agent` 加 `llm_url`、可選 `llm_model`、`llm_key`。有 `llm_url` 的 agent 用自己的 client；沒寫的沿用上面 Host 環境變數／預設執行器。

Ollama 範例（OpenAI 相容埠）：

```bash
export MYCOAGENT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export MYCOAGENT_LLM_MODEL=llama3
python -m mycoagent node --manager http://127.0.0.1:8080 --group default \
  --name gamma --port 9003 --advertise http://127.0.0.1:9003 \
  --skills coding --tools shell --executor agent
```

本機 **oMLX**（預設 `http://127.0.0.1:8000/v1`，須先自己 `omlx start` 或開選單列 App）：

```bash
export MYCOAGENT_LLM_BASE_URL=http://127.0.0.1:8000/v1
export MYCOAGENT_LLM_MODEL=你的模型目錄名   # 或 curl http://127.0.0.1:8000/v1/models
python -m mycoagent node --manager http://127.0.0.1:8080 --group default \
  --name gamma --port 9003 --advertise http://127.0.0.1:9003 \
  --skills coding --tools shell --executor agent
```

Docker 裡連 Mac 上的 oMLX：`python -m mycoagent init --provider omlx`（容器內 URL 是 `http://host.docker.internal:8000/v1`）。oMLX 若開了 `--api-key`，init 再加 `--llm-key`。

## 本機 OpenCode Host（推薦：把已設定好的 OpenCode 當執行器）

MycoAgent 管群組、目錄、信箱、父 JobMemory、工作區回收；**OpenCode 管 LLM／skills／MCP／權限**（沿用你本機已有的設定）。

```bash
# Manager／MinIO 可用 Compose；這個 Host 請在 Mac 本機跑（容器裡通常沒有你的 opencode）
python -m mycoagent node \
  --manager http://127.0.0.1:8080 \
  --group default \
  --name gamma \
  --port 9003 \
  --advertise http://127.0.0.1:9003 \
  --executor opencode
```

- 未手寫 `--skills` 時，會掃描 `~/.config/opencode/skills`、`~/.claude/skills`、`~/.agents/skills`（與 `OPENCODE_CONFIG_DIR/skills`）並註冊進 catalog；tools 會帶標籤 `opencode`。
- 子行程**保留真實 HOME**，因此 OpenCode 仍讀你的 auth、全域 skills、MCP。
- 可選：`--opencode-model provider/model`、`--opencode-agent`、`--opencode-auto`、`--opencode-attach http://127.0.0.1:4096`（接已跑的 `opencode serve`）、`--opencode-skills-dir`（額外 skills 目錄）、`MYCOAGENT_OPENCODE_BIN`。
- 設定檔：`init --yes --executor opencode` 或 `--provider opencode`。

Docker 容器預設**沒有**本機 `opencode` 與 `~/.config/opencode`；此模式請本機跑 Host，不要指望 Compose 裡的 node-a／node-b 直接套用你的 OpenCode。

Docker 裡「這台 Host 自己的 LLM」：該 service 設自己的 `MYCOAGENT_LLM_*`（一容器一組仍最單純），或跑 `python -m mycoagent init` 寫入 `/config/agents.yaml` 並 `POST /configure`。容器內不要用 `127.0.0.1` 指主機上的 oMLX／Ollama。

父切分也走同一套 LLM client（有連線才會自動切 subtasks）。信箱協定不變。
