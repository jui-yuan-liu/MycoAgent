# CLI 參考

常用子命令、旗標與環境變數。完整清單以各指令的 `--help` 為準。

入門路徑見 [Quick Start](../getting-started/quickstart.md)、[本機安裝](../getting-started/local-setup.md)。`init` 詳見 [init 參數說明](init.md)。

## 進入點

```bash
python -m mycoagent --help
# 子命令：manager | node | init | ctl
```

## `manager`

啟動 Cluster Manager。

| 常用旗標 | 說明 |
| --- | --- |
| `--host`／`--port` | 預設 `0.0.0.0:8080` |
| `--db` | SQLite 路徑，或 `postgres://…` |
| `--bootstrap-group` | 啟動時建立群組（預設 `default`） |
| `--heartbeat-timeout` | 無心跳標 offline 的秒數（預設 15） |
| `--token` | 共享 Bearer（或環境變數 `MYCOAGENT_TOKEN`） |

## `node`

啟動 Host（可掛一或多個 agent）。

| 常用旗標 | 說明 |
| --- | --- |
| `--manager` | Manager base URL（或 `MYCOAGENT_MANAGER`） |
| `--group` | 必填；群組必須已存在 |
| `--name` | 單一 agent 顯示名 |
| `--agent` | 可重複：`name=…,skills=…,tools=…`；可選 `llm_url`／`llm_model`／`llm_key`、`id`／`id_file`、skills／tools 檔案鍵 |
| `--port`／`--advertise` | 聽埠與對外信箱基底 URL |
| `--skills`／`--tools`／`--models` | 目錄標籤 |
| `--skills-file`／`--tools-file`／`--capabilities-file` | 從檔案或目錄載入 |
| `--executor` | `auto`／`echo`／`agent`／`opencode` |
| `--max-steps` | 內建 loop 步數上限 |
| `--opencode-*` | bin、timeout、model、agent、auto、attach、config、skills-dir |
| `--id-file`／`--job-db` | 穩定 agent id；可選 JobMemory SQLite |
| `--config` | 讀 `.mycoagent/agents.yaml`（或 Compose 的 `/config/agents.yaml`） |
| `--token` | 共享 Bearer |

詳見 [加入 Agent](../guides/add-agents.md)。

## `init`

寫入本機 agents 設定並可選 `POST /configure`。見 [init 參數說明](init.md)。

## `ctl`

對已在跑的 Manager／Host 下管理指令。多數子命令接受 `--manager`（預設 `http://127.0.0.1:8080`）。

| 子命令 | 用途 |
| --- | --- |
| `groups`／`groups-create`／`groups-update`／`group` | 群組列表、建立、更新、詳情 |
| `approve`／`deny` | 手動群組成員核准／拒絕 |
| `catalog` | 查資源目錄（可 `--skills`／`--tools`／`--no-idle-only` 等） |
| `submit` | 對某 Host `--node` 發 job（可重複 `--subtask`） |
| `job` | 查父 JobMemory |
| `forward` | 父對同一 job 再派（可 `--from-subtask`／`--target`） |

範例：

```bash
python -m mycoagent ctl catalog default
python -m mycoagent ctl submit --node http://127.0.0.1:9001 --description "demo" --subtask "child work|coding"
python -m mycoagent ctl forward <job_id> --node http://127.0.0.1:9001 --description "follow-up"
```

Docker 內（主機沒裝套件時）：

```bash
docker compose exec node-a python -m mycoagent ctl catalog default --manager http://manager:8080
```

## 環境變數

| 變數 | 用途 |
| --- | --- |
| `MYCOAGENT_MANAGER` | 預設 Manager URL |
| `MYCOAGENT_TOKEN` | 共享 Bearer |
| `MYCOAGENT_LLM_BASE_URL`／`_API_KEY`／`_MODEL` | Host 級 LLM |
| `MYCOAGENT_S3_ENDPOINT`／`_ACCESS_KEY`／`_SECRET_KEY`／`_BUCKET` | 產物庫；未設則假 S3 |
| `MYCOAGENT_OPENCODE_BIN` | `opencode` 執行檔 |
| `MYCOAGENT_ID_FILE`／`MYCOAGENT_AGENT_ID` | 穩定 agent id |
| `MYCOAGENT_JOB_DB` | 可選 JobMemory SQLite 路徑 |

## 測試

```bash
.venv/bin/pytest -q
```
