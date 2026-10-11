# 第三方接入API文档

## 接入准备

本文件适用于与云帆科技签署《开放平台接入协议》的第三方合作方，用于说明生产及沙箱环境接入规范。接入前，合作方须提交营业执照、法人身份证明、接口用途说明、数据安全承诺函及回调域名清单，云帆科技在 3 个工作日内完成审核。

环境域名如下：生产环境 `https://api.yunfan-tech.com/open/v1`；沙箱环境 `https://sandbox-api.yunfan-tech.com/open/v1`；开放平台控制台 `https://open.yunfan-tech.com`。沙箱与生产凭证完全隔离，沙箱数据保留 30 天，生产数据按业务类型保留不少于 3 年。

合作方通过控制台创建应用，获得 `app_id`（格式为 `YF` + 16 位数字）和 `app_secret`（32 位字符串）。`app_secret` 仅在创建时完整展示一次，须存放于加密配置中心，不得写入前端代码或公开仓库。密钥每 180 天轮换一次，轮换期新旧密钥并行 24 小时。回调地址须为 HTTPS，端口 443，不得携带 query 参数，每个应用最多配置 5 个白名单地址，变更后 5 分钟生效。

接口统一使用 UTF-8 JSON，时间格式为 RFC3339，金额单位为分，币种默认 CNY。对接流程为：提交申请→资质审核→沙箱联调→验收测试→生产开通。沙箱联调需覆盖鉴权、查询、写入、回调验签及幂等场景。

## 鉴权流程

云帆开放平台采用 OAuth2.0 客户端凭证模式，业务接口使用 Bearer Token。`access_token` 有效期 7200 秒，`refresh_token` 有效期 30 天。鉴权时序如下：

1. 第三方服务端使用 `app_id`、`app_secret` 调用 `POST /oauth2/token`，请求参数包括 `grant_type=client_credentials`、`app_id`、`timestamp`、`nonce`、`sign`。
2. `timestamp` 为 Unix 秒，与服务端时间偏差不得超过 300 秒；`nonce` 为 32 位随机字符串，10 分钟内不可重复。
3. `sign` 使用 HMAC-SHA256 计算，待签串为 `app_id + timestamp + nonce`，输出十六进制小写。
4. 云帆校验应用状态、IP 白名单、签名及 nonce 唯一性，通过后返回 `access_token`、`token_type=Bearer`、`expires_in=7200`、`refresh_token`、`refresh_expires_in=2592000`。
5. 第三方缓存 token，调用业务接口时在 Header 中携带 `Authorization: Bearer <access_token>`。
6. 当 token 剩余有效期小于 600 秒时，使用 `refresh_token` 刷新；刷新失败或 `refresh_token` 过期，须重新走客户端凭证流程。若业务接口返回 401，应清理缓存后重新获取，禁止无限重试。

Python 示例：

```python
import hashlib
import hmac
import time
import uuid
import requests

BASE = "https://api.yunfan-tech.com/open/v1"
APP_ID = "YF202405180001"
APP_SECRET = b"your_app_secret"

def sign(secret: bytes, text: str) -> str:
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()

def get_token():
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    payload = {
        "grant_type": "client_credentials",
        "app_id": APP_ID,
        "timestamp": ts,
        "nonce": nonce,
    }
    payload["sign"] = sign(APP_SECRET, f"{APP_ID}{ts}{nonce}")
    resp = requests.post(f"{BASE}/oauth2/token", json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()

def call_api(path, params=None):
    # 生产环境应缓存 token，并在剩余有效期小于 600 秒时刷新
    token = get_token()["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Yunfan-App-Id": APP_ID,
    }
    resp = requests.get(f"{BASE}{path}", params=params, headers=headers, timeout=5)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", "1")))
        return call_api(path, params)
    resp.raise_for_status()
    return resp.json()
```

常见鉴权错误码：40101 签名错误，40102 token 过期，40103 IP 不在白名单，42901 触发限流。

## 回调接口

第三方须在控制台配置回调地址。云帆在事件发生后 5 秒内发起首次回调，请求方法为 `POST`，`Content-Type` 为 `application/json; charset=utf-8`。请求头包含：

| 请求头 | 说明 |
|---|---|
| X-Yunfan-App-Id | 应用标识 |
| X-Yunfan-Timestamp | Unix 秒时间戳 |
| X-Yunfan-Nonce | 32 位随机字符串 |
| X-Yunfan-Event | 事件类型，如 `order.paid` |
| X-Yunfan-Signature | HMAC-SHA256 签名，十六进制小写 |

验签规则：待签串为 `timestamp + "\n" + nonce + "\n" + sha256(body)`，使用 `app_secret` 进行 HMAC-SHA256 计算。时间戳偏差不得超过 300 秒，nonce 在 10 分钟内不可重复。密钥轮换期 24 小时内，新旧 `app_secret` 均可验签。

回调体示例：

```json
{
  "event_id": "evt_20240518123000001",
  "event_type": "order.paid",
  "event_time": "2024-05-18T12:30:00+08:00",
  "app_id": "YF202405180001",
  "data": {
    "order_no": "YF2024051800012345",
    "amount": 19900,
    "currency": "CNY",
    "paid_at": "2024-05-18T12:29:58+08:00"
  }
}
```

第三方须在 5 秒内返回 HTTP 200，响应体为 `{"code":"0","message":"success"}`。其他状态码或超时均视为失败。失败重试间隔为 1 分钟、5 分钟、15 分钟、1 小时、6 小时、24 小时，共 7 次；仍失败则进入人工补推队列，保留 7 天。第三方应按 `event_id` 幂等去重，回调处理仅做落库，耗时业务异步执行。

## 限流规则

限流维度为 `app_id + 接口路径 + 来源 IP`，采用令牌桶算法。超限返回 HTTP 429，响应体包含 `code=42901`、`message=too many requests` 和 `request_id`，响应头包含 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`、`Retry-After`。生产环境默认限额如下：

| 接口分类 | 典型接口 | 限额（次/分钟） | 突发上限（次/秒） | 超限建议 |
|---|---|---:|---:|---|
| 鉴权类 | POST /oauth2/token | 20 | 5 | 按 Retry-After 重试，最长等待 60 秒 |
| 查询类 | GET /orders/{order_no}、GET /transactions | 600 | 100 | 指数退避，初始 1 秒，最大 60 秒 |
| 写入类 | POST /refunds、POST /orders | 120 | 20 | 重试不超过 3 次，需保证幂等 |
| 对账类 | GET /settlements、GET /reconciliations | 30 | 5 | 建议 10:30 后错峰调用 |
| 回调配置类 | PUT /callback/config、GET /callback/config | 60 | 10 | 变更后 5 分钟生效，避免频繁修改 |

表中突发上限为单秒瞬时峰值，持续时间不得超过 1 秒，且计入分钟限额。单应用全接口合计限额为 3000 次/分钟，短时突发以令牌桶容量 300 为准。沙箱环境限额为生产环境的 10%。如需提额，须提前 5 个工作日提交《限额调整申请》，最高可提升至默认值的 3 倍。因未按限流规则调用导致的服务不可用，云帆科技不承担相应责任。
