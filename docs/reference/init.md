# `mycoagent init` 參數說明

`python -m mycoagent init` 用來寫本機 agent 設定（預設 `.mycoagent/agents.yaml`；Docker 內為 `/config/agents.yaml`），並可選對**已在跑**的 Host 做 `POST /configure`。已設定過則印摘要後離開；要重跑加 `--force`。

相關：[Quick Start](../getting-started/quickstart.md)、[加入 Agent](../guides/add-agents.md)（本機 OpenCode Host）。

## 何時跑

1. 先起堆疊：`docker compose up --build -d`（或本機 `manager` + 兩個 `node`）。
2. 再跑 init：

```bash
docker compose exec -it node-a python -m mycoagent init
# 或本機：
python -m mycoagent init
```

## CLI 參數一覽

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `--config PATH` | Docker：`/config/agents.yaml`；本機：`.mycoagent/agents.yaml` | 寫入／讀取的 YAML 路徑 |
| `--force` | 關 | 即使已設定完整也重跑 wizard／覆寫 |
| `--yes` | 關 | 非互動：用旗標或既有檔寫入，不問 prompt |
| `--apply`／`--no-apply` | `--apply` | 寫檔後是否 `POST /configure` 到 Host |
| `--provider` | 無 | `echo`／`omlx`／`ollama`／`custom`／`opencode` |
| `--llm-url` | 依 provider | OpenAI-compatible base URL；空＝Echo。容器內連主機請用 `host.docker.internal` |
| `--llm-model` | 可自動選 | 省略且有 URL 時會 `GET /v1/models` 取第一個聊天模型 |
| `--llm-key` | 空 | 可選 Bearer（例如 oMLX 開了 `--api-key`） |
| `--executor` | 無 | 強制 yaml 的 `executor`：`echo`／`auto`／`agent`／`opencode` |
| `--host-url` | 見下 | 可重複。套用目標 Host base URL |

**`--host-url` 預設**

- 在容器內：`http://node-a:9001`、`http://node-b:9002`
- 在主機：`http://127.0.0.1:9001`、`http://127.0.0.1:9002`

**`--provider` 行為摘要**

| 值 | 效果 |
| --- | --- |
| `echo` | 不連 LLM；`executor=echo` |
| `omlx` | URL 預設 `host.docker.internal:8000/v1`（容器）或 `127.0.0.1:8000/v1`（本機）；可自動選模型 |
| `ollama` | 同上，埠 `11434` |
| `custom` | 需自己給 `--llm-url` |
| `opencode` | `executor=opencode`，不寫 `llm_url`；skills 會掃本機 OpenCode 目錄 |

互動模式（不加 `--yes`）會問每個 agent 的名稱、skills、tools、provider／LLM。

## 寫出的 YAML 欄位

| 欄位 | 意義 |
| --- | --- |
| `name` | 目錄顯示名（Compose 預設 `alpha`／`beta`） |
| `skills`／`tools` | catalog 比對用標籤 |
| `executor` | `echo`／`auto`／`agent`／`opencode` |
| `llm_url`／`llm_model`／`llm_key` | 內建 loop／planner 連線（`opencode` 時可空） |
| `models` | 目錄標籤，例如 `llama3:local:8192`（不是自動連線） |

範例檔：`.mycoagent/agents.example.yaml`、`.mycoagent/agents.omlx.example.yaml`。真正的 `agents.yaml` 已 gitignore。

## 推薦設置

### 1. Mac + Docker Compose + 本機 oMLX

```bash
omlx start   # 確認 curl http://127.0.0.1:8000/v1/models
docker compose up --build -d
docker compose exec -it node-a python -m mycoagent init --yes --provider omlx
```

指定模型／API key：再加 `--llm-model`／`--llm-key`。重跑：`--force`。只寫檔：`--no-apply`。

### 2. 本機 OpenCode 當執行器

```bash
python -m mycoagent init --yes --executor opencode --no-apply
# 然後本機起 node --executor opencode，見加入 Agent
```

### 3. Ollama

```bash
docker compose exec node-a python -m mycoagent init --yes --provider ollama --llm-model llama3
```

### 4. Echo（協作 demo）

```bash
python -m mycoagent init --yes --provider echo
```

### 5. 自訂 OpenAI-compatible URL

```bash
python -m mycoagent init --yes --provider custom \
  --llm-url http://127.0.0.1:8088/v1 \
  --llm-model my-model \
  --llm-key sk-...
```

容器內連主機服務時，把 `127.0.0.1` 改成 `host.docker.internal`。

## 套用到其他 Host

```bash
python -m mycoagent init --yes --provider omlx \
  --host-url http://127.0.0.1:9001
```

多台重複 `--host-url`。yaml 裡 agent 順序與 host-url 順序一一對應。

## 行為注意

- 已設定會跳過：要改用 `--force`。
- 探針失敗只警告：LLM 連不上仍會寫檔。
- 若 Host／Manager 設了 `MYCOAGENT_TOKEN`，執行 init 的環境也要設同一值。
- 不要在容器裡用 `127.0.0.1` 指主機上的 oMLX／Ollama。

## 設定後怎麼驗

```bash
docker compose exec node-a python -m mycoagent ctl catalog default --manager http://manager:8080
docker compose exec node-a python -m mycoagent ctl submit \
  --node http://127.0.0.1:9001 \
  --description "demo" \
  --subtask "child work|coding"
```

旗標以 `python -m mycoagent init --help` 為準。
