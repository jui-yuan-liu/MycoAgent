# MycoAgent（第一階段）

群組、資源目錄、節點信箱。Cluster Manager 只管通訊錄；任務記憶只活在發起任務的父節點。子節點只做事、回報，不持有任務樹。

## 角色

- **Cluster Manager：** 建群組、節點註冊／心跳、查詢同群組空閒資源。
- **節點：** 同一套程式。誰在本機發起任務，誰就是這次的父節點；只能派給**同一群組的其他節點**。
- **信箱：** 每個節點都有。父節點另外保存該次 job 的子任務細節。

本階段不做公用市場、計費、OpenCode 綁定、AI 自動切分。

## 本機執行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 終端 1
python -m mycoagent manager --bootstrap-group default

# 終端 2
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name alpha --port 9001 --advertise http://127.0.0.1:9001 --skills coding --tools shell

# 終端 3
python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name beta --port 9002 --advertise http://127.0.0.1:9002 --skills coding --tools shell

python -m mycoagent ctl catalog default
python -m mycoagent ctl submit --node http://127.0.0.1:9001 --description "demo" --subtask "child work|coding"
```

查資源目錄：`GET http://127.0.0.1:8080/catalog?group=default`

父節點任務記憶：`GET http://127.0.0.1:9001/jobs/{job_id}`（子節點上同一 URL 會 404）

```bash
pytest
```

## Docker

```bash
docker compose up --build
```

Manager 在 `:8080`，節點 alpha `:9001`、beta `:9002`。
