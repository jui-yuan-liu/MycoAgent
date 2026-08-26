# Quick Start（Docker）

用 Docker Compose 在幾分鐘內跑起 Manager、兩個 Host 與 MinIO，並完成一次派工。主機不必先裝 Python。

完成後你會有：可查的資源目錄、一筆父／子任務，並能對照「只有父有 JobMemory」。

更細的 `init` 旗標見 [init 參數說明](../reference/init.md)。本機 venv 路徑見 [本機安裝](local-setup.md)。

## 1. 啟動

在專案根目錄（有 `docker-compose.yml` 的那層）：

```bash
docker compose up --build -d
docker compose exec -it node-a python -m mycoagent init
```

會起 Cluster Manager、兩個 Host（alpha／beta）、以及 MinIO。沒有 `.mycoagent/agents.yaml` 時 Host 先以 Echo 起來；`init` 會寫入設定並 `POST /configure` 套用到已在跑的 Host，不必重開容器。已設定過則印摘要後離開（`--force` 才重跑）。

| 服務 | 主機怎麼連 | 角色 |
| --- | --- | --- |
| Manager | `http://127.0.0.1:8080` | 群組與資源目錄 |
| alpha | `http://127.0.0.1:9001` | 範例父 agent |
| beta | `http://127.0.0.1:9002` | 子 agent（做事、沒有任務記憶） |
| MinIO API／console | `http://127.0.0.1:9090`／`9091` | 產物庫（帳密 `minioadmin`） |

在瀏覽器或主機上的 curl 請打 `127.0.0.1`；容器互連才用 `manager`、`node-a` 等服務名。

**完成條件：** `curl http://127.0.0.1:8080/health` 回傳含 `cluster-manager`。

## 2.（可選）本機 oMLX

Compose **不會**起 AI server。oMLX 須跑在 Mac 主機；容器經 `host.docker.internal:8000` 連過去。

```bash
omlx start   # 確認 http://127.0.0.1:8000/v1/models 有模型
docker compose exec -it node-a python -m mycoagent init --yes --provider omlx
```

Ollama 用 `--provider ollama`；自訂 URL 用 `--provider custom --llm-url …`；只要 Echo 用 `--provider echo`。詳見 [init 參數說明](../reference/init.md)。

## 3. 查看目錄

```bash
docker compose exec node-a python -m mycoagent ctl catalog default --manager http://manager:8080
```

**完成條件：** 看到 `alpha`、`beta`，`status` 為 `idle`，`skills` 含 `coding`。

## 4. 派一筆工

未設 LLM 時執行器是 Echo，子任務結果類似 `done:child work`：

```bash
docker compose exec node-a python -m mycoagent ctl submit \
  --node http://127.0.0.1:9001 \
  --description "demo" \
  --subtask "child work|coding"
```

記住回傳的 `job_id`。`parent_node_id` 是 alpha；子任務會派給同組空閒的 beta。

不帶 `--subtask` 且沒有 LLM planner 時，工作留在父本機做完，不必有同伴。

## 5. 對照父有記憶、子沒有

```bash
docker compose exec node-a python -m mycoagent ctl job <job_id> --node http://127.0.0.1:9001
docker compose exec node-b python -m mycoagent ctl job <job_id> --node http://127.0.0.1:9002
```

父回傳完整 JobMemory；子預期結束碼 1、內容 404。

停服務：`docker compose down`。

## 下一步

- [功能說明](../concepts/overview.md)
- [加入 Agent](../guides/add-agents.md)
- [部署](../guides/deploy.md)（Kubernetes、多 Manager）
