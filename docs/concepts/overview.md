# 功能說明

MycoAgent 讓同群組的 agent 透過 HTTP 信箱收派工：父 agent 記任務樹，子 agent 做事並上傳產物，兄弟互不連線。

適合想先了解「系統能做什麼」的讀者。跑起來請從 [Quick Start](../getting-started/quickstart.md) 或 [本機安裝](../getting-started/local-setup.md) 開始；細節結構見 [系統架構](architecture.md)。

## 核心能力

### 群組與政策

一組互相看得見的 agent 構成一個**群組**。Cluster Manager 管通訊錄與政策（誰能註冊、誰能當父、自動或手動核准），不管任務內容，也不存產物檔。群組同時是派工邊界與信任域。

操作見 [自訂群組](../guides/custom-groups.md)。

### 資源目錄（catalog）

查「這個群組裡誰空閒、有哪些技能／工具／模型標籤」。派工只從**同一群組、已核准、空閒、且不是同一個 agent_id**的對象裡挑。同機不同 agent 可以互派。可依 skills、tools、model 名稱、最低 context／記憶體篩選；符合者取最少 in-flight，同分再 round-robin。

### Host、信箱與父任務記憶

一個 Host 行程可掛多個 agent，各有信箱與 idle／busy。誰對該 agent `POST /jobs`，誰就是這次的**父 agent**，只有他保存該次 job 的子任務細節（JobMemory）。子 agent 只做事、上傳產物、回報 artifact id，**沒有任務樹、也不跟兄弟互連**。

### 可插拔執行器

子任務由執行器完成：

| 模式 | 行為 |
| --- | --- |
| `echo` | 回音 demo，不必 LLM |
| `auto` | 無 `MYCOAGENT_LLM_BASE_URL` 用 Echo；有則用內建 tool loop |
| `agent` | 強制內建 OpenAI-compatible tool loop |
| `opencode` | 在工作區跑本機 `opencode run`（LLM／skills／MCP 由 OpenCode 管） |

協作層（群組、目錄、信箱、JobMemory）與執行器分離：換成 OpenCode 不會把 OpenCode 變成 Cluster Manager。

### 工作區與產物庫

每個 assignment 一個暫存目錄，結束必刪。結果不帶本機路徑；檔案本體在 MinIO／S3（未設環境變數則行程內假 S3）。信箱與 forward 只傳 `artifact_ids`。

### 父切分與轉發

- 請求可帶明確 `subtasks`；也可不帶，由父依目錄用 LLM 切分（有連線時）。
- 只有父可以把同一 job 再 **forward** 給另一個子；子不可對同一 job 巢狀再派。孫任務必須由最上層父再 forward。

## 三層分工

```
協作層（本專案）     執行器（可插拔）         節點間協定
群組／目錄／Host     echo / 內建 loop /      assign_subtask
JobMemory／MinIO     OpenCode                subtask_result
派工規則                                     artifact_ids
```

節點間是自有 JSON，不是 Google A2A。

## 下一步

- [系統架構](architecture.md)
- [加入 Agent](../guides/add-agents.md)
- [範圍與限制](limitations.md)
