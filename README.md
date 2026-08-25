# MycoAgent

群組、資源目錄、Host 上的多個 agent 信箱。Cluster Manager 只管通訊錄與群組政策；任務記憶只活在發起任務的父 agent。子 agent 用**可插拔執行器**做事（預設內建 tool loop，可改 Echo 或本機 OpenCode），把產物上傳 MinIO／S3 後清掉工作區，只回報摘要與 artifact id。兄弟不互連。信箱是自有 JSON，不是 A2A。

逐步從零跑起來：請看 **[文件目錄](docs/README.md)**（[快速使用指南](docs/快速使用指南.md)、[Agent 註冊指南](docs/agent註冊指南.md)、[系統架構](docs/系統架構.md)、[工作流程](docs/工作流程.md)、[未來開發計畫](docs/未來開發計畫.md)）。完整逐步操作仍在 **[入門啟用指南](docs/入門啟用指南.md)**。

## 角色

- **Cluster Manager：** 建群組、agent 註冊／心跳、查詢同群組空閒資源、管理員政策。預設 SQLite；`--db postgres://…` 可換 Postgres。
- **Host：** 一個行程可註冊多個 agent，各有信箱 URL、心跳、idle／busy。不能派給**同一個 agent_id**；同機不同 agent 可以互派。
- **信箱：** 每個 agent 都有。父 agent 另外保存該次 job 的子任務細節（摘要與 artifact id，不存檔案本體）。
- **產物庫：** MinIO／S3。信箱與 forward 只傳 `artifact_ids`。Manager 不存 blob。
- **政策：** 群組可設用途說明、誰能註冊／加入、誰能當父 agent 發任務；預設自動入組，可改為管理員核准。
- **轉發：** 只有父 agent 可以把結果再派給另一個子 agent；子不可對同一 job 巢狀再派。兄弟之間仍拿不到對方主機或網路。

父 agent **可以用 LLM 依目錄把 description 切成 subtasks**；`ctl --subtask` 仍可顯式指定。**巢狀父節點仍然禁止**（孫任務必須由最上層父再 forward）。不做公用市場、計費；信箱**不是** A2A。執行器可選本機 OpenCode，預設仍是內建 loop。

## 三層分別是什麼

- **協作層（本專案必做）：** 群組、目錄、Host 多 agent、父 JobMemory、MinIO、派工規則。
- **執行器（可插拔）：** `--executor auto|echo|agent|opencode`。`auto`：沒有 `MYCOAGENT_LLM_BASE_URL` 用 Echo，有則用內建 tool loop。`opencode` 在該次工作區跑 `opencode run`（需本機已安裝）。這不是第二套 OpenCode，也不把 OpenCode 當成 Cluster Manager。
- **節點間協定：** `assign_subtask`／`subtask_result` + `artifact_ids`。不是 Google A2A。

OpenCode 若依自己的設定寫出工作區以外的檔，第一版只信任本機 OpenCode 權限；MycoAgent 只保證 `cwd` 在 assignment workspace，結束後仍刪該目錄並只上傳其中檔案。

## 本機執行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# 或：uv pip install -e ".[dev]" --python .venv/bin/python

# 終端 1（--bootstrap-group 預設即 default）
python -m mycoagent manager

# 終端 2
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name alpha --port 9001 --advertise http://127.0.0.1:9001 --skills coding --tools shell

# 終端 3
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name beta --port 9002 --advertise http://127.0.0.1:9002 --skills coding --tools shell

python -m mycoagent ctl catalog default
python -m mycoagent ctl submit --node http://127.0.0.1:9001 --description "demo" --subtask "child work|coding"
# 服務已啟動時也可：./scripts/demo.sh

# 一機兩個 agent（信箱在 /agents/{id}）
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --port 9000 \
  --agent name=alpha,skills=coding,tools=shell \
  --agent name=beta,skills=coding,tools=shell

# 群組政策與核准（可選；預設仍是自動入組）
python -m mycoagent ctl groups-create locked --description "需核准" --join-mode manual --allow-parent alpha
python -m mycoagent ctl groups-update default --description "一般開發"
python -m mycoagent ctl approve locked <node_id>

# 父 agent 把 A 的結果轉派給 B（同一 job；B 仍沒有任務記憶）
python -m mycoagent ctl forward <job_id> --node http://127.0.0.1:9001 --description "follow-up" --from-subtask <subtask_id>
```

Agent 發現 Manager 用 `--manager` 或環境變數 `MYCOAGENT_MANAGER`（例如 `https://manager.internal`），不要寫死單一 IP。

未設定 `MYCOAGENT_LLM_BASE_URL` 時 `--executor auto` 是 Echo（測試／demo）。設定後子 agent 走內建 tool-calling loop，且不帶 `--subtask` 的 `POST /jobs` 會依目錄切分。規劃失敗則 job `failed`。

要用本機 OpenCode 當子執行器（協作層不變）：

```bash
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name beta \
  --port 9002 --executor opencode
# 可選：MYCOAGENT_OPENCODE_BIN、--opencode-timeout
```

產物庫：`MYCOAGENT_S3_ENDPOINT`、`MYCOAGENT_S3_ACCESS_KEY`、`MYCOAGENT_S3_SECRET_KEY`、`MYCOAGENT_S3_BUCKET`（未設定則行程內假 S3，僅供本機）。

查資源目錄：`GET http://127.0.0.1:8080/catalog?group=default`

群組政策：`GET/PATCH http://127.0.0.1:8080/groups/{name}`

父 agent 任務記憶：`GET http://127.0.0.1:9001/jobs/{job_id}`（子 agent 上同一 URL 會 404）

父 agent 轉發：`POST http://127.0.0.1:9001/jobs/{job_id}/forward`

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

Compose 專案與映像名稱是 `mycoagent`（映像標籤 `mycoagent:mvp`）。容器會是 `mycoagent-manager-1`、`mycoagent-node-a-1`、`mycoagent-node-b-1`、`mycoagent-minio-1`。兩個 node 仍是兩個 Host、各一個 agent，demo 指令不變。MinIO API 在主機 `http://127.0.0.1:9090`，console `http://127.0.0.1:9091`（帳密 `minioadmin`／`minioadmin`）。

若本機還留著舊的 `agentgraph-*` 容器／映像：

```bash
docker compose down
docker images | grep -i agentgraph
```

## 控制面叢集化

C0／C1 已做（Postgres + 兩份 Manager 見 [`docker-compose.cluster.yml`](docker-compose.cluster.yml)）。**C2（etcd／Redis lease）與 C3（gossip）仍是未來**，見 **[未來開發計畫](docs/未來開發計畫.md)**。刻意不做：Akka Cluster、把 JobMemory 搬進 Manager、LiteFS 硬撐多寫者 SQLite。
