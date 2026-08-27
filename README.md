# MycoAgent

**讓多個 AI agent 在多台機器上安全協作，而不必綁死單一模型或執行環境。**

當一個 agent 無法獨力完成工作——需要依技能派給專長不同的同伴、跨主機平行處理、或把產物交給下一棒——MycoAgent 提供**協作層**：群組、資源目錄、派工規則與任務追蹤。你繼續用既有的 LLM（oMLX、Ollama、OpenAI-compatible）或本機 OpenCode 當執行器；MycoAgent 負責「誰來做、怎麼交接、結果放哪」。

## 解決什麼問題

| 痛點 | MycoAgent 怎麼做 |
| --- | --- |
| 子任務只能在本機跑，無法跨機擴展 | 同群組 agent 透過 HTTP 信箱收派工，catalog 依技能／空閒狀態自動選人 |
| 多 agent 各自為政，缺乏治理邊界 | 群組政策：誰能入組、誰能發任務、手動核准；派工僅限同組已核准成員 |
| 任務狀態散落各節點，難追蹤 | 父 agent 集中保存 JobMemory；子 agent 無狀態做事，只回報摘要與 artifact id |
| 換執行器就要重寫協作邏輯 | 協作層與執行器分離：`echo`／內建 loop／本機 OpenCode 可插拔 |
| 產物與本機路徑綁在一起 | 工作區用完即刪；檔案上傳 MinIO／S3，協作只傳 artifact id |

## 核心特色

- **群組即信任域** — 通訊錄、核准、派工邊界一體；Cluster Manager 不管任務內容，專心做治理。
- **智慧派工** — 依 skills、tools、模型標籤與負載（最少 in-flight）選最合適的空閒 agent；父 agent 也可用 LLM 自動切分子任務。
- **一機多 agent** — 單一 Host 可掛多個身份，各自信箱與執行器設定，降低部署成本。
- **執行器隨你選** — 內建 tool loop 夠用就留著；要 MCP、skills、權限模型就接 OpenCode，不必把 OpenCode 當成叢集管理器。
- **開箱即用** — Docker Compose 數分鐘跑通；Kubernetes manifests 與多 Manager（Postgres）路徑已備好。

完整說明見 **[功能說明](docs/concepts/overview.md)**；閱讀路徑見 **[文件目錄](docs/README.md)**。

## 最快開始

```bash
docker compose up --build -d
docker compose exec -it node-a python -m mycoagent init
```

[Quick Start](docs/getting-started/quickstart.md) · [本機安裝](docs/getting-started/local-setup.md) · [部署](docs/guides/deploy.md) · [加入 Agent](docs/guides/add-agents.md)

## 開發

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
