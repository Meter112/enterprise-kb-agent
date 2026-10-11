# 内部订单API文档

## 概述

内部订单API面向云帆科技各业务系统，用于订单查询、创建与取消等核心操作。基础地址为 `https://api.yunfan-tech.internal/v1`，仅限公司内网及授权服务调用。接口采用 HTTPS 协议，请求与响应均为 `application/json`，字符编码 UTF-8。时间格式遵循 ISO 8601，时区为 `+08:00`，例如 `2025-06-18T10:30:00+08:00`。金额单位均为人民币元，保留两位小数。

统一响应结构如下：`code` 为业务状态码，`0` 表示成功；`message` 为提示信息；`data` 为业务数据。接口列表如下：

| 接口 | 方法 | 路径 | 权限 |
| --- | --- | --- | --- |
| 订单查询 | POST | `/v1/orders/query` | `order.read` |
| 订单创建 | POST | `/v1/orders` | `order.write` |
| 订单取消 | POST | `/v1/orders/{order_id}/cancel` | `order.cancel` |

## 鉴权

所有接口必须通过内部授权中心鉴权。调用方需在请求头携带 `Authorization: Bearer <access_token>`，并传入 `X-App-Id` 与 `X-Request-Id`。`access_token` 默认有效期为 7200 秒，建议在过期前 300 秒刷新。网关将校验令牌有效性、应用权限及请求重放标识。示例请求头如下：

```http
POST /v1/orders/query HTTP/1.1
Host: api.yunfan-tech.internal
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
X-App-Id: yunfan-oms
X-Request-Id: 0f8c1a2b-7d4e-4f6a-9c3d-202506180001
Content-Type: application/json
```

## 订单查询

调用 `POST /v1/orders/query` 可按订单 ID 或订单号查询订单详情。`order_id` 与 `order_no` 必须至少提供一个，同时提供时以 `order_id` 为准。请求示例：

```json
{
  "order_no": "ORD202506180001",
  "with_items": true
}
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "1002345678901234567",
    "order_no": "ORD202506180001",
    "customer_id": "CUST10086",
    "status": "PAID",
    "amount": 447.50,
    "paid_amount": 447.50,
    "currency": "CNY",
    "created_at": "2025-06-18T10:30:00+08:00",
    "paid_at": "2025-06-18T10:42:00+08:00",
    "items": [
      {
        "sku_id": "SKU-A100",
        "sku_name": "智能网关标准版",
        "quantity": 2,
        "unit_price": 199.00,
        "subtotal": 398.00
      },
      {
        "sku_id": "SKU-B200",
        "sku_name": "电源适配器",
        "quantity": 1,
        "unit_price": 59.50,
        "subtotal": 59.50
      }
    ]
  }
}
```

字段说明表：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| order_id | string | 否 | 订单 ID，雪花算法生成，与 order_no 二选一 |
| order_no | string | 否 | 订单号，对外展示唯一编号 |
| with_items | boolean | 否 | 是否返回商品明细，默认 false |
| status | string | 是 | 订单状态：PENDING_PAYMENT、PAID、PROCESSING、SHIPPED、COMPLETED、CANCELLED |
| amount | number | 是 | 订单应付金额 |
| paid_amount | number | 否 | 已支付金额 |
| currency | string | 是 | 币种，当前仅支持 CNY |

## 订单创建

调用 `POST /v1/orders` 创建订单。系统按商品明细、优惠金额和运费计算应付金额。创建成功后订单状态为 `PENDING_PAYMENT`，支付时限为 30 分钟。请求示例：

```json
{
  "customer_id": "CUST10086",
  "source": "YUNFAN_OMS",
  "currency": "CNY",
  "coupon_amount": 10.00,
  "freight_amount": 0.00,
  "remark": "内部测试订单",
  "items": [
    {
      "sku_id": "SKU-A100",
      "quantity": 2,
      "unit_price": 199.00
    },
    {
      "sku_id": "SKU-B200",
      "quantity": 1,
      "unit_price": 59.50
    }
  ]
}
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "1002345678901234567",
    "order_no": "ORD202506180001",
    "status": "PENDING_PAYMENT",
    "amount": 447.50,
    "currency": "CNY",
    "created_at": "2025-06-18T10:30:00+08:00",
    "expire_at": "2025-06-18T11:00:00+08:00"
  }
}
```

字段说明表：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| customer_id | string | 是 | 客户编号 |
| source | string | 是 | 来源系统标识，如 YUNFAN_OMS |
| currency | string | 是 | 币种，当前仅支持 CNY |
| coupon_amount | number | 否 | 优惠金额，默认 0.00 |
| freight_amount | number | 否 | 运费金额，默认 0.00 |
| items | array | 是 | 商品明细，至少一条 |
| items.sku_id | string | 是 | 商品编码 |
| items.quantity | integer | 是 | 购买数量，须大于 0 |
| items.unit_price | number | 是 | 商品单价 |
| amount | number | 是 | 应付金额，计算方式：商品总额 - 优惠金额 + 运费 |
| expire_at | string | 是 | 支付截止时间，创建后 30 分钟 |

## 订单取消

调用 `POST /v1/orders/{order_id}/cancel` 取消订单。未支付订单可随时取消；已支付且未发货订单，须在支付成功后 30 分钟内取消，退款比例为 100%；已发货订单不可取消。请求示例：

```json
{
  "order_id": "1002345678901234567",
  "reason": "客户误购",
  "operator_id": "U10086"
}
```

响应示例：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "order_id": "1002345678901234567",
    "order_no": "ORD202506180001",
    "status": "CANCELLED",
    "cancel_time": "2025-06-18T10:54:00+08:00",
    "refund_amount": 447.50,
    "refund_status": "REFUND_PROCESSING"
  }
}
```

字段说明表：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| order_id | string | 是 | 订单 ID，路径参数与请求体需一致 |
| reason | string | 是 | 取消原因，长度不超过 200 字符 |
| operator_id | string | 是 | 操作人编号 |
| status | string | 是 | 取消后订单状态为 CANCELLED |
| cancel_time | string | 是 | 取消时间 |
| refund_amount | number | 是 | 退款金额，已支付订单为实付金额，未支付为 0.00 |
| refund_status | string | 是 | 退款状态：NO_REFUND、REFUND_PROCESSING、REFUND_SUCCESS、REFUND_FAILED |

## 错误码

接口调用失败时，`code` 非 `0`，`message` 返回可读提示。错误响应示例：

```json
{
  "code": 40901,
  "message": "订单状态不允许取消",
  "data": null
}
```

常见错误码如下：

| 错误码 | HTTP 状态 | 说明 | 处理建议 |
| --- | --- | --- | --- |
| 40001 | 400 | 请求参数错误 | 检查必填字段、金额格式及数量范围 |
| 40101 | 401 | 未鉴权或令牌失效 | 重新获取 access_token 后重试 |
| 40301 | 403 | 权限不足 | 确认应用已开通对应权限范围 |
| 40401 | 404 | 订单不存在 | 核对 order_id 或 order_no |
| 40901 | 409 | 订单状态不允许取消 | 检查是否已发货或超过支付后 30 分钟 |
| 40902 | 409 | 重复请求 | 使用相同 X-Request-Id 查询原结果 |
| 42901 | 429 | 请求过于频繁 | 降低频率，建议按指数退避重试 |
| 50001 | 500 | 系统内部错误 | 记录 X-Request-Id 并联系订单平台值班人员 |
