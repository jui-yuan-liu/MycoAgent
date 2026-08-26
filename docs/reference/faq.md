# 常見問題

操作與網路相關問題。部署錯誤表見 [部署](../guides/deploy.md)；產品邊界見 [範圍與限制](../concepts/limitations.md)。

## advertise URL 填錯，派工失敗

`--advertise` 必須是**其他 Host（以及回報用的父 agent）連得上的基底 URL**。

- 本機三終端：用 `http://127.0.0.1:9001` 這類迴環位址即可。
- Docker Compose：容器互連用 `http://node-a:9001`（compose 已設好）。你在**主機**查目錄、送任務請打 `http://127.0.0.1:9001`，不要打服務名（主機解析不到）。
- Host 在不同機器：advertise 要填對方可路由到的位址，不要留 `127.0.0.1`。
- 發現 Manager：用 DNS 或 `MYCOAGENT_MANAGER`。

目錄裡的 `mailbox_url` 就是這個 agent 的信箱。

## 心跳離線，目錄是空的

Host 上每個 agent 預設約每 **5** 秒心跳。Manager 預設 **15** 秒沒更新就標 `offline`。離線者不會出現在預設的 `ctl catalog`（`idle_only`）。

```bash
python -m mycoagent ctl catalog default --no-idle-only
curl http://127.0.0.1:9001/health
```

重啟 Host 預設會拿到**新的** agent id。要沿用同一列：`--id-file`、`MYCOAGENT_ID_FILE` 或 `MYCOAGENT_AGENT_ID`。

## 不能派給自己

有 `--subtask`（或 LLM 切出的 subtasks）時，父會從目錄排除自己的 agent_id。只有一個 agent、或同伴 busy／offline／技能不符，子任務會 `failed`（例如 `no idle matching node in the same group`）。再開一個同組 agent，或先做不帶 `--subtask`、也沒設 LLM 的本機 Echo 任務。

## 不能跨群組

目錄依 `--group`／`?group=` 過濾。不同群組的 agent 不會被派到。`--target` 若指向別組，會被拒絕。MinIO 物件鍵以 group 為 prefix。

## 其他

- **群組不存在：** `--group` 必須先有（bootstrap 或 `ctl groups-create`）。
- **pending／deny：** `manual` 群組未核准前不進目錄、不能當父（發任務會 403）。
- **allow_parent：** 名單非空且自己不在其中，發任務 403。
- **任務記憶在記憶體：** 重啟父 Host 就沒了（除非 `--job-db`）。Manager 狀態在 `--db`。
- **埠被占用：** 換 `--port`，並讓 `--advertise` 與對外 URL 一致。
- **`--executor opencode` 找不到指令：** 安裝 [OpenCode](https://opencode.ai) 或設 `MYCOAGENT_OPENCODE_BIN`。
- **catalog／submit 401：** 檢查 `MYCOAGENT_TOKEN` 是否 Manager、Host、ctl 一致。
