# MycoAgent 文件

跨主機的 agent 協作框架：群組、資源目錄、HTTP 信箱、父 agent 任務記憶。Cluster Manager 只管通訊錄與政策；任務內容與產物不進 Manager。

程式進入點：`python -m mycoagent`（`manager`／`node`／`init`／`ctl`）。

## 建議閱讀順序

| 順序 | 文件 | 內容 |
| --- | --- | --- |
| 1 | [Quick Start（Docker）](getting-started/quickstart.md) | `up` → `init` → catalog → submit |
| 1b | [本機安裝（venv）](getting-started/local-setup.md) | 三終端最小路徑 |
| 2 | [功能說明](concepts/overview.md) | 群組、目錄、父記憶、執行器、產物 |
| 3 | [系統架構](concepts/architecture.md) | 角色、流程、HTTP 表面 |
| 4 | [init 參數說明](reference/init.md) | `mycoagent init` 與推薦設置 |
| 4b | [CLI 參考](reference/cli.md) | `manager`／`node`／`ctl`、環境變數 |
| 5 | [自訂群組](guides/custom-groups.md) | 政策、核准、forward |
| 6 | [加入 Agent](guides/add-agents.md) | 新 Host、欄位、LLM、OpenCode |
| 7 | [部署](guides/deploy.md) | Compose 進階、Kubernetes |
| 附錄 | [常見問題](reference/faq.md) | advertise、心跳、派工 |
| 附錄 | [範圍與限制](concepts/limitations.md) | 產品邊界、已知限制 |

## 內部文件

[`docs/agents/`](agents/) 供 Cursor／貢獻者 agent 使用，不列入公開閱讀路徑。
