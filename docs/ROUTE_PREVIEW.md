# Route Preview（只算路由、不呼叫模型）

本文件說明 `feat/route-preview` 分支新增的「路由預覽／dry-run」功能。目的是讓你可以用任意輸入，完整跑一次真實的判斷流程，但**最後不呼叫任何模型 API**，只取出「這次會去 device、edge 還是 cloud」以及決策細節。

## 一、它與 mock 模式的分別

| 模式 | 分類器 | 隱私判定 | Token 估算 | 路由決策 | 模型後端 | 語意快取 | 路由軌跡 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `MODEL_BACKEND_MODE=mock` | 真 | 真 | 真 | 真 | 不呼叫（產生假答案） | 會讀寫 | 會寫 |
| `POST /v1/route-preview` | 真 | 真 | 真 | 真 | **不呼叫** | **不讀寫** | **不寫** |

兩者的差別是：mock 模式仍然會把「假的模型回應」包成完整的 `chat.completion`，並且寫入快取與 Dashboard 軌跡；route preview 則在 `decide_route()` 之後就停下，只回傳決策本身，不影響 Dashboard 的統計。

## 二、實作方式

- 新增 `gateway/route_preview.py`：`build_route_preview()` 負責組裝回應；`messages_for_model()` 由 `app.py` 的私有函式搬過來，行為不變。
- `gateway/app.py` 把「分類 → 隱私 → Token → task policy → `decide_route`」抽成 `_plan_route()`，由 `/v1/chat/completions` 與 `/v1/route-preview` 共用，確保預覽與正式請求走完全相同的判斷程式碼。
- `POST /v1/route-preview` 在 `_plan_route()` 之後直接回傳 JSON，**沒有**呼叫 `complete_chat()`、**沒有**建立 `SemanticCache`、**沒有**呼叫 `store_trace()`、也**沒有**更新 Prometheus 指標。

因此「不呼叫模型 API」是由程式結構保證，而不是靠環境變數或 mock 開關。

## 三、啟動方式

### 3.1 使用 Docker Compose（建議）

```bash
git switch feat/route-preview
docker compose up -d redis gateway
```

第一次啟動時，`semantic-router`／RouteLLM 會下載本地模型（預設經 ModelScope 或 HuggingFace），需要網路；完成後即可離線判斷。`MODEL_BACKEND_MODE` 設為 `mock` 或 `real` 都不影響 preview，因為它根本不會走到模型後端。

健康檢查：

```bash
curl -s http://localhost:4000/health | python -m json.tool
```

若 `classifier` 兩個引擎都是 `ready`，便可使用 preview。若都是 `error`，preview 會與正式請求一樣回 `503`，因為它不做任何模擬或降級。

### 3.2 直接以 Python 執行

```bash
cd gateway
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 4000
```

### 3.3 命令列工具（不經 HTTP）

```bash
python scripts/route_preview.py "客户张三的电话 13800138000，请安排下周回访"
python scripts/route_preview.py --system "内部使用" "帮我写一段研发计划"
python scripts/route_preview.py --edge-unavailable "对这篇论文做综合深度分析"
```

工具會印出與 HTTP 端點相同的 JSON；分類器不可用時會以退出碼 2 結束並提示原因。

## 四、HTTP 用法

```bash
curl -s http://localhost:4000/v1/route-preview \
  -H "Authorization: Bearer change-me-local-gateway-key" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "证明任意连通无环图的边数等于节点数减一"}
    ]
  }' | python -m json.tool
```

回應標頭同樣帶有 `X-Route-Tier`、`X-Classifier-Used`、`X-Trace-Id`，方便快速檢查；但 trace ID 不會寫入 `/debug/traces`。

### 4.1 回應欄位

| 欄位 | 說明 |
| --- | --- |
| `dry_run` | 固定為 `true` |
| `decision` | `DEVICE`、`EDGE` 或 `CLOUD`（大寫，便於快速判讀） |
| `route.tier` | 小寫的 tier：`device`、`edge`、`cloud` |
| `route.model` | 邏輯模型名稱（Ollama／vLLM／雲端模型） |
| `route.reason` | `decide_route()` 給出的原因文字 |
| `route.privacy_applied` | 例如 `cloud_blocked`、`desensitized_before_cloud`、`local_file_cloud_blocked` |
| `route.estimated_cost_usd` | 依 `CLOUD_COST_PER_1K_TOKENS` 估算；非雲端為 0 |
| `destination` | 這次「本來會」呼叫的後端：`base_url`、`endpoint`、`model`，`called` 固定為 `false`；不會包含 API key |
| `classification` | 複雜度等級、置信度、分類器、推論文字、分類延遲 |
| `privacy` | 隱私等級、命中規則、是否禁雲、脫敏文字與替換表（最多 50 項）、`cloud_text` |
| `context` | Token 數、`edge_available`、`gpu_queue_depth`、三種本地能力旗標、端側模型規模 |
| `model_input_preview` | 真正會送給模型的前 300 字元（雲端情境下可看到脫敏後文字；system 訊息的行為與正式請求一致） |
| `model_api_called` | 固定為 `false` |
| `latency_ms` | 整個控制平面（分類＋隱私＋Token＋決策）耗時 |

### 4.2 三種決策示例

雲端（REASONING、S3）：

```json
{
  "dry_run": true,
  "decision": "CLOUD",
  "route": {
    "tier": "cloud",
    "model": "deepseek-v4-pro",
    "reason": "REASONING complexity requires cloud model",
    "privacy_applied": null,
    "estimated_cost_usd": 0.05568
  },
  "destination": {
    "kind": "cloud",
    "base_url": "https://api.deepseek.com/v1",
    "endpoint": "https://api.deepseek.com/v1/chat/completions",
    "model": "deepseek-v4-pro",
    "called": false
  },
  "model_api_called": false
}
```

邊緣（S1 且長上下文）：

```json
{
  "decision": "EDGE",
  "route": {
    "tier": "edge",
    "model": "deepseek-ai/DeepSeek-V4-Flash",
    "reason": "S1 privacy blocks cloud; long local context routes to edge",
    "privacy_applied": "cloud_blocked",
    "estimated_cost_usd": 0.0
  },
  "privacy": {"level": "S1", "block_cloud": true},
  "model_api_called": false
}
```

端側（SIMPLE 或 S3 短輸入）：

```json
{
  "decision": "DEVICE",
  "route": {
    "tier": "device",
    "model": "qwen3:1.5b",
    "reason": "low complexity or edge unavailable; route to device",
    "privacy_applied": null,
    "estimated_cost_usd": 0.0
  },
  "destination": {
    "kind": "device",
    "base_url": "http://ollama:11434/v1",
    "called": false
  },
  "model_api_called": false
}
```

> 實際的 `estimated_cost_usd`、模型名稱與 endpoint 會依 `.env` 與 `context_tokens` 而變；以上僅示範欄位結構。

## 五、注意事項與限制

1. **分類器不可用時回 503**：這與正式 `/v1/chat/completions` 一致。本功能不提供啟發式或固定等級的假分類，否則就不是「真實流程」。
2. **第一次執行需要下載模型**：`semantic-router` 的 encoder 與 RouteLLM 權重會由 ModelScope／HuggingFace 取得（可用 `SEMANTIC_ROUTER_ENCODER_MODEL`、`MODELSCOPE_CACHE_DIR` 等調整）。這是模型權重，不是使用者資料。
3. **分類器仍可能外連**：若把 `ROUTELLM_LOCAL_EMBEDDINGS` 設為 `false`，RouteLLM 會改用外部 embedding API；這屬於「中間判斷」的一部分，符合你的要求，但仍請確認該外連政策。預設為本地 embedding。
4. **不寫 Dashboard 軌跡**：預覽不會出現在 `/debug/traces` 或 Dashboard，避免污染統計；若你需要記錄，可再加查詢參數控制，目前未實作。
5. **分類快取仍會讀寫 Redis**：`classify()` 本身有分類結果快取（`hybrid-router:classify:*`），這是真實流程的一部分；不影響「不呼叫模型」的保證。
6. **S2 只換最後一條 user 訊息**：`messages_for_model()` 沿用原專案行為，因此 `model_input_preview` 會忠實顯示「system 訊息仍是原文」的現況。這正是先前的隱私風險之一，可用 preview 直接觀察。
7. **`/v1/route-preview` 只接受 `messages` 陣列**：與 `/v1/chat/completions` 相同的輸入格式；`stream`、`model` 等欄位會被忽略。

## 六、測試

```bash
pytest tests/test_route_preview.py -q
```

測試涵蓋：雲端決策且不呼叫模型、S1 長上下文走 edge、S2 雲端預覽顯示脫敏文字、SIMPLE 留端側、未帶 API key 回 401，以及 `messages_for_model()` 只替換最後一條 user 訊息。

## 七、變更檔案

- 新增 `gateway/route_preview.py`
- 修改 `gateway/app.py`（抽出 `_plan_route()`、新增 `POST /v1/route-preview`）
- 新增 `scripts/route_preview.py`
- 新增 `tests/test_route_preview.py`
- 新增 `docs/ROUTE_PREVIEW.md`
