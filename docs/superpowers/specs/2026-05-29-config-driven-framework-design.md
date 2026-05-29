# 配置驱动截流框架 — 设计方案

**日期**: 2026-05-29 | **版本**: v1.0 | **状态**: 待实现

---

## 1. 目标

消除每个 App 需要手写 `client.py` 的现状。用户通过 Dashboard 上传 JSON 配置文件即可接入新 App，框架自动解析并运行完整 Pipeline。

### 成功标准

- 新 App 接入：上传 JSON 配置 → 填写认证凭据 → 点"测试连接" → 可用
- 漂漂 App：用新配置文件完全替代现有 `client.py`，功能不降级
- 逆向 skill 产出：JSON 配置文件，可直接上传使用
- 处理器扩展：新增加密/签名/认证模式，只需新增一个处理器文件

---

## 2. 架构

### 2.1 核心变化

BaseClient 重构为配置驱动，不再需要子类：

```
现在:  config.json → client.py (手写3方法) → BaseClient (Pipeline)
改后:  config.json → BaseClient (读配置+加载处理器+执行Pipeline)
```

### 2.2 处理器管道

每个 HTTP 请求自动经过处理器链：

```
请求: body → encryption.encode → signing.sign → HTTP POST
响应: HTTP → signing.verify → encryption.decode → JSON
```

处理器分 4 类，每类有统一接口：

| 类别 | 接口 | 示例 |
|------|------|------|
| encryption | `encode(body)→cipher` / `decode(cipher)→body` | aes-cbc, plaintext |
| signing | `sign(url,headers)→headers` / `verify(resp)→bool` | xor-triple-sign, plaintext |
| auth | `execute(client)→bool` | password-login, sms-login, manual-token |
| messaging | `send(client, uid, text)→dict` | rest-json, rongcloud-tcp |

### 2.3 Pipeline 不变

```
认证 → 扫描房间(cache) → 断点恢复 → 逐房循环:
  拉排行 → 过滤已发 → 过滤性别 → 按金额降序 → 逐人私信
  → 写 sent_today → 更新 progress → sleep N 秒
→ 全部完成
```

StateManager / sent_today.json / progress.json / 暂停/恢复/停止 逻辑全部不动。

---

## 3. 目录结构

```
framework/core/
├── base_client.py           ← 重构: 配置驱动, ~200行
├── state_manager.py          ← 不动
├── task_manager.py           ← 微改: 读 config.json 而非 importlib
├── account_manager.py        ← 不动
├── dashboard.py              ← 新增 /apps/manage 路由 + 校验 API
├── processor_registry.py     ← 自动扫描 processors/ 注册
├── processors/
│   ├── base.py               ← Processor 接口
│   ├── encryption/
│   │   ├── aes_cbc.py        ← AES-256-CBC (双鱼)
│   │   └── plaintext.py      ← 明文透传
│   ├── signing/
│   │   ├── xor_triple.py     ← p1/p2/p3 XOR 签名
│   │   └── plaintext.py
│   ├── auth/
│   │   ├── password_login.py ← 密码登录
│   │   ├── sms_login.py      ← 短信验证码登录
│   │   └── manual_token.py   ← 手动填 token
│   └── messaging/
│       ├── rest_json.py      ← 纯 HTTP 私信 (preCheck→send)
│       └── rongcloud_tcp.py  ← 融云 TCP 私信

apps/
├── piaopiao/config.json      ← 新格式: plaintext + rest-json
├── shuangyu/config.json      ← 新格式: aes-cbc + xor-triple + rongcloud-tcp
└── [新app]/
    ├── config.json            ← 逆向 skill 产出，上传即用
    ├── runtime.json           ← Dashboard 写入 (凭据/参数)
    └── .state/                ← rooms_cache / sent_today / progress
```

---

## 4. 配置文件格式 (JSON Schema)

### 4.1 完整示例 (双鱼部落)

```json
{
  "meta": {
    "app_name": "双鱼部落",
    "version": "2.47.1",
    "platform": "Android",
    "config_schema": "2.0"
  },

  "server": {
    "base_url": "https://ui-api-cn.shuangyuxingqiu.com",
    "default_headers": {
      "clienttype": "Android",
      "channel": "oppo",
      "build": "334",
      "appversion": "2.47.1",
      "devicetype": "Samsung SM-S9280"
    }
  },

  "pipeline": {
    "encryption": {
      "plugin": "aes-cbc",
      "params": { "key": null, "iv": null, "key_derivation": "device_token" }
    },
    "signing": {
      "plugin": "xor-triple-sign",
      "params": { "read_key": "01528e5f", "write_key": "015357de", "p3_key": "0001d981" }
    },
    "auth": {
      "plugin": "password-login",
      "params": {
        "endpoint": "/UI/PasswordLoginPage/passwordLogin",
        "fields": { "phone": "phone", "password": "password", "code": "code", "mobile_token": "mobile_token" },
        "response_mapping": { "token": "token", "uid": "id" }
      }
    },
    "messaging": {
      "plugin": "rongcloud-tcp",
      "params": { "app_key": "m7ua80gbmdddm" }
    }
  },

  "endpoints": {
    "all_rooms": {
      "steps": [
        {
          "name": "categories",
          "path": "/UI/Room/Home/categoryList",
          "method": "POST",
          "body": {}
        },
        {
          "name": "room_list",
          "path": "/UI/Room/Home/roomList",
          "method": "POST",
          "body": {
            "id": "{{categories.list.*.id}}",
            "page": "{{page}}",
            "page_size": 20
          },
          "iter_source": "categories.list",
          "pagination": { "type": "page_number", "page_param": "page", "size": 20, "stop_on": "empty_list" }
        }
      ],
      "output_mapping": { "id": "id", "name": "name", "type": "room_type", "category": "{{_iter.key}}" }
    },

    "ranking": {
      "path": "/UI/Room/UserRank/list",
      "method": "POST",
      "body": {
        "room_id": "{{room.id}}",
        "mode": "{{data_source_key}}",
        "rank_type": "{{period_key}}"
      },
      "pagination": { "type": "offset_limit", "offset_param": "offset", "size": 20, "stop_on": "empty_list" },
      "output_mapping": { "uid": "uid", "nick": "nickname", "amount": "amount", "gender": "gender" }
    }
  },

  "runtime_config": {
    "settings": { "send_interval": 3 },
    "data_sources": { "贡献榜": "rich", "魅力榜": "charm", "财富榜": "wealth" },
    "periods": { "今日": "day", "本周": "week", "本月": "month" },
    "genders": { "全部": null, "男": 1, "女": 2 },
    "templates": ["{nick} 你好~", "{nick} 在吗？聊聊呀"]
  }
}
```

### 4.2 最简示例 (漂漂)

```json
{
  "meta": { "app_name": "漂漂", "version": "1.0" },
  "server": { "base_url": "https://api.pp.weimipopo.com" },
  "pipeline": {
    "encryption": "plaintext",
    "signing": "plaintext",
    "auth": { "plugin": "manual-token", "params": { "token_field": "token", "uid_field": "uid" } },
    "messaging": { "plugin": "rest-json", "params": { "precheck_path": "/plpl/im/msg/preCheck", "send_path": "/plpl/im/msg/send" } }
  },
  "endpoints": {
    "all_rooms": {
      "path": "/plpl/room/main/listByCat",
      "method": "POST",
      "body": { "catId": 1, "offset": "{{offset}}", "limit": 20 },
      "pagination": { "type": "offset_limit", "size": 20, "stop_on": "empty_list" },
      "output_mapping": { "id": "unRoomId", "name": "roomName", "type": "voice" }
    },
    "ranking": {
      "path": "/room/rank/list/contribute/rank",
      "method": "POST",
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

### 4.3 all_rooms 两种模式

**单端点模式** (漂漂): `all_rooms` 是一个对象，包含 `path/method/body/pagination/output_mapping`。BaseClient 直接分页调用这一个端点。

**多步骤模式** (双鱼): `all_rooms.steps` 是一个数组，按顺序执行。步骤间通过 `iter_source` 建立迭代关系：

```
Step 1 "categories"    → 返回 {list: [{id: 1, name: "聊天"}, {id: 2, name: "游戏"}]}
                            ↓
Step 2 "room_list"     → for each category, call API with body.id = category.id
       iter_source: "categories.list"  ← 指定迭代来源
       body: {"id": "{{_iter.id}}"}    ← {{_iter}} = 当前 category 对象
```

`iter_source` 格式: `"<step_name>.<response_path>"`。如果省略，步骤只执行一次。
`pagination` 格式: `type` 可选 `page_number`(page 从 1 开始递增)、`offset_limit`(offset 从 0 开始，步长 size)。
`stop_on: "empty_list"` 表示响应 list 为空时停止分页。
```

### 4.4 模板变量语法

| 语法 | 说明 | 示例 |
|------|------|------|
| `{{room.id}}` | 从上下文对象取值 | room 对象 id 字段 |
| `{{page}}` | 分页内置变量 | 当前页码(从 1 开始) |
| `{{offset}}` | 分页内置变量 | 当前偏移量(从 0 开始) |
| `{{data_source_key}}` | runtime_config 映射值 | "贡献榜" → "rich" |
| `{{period_key}}` | 同上 | "今日" → "day" |
| `{{_iter.field}}` | 多步骤模式中，当前迭代对象的字段 | _iter = {id: 1, name: "聊天"}, {{_iter.id}} = 1 |

所有模板变量在执行时以 `Template("...").substitute(...)` 方式填充。未定义变量抛出 `KeyError`，在上传校验阶段即可检测到。

---

## 5. 处理器接口

```python
class BaseProcessor(ABC):
    name: str          # "aes-cbc"
    category: str      # "encryption"

    @classmethod
    def params_schema(cls) -> dict:
        """返回 JSON Schema 描述需要的参数，供 Web UI 渲染表单"""
        ...

    def __init__(self, params: dict):
        self.params = params


class EncryptionProcessor(BaseProcessor):
    category = "encryption"

    def encode(self, body: dict) -> bytes: ...
    def decode(self, response_text: str) -> dict: ...


class SigningProcessor(BaseProcessor):
    category = "signing"

    def sign(self, url: str, headers: dict) -> dict: ...
    def verify(self, response: requests.Response) -> bool: ...


class AuthProcessor(BaseProcessor):
    category = "auth"

    def authenticate(self, client) -> bool: ...
    def load_credentials(self) -> dict:
        """从 runtime.json 读取凭据（phone/password/token等）"""
        ...


class MessagingProcessor(BaseProcessor):
    category = "messaging"

    def send(self, client, uid: str, text: str) -> dict:
        """返回 {success: bool, error: str}"""
        ...
```

### 处理器注册表

```python
class ProcessorRegistry:
    _registry: dict = {}

    @classmethod
    def register(cls, processor_class):
        key = f"{processor_class.category}/{processor_class.name}"
        cls._registry[key] = processor_class

    @classmethod
    def load(cls, config: dict, category: str):
        spec = config["pipeline"].get(category, "plaintext")
        if isinstance(spec, str):
            spec = {"plugin": spec, "params": {}}
        key = f"{category}/{spec['plugin']}"
        processor_cls = cls._registry[key]
        return processor_cls(spec.get("params", {}))
```

处理器通过 `framework/core/processors/` 目录下的 `__init__.py` 链自动注册。新增处理器只需创建文件并 import。

---

## 6. Web 上传流程

### 6.1 路由

`GET /apps/manage` — App 管理页
`POST /api/apps/upload` — 上传配置 JSON
`POST /api/apps/<id>/test` — 测试连接
`DELETE /api/apps/<id>` — 删除 App

### 6.2 上传校验链

| 步骤 | 说明 | 失败级别 |
|------|------|---------|
| JSON 语法 | `json.loads()` 成功 | 错误 |
| Schema 版本 | `meta.config_schema` 检查 | 错误 |
| 必填字段 | `meta.app_name`, `server.base_url` | 错误 |
| 处理器存在 | 引用的 plugin 名在注册表中 | 错误 |
| URL 可达 | `HEAD` base_url, 5s 超时 | 警告 |
| 端点占位符闭包 | 模板变量引用的变量路径存在 | 错误 |
| 循环依赖 | all_rooms.steps 引用链无环 | 错误 |

校验结果在 Web 页面实时显示，错误阻塞上传，警告不阻塞。

### 6.3 存储

```
apps/<app_name>/
├── config.json     ← 上传的配置 (只读)
├── runtime.json    ← 用户填写的凭据和参数
└── .state/         ← 运行时数据
```

`config.json` 是逆向产物，不可通过 Web 编辑（只能替换/删除）。`runtime.json` 包含敏感凭据和用户自定义参数（间隔秒数、模板等）。

---

## 7. Dashboard 变更

### 7.1 首页 (不变)

App 卡片网格，每个卡片：名称/状态/进度/已发/失败。`+` 空位卡片跳转到 `/apps/manage`。

### 7.2 App 管理页 (新增)

- 左侧：已安装 App 列表
- 右侧：拖拽上传区 + 校验结果面板
- 上传后预览 App 名称/端点/处理器配置
- "测试连接" 按钮 → 调用 authenticate() → 显示绿色成功/红色失败+错误信息
- "激活" 按钮 → 写入 apps/ 目录 → TaskManager 动态注册 → 返回首页

### 7.3 详情页 (微调)

发送设置面板新增：数据源/时段/性别下拉框（从 runtime_config 读取）。processor 信息只读展示区。

---

## 8. 与逆向 skill 的接口

逆向 skill 输出 JSON 配置文件（Schema 4.1），用户在 Dashboard 上传即用。

**Skill 输出规范**：
- 文件名：`<app_slug>-config.json`
- `encryption.key` / `encryption.iv`：如果 key 是硬编码的，填实际值；如果是会话派生，填 `null`，由应用首次运行时根据 `key_derivation` 计算
- `signing`：只填已知的 XOR key 等参数
- 所有 `null` 值的参数在 Web 页面显示为"需要手动填写"

---

## 9. 迁移计划

### Phase 1: 核心引擎
- 实现 Processor 接口 + ProcessorRegistry
- 实现 plaintext, aes-cbc, xor-triple-sign, manual-token, password-login, rest-json 处理器
- BaseClient 重构为配置驱动
- piaopiao/shuangyu 旧 client.py 删除，换新 config.json

### Phase 2: Web 上传
- Dashboard 新增 /apps/manage 页面
- 实现上传/校验/预览/测试连接 完整流程
- 前端拖拽上传 + 实时校验反馈

### Phase 3: 复杂处理器
- rongcloud-tcp 处理器
- sms-login 处理器
- any additional processors from new apps

### 回退安全
- 旧 `client.py` 在确认新系统功能完整前保留在 git history
- TaskManager 兼容新旧两种模式（优先用配置，fallback 到 importlib）

---

## 10. 风险与限制

| 风险 | 缓解 |
|------|------|
| 处理器不够用 | 处理器接口简单（4个方法），新增成本低；极端情况允许嵌入 Python lambda |
| 配置复杂度增长 | 双鱼已覆盖 AES/XOR/密码登录/融云，是目前已知最复杂场景；更复杂的 case 暂时手写 |
| 向后兼容 | runtime.json 与 config.json 分离，升级配置不影响用户数据 |
| JSON Schema 版本 | `config_schema` 字段，Dashboard 可拒绝旧版本配置并提示升级 |
