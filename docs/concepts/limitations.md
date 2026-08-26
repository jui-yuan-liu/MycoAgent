# 範圍與限制

本頁說明產品邊界與目前實作的已知限制，方便評估是否適合你的場景。功能總覽見 [功能說明](overview.md)。

## 產品範圍

MycoAgent 聚焦**協作層**：群組、目錄、Host 多 agent、父 JobMemory、MinIO、派工與 forward。

執行器可插拔（Echo、內建 loop、本機 OpenCode）。內建 loop 維持精簡；需要更完整的編碼／MCP／權限模型時，使用 `--executor opencode`，而不是在 MycoAgent 內複製 OpenCode。

下列能力**不在**目前產品目標內（直到專門開期）：

- 公用市場、計費、毒性評分
- 以 Google A2A 作為內部信箱協定
- 把 OpenCode 當成 Cluster Manager，或讓子節點對同一 job 巢狀再派
- 巢狀父節點（監督鏈必須回到最上層父）
- 把對話／向量記憶放進 Manager（與「不管任務內容」衝突）
- 完整 Akka Cluster（shard、distributed data 當任務樹）
- 把 JobMemory 搬進 Manager；用 LiteFS 硬撐多 Manager 寫心跳

## 已知限制（現況）

- 未設 `--job-db` 時 JobMemory 與假 S3 在 RAM；重啟父 Host 任務樹會消失（`--job-db` 可把任務樹落 SQLite）。
- 子→父回報會重試一次；父全程掛掉仍可能丟結果。
- 每個 agent 同時跑一件 child；其餘進 in-process 佇列，佇列滿才 409。
- 可選共享 Bearer token；尚未提供 mTLS。shell／OpenCode 仍信任同組與本機設定。
- 同組 MinIO prefix 未另做 ACL。
- 控制面：多份無狀態 Manager + Postgres 可用（見 [部署](../guides/deploy.md)）。以 etcd／Redis lease 表達存活，或以 gossip 擴組員規模，仍屬後續工作。

## 相關

- [系統架構](architecture.md)
- [部署](../guides/deploy.md)
- [`docker-compose.cluster.yml`](../../docker-compose.cluster.yml)
