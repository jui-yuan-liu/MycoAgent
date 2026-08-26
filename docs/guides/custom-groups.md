# 自訂群組

設定群組政策、手動核准成員，以及父 agent 的 forward。適合已跑通 [Quick Start](../getting-started/quickstart.md) 或 [本機安裝](../getting-started/local-setup.md) 的讀者。

## 政策欄位

預設 `default`：自動入組，任何人可註冊，任何已核准成員都可當父 agent 發任務。

| 欄位 | 空／預設 | 有值時 |
| --- | --- | --- |
| `join_mode` | `auto`：註冊即核准 | `manual`：註冊後為 `pending`，管理員核准才進目錄、才能當父 |
| `allow_register` | 空 = 誰都能註冊 | 僅列出的**名稱**能註冊 |
| `allow_parent` | 空 = 已核准成員都能發任務 | 僅列出的**名稱或 agent id**能當父 |

```bash
python -m mycoagent ctl groups-create locked \
  --description "需核准" \
  --join-mode manual \
  --allow-parent alpha

python -m mycoagent ctl groups-update default --description "一般開發"
python -m mycoagent ctl group locked
```

`allow_register`／`allow_parent` 為逗號分隔字串，例如 `--allow-register alpha,beta`。`groups-update` 傳空字串可清成「不限制」。

## 手動核准

手動群組裡，新 agent 註冊後 `membership_status` 為 `pending`，`ctl catalog locked` 為空。從 `ctl group locked` 的 `pending_ids` 取出 id：

```bash
python -m mycoagent ctl approve locked <agent_id>
python -m mycoagent ctl deny locked <agent_id>
```

**完成條件：** `ctl catalog locked` 看得到已核准且 `idle` 的成員。

## 父 agent forward

只有**保存該 job 的父 agent**可以把後續工作再派出去。兄弟互不知道對方主機或信箱；若帶 `--from-subtask`，父只把該子任務的**結果文字與 artifact id**放進下一筆 payload，不會轉交網路位址或本機路徑。

同一個 job，在父（例如 alpha）上：

```bash
python -m mycoagent ctl forward <job_id> \
  --node http://127.0.0.1:9001 \
  --description "follow-up" \
  --skills coding
```

這會再派給同組另一個空閒 agent（兩個 agent 時通常是 beta，因為不能派給自己）。

若要把「某筆子任務的結果」帶給**另一個**同伴，加上 `--from-subtask <subtask_id>`。此時來源子會被排除，**需要第三個同組 agent**：

```bash
python -m mycoagent ctl forward <job_id> \
  --node http://127.0.0.1:9001 \
  --description "follow-up" \
  --from-subtask <subtask_id> \
  --target <第三個 agent 的 id> \
  --skills coding
```

第三個 agent 範例見 [加入 Agent](add-agents.md)。在子 agent 上對同一 `job_id` 做 `ctl job` 或 `ctl forward` 都是 404（禁止巢狀父節點）。

HTTP：`POST http://127.0.0.1:9001/jobs/<job_id>/forward`，JSON 欄位為 `description`、`skills`、`tools`、`source_subtask_id`、`target_node_id`。

## 下一步

- [加入 Agent](add-agents.md)
- [系統架構](../concepts/architecture.md)
- [常見問題](../reference/faq.md)
