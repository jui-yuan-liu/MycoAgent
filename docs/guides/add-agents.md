# 加入 Agent

把新 agent 加入既有群組：必填欄位、CLI 操作、連線 LLM，以及本機 OpenCode 當執行器。

前提：群組已存在（Manager `--bootstrap-group default` 或 `ctl groups-create`）。政策細節見 [自訂群組](custom-groups.md)。

## 需要哪些內容

| 項目 | 必填 | 說明 |
| --- | --- | --- |
| Manager URL | 是 | `--manager` 或 `MYCOAGENT_MANAGER` |
| 群組 | 是 | `--group`，必須已建立 |
| 顯示名稱 | 是 | `--name` 或 `--agent name=` |
| 信箱 URL | 是 | `--advertise`：其他 Host 要連得上。本機可用 `http://127.0.0.1:埠`；Docker 容器互連用服務名；跨機不要留迴環位址 |
| skills／tools | 否 | 手寫或 `--skills-file`／`--tools-file`／`--capabilities-file` |
| models | 否 | `--models llama3:local:8192` 寫進目錄，**不是** LLM 連線 |
| 機器／OS | 自動 | CPU、記憶體、是否容器等 |

註冊行為：

- `join_mode=auto`（預設 `default`）：註冊即核准，立刻進 `ctl catalog`。
- `manual`：先 `pending`，管理員 `ctl approve <群組> <agent_id>` 後才進目錄、才能當父。
- `allow_register` 非空：只有名單裡的**名稱**能註冊。

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

從檔案／目錄載入 skills／tools：

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 --group default --name gamma \
  --port 9003 --advertise http://127.0.0.1:9003 \
  --skills-file ~/.cursor/skills \
  --tools-file ./tools.json
```

`--capabilities-file` 可一次帶 JSON：`{"skills":["coding"],"tools":["shell"]}`，或帶同時含 `skills/`、`tools/` 子目錄的資料夾。可與手寫 `--skills`／`--tools` 合併。`--agent` 可用 `skills_file=`／`tools_file=`／`capabilities_file=`（路徑不要含逗號）。

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
python -m mycoagent ctl group locked
python -m mycoagent ctl approve locked <agent_id>
```

**完成條件：** `ctl catalog default` 看得到新名稱且 `idle`。

預設每次啟動是新的 agent id；要重啟沿用同一列請加 `--id-file ./gamma.id`（或 `MYCOAGENT_ID_FILE`／`MYCOAGENT_AGENT_ID`）。`--agent` 可用 `id=` 或 `id_file=`。

## 與 LLM 連線

子任務進內建執行器時，Host 打 OpenAI-compatible `POST {base_url}/chat/completions`。兩層不要混：

1. **目錄標籤** `--models name:source[:context]`：給父／planner 看「宣稱有什麼模型」，不會開連線。
2. **真正連線**
   - **整台 Host：** `MYCOAGENT_LLM_BASE_URL`（必填才走 LLM）、可選 `MYCOAGENT_LLM_API_KEY`、`MYCOAGENT_LLM_MODEL`。沒設 URL 且 `--executor auto` → Echo。
   - **每個 agent 不同 LLM：** `--agent` 加 `llm_url`、可選 `llm_model`、`llm_key`。

Ollama 範例：

```bash
export MYCOAGENT_LLM_BASE_URL=http://127.0.0.1:11434/v1
export MYCOAGENT_LLM_MODEL=llama3
python -m mycoagent node --manager http://127.0.0.1:8080 --group default \
  --name gamma --port 9003 --advertise http://127.0.0.1:9003 \
  --skills coding --tools shell --executor agent
```

本機 oMLX（預設 `http://127.0.0.1:8000/v1`）：

```bash
export MYCOAGENT_LLM_BASE_URL=http://127.0.0.1:8000/v1
export MYCOAGENT_LLM_MODEL=你的模型目錄名
python -m mycoagent node … --executor agent
```

Docker 裡連 Mac 上的 oMLX：`python -m mycoagent init --provider omlx`（見 [init 參數說明](../reference/init.md)）。容器內不要用 `127.0.0.1` 指主機上的 LLM。

## 本機 OpenCode Host

MycoAgent 管群組、目錄、信箱、父 JobMemory、工作區回收；**OpenCode 管 LLM／skills／MCP／權限**。Host 請在本機 venv 跑（Compose 容器通常沒有你的 `opencode`）。

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 \
  --group default \
  --name gamma \
  --port 9003 \
  --advertise http://127.0.0.1:9003 \
  --executor opencode
```

- 未手寫 `--skills` 時，會掃描 `~/.config/opencode/skills`、`~/.claude/skills`、`~/.agents/skills`（與 `OPENCODE_CONFIG_DIR/skills`）並註冊進 catalog；tools 帶標籤 `opencode`。
- 子行程保留真實 HOME，因此 OpenCode 仍讀你的 auth、全域 skills、MCP。
- 可選：`--opencode-model`、`--opencode-agent`、`--opencode-auto`、`--opencode-attach`、`--opencode-skills-dir`、`MYCOAGENT_OPENCODE_BIN`。
- 設定檔：`init --yes --executor opencode` 或 `--provider opencode`。

Manager／MinIO 仍可用 Compose。父切分也走同一套 LLM client（有連線才會自動切 subtasks）。

## 下一步

- [init 參數說明](../reference/init.md)
- [CLI 參考](../reference/cli.md)
- [部署](deploy.md)
