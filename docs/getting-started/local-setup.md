# 本機安裝（venv）

不用 Docker 時，用虛擬環境開一個 Manager 與兩個 Host，完成與 Quick Start 相同的 catalog → submit 路徑。

Docker 一鍵見 [Quick Start](quickstart.md)。概念見 [功能說明](../concepts/overview.md)。

## 環境需求

- Python 3.11+
- 可選：`uv`、`curl`、本機 oMLX／Ollama／其他 OpenAI-compatible LLM、[OpenCode](https://opencode.ai)（僅 `--executor opencode`）

## 1. 安裝

在專案根目錄（含 `pyproject.toml`）：

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
# 或：uv pip install -e ".[dev]" --python .venv/bin/python
```

**完成條件：** `python -m mycoagent --help` 列出 `manager`、`node`、`init`、`ctl`。

## 2. 啟動 Cluster Manager

終端 1：

```bash
python -m mycoagent manager
```

預設：聽 `0.0.0.0:8080`、SQLite `mycoagent.db`、`--bootstrap-group default`。也可用 `postgres://…` 當 `--db`。

**完成條件：**

```bash
curl http://127.0.0.1:8080/health
# {"status":"ok","role":"cluster-manager"}
python -m mycoagent ctl groups
```

可選共享 Bearer：設 `MYCOAGENT_TOKEN`（Manager、各 Host、`ctl` 同一值）。詳見 [CLI 參考](../reference/cli.md)。

## 3. 啟動兩個 Host

終端 2（alpha）：

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 \
  --group default \
  --name alpha \
  --port 9001 \
  --advertise http://127.0.0.1:9001 \
  --skills coding \
  --tools shell
```

終端 3（beta）：

```bash
python -m mycoagent node \
  --manager http://127.0.0.1:8080 \
  --group default \
  --name beta \
  --port 9002 \
  --advertise http://127.0.0.1:9002 \
  --skills coding \
  --tools shell
```

同一行程掛兩個 agent 時用可重複的 `--agent`（信箱在 `{advertise}/agents/{id}`），見 [加入 Agent](../guides/add-agents.md)。

**完成條件：** `curl http://127.0.0.1:9001/health` 與 `9002` 皆成功。

## 4. 目錄與派工

```bash
python -m mycoagent ctl catalog default

python -m mycoagent ctl submit \
  --node http://127.0.0.1:9001 \
  --description "demo" \
  --subtask "child work|coding"
```

對照父／子記憶：

```bash
python -m mycoagent ctl job <job_id> --node http://127.0.0.1:9001
python -m mycoagent ctl job <job_id> --node http://127.0.0.1:9002
```

服務已起來時也可：`./scripts/demo.sh`（不會啟動長駐行程）。

## 下一步

- [功能說明](../concepts/overview.md)
- [自訂群組](../guides/custom-groups.md)
- [CLI 參考](../reference/cli.md)
- [常見問題](../reference/faq.md)
