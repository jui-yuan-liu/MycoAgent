#!/usr/bin/env bash
# 在 Manager + 兩個節點已啟動後：查目錄、交任務、對照父／子記憶。
# 本腳本不啟動長駐服務。
#
# 三個終端（venv）：
#   1) python -m mycoagent manager
#   2) python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name alpha --port 9001 --advertise http://127.0.0.1:9001 --skills coding --tools shell
#   3) python -m mycoagent node --manager http://127.0.0.1:8080 --group default --name beta --port 9002 --advertise http://127.0.0.1:9002 --skills coding --tools shell
#
# 或：docker compose up --build
#
# 用法：./scripts/demo.sh
# 可覆寫：PYTHON  MANAGER  PARENT  CHILD

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
else
  PYTHON="${PYTHON:-python3}"
fi

MANAGER="${MANAGER:-http://127.0.0.1:8080}"
PARENT="${PARENT:-http://127.0.0.1:9001}"
CHILD="${CHILD:-http://127.0.0.1:9002}"

if ! "$PYTHON" -c "import mycoagent" 2>/dev/null; then
  echo "找不到 mycoagent。請先安裝：" >&2
  echo "  uv pip install -e \".[dev]\" --python .venv/bin/python" >&2
  echo "  或：python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
  exit 1
fi

wait_http() {
  local url="$1"
  local name="$2"
  local i
  for i in $(seq 1 40); do
    if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('$url', timeout=1)" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  echo "等待 $name 逾時：$url" >&2
  echo "請先啟動 Manager 與兩個節點（見本腳本開頭註解，或 docker compose up）。" >&2
  exit 1
}

wait_http "$MANAGER/health" "Cluster Manager"
wait_http "$PARENT/health" "節點 alpha"
wait_http "$CHILD/health" "節點 beta"

echo "== 資源目錄 =="
"$PYTHON" -m mycoagent ctl catalog default --manager "$MANAGER"

echo "== 在 alpha 發起任務（alpha 成為父節點） =="
SUBMIT_JSON="$("$PYTHON" -m mycoagent ctl submit --node "$PARENT" --description "demo" --subtask "child work|coding")"
echo "$SUBMIT_JSON"
JOB_ID="$("$PYTHON" -c "import json,sys; print(json.loads(sys.argv[1])['job_id'])" "$SUBMIT_JSON")"

echo "== 輪詢父節點任務記憶 =="
JOB_JSON=""
for i in $(seq 1 40); do
  JOB_JSON="$("$PYTHON" -m mycoagent ctl job "$JOB_ID" --node "$PARENT")"
  STATUS="$("$PYTHON" -c "import json,sys; print(json.loads(sys.argv[1])['status'])" "$JOB_JSON")"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    echo "$JOB_JSON"
    break
  fi
  sleep 0.25
done
if [[ -z "$JOB_JSON" ]]; then
  echo "輪詢任務逾時：$JOB_ID" >&2
  exit 1
fi

echo "== 子節點沒有任務記憶（預期 404） =="
set +e
"$PYTHON" -m mycoagent ctl job "$JOB_ID" --node "$CHILD"
set -e
echo
echo "完成。job_id=$JOB_ID  父節點=$PARENT  子節點=$CHILD"
