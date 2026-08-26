# MycoAgent

跨主機的 agent 協作框架：群組、資源目錄、Host 上的多個 agent 信箱。Cluster Manager 只管通訊錄與群組政策；任務記憶只活在發起任務的父 agent。子 agent 用可插拔執行器做事，產物上傳 MinIO／S3 後清掉工作區；兄弟不互連。信箱是自有 JSON，不是 A2A。

## 能力摘要

- 群組與政策（自動／手動入組、誰能當父）
- 同組資源目錄與派工（skills／tools／模型標籤）
- 父 JobMemory、子無任務樹、父 forward
- 執行器：`echo`／內建 tool loop／本機 OpenCode

詳見 **[功能說明](docs/concepts/overview.md)**。完整閱讀路徑見 **[文件目錄](docs/README.md)**。

## 最快開始

```bash
docker compose up --build -d
docker compose exec -it node-a python -m mycoagent init
```

步驟與驗收：[Quick Start](docs/getting-started/quickstart.md)。本機 venv：[本機安裝](docs/getting-started/local-setup.md)。Kubernetes：[`deploy/k8s/`](deploy/k8s/)（見 [部署](docs/guides/deploy.md)）。

## 開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
