# 公开API文档

## 概述

本文档面向已与云帆科技签署《开放平台服务协议》的签约商户，说明云帆开放平台公开 API 的接入方式、签名规则、接口定义与错误处理规范。文档版本为 V1.2.0，发布日期为 2025 年 3 月 18 日，自发布之日起生效，历史版本不再单独维护。

平台统一接入地址为 `https://api.yunfan-tech.com`，所有业务接口路径以 `/v1` 开头。接口仅支持 HTTPS 传输，TLS 版本不得低于 1.2；请求与响应报文均为 UTF-8 编码的 JSON 格式，`Content-Type` 固定为 `application/json; charset=utf-8`。

通用约定如下：金额字段一律以“分”为单位的整数表示，不接受小数与千分位分隔符；时间字段采用 ISO 8601 格式并携带东八区偏移（如 `2025-03-18T14:20:00+08:00`）；商户订单号 `out_trade_no` 与商户退款单号 `out_refund_no` 由商户自行生成，须在商户系统内全局唯一，作为接口幂等键使用；平台交易手续费率为 0.6%，按订单实付金额计收，结算周期为 T+1，单次结算最低起结金额为 100.00 元；订单支付有效期为 15 分钟，超时未支付将自动关闭且不可恢复。生产环境单应用默认限流为 600 次/分钟，超出后返回 429 状态码。

## 认证方式

平台采用 AppKey 与 AppSecret 组合的签名认证，不传递明文密钥。商户可在开放平台控制台“应用管理—密钥管理”中获取 `AppKey` 与 `AppSecret`，其中 `AppSecret` 仅用于本地签名计算，严禁写入客户端代码或对外披露。密钥支持自助轮换，轮换后旧密钥保留 24 小时生效窗口。

每个请求须在 HTTP 请求头中携带以下字段：

| 请求头 | 必填 | 说明 |
| --- | --- | --- |
| X-YF-AppKey | 是 | 应用唯一标识，由控制台分配 |
| X-YF-Timestamp | 是 | 当前 UNIX 时间戳（秒），与平台服务器时间偏差不得超过 300 秒 |
| X-YF-Nonce | 是 | 32 位随机字符串，同一 AppKey 下 5 分钟内不得重复 |
| X-YF-Signature | 是 | 签名值，大写十六进制字符串 |
| X-YF-Version | 否 | 接口版本，默认 `v1` |

签名计算步骤如下：第一步，将请求头参数（`X-YF-AppKey`、`X-YF-Timestamp`、`X-YF-Nonce`）与请求体中的一级字段合并；第二步，剔除值为空、值为 `null` 以及 `sign` 字段；第三步，按参数名 ASCII 码升序排列，拼接为 `key=value&key=value` 形式；第四步，在拼接串末尾追加 `&key=AppSecret`；第五步，对最终字符串执行 HMAC-SHA256 运算，输出结果转为大写十六进制。校验失败时接口返回 40101 错误码，时间戳超出允许偏差返回 40102。

## 接口列表

平台当前对外开放 3 个核心交易接口，接口总览如下：

| 序号 | 接口名称 | 请求方法 | 接口路径 | 功能说明 |
| --- | --- | --- | --- | --- |
| 1 | 创建订单 | POST | /v1/orders | 创建支付订单，返回支付链接与二维码地址 |
| 2 | 查询订单 | GET | /v1/orders/{order_id} | 查询订单状态、金额与结算信息 |
| 3 | 申请退款 | POST | /v1/refunds | 发起全额或部分退款申请 |

成功响应的 HTTP 状态码为 200，响应体 `code` 字段为 `0`，业务数据位于 `data` 对象中；业务失败时 `code` 为非 0 值，`data` 为 `null`。

### 创建订单

调用该接口创建支付订单，订单默认有效期为 15 分钟，可通过 `expire_minutes` 调整。商户须提供 HTTPS 协议的异步通知地址，支付结果通知在支付成功后 5 秒内首次发起，未收到 `SUCCESS` 应答时按 5 秒、30 秒、5 分钟、30 分钟、2 小时、6 小时共 6 次重试。

请求参数如下：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| out_trade_no | string(32) | 是 | 商户订单号，全局唯一，幂等键 |
| subject | string(64) | 是 | 商品或服务名称 |
| total_amount | int | 是 | 订单金额，单位分，取值范围 1 至 100000000 |
| currency | string(3) | 否 | 币种，默认 `CNY` |
| notify_url | string(255) | 是 | 异步通知地址，须为 HTTPS |
| expire_minutes | int | 否 | 支付有效期，默认 15，取值 5 至 120 |
| attach | string(128) | 否 | 商户附加数据，原样回传 |

请求示例：

```json
{
  "out_trade_no": "M202503180001",
  "subject": "云帆企业版年度授权",
  "total_amount": 128000,
  "currency": "CNY",
  "notify_url": "https://merchant.example.com/yunfan/notify",
  "expire_minutes": 15,
  "attach": "order-from-crm"
}
```

响应示例：

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "order_id": "YF2025031800000123",
    "out_trade_no": "M202503180001",
    "total_amount": 128000,
    "fee_amount": 768,
    "status": "PAYING",
    "pay_url": "https://pay.yunfan-tech.com/qr/YF2025031800000123",
    "expire_at": "2025-03-18T14:35:00+08:00",
    "created_at": "2025-03-18T14:20:00+08:00"
  }
}
```

### 查询订单

该接口用于主动对账与状态补偿，`order_id` 与 `out_trade_no` 二者必传其一，同时传入时以 `order_id` 为准。建议商户在未收到异步通知时，按 3 秒、10 秒、30 秒、60 秒的间隔查询，最多查询 6 次。

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| order_id | string(24) | 否 | 云帆订单号，与 out_trade_no 二选一 |
| out_trade_no | string(32) | 否 | 商户订单号，与 order_id 二选一 |

请求示例：

```json
{
  "order_id": "YF2025031800000123"
}
```

响应示例：

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "order_id": "YF2025031800000123",
    "out_trade_no": "M202503180001",
    "total_amount": 128000,
    "paid_amount": 128000,
    "fee_amount": 768,
    "settle_amount": 127232,
    "status": "SUCCESS",
    "pay_channel": "WECHAT_QR",
    "paid_at": "2025-03-18T14:22:36+08:00",
    "settle_date": "2025-03-19"
  }
}
```

订单状态取值包括 `PAYING`（待支付）、`SUCCESS`（支付成功）、`CLOSED`（已关闭）、`PARTIAL_REFUNDED`（部分退款）、`REFUNDED`（全额退款）。结算金额 `settle_amount` 等于实付金额扣除 0.6% 手续费后的余额。

### 申请退款

退款支持全额与部分退款，同一订单可多次部分退款，但累计退款金额不得超过订单实付金额 128000 分。退款按原支付渠道原路退回，到账时限为 1 至 7 个工作日；退款手续费按原交易费率 0.6% 计收，从退款金额中扣除。

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| out_refund_no | string(32) | 是 | 商户退款单号，全局唯一，幂等键 |
| order_id | string(24) | 否 | 云帆订单号，与 out_trade_no 二选一 |
| out_trade_no | string(32) | 否 | 商户订单号，与 order_id 二选一 |
| refund_amount | int | 是 | 退款金额，单位分，不超过可退金额 |
| reason | string(64) | 否 | 退款原因，用于对账备注 |
| notify_url | string(255) | 否 | 退款结果通知地址，为空时沿用下单地址 |

请求示例：

```json
{
  "out_refund_no": "R202503180001",
  "order_id": "YF2025031800000123",
  "refund_amount": 64000,
  "reason": "客户申请退订半年期授权",
  "notify_url": "https://merchant.example.com/yunfan/refund-notify"
}
```

响应示例：

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "refund_id": "YFR2025031800000456",
    "out_refund_no": "R202503180001",
    "order_id": "YF2025031800000123",
    "refund_amount": 64000,
    "refund_fee": 384,
    "actual_refund_amount": 63616,
    "status": "PROCESSING",
    "estimated_arrival": "2025-03-20",
    "created_at": "2025-03-18T15:05:12+08:00"
  }
}
```

退款状态取值包括 `PROCESSING`（处理中）、`SUCCESS`（退款成功）、`FAILED`（退款失败）、`CLOSED`（退款关闭）。

## 错误码

平台错误码由 HTTP 状态码与业务错误码共同标识，业务错误码在所有接口中保持统一语义。错误响应体结构如下：

```json
{
  "code": 42202,
  "message": "退款金额超过订单可退金额",
  "request_id": "req_7f2c9a41b8e34d6f",
  "data": null
}
```

| HTTP 状态码 | 错误码 | 错误标识 | 说明 | 处理建议 |
| --- | --- | --- | --- | --- |
| 400 | 40001 | INVALID_PARAM | 请求参数缺失或格式错误 | 按 message 提示修正参数后重试 |
| 401 | 40101 | SIGNATURE_INVALID | 签名校验失败 | 核对签名拼接顺序与 AppSecret |
| 401 | 40102 | TIMESTAMP_EXPIRED | 时间戳偏差超过 300 秒 | 同步服务器时间后重新发起请求 |
| 403 | 40301 | PERMISSION_DENIED | 应用未开通该接口权限 | 联系商务经理申请开通 |
| 404 | 40401 | ORDER_NOT_FOUND | 订单或退款单不存在 | 核对单号后重新查询 |
| 409 | 40901 | DUPLICATE_REQUEST | 幂等键重复提交 | 使用原单号查询最终处理结果 |
| 422 | 42201 | ORDER_STATUS_INVALID | 订单当前状态不允许该操作 | 先查询订单状态再决定后续动作 |
| 422 | 42202 | REFUND_AMOUNT_EXCEEDED | 退款金额超过可退金额 | 调整退款金额，累计不得超过 128000 分 |
| 429 | 42901 | RATE_LIMIT_EXCEEDED | 请求超过 600 次/分钟限流 | 退避重试，建议重试间隔不小于 1 秒 |
| 500 | 50001 | SYSTEM_ERROR | 平台内部处理异常 | 记录 request_id，稍后幂等重试 |
| 503 | 50301 | SERVICE_UNAVAILABLE | 服务暂时不可用 | 采用指数退避重试，最多 3 次 |

如遇 50001 或 50301 错误，请勿更换商户订单号重复下单，应在重试前通过查询订单接口确认订单是否已创建。所有错误响应均返回 `request_id`，商户在提交工单时须一并提供该字段及请求时间，以便平台在 1 个工作日内完成定位与反馈。
