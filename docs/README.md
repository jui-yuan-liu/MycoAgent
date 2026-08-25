# MycoAgent 文件

跨主機的 agent 協作框架：群組、資源目錄、信箱、父 agent 任務記憶。Cluster Manager 只管通訊錄與政策；任務內容與產物不進 Manager。

| 文件 | 內容 |
| --- | --- |
| [快速使用指南](快速使用指南.md) | Docker Compose：`up` → `init` → catalog → submit |
| [Agent 註冊指南](agent註冊指南.md) | 新 agent 入組、必填欄位、與 LLM 連線 |
| [入門啟用指南](入門啟用指南.md) | 逐步操作、群組政策、forward、FAQ |
| [系統架構](系統架構.md) | 角色、三層、資料放哪、HTTP 表面 |
| [工作流程](工作流程.md) | 註冊心跳、派工、執行、產物、轉發 |
| [未來開發計畫](未來開發計畫.md) | 已完成／下一步／刻意不做 |

程式進入點：`python -m mycoagent`（`manager`／`node`／`init`／`ctl`）。
