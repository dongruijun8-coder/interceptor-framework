# WeFun 闭环测试套件

日期: 2026-06-06
App: com.pico.live v11.2.6.2381

## 文件说明

| 文件 | 用途 |
|------|------|
| `closed_loop_test.py` | 可独立运行的闭环测试脚本 |
| `wefun-config.json` | 完整 API 配置（所有端点、auth、body schema） |

## 闭环链路

```
房间列表 ──→ 在线用户 ──→ 发私信
   │             │            │
   │  POST       │  POST      │  POST
   │  /recommend_app/app/v1/roomlist
   │             │  /room_v2/app/room/room_online_user
   │             │            │  /api/app/im/v1/conversation/send
   ✅           ✅          ✅
```

## 运行

```bash
NO_PROXY=* python closed_loop_test.py
```

预期输出三个 `ret=1`，最后显示 `send_message ✅`

## 端点详情

### 1. 房间列表
- URL: `POST https://api.hiyaparty.com/recommend_app/app/v1/roomlist`
- Auth: token + device_id in body，无签名
- 分页: `offset` 字段（字符串，首页空串），每页 16 间
- 分类: `kind_id`: 100002=热门, 100057=男生, 100039=女生

### 2. 在线用户
- URL: `POST https://api.hiyaparty.com/room_v2/app/room/room_online_user`
- Auth: 同上，无签名
- 返回: `data.list[]` 每项 `{mid, name}`

### 3. 发私信
- URL: `POST https://api.hiyaparty.com/api/app/im/v1/conversation/send`
- Auth: 同上，无签名
- Body: snake_case 扁平 + 嵌套混合结构
- URL 参数 `?sign=` 可选（服务端不验证）
- conv_id: `single_chat-{min(uid,target)}-{max(uid,target)}`

## Auth 说明

| 参数 | 位置 | 必填 |
|------|------|------|
| `h_m` / `token` / `h_did` | body root（房间列表、在线用户） | ✅ |
| `token` | body root（发私信） | ✅ |
| `sign` | query string（发私信） | ❌ 不验证 |
| `h_sn` | query + body（榜单等 H5 API） | ✅ 仅 H5 |

h_sn 签名公式: `MD5(h_m + secret + ts_seconds)`，secret = `a3f7e4c2b9d8a1e5f6c7d8e9a0b1c2d3`

## 认证凭证

```
UID         = 302207594
TOKEN       = T2K2N5CQJgSjTSCFlPDEsWTUBgnItXq4_bWxAyaIIitBtJjo0DBopP7ccfuA7wtBZzBjr
DEVICE_ID   = 7700f25aec05ac8b
```

## 已知限制

- 榜单端点: 账号被限制，`ret=1` 但 `data.list=null`
- IM 端点 2026-06-06 从 `/app/im/*` 迁移到 `/api/app/im/*`，旧路径 404
