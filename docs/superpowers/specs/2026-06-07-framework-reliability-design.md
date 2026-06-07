# Framework Reliability & Developer Experience Design

> 四大改进方向：诊断层、处理器增强、模块化 Frida、环境稳定性

**日期**: 2026-06-07 | **状态**: 设计完成

---

## 背景

当前框架接入新 App 全链路痛点：
- 每次要 Claude 在 VSCode 里手工介入
- 认证/加密/签名组合不灵活
- Frida JS 脚本每 App 要手写
- 环境问题（hluda、Windows 管道、NIS、中文路径）
- 调试信息不足

核心流程本质简单：**认证 → 进房间拿用户 → 发私信**

---

## 1. 诊断层 (D)

### 1.1 请求链路日志

每个 HTTP 请求经过的完整链路自动记录：

```
[diagnose] POST /UI/Room/Home/roomList
  encrypt:  aes-cbc | key=已设置(32B) | body 128B → 176B | 1.2ms
  sign:     xor-triple | p1=extracted(随机) | 0.3ms
  auth:     header X-Token=7937bebd... | uid=22187615
  send:     200 OK | 342B | 180ms
  decrypt:  aes-cbc | 342B → 1.2KB | 0.8ms
  business: code=200 | list=20 items  ← check_response() 内联
```

### 1.2 实现

- `framework/core/diagnose.py` — `DiagnoseLogger` 类
- 嵌入点在 `base_client._post` / `_get`，覆盖 encrypt → sign → auth → send → decrypt → business 全链路
- `business` 步骤在 `_post` 内直接调用 `check_response()`，不再依赖外层 `_fetch_paginated` 的延迟检查
- 默认开启，通过 `config.json` 的 `diagnose: false` 关闭
- 日志输出：
  - **控制台** — `print()` 实时输出
  - **Dashboard SSE** — `GET /api/app/<id>/diagnose/stream`，Flask `Response(stream_with_context(...), mimetype='text/event-stream')`
- SSE 实现：`diagnose.py` 提供 `DiagnoseLogger.subscribe()` 返回 `queue.Queue`，dashboard route 用 `stream_with_context` 读取 queue 推事件

### 1.3 失败高亮

```
[diagnose] POST /UI/PasswordLoginPage/passwordLogin
  encrypt:  aes-cbc | key=已设置(32B) | body 64B → 96B | 1.0ms  
  sign:     xor-triple | p1=... | 0.2ms
  auth:     skip (未认证)
  send:     200 OK | 512B | 210ms
  decrypt:  aes-cbc | FAILED: Padding is incorrect. | 回退到 raw text
  business: code=120001 | msg="密钥获取失败" | ✗ 业务错误
    可能原因: AES key 与 deviceToken 不匹配 | 设备未注册
```

---

## 2. 处理器增强 (A)

### 2.1 自检方法

每个处理器新增 `validate(client) → (ok, warnings)` 方法：

```python
class BaseProcessor(ABC):
    def validate(self, client) -> tuple:
        """返回 (ok: bool, warnings: list[str])"""
        return True, []
```

示例 — AesCbcEncryption:
```python
def validate(self, client):
    warnings = []
    if self._key is None and self.params.get("key_derivation") == "session_key":
        bridge = client.config_path.parent / "bridge_cli.js"
        if not bridge.exists():
            warnings.append(
                "key_derivation=session_key 需要 bridge_cli.js，文件不存在。"
                "请使用 frida-bridge-generator 生成。")
    if not client._base_url:
        warnings.append("server.base_url 未配置")
    return len(warnings) == 0, warnings
```

### 2.2 组合配方

`framework/core/recipes.py` — 预置配方，一个名字展开为一组处理器：

```python
RECIPES = {
    "sybl-pattern": {
        "encryption": {"plugin": "aes-cbc", "params": {
            "key": None, "iv": None, "key_derivation": "session_key"
        }},
        "signing": {"plugin": "xor-triple-sign", "params": {
            "read_key": "01528e5f", "write_key": "01528e5f", "p3_key": "00000000"
        }},
        "auth": {"plugin": "password-login", "params": {
            "endpoint": "/UI/PasswordLoginPage/passwordLogin",
            "fields": {"phone": "phone", "password": "password"},
            "response_mapping": {"token": "token", "uid": "id"}
        }},
        "messaging": {"plugin": "frida-rpc", "params": {
            "script_name": "bridge.js", "note": "使用模块拼接生成的 bridge.js"
        }},
    },
    "simple-rest": {
        "encryption": "plaintext",
        "signing": "plaintext",
        "auth": "header-token",
        "messaging": "rest-json",
    },
}
```

config.json 中引用：
```json
{
  "pipeline": {"recipe": "sybl-pattern"}
}
```

也可以覆盖配方中的部分处理器：
```json
{
  "pipeline": {
    "recipe": "sybl-pattern",
    "signing": {"plugin": "sha1-sorted-kv", "params": {...}}
  }
}
```

### 2.3 逐级测试命令

```bash
# 单独测加密
python -m framework test-processor --app sybl --category encryption
python -m framework test-processor --app sybl --category signing
python -m framework test-processor --app sybl --category auth

# 跑全链路验证（不发送，只验证数据能跑通）
python -m framework validate --app sybl

# 跑验证 + 列出所有 warnings
python -m framework validate --app sybl --verbose
```

测试命令输出：
```
[test] encryption/aes-cbc:
  key: 已设置 (32 bytes, derivation=session_key via bridge_cli.js)
  IV:  clientSession[:16] -> "FCE3F1A4-5DC3-41"
  encode: {"test": "hello"} -> "nyFNSw60IP5ELDnT5AiCEA==" (24 chars, base64)
  decode: "nyFNSw60IP5ELDnT5AiCEA==" -> {"test": "hello"}
  ✓ 加密/解密往返成功

[test] signing/xor-triple-sign:
  sign(url, headers, {}) -> p1=abc123... p2=def456... p3=000000...
  ✓ 签名生成成功

[test] auth/password-login:
  endpoint: /UI/PasswordLoginPage/passwordLogin
  fields: phone=13800138000 password=***
  发送请求...
  response: code=200, token=7937bebd...
  ✓ 登录成功 (uid=22187615, token=7937bebd-...)
```

---

## 3. 模块化 Frida 脚本 (B)

### 3.1 架构

不再为每个 App 写完整 JS 脚本。改为：

```
framework/bridge/modules/
  crypto/
    cipher_init.js          # Java: hook Cipher.init, capture key+IV
    secret_key_spec.js      # Java: hook SecretKeySpec.$init (早于 Cipher, 绕过 NIS)
    evp_cipher_init.js      # Native: EVP_CipherInit_ex (BoringSSL)
  http/
    okhttp.js               # hook OkHttp, 抓请求头/响应
    cronet.js               # hook Cronet
  rpc/
    key_export.js           # RPC.exports: getSessionKey, getHeaders
    messaging_rongcloud.js  # RPC.exports: sendMessage via 融云
    messaging_rest.js       # RPC.exports: sendMessage via HTTP
    ws_rooms.js             # RPC.exports: getRooms via WebSocket
```

### 3.2 配置方式

config.json 声明模块组合，框架运行时拼接：

```json
{
  "frida": {
    "modules": [
      {"name": "secret_key_spec", "params": {}},
      {"name": "okhttp", "params": {"host_blacklist": ["log.xxx.com"]}},
      {"name": "key_export", "params": {}},
      {"name": "messaging_rongcloud", "params": {"app_key": "m7ua80gbmdddm"}}
    ],
    "rpc_methods": ["getSessionKey", "getHeaders", "sendMessage"]
  }
}
```

### 3.3 模块实现规范与注册协议

每个模块是一个 IIFE（立即执行函数），接收 `ctx` 共享上下文，返回 `{install, getState, uninstall}`：

```javascript
// module template
(function(ctx) {
  var state = {};

  function install() {
    Java.perform(function() {
      var Target = Java.use("com.example.Target");
      Target.method.implementation = function(x) {
        state.value = x;
        return this.method(x);
      };
    });
  }

  function getState() {
    return state;
  }

  function uninstall() {
    // optional: restore original implementation
  }

  ctx.register("module_name", {install: install, getState: getState, uninstall: uninstall});
})
```

**共享上下文 `ctx`**：
```javascript
var ctx = {
  shared: {},           // 模块间共享数据（如 sessionHeaders, sessionKey）
  modules: {},          // 注册表: name → {install, getState, uninstall}
  register: function(name, mod) { this.modules[name] = mod; },
  log: function(msg) { console.log("[module:" + arguments[0] + "]", ...Array.prototype.slice.call(arguments, 1)); },
};
```

**初始化顺序**（框架胶水层）：
1. 加载所有模块 IIFE（顺序执行，各模块调用 `ctx.register()` 把自己注册进去）
2. 按依赖顺序调用 `module.install()` — 非 Java 模块先装，Java.perform 模块后装
3. 构建 `rpc.exports` — 从 `ctx.modules` 读取各模块 state
4. 打印就绪信号 `console.log("[bridge] Ready.")` — Python 侧以此检测 hooks 就绪

**模块间共享数据示例**（secret_key_spec 写入，key_export 读取）：

```javascript
// 模块 A: crypto/secret_key_spec.js
(function(ctx) {
  function install() {
    Java.perform(function() {
      var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
      SKS.$init.overload('[B', 'java.lang.String').implementation = function(kb, algo) {
        if (algo.indexOf("AES") >= 0 && kb.length === 32) {
          var h = ""; for (var i = 0; i < kb.length; i++) h += ("0" + (kb[i] & 0xFF).toString(16)).slice(-2);
          ctx.shared.sessionKey = h;  // 写入共享区
        }
        return this.$init(kb, algo);
      };
    });
  }
  ctx.register("secret_key_spec", {install: install, getState: function() { return {key: ctx.shared.sessionKey}; }});
})

// 模块 B: rpc/key_export.js
(function(ctx) {
  // rpc.exports 在 glue 层构建，本模块只提供 getState
  ctx.register("key_export", {
    install: function() {},
    getState: function() { return {key_hex: ctx.shared.sessionKey, headers: ctx.shared.sessionHeaders}; }
  };
})
```

**框架 glue 层（Python 侧拼接）**：
```javascript
// === glue: init ===
var ctx = { shared: {}, modules: {}, register: function(n,m) { this.modules[n] = m; } };

// === module: crypto/secret_key_spec.js ===
(function(ctx) { ... })(ctx);

// === module: http/okhttp.js ===
(function(ctx) { ... })(ctx);

// 安装阶段
ctx.modules["secret_key_spec"].install();
ctx.modules["okhttp"].install();
// ...

// 轮询等待 key 就绪，写入 stdout
setInterval(function() {
  var key = ctx.shared.sessionKey;
  if (key && !ctx._keyWritten) {
    ctx._keyWritten = true;
    console.log("[bridge] KEY_JSON: " + JSON.stringify({key_hex: key, headers: ctx.shared.sessionHeaders}));
  }
}, 500);

// === glue: rpc.exports ===
rpc.exports = {
  sendMessage: function(uid, text) { return ctx.modules["messaging_rongcloud"].send(uid, text); },
  getSessionKey: function() { return ctx.modules["key_export"].getState().key_hex; },
  getHeaders: function() { return ctx.modules["key_export"].getState().headers; },
};
```

### 3.4 模块选择决策树

Dashboard 中提供可视化模块选择向导：

```
1. 加密 hook 类型？
   ○ aes-cbc (Java Cipher.init)        ← 推荐，覆盖大多数
   ○ SecretKeySpec (NIS 绕过)          ← 如果 Cipher.init 被检测
   ○ Native EVP_CipherInit_ex          ← 如果 Java 层全部被检测

2. HTTP 库？
   ○ OkHttp (标准)                     ← 90% 的 App
   ○ Cronet                            ← Google 系
   ○ 不需要（HTTP 在 Python 侧）

3. 消息发送方式？
   ○ HTTP REST API                     ← 直接用 rest-json processor
   ○ 融云 IM (Frida RPC)              ← 融云 TCP 协议
   ○ WebSocket                         ← 实时推送
   ○ 自定义 TCP (需写模块)
```

### 3.5 向后兼容

- 保留 `frida.script` 字段 — 如果写了就直接用完整脚本，不走模块拼接
- 如果 `frida.modules` 存在，优先用模块拼接
- `frida.script` 和 `frida.modules` 互斥

---

## 4. 环境与稳定性 (C)

### 4.1 环境自动检测

`framework/bridge/env_checker.py`:

```python
class FridaEnvChecker:
    @classmethod
    def probe(cls, serial: str, package: str) -> dict:
        """返回环境探测结果"""
        return {
            "server_type": "frida-server",    # or "hluda"
            "port": 27042,
            "pid": 22241,
            "pid_found_via": "enumerate",      # or "adb"
            "nis_protection": False,
            "preferred_mode": "python_binding", # or "cli"
            "platform": "win32",
            "wsl_available": False,
            "warnings": [...],
            "recommendations": [...],
        }
```

检测逻辑：
1. `adb devices` → 确认设备连接
2. `adb shell ps -A | grep <package>` → 找 PID
3. `frida-ps -H 127.0.0.1:27042` → 确认 Frida 连接
4. 尝试 `frida.enumerate_processes()` → 如果找不到进程 = NIS 隐藏
5. 综合判断 → 输出环境报告

### 4.2 一键健康检查

Dashboard "设备检测" 按钮调用 `GET /api/device/<serial>/health`:

```json
{
  "adb": {"ok": true, "serial": "99856ec5"},
  "frida_server": {"ok": true, "type": "hluda", "port": 27042},
  "app": {"ok": true, "pid": 22241, "package": "com.sybl.voiceroom"},
  "nis": {"detected": true, "pid_hidden": true, "recommendation": "cli_mode"},
  "platform": {
    "os": "win32",
    "pipe_risk": true,
    "recommendation": "use WSL for Frida CLI"
  },
  "overall": "ok_with_warnings",
  "warnings": [
    "Windows 管道可能不稳定，建议配置 WSL",
    "检测到 NIS 保护，已自动切换到 CLI 模式"
  ]
}
```

### 4.3 WSL 集成

**网络要点**：Frida server 运行在 Android 设备上，ADB forward（`adb forward tcp:27042 tcp:27042`）在 Windows 侧将 `127.0.0.1:27042` 转发到 Android。WSL 有独立网络栈，`127.0.0.1` 不通 Windows。

**正确连接链**：
```
WSL → $(hostname).local:27042 → Windows ADB forward → Android frida-server:27042
```

Windows 下 Python 通过 WSL 调用 Frida CLI：

```python
# framework/bridge/frida_cli.py
def _get_wsl_host_ip():
    """获取 WSL 可访问的 Windows 主机 IP"""
    # WSL2: nameserver 在 /etc/resolv.conf 指向 Windows
    # WSL1: localhost 共享，直接 127.0.0.1
    try:
        r = subprocess.run(
            ["wsl", "cat", "/etc/resolv.conf"],
            capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if line.startswith("nameserver"):
                return line.split()[1]
    except Exception:
        pass
    return "127.0.0.1"

def _build_frida_cmd(port, pid, script_path):
    if sys.platform == "win32" and _wsl_available():
        host_ip = _get_wsl_host_ip()
        # WSL 自动挂载 Windows 路径: C:\tmp\ -> /mnt/c/tmp/
        wsl_path = _to_wsl_path(script_path)
        return f"wsl frida -H {host_ip}:{port} -p {pid} -l {wsl_path}"
    else:
        return f"frida -H 127.0.0.1:{port} -p {pid} -l \"{script_path}\""
```

**备选方案（WSL 不可用时）**：不依赖管道，Frida JS 写结果到 Android 设备的 `/data/local/tmp/`，Python 用 `adb pull` 读取：

```javascript
// bridge_cli.js 中
var f = new File("/data/local/tmp/sybl_key.json", "w");
f.write(JSON.stringify({key_hex: sessionKey, headers: sessionHeaders}));
f.close();
```

```python
# Python 侧
subprocess.run(["adb", "-s", serial, "pull", "/data/local/tmp/sybl_key.json", tmp_path])
key_data = json.loads(Path(tmp_path).read_text())
```

此方案彻底回避 Windows 管道问题，但要求 App 有 SD 卡写入权限（通常有）。

### 4.4 错误恢复策略

```python
# Frida CLI 断开自动重连
MAX_RETRIES = 3
for attempt in range(MAX_RETRIES):
    try:
        proc = launch_frida_cli(...)
        break
    except FridaCLIError as e:
        if attempt == MAX_RETRIES - 1:
            raise
        time.sleep(2 ** attempt)

# 进程被杀自动重启
def _ensure_app_running(serial, package):
    pid = _get_pid_via_adb(serial, package)
    if not pid:
        subprocess.run(["adb", "-s", serial, "shell", "monkey", "-p", package, "1"])
        time.sleep(5)
        pid = _get_pid_via_adb(serial, package)
    return pid
```

### 4.5 统一 PID 检测（消除重复代码）

当前两份独立实现：
- `frida_session.py:157` `_find_pid_via_adb()` — 用 `-o PID,NAME` 格式
- `aes_cbc.py:211` `_get_pid_via_adb()` — 用裸 `ps -A`

统一到 `framework/bridge/adb_device.py`：

```python
# AdbDevice 新增静态方法
@staticmethod
def get_pid(serial: str, package: str) -> int | None:
    """通过 adb shell ps 查找进程 PID（绕过 NIS 隐藏）"""
    try:
        raw = subprocess.check_output(
            ["adb", "-s", serial, "shell", "ps", "-A"],
            timeout=10, text=True, stderr=subprocess.DEVNULL,
        )
        for line in raw.splitlines():
            if package in line:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])  # PID is 2nd column
                    except ValueError:
                        pass
    except Exception:
        pass
    return None
```

`frida_session.py` 和 `aes_cbc.py` 改为 `from framework.bridge.adb_device import AdbDevice; AdbDevice.get_pid(...)`，删除各自的 private 方法。`env_checker.py` 也复用此方法。

### 4.6 路径规范

- 要求用户将项目放在纯 ASCII 路径（如 `C:\projects\interceptor-framework`）
- 如果检测到中文路径，Dashboard 显示警告
- 框架内部生成的临时文件统一写入 `tempfile.gettempdir()`

---

## 5. 现有 App 迁移策略

### 5.1 4 个现有 App 迁移路径

| App | 当前状态 | 迁移动作 |
|-----|---------|---------|
| **sybl** | 自定义 `bridge_cli.js` (76行) + `frida_key_bridge.js` (95行) | 拆分为: secret_key_spec + okhttp + messaging_rongcloud 模块。config 加 `frida.modules`。保留 `frida.script` 作为回退直到验证通过。 |
| **wefun** | `hook_send_msg.js` + `hook_ws_rooms.js` | 拆分为: cipher_init + okhttp + ws_rooms 模块。ws_rooms 是 `_script_second`，需单独注入。 |
| **hifun** | `hook_send_msg.js` | 拆分为: cipher_init + messaging_rest 模块。加密若为明文则跳过 crypto 模块。 |
| **piaopiao** | 无 Frida 脚本（纯 HTTP） | 无需迁移。config 保持 `"frida": {"enabled": false}`。 |

### 5.2 渐进式迁移

1. **阶段 1**：模块系统和 `frida.script` 共存 — 优先用 `frida.modules`，不存在时回退到 `frida.script`
2. **阶段 2**：每个 App 逐个验证 — 生成模块化 bridge.js，与旧脚本对比 RPC 行为一致
3. **阶段 3**：清理 — 删除旧 `frida.script` 字段和旧 JS 文件，全量切到模块

### 5.3 配置文件迁移

- `frida.script` → `frida.modules`：旧字段保留但不推荐，新 App 只能用 `frida.modules`
- `pipeline.*` → `pipeline.recipe`：旧的手动指定处理器仍然有效，recipe 是可选快捷方式
- config.json 上传时 `_validate_config()` 自动检测并用哪个模式

---

## 6. 实施优先级

| 优先级 | 模块 | 影响范围 | 依赖 |
|--------|------|---------|------|
| P0 | 诊断层 (D) | 所有 App | 无 |
| P0 | 处理器 validate() (A-2.1) | 所有 processor | 无 |
| P1 | 环境检测 + 健康检查 (C-4.1/4.2) | 设备管理 | D |
| P1 | 逐级测试命令 (A-2.3) | 调试体验 | A-2.1, D |
| P2 | 模块化 Frida (B) | Frida 脚本 | 无 |
| P2 | 组合配方 (A-2.2) | config.json | 无 |
| P3 | WSL 集成 (C-4.3) | Windows 环境 | 无 |
| P3 | 错误恢复 (C-4.4) | 运行时稳定性 | C-4.1 |

---

## 7. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `framework/core/diagnose.py` | 诊断日志器 + SSE subscribe |
| 修改 | `framework/core/base_client.py` | 嵌入 diagnose 点，`_post`/`_get` 内联 check_response() |
| 修改 | `framework/core/processors/base.py` | 新增 validate() 默认 no-op |
| 修改 | 16 个 processor 文件 | 实现 validate() |
| 新建 | `framework/core/recipes.py` | 组合配方定义 |
| 修改 | `framework/core/processor_registry.py` | 支持 recipe 展开 |
| 新建 | `framework/bridge/modules/` | 模块目录 (8个 JS 模块) |
| 新建 | `framework/bridge/frida_module_loader.py` | 模块拼接引擎（ctx + IIFE + rpc.exports 构建） |
| 新建 | `framework/bridge/env_checker.py` | 环境检测 |
| 新建 | `framework/bridge/frida_cli.py` | Frida CLI 抽象（含 WSL 网络 + adb pull 备选） |
| 修改 | `framework/bridge/adb_device.py` | 新增 `get_pid()` 统一 PID 检测 |
| 修改 | `framework/bridge/frida_session.py` | 删除 `_find_pid_via_adb()`，改用 AdbDevice |
| 修改 | `framework/core/processors/encryption/aes_cbc.py` | 删除 `_get_pid_via_adb()`，改用 AdbDevice |
| 修改 | `framework/core/dashboard.py` | SSE 诊断流 + 健康检查 API |
| 修改 | `apps/*/config.json` | 支持 `frida.modules`, `pipeline.recipe`；保留 `frida.script` 兼容 |
