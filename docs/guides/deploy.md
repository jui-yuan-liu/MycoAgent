# 部署

本機 Docker Compose 的日常操作見 [Quick Start](../getting-started/quickstart.md)。本頁涵蓋：Compose 部署注意事項、可選多 Manager、以及 Kubernetes 純 manifests。

一句話：**Manager**＝通訊錄；**Host**＝信箱；**MinIO**＝產物庫。`--advertise` 必須是**其他節點／Pod 連得到的 URL**（Compose 用服務名如 `http://node-a:9001`；K8s 用 Service DNS 如 `http://alpha:9001`），不能是主機上的 `127.0.0.1`。

不涵蓋 Helm、Operator、Ingress、把 oMLX 做成叢集服務。

## Docker Compose

### 啟動與驗證

與 Quick Start 相同：

```bash
docker compose up --build -d
docker compose exec -it node-a python -m mycoagent init
curl http://127.0.0.1:8080/health
```

映像標籤 `mycoagent:mvp`。容器互連用服務名；主機操作打 `127.0.0.1`。

| 服務 | 主機連線 |
| --- | --- |
| Manager | `http://127.0.0.1:8080` |
| alpha／beta | `9001`／`9002` |
| MinIO API／console | `9090`／`9091`（帳密預設 `minioadmin`） |

### 共享 Bearer（可選）

在 `docker-compose.yml` 為 manager／node-a／node-b 設**同一個** `MYCOAGENT_TOKEN`。設了之後 `ctl` 也要帶同一值，否則目錄／派工會 401。未設＝本機開放 MVP。

### 多 Manager（Postgres）

預設 Compose 是一份 Manager + SQLite。若要兩份無狀態 Manager 共用 Postgres、主機仍只開一個 `8080`：

```bash
docker compose -f docker-compose.cluster.yml up --build
```

nginx（`manager-lb`）對 `manager-1`／`manager-2` 做 `least_conn`。節點與 `ctl` 的 Manager URL 仍是 `http://127.0.0.1:8080`（容器內 `http://manager-lb:8080`）。**不要**與預設 `docker-compose.yml` 同時跑。詳見 [`docker-compose.cluster.yml`](../../docker-compose.cluster.yml) 與 [範圍與限制](../concepts/limitations.md)。

## Kubernetes

範本在 [`deploy/k8s/`](../../deploy/k8s/)：namespace、Secret、MinIO、Manager（SQLite PVC）、alpha／beta。預設映像 `mycoagent:mvp`、`imagePullPolicy: IfNotPresent`。對外用 `kubectl port-forward`，不強制 Ingress。

### 1. 建叢集與改 Secret

需要可跑的叢集（kind／minikube／其他）與 `kubectl`。

編輯 [`deploy/k8s/01-secret.yaml`](../../deploy/k8s/01-secret.yaml)：改 MinIO 帳密；非本機請改掉範例值。`MYCOAGENT_TOKEN` 留空＝開放；要啟用時設非空字串，Manager、Hosts、ctl 必須一致。

### 2. 建映像並載入叢集

```bash
docker build -t mycoagent:mvp .
# kind
kind load docker-image mycoagent:mvp
# minikube
minikube image load mycoagent:mvp
```

或推到 registry，再改 Deployment 的 `image:`。

### 3. Apply

```bash
kubectl apply -k deploy/k8s
kubectl -n mycoagent get pods
kubectl -n mycoagent rollout status deploy/manager
kubectl -n mycoagent rollout status deploy/alpha
kubectl -n mycoagent rollout status deploy/beta
```

叢集內：`manager:8080`、`alpha:9001`、`beta:9002`、`minio:9000`。advertise 已是 `http://alpha:9001`／`http://beta:9002`。

### 4. Port-forward 與操作

```bash
kubectl -n mycoagent port-forward svc/manager 8080:8080
curl http://127.0.0.1:8080/health

kubectl -n mycoagent exec -it deploy/alpha -- \
  python -m mycoagent ctl catalog default --manager http://manager:8080

kubectl -n mycoagent exec -it deploy/alpha -- \
  python -m mycoagent ctl submit \
  --node http://127.0.0.1:9001 \
  --description "demo" \
  --subtask "child work|coding"
```

容器內打本機埠 `127.0.0.1:9001`（alpha 聽在自己 Pod）；其他 Pod 互連請用 Service 名。預設無 LLM＝Echo。

### 5. 加第三個 agent

1. 複製 `04-node-alpha.yaml` 為例如 `06-node-gamma.yaml`。
2. 改 Deployment／Service 名、標籤、`--name`、`--port`、`--advertise`、Service port、`--id-file`。
3. 把檔名加進 `kustomization.yaml` 的 `resources`，再 `kubectl apply -k deploy/k8s`。

### 6. 清理

```bash
kubectl delete -k deploy/k8s
```

## 常見錯誤

| 症狀 | 原因與處理 |
| --- | --- |
| 註冊成功但派工連不到同伴 | `--advertise` 用了 `127.0.0.1` 或主機名，對方連不到。Compose 用服務名；K8s 用 Service DNS。 |
| Pod `ErrImageNeverPull`／找不到映像 | 忘了 `kind load`／`minikube image load`，或映像名不一致。 |
| MinIO 上傳失敗／403 | Secret 裡 MinIO 帳密與 Host 的 `MYCOAGENT_S3_*` 不一致。 |
| catalog／submit 401 | `MYCOAGENT_TOKEN` 缺漏或值不同。全開或全關、同一字串。 |
| 主機 curl 連不上 K8s 服務 | 忘了 `port-forward`；或打了叢集內 DNS。 |

## 相關

- [Quick Start](../getting-started/quickstart.md)
- [加入 Agent](add-agents.md)
- [系統架構](../concepts/architecture.md)
- [`deploy/README.md`](../../deploy/README.md)
