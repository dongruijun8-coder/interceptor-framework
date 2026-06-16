# 截流框架 config.json 生成规范

你是逆向分析 skill，输出目标是生成一份可直接被截流框架加载的 `config.json`。框架路径: `framework/core/client.py` → `Client(config_path)`。

## 输出格式

```json
{
  "meta": {
    "app_name": "小写英文标识",
    "package": "com.example.app",
    "version": "1.0.0",
    "config_schema": "2.0"
  },
  "server": {
    "base_url": "https://api.example.com",
    "default_headers": {}
  },
  "pipeline": {},
  "frida": {},
  "endpoints": {},
  "runtime_config": {}
}
```

### pipeline 快捷方式: recipe

如果 pipeline 四件套匹配已知模式，可用 `recipe` 一键展开:

```json
"pipeline": { "recipe": "sybl-pattern" }
```

可用 recipe（定义在 `framework/core/recipes.py`）:
- `sybl-pattern` — aes-cbc + xor-triple-sign + password-login + frida-rpc
- `simple-rest` — 全 plaintext + header-token + rest-json
- `rongcloud` — plaintext + header-token + rongcloud-tcp

用 recipe 时仍可覆盖个别处理器:

```json
"pipeline": {
  "recipe": "sybl-pattern",
  "auth": { "plugin": "sms-login", "params": { ... } }  // 覆盖 recipe 的 password-login
}
```

不匹配任何 recipe → 全量手写 pipeline。

----------

## pipeline 四件套

框架通过 pipeline 拼接处理器链: **加密 → 签名 → 认证 → 私信**

### 1. encryption — 请求体加密 / 响应体解密

可用插件:

**`plaintext`** — 无加密，字符串速记即可:
```json
"encryption": "plaintext"
```

**`aes-cbc`** — AES-256-CBC，通过 Frida 动态获取 session key:
```json
"encryption": {
  "plugin": "aes-cbc",
  "params": {
    "key": null,                // null=动态获取, 或 32字节 hex
    "iv": null,                 // null=用 clientSession[:16]
    "key_derivation": "session_key"  // 或 "device_token" | "clientsession" | null(静态key)
  }
}
```

如果你分析出的是 AES-128-ECB / DES / RSA 等不在列表中的算法 → 填 `"encryption": "plaintext"` 并在此处标注:

```json
"encryption_notes": "AES-128-ECB, key=vt5i9pn9dwxj8na8, PKCS7, body层+data层两层解密"
```

----------

### 2. signing — 请求签名

可用插件:

**`plaintext`** — 无签名，字符串速记即可

**`xor-triple-sign`** — p1/p2/p3 XOR 签名（sybl 风格）。登录前三者相同，登录后 p2 = p1 XOR key, p3 = p2 XOR p3_key。**签名输出到 HTTP header（p1/p2/p3/timestamp）**:
```json
"signing": {
  "plugin": "xor-triple-sign",
  "params": {
    "read_key": "01528e5f",     // 4字节 hex, 读请求
    "write_key": "015357de",    // 4字节 hex, 写请求
    "p3_key": "0001d981"        // 4字节 hex, p2→p3 的 XOR key
  }
}
```
注意: read/write 端点分类在 `framework/core/processors/signing/xor_triple.py` 的 `WRITE_ENDPOINTS` 集合中，需要根据实际 app 调整。

**`sha1-sorted-kv`** — 参数按 key 字母排序 + SHA1/MD5 + 追加 secret（hifun 风格）。**签名输出到 URL query string**:
```json
"signing": {
  "plugin": "sha1-sorted-kv",
  "params": {
    "secret_key": "your-secret",
    "hash": "sha1",                   // 或 "sha256"
    "output_format": "uppercase_hex", // 或 "lowercase_hex"
    "excluded_params": ["pub_sign", "pub_uid", "model", "os"],
    "sign_param": "sign",             // 签名输出参数名
    "nonce_param": "nonce",           // 随机数参数名(可选)
    "nonce_length": 32,               // 随机数长度(可选)
    "timestamp_param": "timestamp",   // 时间戳参数名(可选)
    "access_key_param": "access_key", // access key参数名(可选)
    "access_key_value": "xxx"         // access key值(可选)
  }
}
```

**`md5-h5-sign`** — 简单 MD5 模板拼接（wefun 风格）。**签名输出到 URL query string**:
```json
"signing": {
  "plugin": "md5-h5-sign",
  "params": {
    "secret": "your-secret",
    "pattern": "{uid}{secret}{ts}",  // 签名串模板
    "uid_field": "h_m",              // UID 参数名
    "ts_field": "ts",                // 秒时间戳参数名
    "ts_ms_field": "h_ts",           // 毫秒时间戳参数名(可选)
    "sign_field": "h_sn"             // 签名输出参数名
  }
}
```

**签名输出位置说明**:
- `xor-triple-sign` → HTTP headers (p1/p2/p3/timestamp)
- `sha1-sorted-kv` / `md5-h5-sign` → URL query string (&sign=xxx&ts=yyy)
- 这决定了签名参数在请求的哪里出现，分析抓包时注意区分

如果你分析出的签名算法不在列表 → 填 `"signing": "plaintext"` 并标注:
```json
"signing_notes": "MD5(sorted params + &key=sign_key).upper(), headers: pub_sign/pub_timestamp/pub_sid"
```

----------

### 3. auth — 认证/登录

可用插件:

**`plaintext`** — 不认证，字符串速记

**`manual-token`** — 手动填入 token/uid（runtime.json → credentials）:
```json
"auth": "manual-token"
```

**`header-token`** — 从 runtime.json 读 token，注入到自定义 header:
```json
"auth": {
  "plugin": "header-token",
  "params": {
    "header_name": "access-token",    // 注入到哪个 header
    "token_field": "token",           // runtime.json 中的字段名
    "uid_field": "uid"
  }
}
```

**`password-login`** — 账号密码登录:
```json
"auth": {
  "plugin": "password-login",
  "params": {
    "endpoint": "/UI/PasswordLoginPage/passwordLogin",
    "fields": { "phone": "phone", "password": "password" },
    "response_mapping": {
      "token": "token",                   // 响应中 token 路径
      "uid": "id",                        // 响应中 uid 路径
      "rongcloud_token": "rongCloudToken" // 额外提取(可选)
    }
  }
}
```
`response_mapping` 支持点路径: `"token": "data.user.token"`。

**`sms-login`** — 短信验证码登录:
```json
"auth": {
  "plugin": "sms-login",
  "params": {
    "login_endpoint": "/api/user/login/sms",
    "response_mapping": { "token": "data.access_token", "uid": "data.user_id" }
  }
}
```

----------

### 4. messaging — 发送私信

可用插件:

**`none`** — 不发送，字符串速记

**`rest-json`** — HTTP REST 接口发送:
```json
"messaging": {
  "plugin": "rest-json",
  "params": {
    "precheck_path": "/im/msg/preCheck",   // 预检(可选)
    "send_path": "/im/msg/send"            // 发送路径
  }
}
```
或使用模板模式（在 endpoints 中定义 send_message 端点）。

**`frida-rpc`** — 通过 Frida RPC 调用 app 内 SDK 发送（融云/腾讯IM/网易NIM）:
```json
"messaging": {
  "plugin": "frida-rpc",
  "params": {
    "script_name": "hook_send_msg.js"      // Frida JS hook 脚本(放在 app 目录)
  }
}
```

注意:
- 当 messaging 为 frida-rpc 时，必须同时在 app 目录提供对应的 `.js` hook 脚本。
- 脚本需实现 `sendMessage(targetUid, text)` 的 Frida RPC exports。
- 有两种模式:
  - **单脚本模式**: 同一个 JS 文件既抓 AES key 又发消息（如 sybl 的 `frida_key_bridge.js`）— 脚本名填在 encryption 和 messaging 两处
  - **双脚本模式**: 抓 key 和发消息分开两个 JS 文件 — encryption 用一个，messaging 用另一个

----------

## endpoints — API 端点定义

```json
{
  "endpoints": {
    "all_rooms": {
      "path": "/api/room/list",
      "method": "POST",                      // "POST" | "GET" (默认 POST)
      "body": { "page": "{{page}}", "page_size": 20 },
      "pagination": {
        "type": "page_number",    // "page_number" | "offset_limit" | "cursor_offset"
        "size": 20,
        "stop_on": "empty_list"   // "empty_list" | "max_pages:10" | "total_count"
      },
      "response_path": "data.list",        // 数据在响应中的路径(可选, 默认 "data.list")
      "output_mapping": {
        "id": "room_id",                   // 输出字段: 响应中路径
        "name": "room_name"
      }
    },
    "join_room": {
      "path": "/api/room/join",
      "method": "POST",
      "body": { "roomId": "{{room.id}}", "password": "" },
      "note": "per_room 类型的 user_source 需要 join 房间后才能拉榜单。如果 app 无此要求可省略"
    },
    "ranking": {
      "path": "/api/rank/list",
      "method": "POST",                      // GET 请求可设 "method": "GET"
      "body": {
        "room_id": "{{room.id}}",
        "mode": "{{data_source_key}}",
        "type": "{{period_key}}",
        "page": "{{page}}",
        "page_size": 20
      },
      "pagination": { "type": "page_number", "size": 20, "stop_on": "empty_list" },
      "response_path": "data.list",           // 默认值即 "data.list"，不填也行
      "output_mapping": {
        "uid": "user.uid",                 // 支持点路径
        "nick": "user.nickname",
        "amount": "score"
      }
    },
    "send_message": {
      "path": "/api/im/send",
      "method": "POST",
      "body": {
        "to_uid": "{{to_uid}}",
        "content": "{{message}}",
        "type": "TEXT"
      },
      "note": "仅 rest-json messaging 使用。template 变量见 messaging 章节"
    }
  }
}
```

**模板变量**:
- `{{page}}` — 自动注入页码
- `{{room.id}}`, `{{room.name}}` — 当前房间
- `{{data_source_key}}` — 当前数据源值 (如 "rich")
- `{{period_key}}` — 当前周期值 (如 "day")
- `{{gender_key}}` — 当前性别值 (如 1)
- `{{uid}}`, `{{token}}`, `{{device_id}}`, `{{uid_str}}` — 身份信息（自动注入）
- `{{to_uid}}`, `{{message}}` — send_message 专用
- `{{ts_ms}}`, `{{uuid_v4}}` — 毫秒时间戳/随机UUID（send_message 专用）
- 自定义变量通过 `{{varname}}` 引用

**pagination 类型**:
- `page_number` — body 使用 `page`/`page_size`
- `offset_limit` — body 使用 `offset`/`limit`
- `cursor_offset` — 使用 `cursor`/`offset` (响应需含 `cursor` 字段)

**stop_on**:
- `empty_list` — 返回空列表时停止
- `max_pages:10` — 最多翻10页
- 不填 — 翻到无数据

**response_path 默认**: 不填时默认走 `data.list`。如果你的 app 响应结构不同（如 `{"result": {"items": [...]}}`），必须显式指定 `"response_path": "result.items"`。

**method 字段**: 支持 `"POST"` 和 `"GET"`，默认 POST。GET 请求的参数放到 URL query string，不加密 body。

----------

## user_sources — 用户数据来源

```json
{
  "user_sources": {
    "房间榜单": {
      "endpoint": "ranking",
      "type": "per_room",                 // "per_room" | "global"
      "filters": ["data_source", "period", "gender"]
    },
    "首页总榜": {
      "endpoint": "global_ranking",
      "type": "global",
      "filters": ["data_source", "period"]
    }
  }
}
```

- `type: "per_room"` — 每个房间分别拉取
- `type: "global"` — 一次拉取全站用户
- `filters` — 前端展示哪些筛选器

----------

## runtime_config — 筛选器选项 + 消息模板

```json
{
  "runtime_config": {
    "data_sources": {
      "贡献榜": "rich",
      "魅力榜": "charm"
    },
    "periods": {
      "日榜": "day",
      "周榜": "week",
      "月榜": "month"
    },
    "genders": {
      "全部": null,
      "男": 1,
      "女": 2
    },
    "templates": [
      "{nick} 你好~",
      "想跟你说点悄悄话"
    ]
  }
}
```

- `data_sources` — 前端标签 → API 请求体中的值 (`{{data_source_key}}`)。**值可以是字符串或数字**（如 `"rich"` 或 `6`），按抓包实际值填写
- `periods` — 前端标签 → API 请求体中的值 (`{{period_key}}`)。同上，值类型按实际
- `genders` — 前端标签 → API 请求体中的值 (`{{gender_key}}`)，null 表示不传
- `templates` — 消息模板，`{nick}` 和 `{room_name}` 自动替换

**筛选器按实际声明，不要编造**: 如果 app 只有日榜/月榜、没有周榜，periods 就两条。如果 app 有特殊榜单（如"头条榜""灵魂伴侣榜"），就如实列出。前端自适应渲染，有几条显示几条。

**参数名按实际**: `{{data_source_key}}` / `{{period_key}}` 是值占位符，参数名叫什么取决于 app。例如:
```json
// 梦音: 参数名 type + subType，值是数字
"body": { "type": "{{data_source_key}}", "subType": "{{period_key}}" }
```
```json
// sybl: 参数名 mode + rank_type，值是字符串
"body": { "mode": "{{data_source_key}}", "rank_type": "{{period_key}}" }
```

----------

## default_headers — 全局请求头

```json
"default_headers": {
  "ClientType": "Android",
  "DeviceType": "Samsung SM-S9280",
  "AppVersion": "2.47.1",
  "Content-Type": "application/json; charset=utf-8",
  "User-Agent": "Mozilla/5.0 ... dreamAppAndroid"
}
```

全局不变的 header 放这里。签名/认证产生的动态 header（token/sign/timestamp）由 processor 自动注入。

----------

## 输出要求

1. **完整可加载** — 框架 `Client(config.json)` 能初始化成功
2. **真实值** — 所有密钥/secret/url 从逆向报告中提取，不填占位符
3. **标注缺口** — 分析不到的部分用 `_notes` 字段说明，不要编造
4. **endpoints 至少包含** — all_rooms、ranking、join_room（如果 app 需要 join）、send_message（如果 messaging=rest-json）
5. **pipeline 四件套必须明确指定** — 不采用默认值（默认值 behavior 不可预测）
6. **标注成功码** — 逆向分析时记录成功的 `code` 值。框架默认识别 `code ∈ {200, "S_OK", 0}` / `status == 0` / `ret == 1`。如果你的 app 使用不同成功码（如 `code: 1` 或 `code: "SUCCESS"`），在 config 中明确标注以便后续调整框架

----------

## frida — 设备环境配置

当 messaging 或 encryption 使用 frida-rpc/aes-cbc 动态 key 时需要:

```json
"frida": {
  "enabled": true,
  "device": "usb",
  "package": "com.example.app",
  "script": "frida_key_bridge.js",
  "modules": [
    { "name": "secret_key_spec", "params": {} },
    { "name": "okhttp", "params": {} }
  ],
  "rpc_methods": [
    "getSessionKey",
    "getHeaders",
    "sendMessage",
    "getStatus"
  ]
}
```

- `script` — 主 bridge 脚本（单脚本模式下兼做 key capture + messaging）
- `modules` — 脚本内注册的模块列表，框架按需加载
- `rpc_methods` — 脚本暴露的 RPC 方法
- 框架 dashboard 的 `EnvChecker.probe()` 通过此配置做健康检查
