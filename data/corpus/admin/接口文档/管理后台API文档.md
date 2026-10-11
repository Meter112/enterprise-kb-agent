# 管理后台API文档

## 概述

云帆科技管理后台 API（以下简称“本 API”）用于企业内部管理平台与统一身份、权限中心、审计中心之间的数据交互。本文档适用于研发、测试、运维及经授权的交付人员。当前版本为 v1.0.0，基础地址为 `https://admin.yunfan-tech.com/api/v1`。

除登录、刷新令牌等免认证接口外，调用方须在请求头携带 `Authorization: Bearer <access_token>`。访问令牌有效期为 7200 秒，刷新令牌有效期为 30 天。请求与响应默认采用 `application/json; charset=UTF-8`，服务端时间统一为 UTC+8。统一响应结构如下：`code` 为业务状态码，`message` 为提示信息，`data` 为业务数据，`request_id` 为本次请求链路标识。成功时 `code=0`；失败时 `code` 非 0，并应结合 HTTP 状态码判断。

接口默认限流 100 次/分钟，单应用每日调用配额为 50000 次。超限返回 HTTP 429，业务码 42901。分页参数默认每页 20 条，最大 100 条。金额字段单位均为分，例如 `19900` 表示 199.00 元。权限变更缓存为 300 秒，原则上 5 分钟内全网生效。审计日志在线保留 180 天，超期归档至冷存储，归档查询时限为 3 个工作日。常用错误码如下：

| 业务码 | HTTP 状态码 | 说明 |
| --- | --- | --- |
| 0 | 200 | 成功 |
| 40001 | 400 | 参数校验失败 |
| 40101 | 401 | 凭证无效或已过期 |
| 40301 | 403 | 权限不足 |
| 40401 | 404 | 资源不存在 |
| 42901 | 429 | 请求超出限流 |
| 50001 | 500 | 系统内部错误 |

## 用户管理接口

### 接口说明

本接口用于在管理后台创建内部用户。调用方须具备 `user:create` 权限。用户名、手机号在系统内须唯一。初始密码有效期 90 天，首次登录须强制修改。账号有效期默认 365 天，最长不得超过 1095 天。

**请求方法**：`POST /api/v1/admin/users`

**请求参数**：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| username | string | 是 | 登录名，4—20 位，支持字母、数字、下划线 |
| real_name | string | 是 | 真实姓名，2—20 个汉字或字符 |
| mobile | string | 是 | 手机号，11 位中国大陆号码 |
| email | string | 否 | 企业邮箱，须为 `@yunfan-tech.com` 域名 |
| department_id | string | 是 | 部门 ID，如 `dept_1001` |
| role_ids | array[string] | 是 | 角色 ID 列表，最多 10 个 |
| initial_password | string | 否 | 初始密码，默认 `Yunfan@2025` |
| status | string | 否 | 用户状态，`enabled` 或 `disabled`，默认 `enabled` |
| expire_days | integer | 否 | 账号有效期天数，默认 365，最大 1095 |
| balance_cent | integer | 否 | 账户余额，单位分，默认 0 |
| remark | string | 否 | 备注，最多 200 字 |

**请求示例**：

```json
{
  "username": "zhangsan",
  "real_name": "张三",
  "mobile": "13800138000",
  "email": "zhangsan@yunfan-tech.com",
  "department_id": "dept_1001",
  "role_ids": ["role_ops", "role_audit_viewer"],
  "initial_password": "Yunfan@2025",
  "status": "enabled",
  "expire_days": 365,
  "balance_cent": 19900,
  "remark": "运营中心新员工"
}
```

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "user_id": "U20250612001",
    "username": "zhangsan",
    "status": "enabled",
    "expire_at": "2026-06-12T23:59:59+08:00",
    "balance_cent": 19900,
    "created_at": "2025-06-12T10:30:00+08:00"
  },
  "request_id": "req_20250612103000_abc123"
}
```

## 权限管理接口

### 接口说明

本接口用于更新指定角色的权限集合与数据范围。调用方须具备 `role:update` 权限。权限码单次最多提交 200 个，变更原因不得少于 10 个汉字。权限缓存有效期为 300 秒，变更后原则上 5 分钟内生效。授权有效期默认 365 天，最长 730 天。

**请求方法**：`PUT /api/v1/admin/roles/{role_id}/permissions`

**请求参数**：

| 参数名 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| role_id | path | string | 是 | 角色 ID，如 `role_ops` |
| permission_codes | body | array[string] | 是 | 权限码列表，最多 200 个 |
| data_scope | body | string | 是 | 数据范围：`all`、`dept_and_child`、`dept`、`self` |
| effective_days | body | integer | 否 | 授权有效期天数，默认 365，最大 730 |
| operator_id | body | string | 是 | 操作人用户 ID，如 `U20250001` |
| reason | body | string | 是 | 变更原因，不少于 10 个汉字 |

**请求示例**：

```json
{
  "permission_codes": [
    "user:read",
    "user:create",
    "role:update",
    "audit:read",
    "audit:export"
  ],
  "data_scope": "dept_and_child",
  "effective_days": 365,
  "operator_id": "U20250001",
  "reason": "调整运营中心角色权限范围"
}
```

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "role_id": "role_ops",
    "permission_codes": [
      "user:read",
      "user:create",
      "role:update",
      "audit:read",
      "audit:export"
    ],
    "data_scope": "dept_and_child",
    "effective_at": "2025-06-12T11:00:00+08:00",
    "expire_at": "2026-06-12T23:59:59+08:00",
    "cache_expire_seconds": 300,
    "updated_at": "2025-06-12T11:00:00+08:00"
  },
  "request_id": "req_20250612110000_def456"
}
```

## 审计日志接口

### 接口说明

本接口用于分页查询管理后台审计日志。调用方须具备 `audit:read` 权限；导出操作须额外具备 `audit:export` 权限。在线日志保留 180 天，单次查询时间跨度不得超过 31 天，单次导出不得超过 10000 条。日志内容不得篡改，如发现异常须在 2 小时内通过安全工单上报。

**请求方法**：`GET /api/v1/admin/audit-logs`

**查询参数**：

| 参数名 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| page | integer | 否 | 页码，默认 1 |
| page_size | integer | 否 | 每页条数，默认 20，最大 100 |
| operator | string | 否 | 操作人 ID 或姓名 |
| action | string | 否 | 操作类型，如 `user.create`、`role.update` |
| resource_type | string | 否 | 资源类型：`user`、`role`、`audit_log` |
| result | string | 否 | 结果：`success`、`fail` |
| start_time | string | 否 | 开始时间，ISO 8601，如 `2025-06-01T00:00:00+08:00` |
| end_time | string | 否 | 结束时间，ISO 8601，与开始时间跨度不超过 31 天 |
| order | string | 否 | 排序：`asc`、`desc`，默认 `desc` |

**响应示例**：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 2,
    "page": 1,
    "page_size": 20,
    "retention_days": 180,
    "items": [
      {
        "log_id": "AUD202506121030001",
        "operator": "U20250001",
        "action": "user.create",
        "resource_type": "user",
        "resource_id": "U20250612001",
        "result": "success",
        "ip": "10.20.30.40",
        "created_at": "2025-06-12T10:30:00+08:00",
        "detail": {
          "username": "zhangsan",
          "expire_days": 365
        }
      },
      {
        "log_id": "AUD202506121100002",
        "operator": "U20250001",
        "action": "role.update",
        "resource_type": "role",
        "resource_id": "role_ops",
        "result": "success",
        "ip": "10.20.30.40",
        "created_at": "2025-06-12T11:00:00+08:00",
        "detail": {
          "permission_count": 5,
          "data_scope": "dept_and_child"
        }
      }
    ]
  },
  "request_id": "req_20250612110500_ghi789"
}
