# 逆向 Skill 标准输出规范

逆向完成一个 App 后，输出 `<app_name>.zip` 压缩包，Dashboard 上传即用。

---

## 交付格式

**`<app_name>.zip`**，解压后目录结构：

```
<app_name>/
├── config.json          [必填]
├── runtime.json         [必填，模板]
└── hook_send_msg.js     [可选，仅 frida-rpc 时需要]
```

Dashboard 上传 zip → 自动解压到 `apps/<app_name>/` → 用户填 runtime.json 凭据 → 运行。

---

## 输出清单

| 文件 | 产出方 | 用途 |
|------|--------|------|
| `config.json` | 逆向 skill | 框架核心配置，直接消费 |
| `runtime.json` | 逆向 skill 生成模板，用户 Dashboard 填写 | 运行时参数 |
| `hook_send_msg.js` | 逆向 skill | Frida IM 私信 RPC 脚本，仅 frida-rpc 时需要 |

---

## 一、config.json

### 完整 Schema

```json
{
  "meta": {
    "app_name": "",          // [必填] App 显示名，如 "hifun"
    "package": "",           // [必填] 包名，如 "chat.hifun.android"
    "version": "",           // [必填] 版本号
    "config_schema": "2.0"   // [必填] 固定 "2.0"
  },

  "server": {
    "base_url": "",          // [必填] API 根地址
    "default_headers": {}    // [必填] 每个请求都带的 headers，至少要有 User-Agent
  },

  "pipeline": {
    "encryption": {},        // [必填] 加密方案
    "signing": {},           // [必填] 签名方案
    "auth": {},              // [必填] 认证方案
    "messaging": {}          // [必填] 私信方案
  },

  "endpoints": {
    "all_rooms": {},         // [必填] 获取房间列表
    "ranking": {}            // [必填] 获取房间排行用户
  },

  "runtime_config": {        // [必填] 默认值，Dashboard 可覆盖
    "data_sources": {},      // 数据源映射，如 {"富豪榜": "rich"}
    "periods": {},           // 周期映射，如 {"今日": "day", "本周": "week"}
    "genders": {}            // 性别映射，如 {"全部": null, "男": 1, "女": 2}
  }
}
```

### meta

```json
{
  "app_name": "hifun",
  "package": "chat.hifun.android",
  "version": "1.44.0",
  "config_schema": "2.0"
}
```

### server

```json
{
  "base_url": "https://api.hifunclub.com",
  "default_headers": {
    "User-Agent": "(hifun) 116 (1.44.0) android Samsung SM-S9210 android 12",
    "client-platform": "android",
    "app-version": "1.44.0",
    "Content-Type": "application/json; charset=utf-8"
  }
}
```

### pipeline — 四类处理器

每种处理器有对应的 plugin 名和 params。框架已内置以下 plugin：

| 类别 | plugin | 适用场景 |
|------|--------|---------|
| encryption | `plaintext` | 明文，无加密 |
| encryption | `aes-cbc` | AES-256-CBC，需要 key + iv |
| signing | `plaintext` | 无签名 |
| signing | `sha1-sorted-kv` | 参数排序拼接 secret_key 后 SHA1 |
| signing | `xor-triple-sign` | 三组密钥 XOR 滚动签名 |
| auth | `manual-token` | 手动填入 token |
| auth | `header-token` | 从 HTTP header 传 token |
| auth | `password-login` | 密码/验证码登录 |
| messaging | `rest-json` | 纯 HTTP API 发私信 |
| messaging | `frida-rpc` | Frida 注入 TencentIM 等 SDK |
| messaging | `rongcloud-tcp` | 融云 TCP 私信 |
| messaging | `none` | 不发送（仅扫描模式） |

**简写：** 无参时可用字符串代替对象，如 `"encryption": "plaintext"` 等价于 `"encryption": { "plugin": "plaintext" }`

#### 示例

```json
// 明文 + 无签名 + header传token + Frida私信 (hifun)
"pipeline": {
  "encryption": "plaintext",
  "signing": {
    "plugin": "sha1-sorted-kv",
    "params": {
      "secret_key": "83cdd18ed05da8268e0e121a1f9a4007",
      "secret_key_placement": "append",
      "hash": "sha1",
      "output_format": "uppercase_hex",
      "param_sort": "alphabetical_by_key",
      "nonce_param": "nonce",
      "nonce_length": 30,
      "timestamp_param": "timestamp",
      "access_key_param": "access_key",
      "access_key_value": "0cf4a77a4428f606a3897adb533abc4a",
      "sign_param": "sign"
    }
  },
  "auth": {
    "plugin": "header-token",
    "params": {
      "header_name": "access-token",
      "token_source": "shared_prefs"
    }
  },
  "messaging": {
    "plugin": "frida-rpc",
    "params": {}
  }
}

// AES加密 + XOR签名 + 密码登录 + 融云私信 (双鱼)
"pipeline": {
  "encryption": {
    "plugin": "aes-cbc",
    "params": { "key": "BASE64_KEY", "iv": "IV_STRING" }
  },
  "signing": {
    "plugin": "xor-triple-sign",
    "params": { "read_key": "01528e5f", "write_key": "015357de", "p3_key": "0001d981" }
  },
  "auth": {
    "plugin": "password-login",
    "params": {
      "endpoint": "/UI/PasswordLoginPage/passwordLogin",
      "fields": { "phone": "phone", "password": "password" },
      "response_mapping": { "token": "token", "uid": "id" }
    }
  },
  "messaging": {
    "plugin": "rongcloud-tcp",
    "params": { "app_key": "m7ua80gbmdddm" }
  }
}
```

### endpoints

#### all_rooms — 两种模式

**单端点模式**（直接分页拉列表）：

```json
"all_rooms": {
  "path": "/api/room/list",
  "method": "POST",
  "body": { "offset": "{{offset}}", "limit": 20 },
  "response_path": "data.list",
  "pagination": { "type": "offset_limit", "size": 20, "stop_on": "empty_list" },
  "output_mapping": {
    "id": "room_id",
    "name": "room_name"
  }
}
```

**多步骤模式**（先拉分类，再按分类拉房间）：

```json
"all_rooms": {
  "steps": [
    {
      "name": "categories",
      "path": "/UI/Room/Home/categoryList",
      "method": "POST",
      "body": {},
      "response_path": "data"
    },
    {
      "name": "room_list",
      "path": "/UI/Room/Home/roomList",
      "method": "POST",
      "body": { "id": "{{_iter.id}}", "page": 1, "page_size": 20 },
      "iter_source": "categories.list",
      "pagination": { "type": "page_number", "size": 20, "stop_on": "empty_list" }
    }
  ],
  "output_mapping": {
    "id": "id",
    "name": "name"
  }
}
```

#### ranking

```json
"ranking": {
  "path": "/api/room/billboard",
  "method": "GET",
  "body": { "room_id": "{{room.id}}" },
  "response_path": "data.list",
  "pagination": { "type": "none" },
  "output_mapping": {
    "uid": "user_info.userid",
    "nick": "user_info.nickname",
    "gender": "user_info.gender",
    "amount": "score"
  }
}
```

**多榜单支持**（富豪日榜/周榜切 period 时自动选不同 response_path）：

```json
"ranking": {
  "path": "/api/room/billboard",
  "method": "GET",
  "body": { "room_id": "{{room.id}}" },
  "output_mapping": {
    "uid": "user_info.userid",
    "nick": "user_info.nickname",
    "gender": "user_info.gender",
    "lists": {
      "rich_day": {
        "response_list": "data.rich_day_billboard_list",
        "amount_field": "rich_value"
      },
      "rich_week": {
        "response_list": "data.rich_week_billboard_list",
        "amount_field": "rich_value"
      }
    }
  }
}
```

`lists` 的 key 格式：`<data_source_key>_<period_key>`。框架选择时优先匹配，未匹配到则用 `response_path` 默认值。

### 分页类型

| type | 行为 | 内置变量 | 说明 |
|------|------|---------|------|
| `"none"` | 不分页，单次请求 | — | — |
| `"offset_limit"` | offset 从 0 递增 | `{{offset}}` | body 中需包含 offset/limit |
| `"page_number"` | page 从 1 递增 | `{{page}}` | body 中需包含 page/page_size |

`stop_on: "empty_list"` 表示返回空列表时停止。

### output_mapping 映射规则

左边是框架统一字段名，右边是 API 原始字段名。三优先级：

| 右边值特征 | 行为 | 示例 |
|-----------|------|------|
| 包含 `{{...}}` | 模板替换 | `"category": "{{_iter.name}}"` |
| 包含 `.` 且不以 `{{` 开头 | JSON path 取值 | `"name": "user_info.nickname"` → `item["user_info"]["nickname"]` |
| 纯字符串，不含 `.` | API 字段直取 | `"id": "room_id"` → `item["room_id"]` |

**必须映射的字段：**

| endpoint | 必须映射 |
|----------|---------|
| all_rooms | `id`, `name` |
| ranking | `uid`, `nick`, `gender` |

### 模板变量全集

| 变量 | 可用范围 | 说明 |
|------|---------|------|
| `{{room.id}}` | ranking body | 当前房间 id |
| `{{room.name}}` | ranking body | 当前房间名 |
| `{{room.xxx}}` | ranking body | 房间对象任意字段（来自 all_rooms output_mapping） |
| `{{offset}}` | all_rooms body | 分页偏移量 |
| `{{page}}` | all_rooms body | 分页页码 |
| `{{data_source_key}}` | ranking body | runtime_config 映射值，如"富豪榜"→"rich" |
| `{{period_key}}` | ranking body | runtime_config 映射值，如"今日"→"day" |
| `{{_iter.xxx}}` | all_rooms steps | 多步骤模式下当前迭代对象的字段 |

### runtime_config

```json
"runtime_config": {
  "data_sources": { "富豪榜": "rich", "魅力榜": "charm" },
  "periods": { "今日": "day", "本周": "week", "本月": "month" },
  "genders": { "全部": null, "男": 1, "女": 2 },
  "templates": ["{nick} 你好~"]
}
```

- `data_sources`: 中文名 → 传给 API 的 key。用户运行时选一个。
- `periods`: 同上，周期选择。
- `genders`: 中文名 → API 性别值。`null` 表示不过滤。
- `templates`: 默认话术，`{nick}` 会自动替换为用户昵称。

---

## 二、hook_send_msg.js

仅当 `messaging.plugin = "frida-rpc"` 时需要。注入目标 App 的 IM SDK，暴露 RPC 接口。

### 最小实现

```javascript
// 轮询等待 IM SDK 就绪
var installed = false;

function doInstall() {
  if (installed) return;
  var V2TIMManager = Java.use("com.tencent.imsdk.v2.V2TIMManager");
  var manager = V2TIMManager.getInstance();

  rpc.exports = {
    sendText: function(uid, text) {
      var result = {};
      var msgManager = V2TIMManager.getMessageManager();
      // ... 构造消息、发送 ...
      return JSON.stringify(result);  // 必须返回 JSON 字符串
    }
  };
  installed = true;
}

setInterval(function() {
  try { doInstall(); } catch(e) {}
}, 200);
```

### 接口约定

```javascript
rpc.exports = {
  sendText: function(uid, text) {
    // uid: string, text: string
    // 同步返回 JSON string: {"success": true} 或 {"queued": true, "key": "xxx"}
    // 异步模式: 返回 queued+key，另暴露 pollResult(key) 返回最终结果
  },

  pollResult: function(key) {
    // [可选] 查询异步发送结果
    // 返回 JSON string: {"success": true, "msgId": "xxx"} 或 {"status": "pending"}
  }
};
```

---

## 三、runtime.json

逆向 skill 输出模板，用户通过 Dashboard 填写实际值。

```json
{
  "credentials": {
    "token": "",      // [用户填] access token
    "uid": 0          // [用户填] 当前用户 uid
  },
  "settings": {
    "send_interval": 5    // 发信间隔秒数
  },
  "data_sources": { "富豪榜": "rich" },
  "periods": { "今日": "day", "本周": "week" },
  "genders": { "全部": null, "男": 1, "女": 2 },
  "templates": [
    "想跟你说点悄悄话",
    "私下里的我，可比你想象中有趣多了"
  ],
  "device": {
    "serial": "",           // [用户填] ADB 设备序列号
    "app_package": "",      // [用户填] 包名
    "script_name": "hook_send_msg.js"
  }
}
```

`runtime.json` 中的 `data_sources`/`periods`/`genders` 覆盖 `config.json` 的 `runtime_config` 默认值。

---

## 完整示例

### 最简 App（明文、无签名、手动token、HTTP私信）

`config.json`:

```json
{
  "meta": {
    "app_name": "漂漂",
    "package": "com.pop.live",
    "version": "1.0",
    "config_schema": "2.0"
  },
  "server": {
    "base_url": "https://api.pp.weimipopo.com"
  },
  "pipeline": {
    "encryption": "plaintext",
    "signing": "plaintext",
    "auth": { "plugin": "manual-token", "params": { "token_field": "token", "uid_field": "uid" } },
    "messaging": { "plugin": "rest-json", "params": { "precheck_path": "/im/msg/preCheck", "send_path": "/im/msg/send" } }
  },
  "endpoints": {
    "all_rooms": {
      "path": "/room/main/listByCat",
      "method": "POST",
      "body": { "catId": 1, "offset": "{{offset}}", "limit": 20 },
      "pagination": { "type": "offset_limit", "size": 20, "stop_on": "empty_list" },
      "output_mapping": { "id": "unRoomId", "name": "roomName" }
    },
    "ranking": {
      "path": "/room/rank/list/contribute/rank",
      "method": "POST",
      "body": { "room_id": "{{room.id}}" },
      "pagination": { "type": "offset_limit", "size": 20, "stop_on": "empty_list" },
      "output_mapping": { "uid": "uid", "nick": "nick", "amount": "amount", "gender": "gender" }
    }
  },
  "runtime_config": {
    "data_sources": { "贡献榜": "contribute" },
    "periods": { "今日": "day", "本周": "week" },
    "genders": { "全部": null, "男": 1, "女": 2 }
  }
}
```

### 完整 App（签名、header-token、Frida IM）

见 `apps/hifun/config.json` 作为参考。

---

## 校验清单

逆向 skill 输出后自检：

- [ ] `config.json` JSON 语法有效
- [ ] `meta.app_name` 非空，`meta.config_schema` = `"2.0"`
- [ ] `server.base_url` 非空
- [ ] `pipeline` 四个处理器都有 plugin 名
- [ ] `endpoints.all_rooms` 存在，`output_mapping` 有 `id` 和 `name`
- [ ] `endpoints.ranking` 存在，`output_mapping` 有 `uid`、`nick`、`gender`
- [ ] `runtime_config.data_sources` / `periods` / `genders` 都有至少一个条目
- [ ] 如果 messaging 用 `frida-rpc`，`hook_send_msg.js` 存在且暴露出 `sendText` 方法
- [ ] 模板变量 `{{room.id}}` / `{{offset}}` 等在对应 body 中可用
- [ ] `body` 中不要出现 `{{room.xxx}}` 但 `xxx` 不在 `all_rooms.output_mapping` 中
