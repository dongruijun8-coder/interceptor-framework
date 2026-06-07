# Framework Reliability & Developer Experience — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提升框架可靠性、可观测性和新 App 接入效率 — 诊断层、处理器自检、模块化 Frida、环境稳定性

**Architecture:** 分 4 阶段实现，每阶段独立可交付。P0 先建诊断层和处理器自检，P1 加环境检测和测试工具，P2 改造 Frida 为模块组合，P3 做 WSL 集成和错误恢复。

**Tech Stack:** Python 3.12+, Flask, Frida CLI, JavaScript (Frida), ADB

---

## Phase 1: P0 — 诊断层 + 处理器自检

### Task 1: DiagnoseLogger

**Files:**
- Create: `framework/core/diagnose.py`

- [ ] **Step 1: 创建 diagnose.py**

```python
"""DiagnoseLogger — request pipeline observability with SSE streaming"""
import queue
import threading
import time
import json


class DiagnoseLogger:
    """Per-client singleton logger. Hooks into _post/_get pipeline steps."""

    def __init__(self, app_name: str, enabled: bool = True):
        self.app_name = app_name
        self.enabled = enabled
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        """Return a Queue that receives diagnose events (for SSE streaming)."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def log(self, method: str, path: str, step: str, detail: str, ms: float = 0):
        if not self.enabled:
            return
        ts = time.strftime("%H:%M:%S")
        line = f"[diagnose {ts}] {method} {path} | {step}: {detail}"
        if ms > 0:
            line += f" | {ms:.1f}ms"
        print(line)

        payload = {
            "app": self.app_name, "method": method, "path": path,
            "step": step, "detail": detail, "ms": round(ms, 1), "time": ts,
        }
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(json.dumps(payload))
                except queue.Full:
                    pass
                except Exception:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/diagnose.py
git commit -m "feat: add DiagnoseLogger for request pipeline observability"
```

---

### Task 2: Embed diagnose in base_client

**Files:**
- Modify: `framework/core/base_client.py:278-335`

- [ ] **Step 1: 在 BaseClient.__init__ 中初始化 logger**

在 `self._messenger = ...` 之后加入：
```python
self._diagnose = DiagnoseLogger(
    self.app_name,
    enabled=self.config.get("diagnose", True),
)
```

在 import 区添加：
```python
from .diagnose import DiagnoseLogger
```

- [ ] **Step 2: 修改 _post 嵌入诊断点**

替换 `_post` 方法（行 278-308）：
```python
def _post(self, url: str, body: dict) -> dict:
    path = url.replace(self._base_url, "") if self._base_url else url
    _d = self._diagnose

    t0 = time.time()
    try:
        encrypted = self._encryptor.encode(body)
    except Exception as e:
        _d.log("POST", path, "encrypt", f"FAILED: {e}")
        raise RuntimeError(f"encryption.encode failed: {e}")
    t1 = time.time()
    _d.log("POST", path, "encrypt",
           f"{self._encryptor.name} | body {len(json.dumps(body))}B -> {len(encrypted)}B",
           (t1 - t0) * 1000)

    headers = dict(self._default_headers)
    ct_present = any(k.lower() == "content-type" for k in headers)
    if not ct_present:
        headers["Content-Type"] = "application/json; charset=utf-8"
    headers["__auth_token__"] = self._auth_token

    t0 = time.time()
    headers, extra_params = self._signer.sign(url, headers, body)
    t1 = time.time()
    _d.log("POST", path, "sign",
           f"{self._signer.name}", (t1 - t0) * 1000)

    if self._auth_token:
        _d.log("POST", path, "auth", f"token={self._auth_token[:12]}... uid={self._uid}")
    else:
        _d.log("POST", path, "auth", "skip (未认证)")

    import urllib.parse
    if extra_params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(extra_params)

    t0 = time.time()
    r = self.session.post(url, data=encrypted, headers=headers, timeout=30)
    t1 = time.time()
    r.raise_for_status()
    _d.log("POST", path, "send",
           f"{r.status_code} | {len(r.content)}B", (t1 - t0) * 1000)

    t0 = time.time()
    try:
        decoded = self._encryptor.decode(r.content)
    except Exception as e:
        _d.log("POST", path, "decrypt", f"FAILED: {e}. 回退到 raw text")
        try:
            decoded = json.loads(r.text)
        except json.JSONDecodeError:
            raise RuntimeError(f"decryption failed: {r.text[:200]}")
    t1 = time.time()
    _d.log("POST", path, "decrypt",
           f"{self._encryptor.name} | {len(r.content)}B -> {len(json.dumps(decoded))}B",
           (t1 - t0) * 1000)

    # Business-level check (inlined from check_response)
    ok = self.check_response(decoded)
    code = decoded.get("code", "?")
    if ok:
        _d.log("POST", path, "business", f"code={code} | OK")
    else:
        msg = decoded.get("msg", decoded.get("message", ""))
        _d.log("POST", path, "business", f"code={code} | msg={msg} | ✗ 业务错误")

    return decoded
```

- [ ] **Step 3: 同步修改 _get**

```python
def _get(self, url: str, params: dict = None) -> dict:
    path = url.replace(self._base_url, "") if self._base_url else url
    _d = self._diagnose
    params = dict(params or {})

    headers = dict(self._default_headers)
    headers["__auth_token__"] = self._auth_token

    t0 = time.time()
    headers, extra_params = self._signer.sign(url, headers, params)
    t1 = time.time()
    _d.log("GET", path, "sign", f"{self._signer.name}", (t1 - t0) * 1000)

    if self._auth_token:
        _d.log("GET", path, "auth", f"token={self._auth_token[:12]}...")
    else:
        _d.log("GET", path, "auth", "skip")

    params.update(extra_params)

    t0 = time.time()
    r = self.session.get(url, params=params, headers=headers, timeout=30)
    t1 = time.time()
    r.raise_for_status()
    _d.log("GET", path, "send", f"{r.status_code} | {len(r.content)}B", (t1 - t0) * 1000)

    try:
        decoded = self._encryptor.decode(r.content)
    except Exception:
        _d.log("GET", path, "decrypt", "no encryption / raw JSON")
        decoded = json.loads(r.text)

    ok = self.check_response(decoded)
    code = decoded.get("code", "?")
    if ok:
        _d.log("GET", path, "business", f"code={code} | OK")
    else:
        _d.log("GET", path, "business", f"code={code} | ✗")

    return decoded
```

- [ ] **Step 4: Commit**

```bash
git add framework/core/base_client.py
git commit -m "feat: embed diagnose logging in _post/_get pipeline"
```

---

### Task 3: validate() on BaseProcessor

**Files:**
- Modify: `framework/core/processors/base.py:5-14`

- [ ] **Step 1: 加默认 validate()**

在 `BaseProcessor.params_schema()` 之后添加：
```python
    def validate(self, client) -> tuple:
        """返回 (ok: bool, warnings: list[str])。
        派生类覆盖此方法做自检。默认始终通过。"""
        return True, []
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/processors/base.py
git commit -m "feat: add validate() method to BaseProcessor"
```

---

### Task 4: Implement validate() on encryption + signing processors

**Files:**
- Modify: `framework/core/processors/encryption/aes_cbc.py`
- Modify: `framework/core/processors/encryption/plaintext.py`
- Modify: `framework/core/processors/signing/xor_triple.py`
- Modify: `framework/core/processors/signing/md5_h5_sign.py`
- Modify: `framework/core/processors/signing/sha1_sorted_kv.py`
- Modify: `framework/core/processors/signing/plaintext.py`

- [ ] **Step 1: aes_cbc.py validate()**

在 `AesCbcEncryption` 类中添加（`params_schema` 之后）：
```python
    def validate(self, client) -> tuple:
        warnings = []
        if self._key is None:
            method = self.params.get("key_derivation", "device_token")
            if method == "session_key":
                bridge = client.config_path.parent / "bridge_cli.js"
                if not bridge.exists():
                    warnings.append(
                        "key_derivation=session_key 需要 bridge_cli.js，请使用模块化 Frida 生成")
            elif method == "device_token":
                if not client.config.get("device_token"):
                    warnings.append("key_derivation=device_token 但 device_token 未配置")
        if not client._base_url:
            warnings.append("server.base_url 未配置")
        return len(warnings) == 0, warnings
```

- [ ] **Step 2: plaintext encryption validate()**

```python
    def validate(self, client) -> tuple:
        return True, []
```

- [ ] **Step 3: xor_triple.py validate()**

在 `XorTripleSigning` 中添加：
```python
    def validate(self, client) -> tuple:
        warnings = []
        pk = self.params.get("p3_key", "")
        if not pk or pk == "00000000":
            warnings.append("p3_key 为默认值 00000000，请确认与实际 App 一致")
        rk = self.params.get("read_key", "")
        if not rk:
            warnings.append("read_key 未配置，签名可能无法通过服务端验证")
        return len(warnings) == 0, warnings
```

- [ ] **Step 4: md5_h5_sign.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("salt"):
            warnings.append("md5-h5-sign 缺少 salt 参数")
        return len(warnings) == 0, warnings
```

- [ ] **Step 5: sha1_sorted_kv.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("secret"):
            warnings.append("sha1-sorted-kv 缺少 secret 参数")
        return len(warnings) == 0, warnings
```

- [ ] **Step 6: plaintext signing validate()**

```python
    def validate(self, client) -> tuple:
        return True, []
```

- [ ] **Step 7: Commit**

```bash
git add framework/core/processors/encryption/aes_cbc.py framework/core/processors/encryption/plaintext.py framework/core/processors/signing/xor_triple.py framework/core/processors/signing/md5_h5_sign.py framework/core/processors/signing/sha1_sorted_kv.py framework/core/processors/signing/plaintext.py
git commit -m "feat: add validate() to encryption and signing processors"
```

---

### Task 5: Implement validate() on auth + messaging processors

**Files:**
- Modify: `framework/core/processors/auth/password_login.py`
- Modify: `framework/core/processors/auth/sms_login.py`
- Modify: `framework/core/processors/auth/header_token.py`
- Modify: `framework/core/processors/auth/manual_token.py`
- Modify: `framework/core/processors/auth/plaintext.py`
- Modify: `framework/core/processors/messaging/frida_rpc.py`
- Modify: `framework/core/processors/messaging/rest_json.py`
- Modify: `framework/core/processors/messaging/rongcloud_tcp.py`
- Modify: `framework/core/processors/messaging/plaintext.py`
- Modify: `framework/core/processors/messaging/none.py`

- [ ] **Step 1: password_login.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        ep = self.params.get("endpoint", "")
        if not ep:
            warnings.append("password-login 缺少 endpoint")
        fields = self.params.get("fields", {})
        if "phone" not in fields or "password" not in fields:
            warnings.append("password-login 缺少 fields.phone 或 fields.password 映射")
        rt = client._load_runtime()
        creds = rt.get("credentials", {})
        if not creds.get("phone"):
            warnings.append("未配置手机号，请在 Dashboard 设置凭据")
        if not creds.get("password"):
            warnings.append("未配置密码，请在 Dashboard 设置凭据")
        return len(warnings) == 0, warnings
```

- [ ] **Step 2: sms_login.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("sms_endpoint"):
            warnings.append("sms-login 缺少 sms_endpoint")
        if not self.params.get("login_endpoint"):
            warnings.append("sms-login 缺少 login_endpoint")
        return len(warnings) == 0, warnings
```

- [ ] **Step 3: header_token.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not client._auth_token:
            warnings.append("header-token 需要 auth_token，但当前为空")
        return len(warnings) == 0, warnings
```

- [ ] **Step 4: manual_token.py validate()**

```python
    def validate(self, client) -> tuple:
        return True, []
```

- [ ] **Step 5: plaintext auth validate()**

```python
    def validate(self, client) -> tuple:
        return True, []
```

- [ ] **Step 6: frida_rpc.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        script = self.params.get("script_name", "")
        if not script:
            warnings.append("frida-rpc 缺少 script_name")
        else:
            sp = client.config_path.parent / script
            if not sp.exists():
                warnings.append(f"frida-rpc 脚本 {script} 不存在")
        return len(warnings) == 0, warnings
```

- [ ] **Step 7: rest_json.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("endpoint"):
            warnings.append("rest-json 缺少 endpoint")
        return len(warnings) == 0, warnings
```

- [ ] **Step 8: rongcloud_tcp.py validate()**

```python
    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("app_key"):
            warnings.append("rongcloud-tcp 缺少 app_key")
        if not self.params.get("navi_url"):
            warnings.append("rongcloud-tcp 缺少 navi_url")
        return len(warnings) == 0, warnings
```

- [ ] **Step 9: plaintext messaging + none messaging validate()**

```python
# plaintext messaging
    def validate(self, client) -> tuple:
        return True, []

# none messaging
    def validate(self, client) -> tuple:
        warnings = ["messaging=none: 消息发送已禁用"]
        return False, warnings
```

- [ ] **Step 10: Commit**

```bash
git add framework/core/processors/auth/ framework/core/processors/messaging/
git commit -m "feat: add validate() to auth and messaging processors"
```

---

### Task 6: Dashboard SSE diagnose stream

**Files:**
- Modify: `framework/core/dashboard.py` (add route)

- [ ] **Step 1: 添加 SSE route**

在 `dashboard.py` 中添加（`from flask import Flask, jsonify, request, Response` 需加 `Response`，加 `from queue import Empty`）：
```python
@app.route("/api/app/<app_id>/diagnose/stream")
def api_app_diagnose_stream(app_id):
    """SSE stream of diagnose events for the given app."""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    q = task._diagnose.subscribe()

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            task._diagnose.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/dashboard.py
git commit -m "feat: add SSE diagnose stream endpoint"
```

---

## Phase 2: P1 — 环境检测 + 测试命令 + 配方

### Task 7: Unified PID detection in AdbDevice

**Files:**
- Modify: `framework/bridge/adb_device.py` (add `get_pid`)
- Modify: `framework/bridge/frida_session.py` (remove `_find_pid_via_adb`, use AdbDevice)
- Modify: `framework/core/processors/encryption/aes_cbc.py` (remove `_get_pid_via_adb`, use AdbDevice)

- [ ] **Step 1: 在 AdbDevice 添加 get_pid()**

```python
    @staticmethod
    def get_pid(serial: str, package: str) -> Optional[int]:
        """Find process PID via adb shell ps -A.
        Works around NIS packers that hide processes from Frida."""
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

在文件顶部加 `from typing import Optional`。

- [ ] **Step 2: 替换 frida_session.py 中的调用**

删除 `_find_pid_via_adb` 静态方法（行 157-180），替换调用点（行 92-93）：
```python
# 旧:
if not found_pid:
    found_pid = self._find_pid_via_adb(self.app_package, self.device_serial)
# 新:
if not found_pid:
    from framework.bridge.adb_device import AdbDevice
    found_pid = AdbDevice.get_pid(self.device_serial, self.app_package)
```

- [ ] **Step 3: 替换 aes_cbc.py 中的调用**

删除 `_get_pid_via_adb` 静态方法（行 211-229），替换调用点（行 83）：
```python
# 旧:
pid = self._get_pid_via_adb(serial, package)
# 新:
from framework.bridge.adb_device import AdbDevice
pid = AdbDevice.get_pid(serial, package)
```

同样替换行 92 的第二次调用。

- [ ] **Step 4: Commit**

```bash
git add framework/bridge/adb_device.py framework/bridge/frida_session.py framework/core/processors/encryption/aes_cbc.py
git commit -m "refactor: extract unified PID detection to AdbDevice.get_pid()"
```

---

### Task 8: Environment checker

**Files:**
- Create: `framework/bridge/env_checker.py`

- [ ] **Step 1: 创建 env_checker.py**

```python
"""EnvChecker — probe device environment: ADB, Frida server, NIS detection, platform"""
import subprocess
import sys
from typing import Optional

try:
    import frida
    HAS_FRIDA_PYTHON = True
except ImportError:
    HAS_FRIDA_PYTHON = False


class EnvChecker:
    """Probe the target device + platform and return a structured health report."""

    @classmethod
    def probe(cls, serial: str, package: str, frida_host: str = "127.0.0.1",
              frida_port: int = 27042) -> dict:
        result = {
            "adb": cls._check_adb(serial),
            "frida_server": cls._check_frida_server(frida_host, frida_port),
            "app": cls._check_app(serial, package),
            "nis": {},
            "platform": cls._check_platform(),
            "overall": "ok",
            "warnings": [],
            "recommendations": [],
        }

        # App PID
        if result["app"]["ok"]:
            pid = result["app"]["pid"]

            # Check if Frida Python binding can see processes
            nis_info = cls._check_nis(serial, package, frida_host, frida_port)
            result["nis"] = nis_info
            if nis_info["detected"]:
                result["warnings"].append("NIS/360 保护检测到进程隐藏，Frida CLI 模式将自动启用")
                result["recommendations"].append("cli_mode")

        if result["platform"]["pipe_risk"]:
            result["warnings"].append(
                "Windows 环境 subprocess 管道可能不稳定。建议配置 WSL 或使用 adb pull 备选方案")
            result["recommendations"].append("configure_wsl_or_file_transfer")

        # Chinese path detection
        import os
        cwd = os.getcwd()
        if any('一' <= c <= '鿿' for c in cwd):
            result["warnings"].append(
                f"项目路径包含中文字符: {cwd}。"
                f"Frida CLI subprocess 在 Windows 下可能因此失败。"
                f"建议迁移到纯 ASCII 路径 (如 C:\\projects\\)")
            result["recommendations"].append("move_to_ascii_path")

        if result["warnings"]:
            result["overall"] = "ok_with_warnings"
        if not result["adb"]["ok"] or not result["frida_server"]["ok"]:
            result["overall"] = "error"

        return result

    @classmethod
    def _check_adb(cls, serial: str) -> dict:
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            if serial in r.stdout and "device" in r.stdout:
                return {"ok": True, "serial": serial}
        except Exception:
            pass
        return {"ok": False, "serial": serial, "error": "ADB 未连接或设备未识别"}

    @classmethod
    def _check_frida_server(cls, host: str, port: int) -> dict:
        try:
            r = subprocess.run(
                ["frida-ps", "-H", f"{host}:{port}"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return {"ok": True, "host": host, "port": port}

                # Try to detect server type (hluda uses different port convention)
                server_type = "frida-server"
                if port == 27042:
                    server_type = "hluda (port 27042)"
                return {"ok": True, "host": host, "port": port, "type": server_type}
        except FileNotFoundError:
            return {"ok": False, "error": "frida CLI 未安装或不在 PATH 中"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @classmethod
    def _check_app(cls, serial: str, package: str) -> dict:
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if pid:
            return {"ok": True, "pid": pid, "package": package}
        return {"ok": False, "pid": None, "package": package,
                "error": f"App {package} 未运行"}

    @classmethod
    def _check_nis(cls, serial: str, package: str, host: str, port: int) -> dict:
        """Check if NIS/360 protection hides the process from Frida's enumerate."""
        if not HAS_FRIDA_PYTHON:
            return {"detected": False, "note": "frida Python 库未安装，无法检测"}

        try:
            from framework.bridge.adb_device import AdbDevice
            adb_pid = AdbDevice.get_pid(serial, package)
            if not adb_pid:
                return {"detected": False, "note": "App 未运行"}

            dm = frida.get_device_manager()
            try:
                dev = dm.get_device(serial)
            except Exception:
                try:
                    dev = dm.get_usb_device()
                except Exception:
                    return {"detected": False, "note": "Frida 连接失败"}

            found = False
            for p in dev.enumerate_processes():
                if package.lower() in (p.name or "").lower() or str(adb_pid) == str(p.pid):
                    found = True
                    break

            if not found:
                return {
                    "detected": True,
                    "pid_hidden": True,
                    "pid_via_adb": adb_pid,
                    "recommendation": (
                        "进程被 NIS/360 隐藏。"
                        "用 Frida CLI subprocess (frida -H host:port -p PID) "
                        "而非 Python frida.attach(package)。"
                        "使用 SecretKeySpec 而非 Cipher.init hook 以提前捕获密钥。"
                    ),
                }
            return {"detected": False}
        except Exception as e:
            return {"detected": False, "error": str(e)}

    @classmethod
    def _check_platform(cls) -> dict:
        info = {
            "os": sys.platform,
            "pipe_risk": sys.platform == "win32",
            "wsl_available": False,
        }

        if sys.platform == "win32":
            try:
                r = subprocess.run(
                    ["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    info["wsl_available"] = True
            except Exception:
                pass

        return info
```

- [ ] **Step 2: Commit**

```bash
git add framework/bridge/env_checker.py
git commit -m "feat: add EnvChecker for device+platform health probe"
```

---

### Task 9: Health check API in dashboard

**Files:**
- Modify: `framework/core/dashboard.py` (add route)

- [ ] **Step 1: 添加 health check route**

```python
@app.route("/api/app/<app_id>/health")
def api_app_health(app_id):
    """设备健康检查 — 探测 ADB、Frida、NIS、平台"""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    runtime = task._load_runtime()
    device = runtime.get("device", {})
    serial = device.get("serial", "")
    package = device.get("app_package",
                        task.config.get("meta", {}).get("package", ""))

    if not serial or not package:
        return jsonify({"error": "请先在 Dashboard 设置设备串号和包名"}), 400

    from framework.bridge.env_checker import EnvChecker
    result = EnvChecker.probe(serial, package)
    return jsonify(result)
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/dashboard.py
git commit -m "feat: add health check API endpoint"
```

---

### Task 10: Recipes system

**Files:**
- Create: `framework/core/recipes.py`
- Modify: `framework/core/processor_registry.py` (add recipe expansion)
- Modify: `framework/core/base_client.py:32-36` (recipe support in __init__)

- [ ] **Step 1: 创建 recipes.py**

```python
"""预置处理器组合配方 — 一个名字展开为完整 pipeline 配置"""
RECIPES = {
    "sybl-pattern": {
        "encryption": {
            "plugin": "aes-cbc",
            "params": {"key": None, "iv": None, "key_derivation": "session_key"},
        },
        "signing": {
            "plugin": "xor-triple-sign",
            "params": {"read_key": "01528e5f", "write_key": "01528e5f", "p3_key": "00000000"},
        },
        "auth": {
            "plugin": "password-login",
            "params": {
                "endpoint": "/UI/PasswordLoginPage/passwordLogin",
                "fields": {"phone": "phone", "password": "password"},
                "response_mapping": {"token": "token", "uid": "id"},
            },
        },
        "messaging": {
            "plugin": "frida-rpc",
            "params": {"script_name": "bridge.js"},
        },
    },
    "simple-rest": {
        "encryption": "plaintext",
        "signing": "plaintext",
        "auth": "header-token",
        "messaging": "rest-json",
    },
    "rongcloud": {
        "encryption": "plaintext",
        "signing": "plaintext",
        "auth": "header-token",
        "messaging": {
            "plugin": "rongcloud-tcp",
            "params": {"app_key": "", "navi_url": ""},
        },
    },
}


def expand_recipe(pipeline_config: dict) -> dict:
    """如果 pipeline 有 recipe 字段，展开为完整处理器配置。
    显式指定的处理器覆盖 recipe 中的对应项。"""
    recipe_name = pipeline_config.get("recipe")
    if not recipe_name:
        return pipeline_config

    base = RECIPES.get(recipe_name)
    if not base:
        raise ValueError(
            f"未知配方: {recipe_name}。可用: {list(RECIPES.keys())}")

    result = dict(base)
    for category in ["encryption", "signing", "auth", "messaging"]:
        if category in pipeline_config:
            result[category] = pipeline_config[category]
    return result
```

- [ ] **Step 2: 在 base_client.__init__ 中调用 expand_recipe**

修改 `__init__` 中 pipeline 加载部分（行 32-36）：
```python
        # Load processors from config (支持 recipe 展开)
        from .recipes import expand_recipe
        pipeline = expand_recipe(self.config.get("pipeline", {}))
        self._encryptor = ProcessorRegistry.load(pipeline.get("encryption", "plaintext"), "encryption")
        self._signer = ProcessorRegistry.load(pipeline.get("signing", "plaintext"), "signing")
        self._auth_processor = ProcessorRegistry.load(pipeline.get("auth", "manual-token"), "auth")
        self._messenger = ProcessorRegistry.load(pipeline.get("messaging", "none"), "messaging")
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/recipes.py framework/core/base_client.py
git commit -m "feat: add processor recipe system"
```

---

### Task 11: Processor test CLI

**Files:**
- Create: `framework/test_processor.py` (作为 `__main__` 入口)

- [ ] **Step 1: 创建 test_processor.py**

```python
"""Processor test CLI — python -m framework.test_processor --app <id> --category <cat>"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.core.base_client import BaseClient
from framework.core.processors.base import EncryptionProcessor, SigningProcessor, AuthProcessor


def test_encryption(client: BaseClient):
    enc = client._encryptor
    print(f"\n[test] encryption/{enc.name}:")

    # Validate
    ok, warnings = enc.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    # Key info
    if hasattr(enc, '_key') and enc._key:
        print(f"  key: 已设置 ({len(enc._key)} bytes)")
    else:
        method = enc.params.get("key_derivation", "unknown")
        print(f"  key: 未设置 (derivation={method})")

    if hasattr(enc, '_iv') and enc._iv:
        print(f"  IV:  {enc._iv[:20]}...")

    # Round-trip test
    try:
        test_body = {"test": "hello"}
        encoded = enc.encode(test_body)
        print(f"  encode: {json.dumps(test_body)} -> {encoded[:60]}... ({len(encoded)} chars)")
        decoded = enc.decode(encoded)
        assert decoded == test_body, f"往返失败: {decoded} != {test_body}"
        print(f"  ✓ 加密/解密往返成功")
    except Exception as e:
        print(f"  ✗ 往返测试失败: {e}")


def test_signing(client: BaseClient):
    sig = client._signer
    print(f"\n[test] signing/{sig.name}:")

    ok, warnings = sig.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    try:
        headers, params = sig.sign("https://example.com/api/test", {}, {"test": 1})
        print(f"  sign(url, headers, {{}}) -> p1=... (params={list(params.keys())})")
        print(f"  ✓ 签名生成成功")
    except Exception as e:
        print(f"  ✗ 签名失败: {e}")


def test_auth(client: BaseClient):
    auth = client._auth_processor
    print(f"\n[test] auth/{auth.name}:")

    ok, warnings = auth.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    try:
        result = auth.authenticate(client)
        if result:
            print(f"  ✓ 认证成功 (token={client._auth_token[:20]}..., uid={client._uid})")
        else:
            print(f"  ✗ 认证失败 — 请检查日志")
    except Exception as e:
        print(f"  ✗ 认证异常: {e}")


def test_validate_all(client: BaseClient):
    """Run validate() on all processors and print warnings."""
    all_ok = True
    for name, proc in [
        ("encryption", client._encryptor),
        ("signing", client._signer),
        ("auth", client._auth_processor),
        ("messaging", client._messenger),
    ]:
        ok, warnings = proc.validate(client)
        status = "✓" if ok else "✗"
        print(f"\n[{status}] {name}/{proc.name}:")
        if not warnings:
            print(f"  (无问题)")
        for w in warnings:
            print(f"  ! {w}")
            all_ok = False
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Processor test CLI")
    parser.add_argument("--app", required=True, help="App ID (e.g. sybl)")
    parser.add_argument("--category", choices=["encryption", "signing", "auth", "all"],
                        default="all", help="Processor category to test")
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent.parent / "apps" / args.app / "config.json"
    if not config_path.exists():
        print(f"错误: 找不到 config.json ({config_path})")
        sys.exit(1)

    client = BaseClient(str(config_path))

    if args.category == "encryption":
        test_encryption(client)
    elif args.category == "signing":
        test_signing(client)
    elif args.category == "auth":
        test_auth(client)
    else:
        ok = test_validate_all(client)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add framework/test_processor.py
git commit -m "feat: add processor test CLI (python -m framework.test_processor)"
```

---

## Phase 3: P2 — 模块化 Frida

### Task 12: Create module directory + crypto modules

**Files:**
- Create: `framework/bridge/modules/crypto/cipher_init.js`
- Create: `framework/bridge/modules/crypto/secret_key_spec.js`
- Create: `framework/bridge/modules/crypto/evp_cipher_init.js`

- [ ] **Step 1: cipher_init.js**

```javascript
// Hook Cipher.init( opmode, Key key ) — captures AES key+IV
(function(ctx) {
  var keyHex = null, ivHex = null;

  function install() {
    Java.perform(function() {
      var Cipher = Java.use("javax.crypto.Cipher");
      Cipher.init.overload('int', 'java.security.Key').implementation = function(opmode, key) {
        if (!keyHex && key) {
          var encoded = key.getEncoded();
          if (encoded && encoded.length === 32) {
            var h = ""; for (var i = 0; i < encoded.length; i++) h += ("0" + (encoded[i] & 0xFF).toString(16)).slice(-2);
            keyHex = h;
            ctx.shared.sessionKey = h;
            ctx.log("cipher_init", "AES-256 key captured via Cipher.init");
          }
        }
        return this.init(opmode, key);
      };
      Cipher.init.overload('int', 'java.security.cert.Certificate').implementation = function(opmode, cert) {
        return this.init(opmode, cert);
      };
      Cipher.init.overload('int', 'java.security.Key', 'java.security.spec.AlgorithmParameterSpec').implementation = function(opmode, key, spec) {
        if (!keyHex && key) {
          var encoded = key.getEncoded();
          if (encoded && encoded.length === 32) {
            var h = ""; for (var i = 0; i < encoded.length; i++) h += ("0" + (encoded[i] & 0xFF).toString(16)).slice(-2);
            keyHex = h;
            ctx.shared.sessionKey = h;
            ctx.log("cipher_init", "AES-256 key captured via Cipher.init(3-arg)");
          }
        }
        if (!ivHex && spec && spec.getIV) {
          var iv = spec.getIV();
          if (iv && iv.length === 16) {
            var h = ""; for (var i = 0; i < iv.length; i++) h += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
            ivHex = h;
            ctx.shared.sessionIV = h;
            ctx.log("cipher_init", "IV captured via IvParameterSpec");
          }
        }
        return this.init(opmode, key, spec);
      };
    });
  }

  ctx.register("cipher_init", {
    install: install,
    getState: function() { return {key_hex: keyHex, iv_hex: ivHex}; },
  });
})
```

- [ ] **Step 2: secret_key_spec.js**

```javascript
// Hook SecretKeySpec.$init — captures key BEFORE Cipher.init (bypasses NIS)
(function(ctx) {
  var keyHex = null;

  function install() {
    Java.perform(function() {
      var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
      SKS.$init.overload('[B', 'java.lang.String').implementation = function(kb, algo) {
        if (!keyHex && algo.indexOf("AES") >= 0 && kb.length === 32) {
          var h = ""; for (var i = 0; i < kb.length; i++) h += ("0" + (kb[i] & 0xFF).toString(16)).slice(-2);
          keyHex = h;
          ctx.shared.sessionKey = h;
          ctx.log("secret_key_spec", "AES-256 key captured");
        }
        return this.$init(kb, algo);
      };

      // Also capture IV
      var IvSpec = Java.use("javax.crypto.spec.IvParameterSpec");
      IvSpec.$init.overload('[B').implementation = function(iv) {
        if (!ctx.shared.sessionIV && iv.length === 16) {
          var h = ""; for (var i = 0; i < iv.length; i++) h += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
          ctx.shared.sessionIV = h;
          ctx.log("secret_key_spec", "IV captured");
        }
        return this.$init(iv);
      };
    });
  }

  ctx.register("secret_key_spec", {
    install: install,
    getState: function() { return {key_hex: keyHex, iv_hex: ctx.shared.sessionIV}; },
  });
})
```

- [ ] **Step 3: evp_cipher_init.js**

```javascript
// Native hook: EVP_CipherInit_ex in libcrypto.so (BoringSSL/OpenSSL)
// Zero Java.perform — completely bypasses NIS Java-level detection
(function(ctx) {
  var keyHex = null, ivHex = null;

  function install() {
    var mod = Process.findModuleByName("libcrypto.so");
    if (!mod) {
      ctx.log("evp_cipher_init", "libcrypto.so not loaded yet. Will retry.");
      return false;
    }
    var evpInit = Module.findExportByName("libcrypto.so", "EVP_CipherInit_ex");
    if (!evpInit) {
      ctx.log("evp_cipher_init", "EVP_CipherInit_ex not found");
      return false;
    }
    Interceptor.attach(evpInit, {
      onEnter: function(args) {
        if (keyHex && ivHex) return;
        var keyPtr = args[3], ivPtr = args[4];
        if (keyPtr.isNull() || ivPtr.isNull()) return;
        try {
          if (!keyHex) {
            var h = "";
            for (var i = 0; i < 32; i++) h += ("0" + keyPtr.add(i).readU8().toString(16)).slice(-2);
            if (h !== "0000000000000000000000000000000000000000000000000000000000000000") {
              keyHex = h;
              ctx.shared.sessionKey = h;
              ctx.log("evp_cipher_init", "AES-256 key captured (native)");
            }
          }
          if (!ivHex) {
            var h = "";
            for (var i = 0; i < 16; i++) h += ("0" + ivPtr.add(i).readU8().toString(16)).slice(-2);
            ivHex = h;
            ctx.shared.sessionIV = h;
            ctx.log("evp_cipher_init", "IV captured (native)");
          }
        } catch(e) {}
      }
    });
    ctx.log("evp_cipher_init", "Hooked EVP_CipherInit_ex");
    return true;
  }

  // Poll for libcrypto.so availability
  var installed = false;
  setInterval(function() {
    if (!installed) { installed = install(); }
  }, 1000);

  ctx.register("evp_cipher_init", {
    install: function() {},
    getState: function() { return {key_hex: keyHex, iv_hex: ivHex}; },
  });
})
```

- [ ] **Step 4: Commit**

```bash
mkdir -p framework/bridge/modules/crypto
git add framework/bridge/modules/crypto/cipher_init.js framework/bridge/modules/crypto/secret_key_spec.js framework/bridge/modules/crypto/evp_cipher_init.js
git commit -m "feat: add crypto Frida modules (cipher_init, secret_key_spec, evp_cipher_init)"
```

---

### Task 13: HTTP + RPC modules

**Files:**
- Create: `framework/bridge/modules/http/okhttp.js`
- Create: `framework/bridge/modules/http/cronet.js`
- Create: `framework/bridge/modules/rpc/key_export.js`
- Create: `framework/bridge/modules/rpc/messaging_rongcloud.js`
- Create: `framework/bridge/modules/rpc/messaging_rest.js`
- Create: `framework/bridge/modules/rpc/ws_rooms.js`

- [ ] **Step 1: okhttp.js**

```javascript
// Hook OkHttp Request.Builder.header() — capture session headers
(function(ctx) {
  var headers = {};
  var targetKeys = (ctx.moduleParams && ctx.moduleParams.host_blacklist) || [];

  function install() {
    Java.perform(function() {
      var RB = Java.use("okhttp3.Request$Builder");
      var keys = ["deviceToken", "SMDeviceId", "DeviceId", "clientSession", "Token",
                   "Authorization", "X-Token", "token", "Cookie"];
      RB.header.overload('java.lang.String', 'java.lang.String').implementation = function(k, v) {
        for (var i = 0; i < keys.length; i++) {
          if (k === keys[i]) {
            headers[k] = v;
            ctx.shared.sessionHeaders = headers;
          }
        }
        return this.header(k, v);
      };
    });
    ctx.log("okhttp", "Hooked OkHttp Request.Builder.header");
  }

  ctx.register("okhttp", {
    install: install,
    getState: function() { return {headers: headers}; },
  });
})
```

- [ ] **Step 2: cronet.js**

```javascript
// Hook Cronet (Google's HTTP stack) — stub, fill in per-app
(function(ctx) {
  var headers = {};
  ctx.register("cronet", {
    install: function() {
      ctx.log("cronet", "Cronet hooks not yet implemented for this app");
    },
    getState: function() { return {headers: headers}; },
  });
})
```

- [ ] **Step 3: key_export.js**

```javascript
// rpc/key_export.js — exposes getSessionKey + getHeaders via shared state
(function(ctx) {
  ctx.register("key_export", {
    install: function() {},
    getState: function() {
      return {
        key_hex: ctx.shared.sessionKey || null,
        iv_hex: ctx.shared.sessionIV || null,
        headers: ctx.shared.sessionHeaders || {},
      };
    },
  });
})
```

- [ ] **Step 4: messaging_rongcloud.js**

```javascript
// rpc/messaging_rongcloud.js — sends message via RongCloud IM SDK
(function(ctx) {
  var rongIMClient = null;

  function install() {
    Java.perform(function() {
      try {
        rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
        ctx.log("messaging_rongcloud", "RongIMClient acquired");
      } catch(e) {
        ctx.log("messaging_rongcloud", "RongIMClient not available yet");
      }
    });
  }

  function send(uid, text) {
    var result = {};
    Java.perform(function() {
      try {
        if (!rongIMClient) {
          rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
        }
        var msg = Java.use("io.rong.message.TextMessage").$new(text);
        var conv = Java.use("io.rong.imlib.model.Conversation$ConversationType").valueOf("PRIVATE");
        rongIMClient.sendMessage(conv, String(uid), msg, null, null, null);
        result = {success: true, uid: uid, text: text};
      } catch(e) {
        result = {success: false, error: String(e)};
      }
    });
    return JSON.stringify(result);
  }

  ctx.register("messaging_rongcloud", {
    install: install,
    send: send,
    getState: function() { return {ready: rongIMClient !== null}; },
  });
})
```

- [ ] **Step 5: messaging_rest.js**

```javascript
// rpc/messaging_rest.js — sends message via HTTP REST (generic OkHttp)
// Note: this module hooks app's own HTTP client, not used standalone
(function(ctx) {
  ctx.register("messaging_rest", {
    install: function() {
      ctx.log("messaging_rest", "REST messaging: use rest-json Python processor instead");
    },
    send: function(uid, text) {
      return JSON.stringify({success: false, error: "REST messaging uses Python processor"});
    },
    getState: function() { return {ready: false}; },
  });
})
```

- [ ] **Step 6: ws_rooms.js**

```javascript
// rpc/ws_rooms.js — captures WebSocket room list data
(function(ctx) {
  var rooms = [];

  function install() {
    Java.perform(function() {
      try {
        var OkHttpWS = Java.use("okhttp3.WebSocket");
        // Hook WebSocket onMessage for text frames
        var RealWS = Java.use("okhttp3.internal.ws.RealWebSocket");
        // Best-effort: intercept JSON responses containing room data
        ctx.log("ws_rooms", "WebSocket hooks registered. Navigate to room list in app.");
      } catch(e) {
        ctx.log("ws_rooms", "OkHttp WebSocket not found: " + e);
      }
    });
  }

  ctx.register("ws_rooms", {
    install: install,
    getState: function() { return {rooms: rooms}; },
  });
})
```

- [ ] **Step 7: Commit**

```bash
mkdir -p framework/bridge/modules/http framework/bridge/modules/rpc
git add framework/bridge/modules/http/okhttp.js framework/bridge/modules/http/cronet.js framework/bridge/modules/rpc/key_export.js framework/bridge/modules/rpc/messaging_rongcloud.js framework/bridge/modules/rpc/messaging_rest.js framework/bridge/modules/rpc/ws_rooms.js
git commit -m "feat: add HTTP and RPC Frida modules"
```

---

### Task 14: Frida module loader

**Files:**
- Create: `framework/bridge/frida_module_loader.py`

- [ ] **Step 1: 创建 frida_module_loader.py**

```python
"""FridaModuleLoader — concatenates JS modules and builds rpc.exports glue"""
from pathlib import Path
from typing import Optional


MODULES_DIR = Path(__file__).resolve().parent / "modules"

_GLUE_HEADER = """
// === framework glue: shared context ===
var ctx = {
  shared: {},
  modules: {},
  moduleParams: {module_params_json},
  _keyWritten: false,
  register: function(name, mod) { this.modules[name] = mod; },
  log: function(src, msg) { console.log("[module:" + src + "]", msg); },
};
"""

_GLUE_KEY_WATCHER = """
// === framework glue: key watcher ===
setInterval(function() {
  var key = ctx.shared.sessionKey;
  if (key && !ctx._keyWritten) {
    ctx._keyWritten = true;
    var data = JSON.stringify({
      key_hex: key,
      iv_hex: ctx.shared.sessionIV || null,
      headers: ctx.shared.sessionHeaders || {},
    });
    console.log("[bridge] KEY_JSON: " + data);
  }
}, 500);
"""

_GLUE_RPC_HEADER = """
// === framework glue: rpc.exports ===
rpc.exports = {
"""

_GLUE_RPC_FOOTER = """
};
console.log("[bridge] Ready.");
"""


class FridaModuleLoader:
    """Load and concatenate JS modules into a single injectable Frida script."""

    def __init__(self, module_specs: list[dict], rpc_methods: Optional[list[str]] = None):
        """
        module_specs: [{"name": "secret_key_spec", "params": {}}]
        rpc_methods: ["getSessionKey", "getHeaders", "sendMessage"]
        """
        self.module_specs = module_specs
        self.rpc_methods = rpc_methods or []

    def build_script(self) -> str:
        """Return complete JS script string for Frida injection."""
        parts = []

        # Collect module params for ctx.moduleParams
        module_params = {}
        for spec in self.module_specs:
            module_params[spec["name"]] = spec.get("params", {})

        # 1. Glue header with module params
        import json
        parts.append(_GLUE_HEADER.replace(
            "{module_params_json}", json.dumps(module_params)))

        # 2. Module IIFE blocks
        for spec in self.module_specs:
            mod_name = spec["name"]
            mod_path = self._find_module(mod_name)
            if not mod_path:
                raise FileNotFoundError(f"Frida module not found: {mod_name}")
            js = mod_path.read_text(encoding="utf-8")
            parts.append(f"\n// === module: {mod_name} ===\n")
            parts.append(js)
            parts.append("\n")

        # 3. Install phase
        parts.append("\n// === framework glue: install ===\n")
        # Non-Java modules first, then Java.perform modules
        install_order = self._install_order()
        for mod_name in install_order:
            parts.append(
                f'if (ctx.modules["{mod_name}"] && ctx.modules["{mod_name}"].install) '
                f'{{ ctx.modules["{mod_name}"].install(); }}\n'
            )

        # 4. Key watcher
        parts.append(_GLUE_KEY_WATCHER)

        # 5. RPC exports
        parts.append(_GLUE_RPC_HEADER)
        for method in self.rpc_methods:
            parts.append(self._build_rpc_method(method))
        # Always expose sendMessage if messaging module is present
        for spec in self.module_specs:
            name = spec["name"]
            if name.startswith("messaging_") and "sendMessage" not in self.rpc_methods:
                parts.append(
                    f'  sendMessage: function(uid, text) {{ '
                    f'return ctx.modules["{name}"].send(uid, text); '
                    f'}},\n'
                )
        parts.append(_GLUE_RPC_FOOTER)

        return "".join(parts)

    def _find_module(self, name: str) -> Optional[Path]:
        """Search modules/ subdirs for <name>.js"""
        for subdir in ["crypto", "http", "rpc"]:
            candidate = MODULES_DIR / subdir / f"{name}.js"
            if candidate.exists():
                return candidate
        return None

    def _install_order(self) -> list[str]:
        """Return module names in install order.
        Non-Java (evp_cipher_init) first, then Java.perform modules."""
        native_first = ["evp_cipher_init"]
        ordered = []
        for spec in self.module_specs:
            if spec["name"] in native_first:
                ordered.insert(0, spec["name"])
            else:
                ordered.append(spec["name"])
        return ordered

    def _build_rpc_method(self, method: str) -> str:
        """Map RPC method name to module call."""
        mappings = {
            "getSessionKey": '    getSessionKey: function() { return ctx.modules["key_export"].getState().key_hex; },\n',
            "getHeaders": '    getHeaders: function() { return ctx.modules["key_export"].getState().headers; },\n',
            "getStatus": '    getStatus: function() { var s = {}; for (var k in ctx.modules) { s[k] = ctx.modules[k].getState(); } return JSON.stringify(s); },\n',
            "sendMessage": '',  # handled separately
        }
        return mappings.get(method, f'    {method}: function() {{ return null; }},\n')
```

- [ ] **Step 2: Commit**

```bash
git add framework/bridge/frida_module_loader.py
git commit -m "feat: add FridaModuleLoader for JS module concatenation"
```

---

### Task 15: Integrate module loader into base_client + frida_session

**Files:**
- Modify: `framework/core/base_client.py` (__init__: load frida.modules config)
- Modify: `framework/bridge/frida_session.py` (connect: support module-built script)

- [ ] **Step 1: base_client 加载 frida.modules 配置**

在 `__init__` 中添加（frida 相关初始化区）：
```python
        # Frida module configuration
        frida_cfg = self.config.get("frida", {})
        self._frida_modules = frida_cfg.get("modules", None)  # None = use legacy script
        self._frida_rpc_methods = frida_cfg.get("rpc_methods", [])
        self._frida_legacy_script = frida_cfg.get("script", None)
```

- [ ] **Step 2: frida_session.connect 支持模块拼接**

在 `connect()` 方法中，`js_code = script_path.read_text(...)` 之前添加：
```python
            # Check if modules should be used instead of a monolithic script
            if script_path.suffix == ".json":
                # script_path points to a module manifest
                manifest = json.loads(script_path.read_text(encoding="utf-8"))
                modules = manifest.get("modules", [])
                rpc_methods = manifest.get("rpc_methods", [])
                if modules:
                    from framework.bridge.frida_module_loader import FridaModuleLoader
                    loader = FridaModuleLoader(modules, rpc_methods)
                    js_code = loader.build_script()
                    # skip normal file read
                    script_path_used = False
```

这需要重构 `connect()` 中的脚本加载部分。更简洁的方式：在 `dashboard.py` 的 start 路由中，如果检测到 `frida.modules`，先调用 `FridaModuleLoader.build_script()` 写到临时文件，再传给 `get_or_create()`。

简化方案：在 `dashboard.py` 的 `/api/app/<id>/start` 处理：
```python
    # Check for frida.modules config
    frida_cfg = config.get("frida", {})
    modules = frida_cfg.get("modules")
    if modules:
        from framework.bridge.frida_module_loader import FridaModuleLoader
        loader = FridaModuleLoader(modules, frida_cfg.get("rpc_methods", []))
        js_code = loader.build_script()
        tmp = Path(tempfile.gettempdir()) / "sybl_frida" / f"{app_id}_bridge.js"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(js_code, encoding="utf-8")
        script_path = str(tmp)
    else:
        script_path = str(APPS_DIR / app_id / script_name)
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/base_client.py framework/core/dashboard.py
git commit -m "feat: integrate Frida module loader into pipeline"
```

---

### Task 16: Update sybl config to use modules

**Files:**
- Modify: `apps/sybl/config.json`

- [ ] **Step 1: 添加 frida.modules 配置**

在 sybl config.json 的 `frida` 部分新增 `modules`：
```json
  "frida": {
    "enabled": true,
    "device": "usb",
    "package": "com.sybl.voiceroom",
    "script": "frida_key_bridge.js",
    "modules": [
      {"name": "secret_key_spec", "params": {}},
      {"name": "okhttp", "params": {"host_blacklist": ["log.shuangyuxingqiu.com"]}},
      {"name": "key_export", "params": {}},
      {"name": "messaging_rongcloud", "params": {"app_key": "m7ua80gbmdddm"}}
    ],
    "rpc_methods": ["getSessionKey", "getHeaders", "sendMessage", "getStatus"],
    "note": "Requires hluda-server (NOT frida-server). NIS anti-Frida detection. modules 优先于 script。"
  },
```

- [ ] **Step 2: Commit**

```bash
git add apps/sybl/config.json
git commit -m "feat: add frida.modules config to sybl"
```

---

## Phase 4: P3 — WSL 集成 + 错误恢复

### Task 17: Frida CLI wrapper with WSL support

**Files:**
- Create: `framework/bridge/frida_cli.py`

- [ ] **Step 1: 创建 frida_cli.py**

```python
"""Frida CLI wrapper — WSL-aware subprocess management for Frida CLI tool"""
import subprocess
import sys
import time
from pathlib import Path


class FridaCLI:
    """Manage Frida CLI subprocess with WSL auto-detection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27042):
        self.host = host
        self.port = port
        self._proc = None

    @staticmethod
    def wsl_available() -> bool:
        try:
            r = subprocess.run(
                ["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _get_wsl_host_ip() -> str:
        """Return Windows host IP accessible from WSL."""
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

    @staticmethod
    def _to_wsl_path(win_path: str) -> str:
        """Convert Windows path to WSL path: C:\\tmp\\x.js -> /mnt/c/tmp/x.js"""
        p = Path(win_path)
        drive = p.drive.lower().rstrip(":")
        parts = p.parts[1:]  # skip drive root
        return "/mnt/" + drive + "/" + "/".join(parts)

    def launch(self, pid: int, script_path: str) -> subprocess.Popen:
        """Launch Frida CLI and attach to pid, inject script. Returns Popen."""
        use_wsl = sys.platform == "win32" and self.wsl_available()

        if use_wsl:
            wsl_path = self._to_wsl_path(script_path)
            host_ip = self._get_wsl_host_ip()
            cmd = f"wsl frida -H {host_ip}:{self.port} -p {pid} -l {wsl_path}"
            print(f"[frida_cli] Using WSL: {cmd}")
        else:
            cmd = f'frida -H {self.host}:{self.port} -p {pid} -l "{script_path}"'

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=True,
        )
        self._proc = proc
        return proc

    def kill(self):
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


def build_frida_command(host: str, port: int, pid: int, script_path: str) -> str:
    """Return the appropriate frida command string for the current environment."""
    cli = FridaCLI(host, port)
    use_wsl = sys.platform == "win32" and FridaCLI.wsl_available()
    if use_wsl:
        wsl_path = FridaCLI._to_wsl_path(script_path)
        host_ip = FridaCLI._get_wsl_host_ip()
        return f"wsl frida -H {host_ip}:{port} -p {pid} -l {wsl_path}"
    return f'frida -H {host}:{port} -p {pid} -l "{script_path}"'
```

- [ ] **Step 2: Commit**

```bash
git add framework/bridge/frida_cli.py
git commit -m "feat: add FridaCLI wrapper with WSL support"
```

---

### Task 18: Update aes_cbc _derive_from_frida to use frida_cli

**Files:**
- Modify: `framework/core/processors/encryption/aes_cbc.py` (line 109-115)

- [ ] **Step 1: 替换 frida 命令构建**

```python
        # 旧代码 (line 109-115):
        frida_cmd = (
            f'frida -H 127.0.0.1:27042 -p {pid} '
            f'-l "{_tmp_script}"'
        )
        try:
            proc = subprocess.Popen(
                frida_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=True,
            )

        # 新代码:
        from framework.bridge.frida_cli import FridaCLI
        cli = FridaCLI("127.0.0.1", 27042)
        try:
            proc = cli.launch(pid, str(_tmp_script))
        except FileNotFoundError:
            client._notify("error", "frida CLI 未安装，请确认 PATH 中包含 frida")
            return
```

同时更新 proc kill 部分（约行 168）：
```python
        # 旧: proc.kill()
        # 新: cli.kill()
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/processors/encryption/aes_cbc.py
git commit -m "refactor: use FridaCLI wrapper in aes_cbc key derivation"
```

---

### Task 19: Error recovery + app restart

**Files:**
- Modify: `framework/core/base_client.py` (add `_ensure_app_running`)

- [ ] **Step 1: 在 BaseClient 中添加 _ensure_app_running**

```python
    @staticmethod
    def _ensure_app_running(serial: str, package: str) -> int | None:
        """Ensure the target app is running. Restart via monkey if dead. Returns PID."""
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if pid:
            return pid

        print(f"[base_client] App {package} 未运行，尝试启动...")
        subprocess.run(
            ["adb", "-s", serial, "shell", "monkey", "-p", package, "1"],
            timeout=15, capture_output=True,
        )
        time.sleep(5)
        return AdbDevice.get_pid(serial, package)
```

在 `_run_per_room` 中，FridaDisconnectedError 处理改为尝试恢复：
```python
            except FridaDisconnectedError:
                # Try recovery: restart app + reconnect
                rt = self._load_runtime()
                dev = rt.get("device", {})
                serial = dev.get("serial", "")
                package = dev.get("app_package",
                                  self.config.get("meta", {}).get("package", ""))
                if serial and package:
                    new_pid = self._ensure_app_running(serial, package)
                    if new_pid:
                        self._notify("info", f"App 已恢复 (PID={new_pid})，继续...")
                        # Retry current room once
                        try:
                            self.run_room(room, idx)
                            continue
                        except Exception:
                            pass
                self._notify("error", "Frida 会话已断开且无法恢复，任务暂停")
                self.pause()
                return
```

- [ ] **Step 2: Commit**

```bash
git add framework/core/base_client.py
git commit -m "feat: add app restart and error recovery in pipeline"
```

---

## 验证清单

全部任务完成后运行：

```bash
# 1. 诊断层验证: 启动 Dashboard，观察控制台是否有 diagnose 输出
python -m framework.core.dashboard

# 2. 处理器自检: 对每个 App 跑 validate
python -m framework.test_processor --app sybl
python -m framework.test_processor --app wefun
python -m framework.test_processor --app hifun
python -m framework.test_processor --app piaopiao

# 3. 环境检测: Dashboard 中点击"设备检测"按钮或 curl
curl http://127.0.0.1:3112/api/app/sybl/health

# 4. SSE 流: 浏览器打开
# EventSource("http://127.0.0.1:3112/api/app/sybl/diagnose/stream")

# 5. 模块 Frida: 
python -c "
from framework.bridge.frida_module_loader import FridaModuleLoader
loader = FridaModuleLoader([
    {'name': 'secret_key_spec', 'params': {}},
    {'name': 'okhttp', 'params': {}},
    {'name': 'key_export', 'params': {}},
    {'name': 'messaging_rongcloud', 'params': {}},
], ['getSessionKey', 'getHeaders', 'sendMessage'])
print(loader.build_script()[:500])
"
```

---

## 文件变更总览

| 新建 (15) | 修改 (22) |
|-----------|-----------|
| `framework/core/diagnose.py` | `framework/core/base_client.py` |
| `framework/core/recipes.py` | `framework/core/processors/base.py` |
| `framework/bridge/env_checker.py` | `framework/core/processors/encryption/aes_cbc.py` |
| `framework/bridge/frida_cli.py` | `framework/core/processors/encryption/plaintext.py` |
| `framework/bridge/frida_module_loader.py` | `framework/core/processors/signing/xor_triple.py` |
| `framework/test_processor.py` | `framework/core/processors/signing/md5_h5_sign.py` |
| `framework/bridge/modules/crypto/cipher_init.js` | `framework/core/processors/signing/sha1_sorted_kv.py` |
| `framework/bridge/modules/crypto/secret_key_spec.js` | `framework/core/processors/signing/plaintext.py` |
| `framework/bridge/modules/crypto/evp_cipher_init.js` | `framework/core/processors/auth/password_login.py` |
| `framework/bridge/modules/http/okhttp.js` | `framework/core/processors/auth/sms_login.py` |
| `framework/bridge/modules/http/cronet.js` | `framework/core/processors/auth/header_token.py` |
| `framework/bridge/modules/rpc/key_export.js` | `framework/core/processors/auth/manual_token.py` |
| `framework/bridge/modules/rpc/messaging_rongcloud.js` | `framework/core/processors/auth/plaintext.py` |
| `framework/bridge/modules/rpc/messaging_rest.js` | `framework/core/processors/messaging/frida_rpc.py` |
| `framework/bridge/modules/rpc/ws_rooms.js` | `framework/core/processors/messaging/rest_json.py` |
| | `framework/core/processors/messaging/rongcloud_tcp.py` |
| | `framework/core/processors/messaging/plaintext.py` |
| | `framework/core/processors/messaging/none.py` |
| | `framework/core/dashboard.py` |
| | `framework/bridge/adb_device.py` |
| | `framework/bridge/frida_session.py` |
| | `apps/sybl/config.json` |
