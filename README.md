# MycoAgent

群組、資源目錄、節點信箱。Cluster Manager 只管通訊錄與群組政策；任務記憶只活在發起任務的父節點。子節點只做事、回報，不持有任務樹，也不互連。

逐步從零跑起來：請看 **[入門啟用指南](docs/入門啟用指南.md)**。

## 角色

- **Cluster Manager：** 建群組、節點註冊／心跳、查詢同群組空閒資源、管理員政策。
- **節點：** 同一套程式。誰在本機發起任務，誰就是這次的父節點；只能派給**同一群組的其他節點**。
- **信箱：** 每個節點都有。父節點另外保存該次 job 的子任務細節。
- **政策：** 群組可設用途說明、誰能註冊／加入、誰能當父節點發任務；預設自動入組，可改為管理員核准。
- **轉發：** 父節點可把某個子節點的結果（或自己寫的後續內容）再派給另一個子節點；兄弟之間仍不互連、拿不到對方主機或網路。

本階段不做公用市場、計費、OpenCode 綁定、AI 自動切分。

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

# 群組政策與核准（可選；預設仍是自動入組）
python -m mycoagent ctl groups-create locked --description "需核准" --join-mode manual --allow-parent alpha
python -m mycoagent ctl groups-update default --description "一般開發"
python -m mycoagent ctl approve locked <node_id>

# 父節點把 A 的結果轉派給 B（同一 job；B 仍沒有任務記憶）
python -m mycoagent ctl forward <job_id> --node http://127.0.0.1:9001 --description "follow-up" --from-subtask <subtask_id>
```

查資源目錄：`GET http://127.0.0.1:8080/catalog?group=default`

群組政策：`GET/PATCH http://127.0.0.1:8080/groups/{name}`

父節點任務記憶：`GET http://127.0.0.1:9001/jobs/{job_id}`（子節點上同一 URL 會 404）

父節點轉發：`POST http://127.0.0.1:9001/jobs/{job_id}/forward`

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

Compose 專案與映像名稱是 `mycoagent`（映像標籤 `mycoagent:mvp`），不沿用資料夾名 AgentGraph。容器會是 `mycoagent-manager-1`、`mycoagent-node-a-1`、`mycoagent-node-b-1`。

若本機還留著舊的 `agentgraph-*` 容器／映像：

```bash
docker compose down
docker images | grep -i agentgraph
```
