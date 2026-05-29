# 配置驱动截流框架 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除手写 client.py，用户上传 JSON 配置即可接入新 App

**Architecture:** BaseClient 重构为配置驱动，处理器管道 (encryption→signing→HTTP→解密→JSON) 通过 ProcessorRegistry 动态加载。TaskManager 从 importlib 扫描改为直接读 apps/ 下 config.json

**Tech Stack:** Python 3, requests, pycryptodome, Flask, JSON Schema

---

## File Map

```
新建:
  framework/core/processor_registry.py          ← 处理器注册/加载
  framework/core/processors/__init__.py          ← 自动发现所有处理器
  framework/core/processors/base.py              ← 5个处理器接口
  framework/core/processors/encryption/__init__.py
  framework/core/processors/encryption/plaintext.py
  framework/core/processors/encryption/aes_cbc.py
  framework/core/processors/signing/__init__.py
  framework/core/processors/signing/plaintext.py
  framework/core/processors/signing/xor_triple.py
  framework/core/processors/auth/__init__.py
  framework/core/processors/auth/manual_token.py
  framework/core/processors/auth/password_login.py
  framework/core/processors/messaging/__init__.py
  framework/core/processors/messaging/rest_json.py
  apps/shuangyu/config.json                     ← 新格式替换旧 client.py

修改:
  framework/core/base_client.py                  ← 重构: 配置驱动
  framework/core/task_manager.py                 ← 微改: 读 config.json
  framework/core/dashboard.py                    ← 新增 /apps/manage 路由 + 校验API
  docs/superpowers/specs/homepage.html           ← 添加 "+" 卡片链接
  docs/superpowers/specs/design-mockup.html      ← 详情页 processor 信息区

删除:
  apps/piaopiao/client.py                        ← 被新 config.json 替代
  apps/shuangyu/client.py                        ← 被新 config.json 替代
  apps/piaopiao/__init__.py                      ← 不再需要
  apps/shuangyu/__init__.py                      ← 不再需要
```

---

### Task 1: Processor 接口基类

**Files:**
- Create: `framework/core/processors/__init__.py`
- Create: `framework/core/processors/base.py`
- Create: `framework/core/processor_registry.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p framework/core/processors/encryption
mkdir -p framework/core/processors/signing
mkdir -p framework/core/processors/auth
mkdir -p framework/core/processors/messaging
```

- [ ] **Step 2: Create processors/__init__.py — auto-discovery**

Write `framework/core/processors/__init__.py`:

```python
"""自动发现并注册所有处理器子包"""
from .encryption.plaintext import PlaintextEncryption
from .encryption.aes_cbc import AesCbcEncryption
from .signing.plaintext import PlaintextSigning
from .signing.xor_triple import XorTripleSigning
from .auth.manual_token import ManualTokenAuth
from .auth.password_login import PasswordLoginAuth
from .messaging.rest_json import RestJsonMessaging
from .messaging.none import NoneMessaging
```

- [ ] **Step 3: Create processors/base.py — 5个接口类**

Write `framework/core/processors/base.py`:

```python
"""处理器基类 — 4个类别，统一接口"""
from abc import ABC, abstractmethod


class BaseProcessor(ABC):
    name: str = ""
    category: str = ""

    def __init__(self, params: dict):
        self.params = params

    @classmethod
    def params_schema(cls) -> dict:
        return {}


class EncryptionProcessor(BaseProcessor, ABC):
    category = "encryption"

    @abstractmethod
    def encode(self, body: dict) -> bytes:
        """dict → bytes (加密后)"""

    @abstractmethod
    def decode(self, raw: bytes) -> dict:
        """bytes (解密后) → dict"""

    def derive_key(self, client) -> None:
        """key=null 时从 client 上下文派生 key"""


class SigningProcessor(BaseProcessor, ABC):
    category = "signing"

    @abstractmethod
    def sign(self, url: str, headers: dict) -> dict:
        """返回添加签名头后的 headers"""


class AuthProcessor(BaseProcessor, ABC):
    category = "auth"

    @abstractmethod
    def authenticate(self, client) -> bool:
        """执行认证，成功返回 True"""

    def load_credentials(self, client) -> dict:
        """从 runtime.json 读取凭据"""
        runtime_path = client._runtime_path
        if runtime_path.exists():
            import json
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            return runtime.get("credentials", {})
        return {}


class MessagingProcessor(BaseProcessor, ABC):
    category = "messaging"

    @abstractmethod
    def send(self, client, uid: str, text: str) -> dict:
        """返回 {success: bool, error: str}"""
```

- [ ] **Step 4: Create processor_registry.py**

Write `framework/core/processor_registry.py`:

```python
"""处理器注册表 — 单例，按 category/name 索引"""
from .processors.base import BaseProcessor


class ProcessorRegistry:
    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, proc_class):
        key = f"{proc_class.category}/{proc_class.name}"
        cls._registry[key] = proc_class

    @classmethod
    def load(cls, spec, category: str) -> BaseProcessor:
        # spec can be "plaintext" (str shorthand) or {"plugin": "aes-cbc", "params": {...}}
        if isinstance(spec, str):
            plugin_name = spec
            params = {}
        else:
            plugin_name = spec["plugin"]
            params = spec.get("params", {})
        key = f"{category}/{plugin_name}"
        proc_class = cls._registry[key]
        return proc_class(params)

    @classmethod
    def list_all(cls) -> list[dict]:
        return [{"name": p.name, "category": p.category,
                 "schema": p.params_schema()}
                for p in cls._registry.values()]

    @classmethod
    def get_spec(cls, plugin_name: str, category: str) -> dict:
        """返回 {plugin, params} 规格，供 Web UI 渲染"""
        key = f"{category}/{plugin_name}"
        if key in cls._registry:
            return {
                "plugin": plugin_name,
                "params_schema": cls._registry[key].params_schema(),
            }
        return {}
```

- [ ] **Step 5: Verify import**

```bash
python -c "from framework.core.processor_registry import ProcessorRegistry; print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add framework/core/processors/ framework/core/processor_registry.py
git commit -m "feat: Processor 基类 + ProcessorRegistry 注册表"
```

---

### Task 2: Plaintext 处理器 (encryption + signing)

**Files:**
- Create: `framework/core/processors/encryption/plaintext.py`
- Create: `framework/core/processors/signing/plaintext.py`

- [ ] **Step 1: Create plaintext encryption**

Write `framework/core/processors/encryption/plaintext.py`:

```python
"""明文透传 — 不加密"""
import json
from ..base import EncryptionProcessor
from ...processor_registry import ProcessorRegistry


class PlaintextEncryption(EncryptionProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def encode(self, body: dict) -> bytes:
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def decode(self, raw: bytes) -> dict:
        return json.loads(raw.decode("utf-8"))


ProcessorRegistry.register(PlaintextEncryption)
```

- [ ] **Step 2: Create plaintext signing**

Write `framework/core/processors/signing/plaintext.py`:

```python
"""无签名 — 透传 headers"""
from ..base import SigningProcessor
from ...processor_registry import ProcessorRegistry


class PlaintextSigning(SigningProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def sign(self, url: str, headers: dict) -> dict:
        return headers


ProcessorRegistry.register(PlaintextSigning)
```

- [ ] **Step 3: Verify import**

```bash
python -c "from framework.core.processors.encryption.plaintext import PlaintextEncryption; from framework.core.processors.signing.plaintext import PlaintextSigning; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add framework/core/processors/encryption/plaintext.py framework/core/processors/signing/plaintext.py
git commit -m "feat: plaintext encryption + signing 处理器"
```

---

### Task 3: Manual Token 认证处理器

**Files:**
- Create: `framework/core/processors/auth/manual_token.py`

- [ ] **Step 1: Create manual-token auth**

Write `framework/core/processors/auth/manual_token.py`:

```python
"""手动 token 认证 — 直接从 runtime.json 读取 token/uid"""
from ..base import AuthProcessor
from ...processor_registry import ProcessorRegistry


class ManualTokenAuth(AuthProcessor):
    name = "manual-token"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "token_field": {"type": "string", "default": "token"},
                "uid_field": {"type": "string", "default": "uid"},
            },
        }

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        token = creds.get(self.params.get("token_field", "token"), "")
        uid = creds.get(self.params.get("uid_field", "uid"), "")
        if not token:
            return False
        client._auth_token = token
        client._uid = str(uid) if uid else ""
        client.config["token"] = token
        client.config["uid"] = client._uid
        return True


ProcessorRegistry.register(ManualTokenAuth)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from framework.core.processors.auth.manual_token import ManualTokenAuth; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/processors/auth/manual_token.py
git commit -m "feat: manual-token 认证处理器"
```

---

### Task 4: REST-JSON 私信处理器

**Files:**
- Create: `framework/core/processors/messaging/rest_json.py`

- [ ] **Step 1: Create rest-json messaging**

Write `framework/core/processors/messaging/rest_json.py`:

```python
"""纯 HTTP REST 私信 — preCheck → send 模式"""
from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry


class RestJsonMessaging(MessagingProcessor):
    name = "rest-json"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "precheck_path": {"type": "string"},
                "send_path": {"type": "string"},
            },
            "required": ["precheck_path", "send_path"],
        }

    def send(self, client, uid: str, text: str) -> dict:
        base = client.config["base_url"]
        precheck_path = self.params["precheck_path"]
        send_path = self.params["send_path"]

        try:
            precheck = client._post(
                f"{base}{precheck_path}",
                {"tuids": [uid]},
            )
            if not client.check_response(precheck):
                return {"success": False, "error": f"preCheck: {precheck.get('message', '')}"}

            msg_chat_id = precheck.get("data", {}).get("msgChatId", "")
            if not msg_chat_id:
                return {"success": False, "error": "no msgChatId"}

            resp = client._post(
                f"{base}{send_path}",
                {"tuid": uid, "content": text, "msgChatId": msg_chat_id, "type": "TEXT"},
            )
            if client.check_response(resp):
                return {"success": True, "error": ""}
            return {"success": False, "error": resp.get("message", "send failed")}
        except Exception as e:
            return {"success": False, "error": str(e)}


ProcessorRegistry.register(RestJsonMessaging)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from framework.core.processors.messaging.rest_json import RestJsonMessaging; print('OK')"
```

- [ ] **Step 2a: Create none messaging (no-op)**

Write `framework/core/processors/messaging/none.py`:

```python
"""无消息通道 — 私信不可用"""
from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry


class NoneMessaging(MessagingProcessor):
    name = "none"

    def send(self, client, uid: str, text: str) -> dict:
        return {"success": False, "error": "messaging 未配置 — 私信通道不存在"}


ProcessorRegistry.register(NoneMessaging)
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/processors/messaging/rest_json.py framework/core/processors/messaging/none.py
git commit -m "feat: rest-json + none 私信处理器"
```

---

### Task 5: AES-CBC 加密处理器

**Files:**
- Create: `framework/core/processors/encryption/aes_cbc.py`

- [ ] **Step 1: Create aes-cbc encryption**

Write `framework/core/processors/encryption/aes_cbc.py`:

```python
"""AES-256-CBC 加密 — 双鱼部落"""
import base64
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from ..base import EncryptionProcessor
from ...processor_registry import ProcessorRegistry


class AesCbcEncryption(EncryptionProcessor):
    name = "aes-cbc"

    def __init__(self, params: dict):
        super().__init__(params)
        self._key = None
        self._iv = None
        if params.get("key"):
            self._key = params["key"].encode("utf-8") if isinstance(params["key"], str) else params["key"]
        if params.get("iv"):
            self._iv = params["iv"].encode("utf-8") if isinstance(params["iv"], str) else params["iv"]

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": ["string", "null"], "description": "AES-256 key (32 bytes). null = derive from session"},
                "iv": {"type": ["string", "null"], "description": "AES IV (16 bytes). null = first 16 chars of token"},
                "key_derivation": {"type": ["string", "null"], "enum": [None, "device_token", "clientsession"]},
            },
        }

    def derive_key(self, client) -> None:
        if self._key is not None:
            return
        method = self.params.get("key_derivation", "device_token")
        if method == "device_token":
            seed = client.config.get("device_token", "")
        elif method == "clientsession":
            seed = client._session_id
        else:
            return
        if not seed:
            return
        import hashlib
        self._key = hashlib.sha256(seed.encode()).digest()
        self._iv = seed.replace("-", "")[:16].encode() if len(seed.replace("-", "")) >= 16 else b"FCE3F1A4-5DC3-41"

    def encode(self, body: dict) -> bytes:
        if self._key is None:
            raise RuntimeError("AES key not set — call derive_key() first")
        plain = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = AES.new(self._key, AES.MODE_CBC, self._iv or b"\x00" * 16)
        return base64.b64encode(cipher.encrypt(pad(plain, AES.block_size)))

    def decode(self, raw: bytes) -> dict:
        if self._key is None:
            raise RuntimeError("AES key not set — call derive_key() first")
        try:
            decoded = base64.b64decode(raw)
        except Exception:
            return json.loads(raw.decode("utf-8"))
        cipher = AES.new(self._key, AES.MODE_CBC, self._iv or b"\x00" * 16)
        return json.loads(unpad(cipher.decrypt(decoded), AES.block_size))


ProcessorRegistry.register(AesCbcEncryption)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from framework.core.processors.encryption.aes_cbc import AesCbcEncryption; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/processors/encryption/aes_cbc.py
git commit -m "feat: aes-cbc 加密处理器"
```

---

### Task 6: XOR Triple Sign 签名处理器

**Files:**
- Create: `framework/core/processors/signing/xor_triple.py`

- [ ] **Step 1: Create xor-triple signing**

Write `framework/core/processors/signing/xor_triple.py`:

```python
"""p1/p2/p3 XOR 签名 — 双鱼部落"""
import random
import time

from ..base import SigningProcessor
from ...processor_registry import ProcessorRegistry

WRITE_ENDPOINTS = {
    "passwordLogin", "joinRoom", "room/config",
    "UserRank/index", "sideRoomList", "connectSuccess", "RoomPage/leave",
}


class XorTripleSigning(SigningProcessor):
    name = "xor-triple-sign"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "read_key": {"type": "string", "description": "4-byte hex for read requests"},
                "write_key": {"type": "string", "description": "4-byte hex for write requests"},
                "p3_key": {"type": "string", "description": "4-byte hex for p3 XOR (write only)"},
            },
            "required": ["read_key", "write_key", "p3_key"],
        }

    def sign(self, url: str, headers: dict) -> dict:
        read_key = bytes.fromhex(self.params["read_key"])
        write_key = bytes.fromhex(self.params["write_key"])
        p3_key = bytes.fromhex(self.params["p3_key"])

        path = url.split("/UI/")[-1] if "/UI/" in url else url.split(".com/")[-1]
        is_write = any(w in path for w in WRITE_ENDPOINTS)
        authenticated = bool(headers.get("__auth_token__"))

        p1 = "".join(random.choices("0123456789abcdef", k=32))

        if not authenticated:
            p2 = p3 = p1
        else:
            key = write_key if is_write else read_key
            p2 = self._xor_hex(p1, key)
            p3 = self._xor_hex(p2, p3_key) if is_write else p2

        headers["p1"] = p1
        headers["p2"] = p2
        headers["p3"] = p3
        headers["timestamp"] = str(int(time.time()))
        return headers

    @staticmethod
    def _xor_hex(h: str, key: bytes) -> str:
        b = bytes.fromhex(h)
        repeats = (len(b) + len(key) - 1) // len(key)
        extended = (key * repeats)[:len(b)]
        return bytes(a ^ b for a, b in zip(b, extended)).hex()


ProcessorRegistry.register(XorTripleSigning)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from framework.core.processors.signing.xor_triple import XorTripleSigning; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/processors/signing/xor_triple.py
git commit -m "feat: xor-triple-sign 签名处理器"
```

---

### Task 7: Password Login 认证处理器

**Files:**
- Create: `framework/core/processors/auth/password_login.py`

- [ ] **Step 1: Create password-login auth**

Write `framework/core/processors/auth/password_login.py`:

```python
"""密码登录认证"""
import json

from ..base import AuthProcessor
from ...processor_registry import ProcessorRegistry


class PasswordLoginAuth(AuthProcessor):
    name = "password-login"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "登录 API 路径"},
                "fields": {"type": "object", "description": "请求字段映射"},
                "response_mapping": {
                    "type": "object",
                    "properties": {
                        "token": {"type": "string"},
                        "uid": {"type": "string"},
                    },
                },
            },
            "required": ["endpoint", "fields", "response_mapping"],
        }

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        endpoint = self.params["endpoint"]
        field_map = self.params["fields"]
        resp_map = self.params["response_mapping"]

        body = {}
        for internal_key, api_field in field_map.items():
            # Special: null fields get None value, not from credentials
            if internal_key in ("code", "mobile_token"):
                body[api_field] = None
            else:
                body[api_field] = creds.get(internal_key, "")

        base = client.config["base_url"]
        try:
            resp = client._post(f"{base}{endpoint}", body)
        except Exception as e:
            client._notify("error", f"登录请求失败: {e}")
            return False

        if not client.check_response(resp):
            client._notify("error", f"登录失败: {resp.get('message', '')}")
            return False

        data = resp.get("data", {})
        token = self._resolve_path(data, resp_map.get("token", "token"))
        uid = self._resolve_path(data, resp_map.get("uid", "uid"))

        if not token:
            client._notify("error", "登录响应缺少 token")
            return False

        client._auth_token = token
        client._uid = str(uid) if uid else ""

        # persist to config
        client.config["auth_token"] = token
        client.config["uid"] = client._uid
        client.config_path.write_text(
            json.dumps(client.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        nick = data.get("nickname", data.get("nick", ""))
        client._notify("info", f"登录成功 uid={uid} nick={nick}")
        return True

    @staticmethod
    def _resolve_path(data: dict, path: str):
        """从嵌套 JSON 取值，支持 'data.user.id' 格式"""
        parts = path.split(".")
        current = data
        for p in parts:
            if isinstance(current, dict):
                current = current.get(p)
            else:
                return None
        return current


ProcessorRegistry.register(PasswordLoginAuth)
```

- [ ] **Step 2: Verify import**

```bash
python -c "from framework.core.processors.auth.password_login import PasswordLoginAuth; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/processors/auth/password_login.py
git commit -m "feat: password-login 认证处理器"
```

---

### Task 8: BaseClient 重构为配置驱动

**Files:**
- Modify: `framework/core/base_client.py`

This is the core change. BaseClient 从抽象类变为具体类，直接从 config.json 加载处理器并执行。

- [ ] **Step 1: Rewrite BaseClient**

Write `framework/core/base_client.py`:

```python
"""BaseClient — 配置驱动 Pipeline，加载处理器链执行"""
import json
import random
import threading
import time
from pathlib import Path

import requests
import urllib3

from .state_manager import StateManager
from .processor_registry import ProcessorRegistry


class BaseClient:
    def __init__(self, config_path: str):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.config_path = Path(config_path)
        self._runtime_path = self.config_path.parent / "runtime.json"
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

        if "app_name" not in self.config.get("meta", {}):
            raise KeyError(f"[{config_path}] 缺少 meta.app_name")

        self.app_name = self.config["meta"]["app_name"]
        self.state = StateManager(str(self.config_path.parent))

        # Load processors from config
        pipeline = self.config.get("pipeline", {})
        self._encryptor = ProcessorRegistry.load(pipeline.get("encryption", "plaintext"), "encryption")
        self._signer = ProcessorRegistry.load(pipeline.get("signing", "plaintext"), "signing")
        self._auth_processor = ProcessorRegistry.load(pipeline.get("auth", "manual-token"), "auth")
        self._messenger = ProcessorRegistry.load(pipeline.get("messaging", "none"), "messaging")

        self.session = requests.Session()
        self.session.verify = False
        self._authenticated = False
        self._auth_token = self.config.get("auth_token", "")
        self._uid = str(self.config.get("uid", ""))
        self._session_id = self.config.get("client_session", "")

        # Runtime settings
        rt = self._load_runtime()
        settings = rt.get("settings", {})
        self._interval = settings.get("send_interval", 3)
        self._templates = rt.get("templates", ["{nick} 你好~"])
        self._data_sources = rt.get("data_sources", self.config.get("runtime_config", {}).get("data_sources", {}))
        self._periods = rt.get("periods", self.config.get("runtime_config", {}).get("periods", {}))
        self._genders = rt.get("genders", self.config.get("runtime_config", {}).get("genders", {}))

        self._data_source = list(self._data_sources.keys())[0] if self._data_sources else ""
        self._period = list(self._periods.keys())[0] if self._periods else ""
        self._gender = list(self._genders.keys())[0] if self._genders else ""

        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._lock = threading.Lock()
        self._rooms = []
        self._progress = {}
        self._on_update = None

        # Header defaults from config
        self._default_headers = self.config.get("server", {}).get("default_headers", {}).copy()
        self._base_url = self.config.get("server", {}).get("base_url", "")

    def _load_runtime(self) -> dict:
        if self._runtime_path.exists():
            return json.loads(self._runtime_path.read_text(encoding="utf-8"))
        return {}

    def _save_runtime(self, data: dict):
        current = self._load_runtime()
        current.update(data)
        self._runtime_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    # ═══ Auth ═══

    def authenticate(self) -> bool:
        # Try derive_key for encryption processor
        if hasattr(self._encryptor, 'derive_key'):
            self._encryptor.derive_key(self)
        return self._auth_processor.authenticate(self)

    # ═══ 3 core methods (config-driven) ═══

    def fetch_all_rooms(self) -> list:
        ep = self.config["endpoints"]["all_rooms"]
        if "steps" in ep:
            return self._execute_steps(ep)
        else:
            return self._fetch_paginated(ep)

    def _execute_steps(self, ep: dict) -> list:
        all_rooms = []
        step_results = {}

        for step in ep["steps"]:
            name = step["name"]
            path = step["path"]
            body_template = step.get("body", {})
            pagination = step.get("pagination")
            iter_source = step.get("iter_source", "")

            if iter_source:
                # Iterate over previous step results
                src_name, src_path = iter_source.split(".", 1)
                src_data = step_results.get(src_name, {})
                items = self._resolve_path(src_data, src_path) or []
                for item in items:
                    body = self._fill_template(body_template, _iter=item)
                    results = self._fetch_paginated(step, body)
                    all_rooms.extend(results)
            else:
                body = self._fill_template(body_template)
                results = self._fetch_paginated(step, body)
                step_results[name] = {"list": results, "raw": results}
                if not iter_source:
                    all_rooms = results

        # Apply output_mapping
        mapping = ep.get("output_mapping", {})
        return [self._map_fields(r, mapping) for r in all_rooms]

    def fetch_room_ranking(self, room: dict, period: str) -> list:
        ep = self.config["endpoints"]["ranking"]
        period_key = self._periods.get(period, "day")
        ds_key = self._data_sources.get(self._data_source, "")

        body = self._fill_template(ep.get("body", {}),
                                   room=room, period_key=period_key, data_source_key=ds_key)

        items = self._fetch_paginated(ep, body)
        mapping = ep.get("output_mapping", {})
        return [self._map_fields(u, mapping) for u in items]

    def send_message(self, uid: str, text: str) -> dict:
        return self._messenger.send(self, uid, text)

    # ═══ HTTP with processor pipeline ═══

    def _post(self, url: str, body: dict) -> dict:
        try:
            encrypted = self._encryptor.encode(body)
        except Exception as e:
            raise RuntimeError(f"encryption.encode failed: {e}")

        headers = dict(self._default_headers)
        headers["Content-Type"] = "text/plain; charset=UTF-8"
        headers["__auth_token__"] = self._auth_token

        headers = self._signer.sign(url, headers)

        r = self.session.post(url, data=encrypted, headers=headers, timeout=30)
        r.raise_for_status()

        try:
            return self._encryptor.decode(r.content)
        except Exception:
            try:
                return json.loads(r.text)
            except json.JSONDecodeError:
                raise RuntimeError(f"decryption.decode failed: {r.text[:200]}")

    def _get(self, url: str, params: dict = None) -> dict:
        params = params or {}
        headers = dict(self._default_headers)
        headers["__auth_token__"] = self._auth_token
        headers = self._signer.sign(url, headers)
        r = self.session.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        try:
            return self._encryptor.decode(r.content)
        except Exception:
            return json.loads(r.text)

    # ═══ Pagination ═══

    def _fetch_paginated(self, ep: dict, base_body: dict = None) -> list:
        path = ep["path"]
        method = ep.get("method", "POST")
        pagination = ep.get("pagination")
        base_url = self._base_url

        if base_body is None:
            base_body = {}

        if not pagination:
            resp = self._post(f"{base_url}{path}", base_body)
            if self.check_response(resp):
                return resp.get("data", {}).get("list", resp.get("data", []))
            return []

        ptype = pagination["type"]
        size = pagination.get("size", 20)
        stop_on = pagination.get("stop_on", "empty_list")
        results = []

        if ptype == "offset_limit":
            for offset in range(0, 500, size):
                body = dict(base_body)
                body["offset"] = offset
                body["limit"] = size
                resp = self._post(f"{base_url}{path}", body)
                if not self.check_response(resp):
                    break
                items = resp.get("data", {}).get("list", resp.get("data", []))
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        elif ptype == "page_number":
            for page in range(1, 50):
                body = dict(base_body)
                body["page"] = page
                body["page_size"] = size
                resp = self._post(f"{base_url}{path}", body)
                if not self.check_response(resp):
                    break
                items = resp.get("data", {}).get("list", resp.get("data", []))
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        return results

    # ═══ Template ═══

    def _fill_template(self, template, **kwargs) -> dict:
        import re
        result = {}
        for key, value in template.items():
            if isinstance(value, str) and "{{" in value:
                # Replace {{var.path}} patterns
                def replacer(m):
                    var_path = m.group(1)
                    parts = var_path.split(".", 1)
                    if parts[0] in kwargs:
                        obj = kwargs[parts[0]]
                        if len(parts) > 1 and isinstance(obj, dict):
                            return str(obj.get(parts[1], ""))
                        return str(obj)
                    return m.group(0)
                result[key] = re.sub(r'\{\{(.+?)\}\}', replacer, value)
            elif isinstance(value, dict):
                result[key] = self._fill_template(value, **kwargs)
            else:
                result[key] = value
        return result

    @staticmethod
    def _resolve_path(data: dict, path: str):
        parts = path.split(".")
        current = data
        for p in parts:
            if isinstance(current, dict):
                current = current.get(p)
            elif isinstance(current, list):
                try:
                    idx = int(p)
                    current = current[idx] if idx < len(current) else None
                except ValueError:
                    return None
            else:
                return None
        return current

    def _map_fields(self, raw: dict, mapping: dict) -> dict:
        result = {}
        for framework_field, source in mapping.items():
            if isinstance(source, str) and "{{" in source:
                result[framework_field] = self._fill_template({"k": source}, **{})["k"]
            elif isinstance(source, str) and "." in source:
                result[framework_field] = self._resolve_path(raw, source)
            else:
                # literal or simple field name
                result[framework_field] = raw.get(source, source)
        return result

    # ═══ Pipeline (unchanged) ═══

    def run_pipeline(self) -> None:
        self._running = True
        self._pause_event.set()

        if not self._authenticated:
            if not self.authenticate():
                self._notify("error", "认证失败")
                self._running = False
                return
            self._authenticated = True

        self._rooms = self.state.load_rooms()
        if not self._rooms:
            self._notify("info", "扫描房间...")
            try:
                self._rooms = self.fetch_all_rooms()
            except Exception as e:
                self._notify("error", f"扫描房间失败: {e}")
                self._running = False
                return
            self.state.save_rooms(self._rooms)
            self._notify("info", f"扫描完成: {len(self._rooms)} 间房")

        with self._lock:
            self._progress = self.state.load_progress()
            start_idx = self._progress.get("current_room_index", 0)

        consecutive_failures = 0
        for idx in range(start_idx, len(self._rooms)):
            if not self._wait_if_paused():
                break
            room = self._rooms[idx]
            self._notify("progress", {"current_room_index": idx, "room": room})
            try:
                self.run_room(room, idx)
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                self._notify("error", f"房间 {room.get('name')} 失败: {e}")
                if consecutive_failures >= 3:
                    self._notify("error", "连续3间房间失败，暂停")
                    self.pause()
                    break

        if self._running:
            self._notify("done", "全部房间完成")
            self.state.reset_progress()
        self._running = False

    def run_room(self, room: dict, idx: int) -> None:
        with self._lock:
            self.state.save_progress(
                current_room_index=idx,
                current_room_name=room.get("name", ""),
            )

        try:
            users = self.fetch_room_ranking(room, self._period)
        except Exception as e:
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        if self._gender != "全部":
            target = self._genders.get(self._gender)
            if target is not None:
                users = [u for u in users if u.get("gender") == target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        for user in users:
            if not self._wait_if_paused():
                break

            uid = user.get("uid", "")
            nick = user.get("nick", "")

            if self.state.is_sent_today(uid):
                continue

            template = random.choice(self._templates)
            text = template.replace("{nick}", nick).replace("{room_name}", room.get("name", ""))

            try:
                result = self.send_message(uid, text)
            except Exception as e:
                result = {"success": False, "error": str(e)}

            if result.get("success"):
                self.state.mark_sent(uid, nick, room.get("name", ""))
                with self._lock:
                    sent = self._progress.get("sent_total", 0) + 1
                    self._progress["sent_total"] = sent
                    self.state.save_progress(sent_total=sent)
                self._notify("sent", {"uid": uid, "nick": nick, "text": text})
            else:
                with self._lock:
                    failed = self._progress.get("failed_total", 0) + 1
                    self._progress["failed_total"] = failed
                    self.state.save_progress(failed_total=failed)
                self._notify("failed", {
                    "uid": uid, "nick": nick,
                    "error": result.get("error", "unknown"),
                })

            time.sleep(self._interval)

    # ═══ Control (unchanged) ═══

    def _wait_if_paused(self) -> bool:
        self._pause_event.wait()
        return self._running

    def refresh_rooms(self) -> list:
        self._rooms = self.fetch_all_rooms()
        self.state.save_rooms(self._rooms)
        return self._rooms

    def start(self) -> None:
        t = threading.Thread(target=self.run_pipeline, daemon=True)
        t.start()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        self._running = False
        self._pause_event.set()

    def reset_progress(self) -> None:
        with self._lock:
            self._progress = {}
            self.state.reset_progress()

    @property
    def status(self) -> str:
        if not self._running:
            return "idle"
        if not self._pause_event.is_set():
            return "paused"
        return "running"

    def get_stats(self) -> dict:
        with self._lock:
            progress = dict(self._progress)
            rooms = list(self._rooms)
        total_rooms = len(rooms)
        current_idx = progress.get("current_room_index", 0)
        return {
            "app_name": self.app_name,
            "status": self.status,
            "total_rooms": total_rooms,
            "done_rooms": current_idx,
            "sent": progress.get("sent_total", 0),
            "failed": progress.get("failed_total", 0),
            "current_room": progress.get("current_room_name", ""),
            "mode": self.config.get("send_mode", "rest"),
            "interval": self._interval,
            "data_source": self._data_source,
            "period": self._period,
            "gender": self._gender,
        }

    def _notify(self, event: str, payload) -> None:
        if self._on_update:
            self._on_update(event, payload)

    def check_response(self, resp_data: dict) -> bool:
        code = resp_data.get("code")
        return code in (200, "S_OK", 0)

    def build_headers(self) -> dict:
        return self._default_headers
```

- [ ] **Step 2: Verify import with processors loaded**

```bash
python -c "from framework.core.processors import *; from framework.core.processor_registry import ProcessorRegistry; print('Registered:', len(ProcessorRegistry._registry)); from framework.core.base_client import BaseClient; print('BaseClient OK')"
```

Expected output: Registered: 7 (or number of processors), BaseClient OK

- [ ] **Step 3: Commit**

```bash
git add framework/core/base_client.py
git commit -m "refactor: BaseClient 配置驱动 — 加载处理器链替代子类继承"
```

---

### Task 9: TaskManager 适配配置驱动

**Files:**
- Modify: `framework/core/task_manager.py`

- [ ] **Step 1: Rewrite TaskManager — 读 config.json**

Write `framework/core/task_manager.py`:

```python
"""多 App 任务调度 — 从 apps/ 读取 config.json 发现 App"""
import json
from pathlib import Path
from typing import Optional

from framework.core.base_client import BaseClient


class TaskManager:
    def __init__(self, apps_dir: str = None):
        if apps_dir is None:
            apps_dir = Path(__file__).resolve().parent.parent.parent / "apps"
        self.apps_dir = Path(apps_dir)
        self._tasks: dict[str, BaseClient] = {}
        self._discover()

    def _discover(self) -> None:
        if not self.apps_dir.exists():
            return
        for item in sorted(self.apps_dir.iterdir()):
            if not item.is_dir():
                continue
            config_file = item / "config.json"
            if not config_file.exists():
                continue
            app_id = item.name
            try:
                client = BaseClient(str(config_file))
                self._tasks[app_id] = client
            except Exception as e:
                print(f"[TaskManager] 加载 {app_id} 失败: {e}")

    def register(self, app_id: str, config_path: str) -> bool:
        """动态注册新 App（Web 上传后调用）"""
        try:
            client = BaseClient(config_path)
            self._tasks[app_id] = client
            return True
        except Exception as e:
            print(f"[TaskManager] 注册 {app_id} 失败: {e}")
            return False

    def unregister(self, app_id: str) -> bool:
        if app_id in self._tasks:
            task = self._tasks.pop(app_id)
            task.stop()
            return True
        return False

    # ═══ 控制 ═══

    def start(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task or task.status == "running":
            return False
        task.start()
        return True

    def pause(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task or task.status != "running":
            return False
        task.pause()
        return True

    def stop(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task:
            return False
        task.stop()
        task.reset_progress()
        return True

    def rescan_rooms(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task:
            return False
        try:
            task.refresh_rooms()
            return True
        except Exception as e:
            print(f"[TaskManager] 重新扫描 {app_id} 失败: {e}")
            return False

    # ═══ 查询 ═══

    def get_all_stats(self) -> list:
        return [t.get_stats() for t in self._tasks.values()]

    def get_stats(self, app_id: str) -> Optional[dict]:
        task = self._tasks.get(app_id)
        return task.get_stats() if task else None

    def get_task(self, app_id: str) -> Optional[BaseClient]:
        return self._tasks.get(app_id)

    @property
    def task_ids(self) -> list:
        return list(self._tasks.keys())
```

- [ ] **Step 2: Test discovery**

```bash
python -c "
from framework.core.task_manager import TaskManager
tm = TaskManager()
print('发现:', tm.task_ids)
for tid in tm.task_ids:
    t = tm.get_task(tid)
    print(f'  {tid}: status={t.status}, processors: enc={t._encryptor.name}, sign={t._signer.name}')
"
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/task_manager.py
git commit -m "refactor: TaskManager 读 config.json 发现 App，支持动态注册/注销"
```

---

### Task 10: 迁移 piaopiao 到新配置格式

**Files:**
- Create: `apps/piaopiao/config.json` (新格式)
- Modify: `apps/piaopiao/config.json` (旧 → 新)

- [ ] **Step 1: Write new config.json**

Write `apps/piaopiao/config.json`:

```json
{
  "meta": { "app_name": "漂漂", "subtitle": "Popo Live", "version": "1.0" },
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
      "body": { "catId": 1, "offset": 0, "limit": 20 },
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
    "settings": { "send_interval": 3 },
    "data_sources": { "贡献榜": "contribute" },
    "periods": { "今日": "day", "本周": "week" },
    "genders": { "全部": null, "男": 1, "女": 2 },
    "templates": ["{nick}你好，来我房间玩嘛~", "{nick}在吗？聊聊呀", "{nick}哈喽~ 来听听歌吧"]
  },
  "auth_mode": "manual",
  "token": "jFOm3hZAcMy09UW2QiCs8LHs9AWQwDMg",
  "uid": "91769319",
  "base_url": "https://api.pp.weimipopo.com"
}
```

- [ ] **Step 2: Remove old client.py and __init__.py**

```bash
rm apps/piaopiao/client.py apps/piaopiao/__init__.py
```

- [ ] **Step 3: Test piaopiao discovery**

```bash
python -c "
from framework.core.task_manager import TaskManager
tm = TaskManager()
t = tm.get_task('piaopiao')
print('app_name:', t.app_name)
print('encryptor:', t._encryptor.name)
print('signer:', t._signer.name)
print('messenger:', t._messenger.name)
print('rooms url:', t.config['endpoints']['all_rooms']['path'])
"
```

- [ ] **Step 4: Commit**

```bash
git rm apps/piaopiao/client.py apps/piaopiao/__init__.py
git add apps/piaopiao/config.json
git commit -m "refactor: piaopiao 迁移到配置驱动 — 删除 client.py，换新 config.json"
```

---

### Task 11: 迁移 shuangyu 到新配置格式

**Files:**
- Create: `apps/shuangyu/config.json` (新格式)

- [ ] **Step 1: Write new config.json**

Write `apps/shuangyu/config.json`:

```json
{
  "meta": { "app_name": "双鱼部落", "subtitle": "Shuangyu", "version": "2.47.1", "config_schema": "2.0" },
  "server": {
    "base_url": "https://ui-api-cn.shuangyuxingqiu.com",
    "default_headers": {
      "clienttype": "Android",
      "channel": "oppo",
      "build": "334",
      "appversion": "2.47.1",
      "devicetype": "Samsung SM-S9280",
      "isemulator": "true",
      "isrooted": "false",
      "hasfrida": "false",
      "hasxposed": "false"
    }
  },
  "pipeline": {
    "encryption": {
      "plugin": "aes-cbc",
      "params": { "key": "WW45anNMUmJIazBvNll5a1JKOElMb1ZkMXlncWtBTUs=", "iv": "FCE3F1A4-5DC3-41", "key_derivation": null }
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
      "plugin": "none",
      "params": {}
    }
  },
  "endpoints": {
    "all_rooms": {
      "steps": [
        { "name": "categories", "path": "/UI/Room/Home/categoryList", "method": "POST", "body": {} },
        {
          "name": "room_list",
          "path": "/UI/Room/Home/roomList",
          "method": "POST",
          "body": { "id": "{{_iter.id}}", "page": 1, "page_size": 20 },
          "iter_source": "categories.list",
          "pagination": { "type": "page_number", "size": 20, "stop_on": "empty_list" }
        }
      ],
      "output_mapping": { "id": "id", "name": "name", "type": "room_type" }
    },
    "ranking": {
      "path": "/UI/Room/UserRank/list",
      "method": "POST",
      "body": { "room_id": "{{room.id}}", "mode": "{{data_source_key}}", "rank_type": "{{period_key}}" },
      "pagination": { "type": "offset_limit", "size": 20, "stop_on": "empty_list" },
      "output_mapping": { "uid": "uid", "nick": "nickname", "amount": "amount", "gender": "gender" }
    }
  },
  "runtime_config": {
    "settings": { "send_interval": 3 },
    "data_sources": { "贡献榜": "rich", "魅力榜": "charm", "财富榜": "wealth" },
    "periods": { "今日": "day", "本周": "week", "本月": "month" },
    "genders": { "全部": null, "男": 1, "女": 2 },
    "templates": ["{nick} 你好~"]
  },
  "base_url": "https://ui-api-cn.shuangyuxingqiu.com",
  "device_id": "5f467c6c3f03a8a5",
  "device": "Samsung SM-S9280",
  "auth_mode": "password",
  "phone": "13721057968",
  "password": "zxc2005"
}
```

- [ ] **Step 2: Remove old client.py and __init__.py**

```bash
rm apps/shuangyu/client.py apps/shuangyu/__init__.py
```

- [ ] **Step 3: Test shuangyu discovery**

```bash
python -c "
from framework.core.task_manager import TaskManager
tm = TaskManager()
t = tm.get_task('shuangyu')
print('app_name:', t.app_name)
print('encryptor:', t._encryptor.name)
print('signer:', t._signer.name)
print('auth:', t._auth_processor.name)
print('steps:', len(t.config['endpoints']['all_rooms']['steps']))
"
```

- [ ] **Step 4: Commit**

```bash
git rm apps/shuangyu/client.py apps/shuangyu/__init__.py
git add apps/shuangyu/config.json
git commit -m "refactor: shuangyu 迁移到配置驱动 — 删除 client.py，换新 config.json"
```

---

### Task 12: Dashboard 新增 /apps/manage 路由 + 校验 API

- [ ] **Step 1: Add routes to dashboard.py**

Edit `framework/core/dashboard.py` — add these routes before the `run_dashboard` function:

```python
import shutil

ALLOWED_CONFIG_SCHEMAS = ["2.0"]


@app.route("/apps/manage")
def apps_manage():
    """App 管理页"""
    html = Path(__file__).resolve().parent.parent.parent / "docs" / "superpowers" / "specs" / "app-manage.html"
    if html.exists():
        return html.read_text(encoding="utf-8")
    return "<h1>App 管理</h1><p>app-manage.html not found</p>", 404


@app.route("/api/apps/upload", methods=["POST"])
def api_apps_upload():
    """上传配置 JSON → 校验 → 保存"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求体为空"}), 400

    errors, warnings = _validate_config(data)

    if errors:
        return jsonify({"success": False, "errors": errors, "warnings": warnings}), 400

    app_name = data["meta"]["app_name"]
    app_dir = APPS_DIR / app_name
    app_dir.mkdir(parents=True, exist_ok=True)

    # Save config.json
    config_path = app_dir / "config.json"
    _atomic_write_json(config_path, data)

    # Create runtime.json template
    runtime_path = app_dir / "runtime.json"
    if not runtime_path.exists():
        runtime = {
            "credentials": {},
            "settings": data.get("runtime_config", {}).get("settings", {}),
            "data_sources": data.get("runtime_config", {}).get("data_sources", {}),
            "periods": data.get("runtime_config", {}).get("periods", {}),
            "genders": data.get("runtime_config", {}).get("genders", {}),
            "templates": data.get("runtime_config", {}).get("templates", []),
        }
        _atomic_write_json(runtime_path, runtime)

    # Dynamic register
    manager.register(app_name, str(config_path))

    return jsonify({"success": True, "app_id": app_name, "warnings": warnings})


@app.route("/api/apps/<app_id>/test", methods=["POST"])
def api_apps_test(app_id):
    """测试连接 — 执行 authenticate()"""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"success": False, "error": "App 未找到"}), 404

    result = {"success": False, "error": ""}
    def _on_test(event, payload):
        result["event"] = event
        if event == "error":
            result["error"] = str(payload)
        elif event == "info":
            result["success"] = True

    task._on_update = _on_test
    ok = task.authenticate()
    result["success"] = ok
    return jsonify(result)


@app.route("/api/apps/<app_id>", methods=["DELETE"])
def api_apps_delete(app_id):
    """删除 App"""
    manager.unregister(app_id)
    app_dir = APPS_DIR / app_id
    if app_dir.exists():
        shutil.rmtree(str(app_dir))
    return jsonify({"success": True})


@app.route("/api/processors")
def api_processors():
    """返回所有已注册处理器列表"""
    from framework.core.processor_registry import ProcessorRegistry
    return jsonify(ProcessorRegistry.list_all())


def _validate_config(data: dict) -> tuple:
    errors = []
    warnings = []

    # JSON syntax (already done by request.get_json)
    try:
        schema_ver = data.get("meta", {}).get("config_schema", "1.0")
        if schema_ver not in ALLOWED_CONFIG_SCHEMAS:
            errors.append(f"不支持的 config_schema 版本: {schema_ver}")
    except Exception:
        errors.append("meta.config_schema 字段缺失或无效")

    # Required fields
    if not data.get("meta", {}).get("app_name"):
        errors.append("缺少 meta.app_name")
    if not data.get("server", {}).get("base_url"):
        errors.append("缺少 server.base_url")

    # Processor existence
    from framework.core.processor_registry import ProcessorRegistry
    pipeline = data.get("pipeline", {})
    for category in ["encryption", "signing", "auth", "messaging"]:
        spec = pipeline.get(category, "plaintext")
        plugin_name = spec if isinstance(spec, str) else spec.get("plugin", "plaintext")
        key = f"{category}/{plugin_name}"
        if key not in ProcessorRegistry._registry:
            errors.append(f"处理器不存在: {key}")

    # URL reachability (warning only)
    base_url = data.get("server", {}).get("base_url", "")
    if base_url and not errors:
        try:
            import urllib3
            urllib3.disable_warnings()
            r = requests.head(base_url, timeout=5, verify=False)
        except Exception:
            warnings.append(f"base_url 不可达: {base_url}")

    return errors, warnings
```

- [ ] **Step 2: Add import at top of dashboard.py**

Edit `framework/core/dashboard.py` — add `import shutil` near the existing imports.

- [ ] **Step 3: Verify routes exist**

```bash
python -c "
from framework.core.dashboard import app
rules = [r.rule for r in app.url_map.iter_rules()]
print('Routes:', [r for r in rules if 'apps' in r or 'processors' in r])
"
```

- [ ] **Step 4: Commit**

```bash
git add framework/core/dashboard.py
git commit -m "feat: Dashboard 新增 /apps/manage 路由 + 配置上传校验 API"
```

---

### Task 13: End-to-end 集成验证

- [ ] **Step 1: Full import chain test**

```bash
python -c "
# Import chain: processors → registry → base_client → task_manager → dashboard
from framework.core.processors import *
from framework.core.processor_registry import ProcessorRegistry
from framework.core.base_client import BaseClient
from framework.core.task_manager import TaskManager
from framework.core.dashboard import app

print('All imports OK')
print(f'Processors registered: {len(ProcessorRegistry._registry)}')
for k, v in ProcessorRegistry._registry.items():
    print(f'  {k}')

tm = TaskManager()
print(f'Apps discovered: {tm.task_ids}')
for tid in tm.task_ids:
    t = tm.get_task(tid)
    print(f'  {tid}: enc={t._encryptor.name} sign={t._signer.name} auth={t._auth_processor.name} msg={t._messenger.name}')
"
```

- [ ] **Step 2: Start dashboard and verify**

```bash
timeout 5 python -m framework.core.dashboard 2>&1 || true
```

Expected: Flask starts without import errors.

- [ ] **Step 3: Commit (if any final fixes)**

```bash
git add -A
git commit -m "chore: 端到端集成验证通过"
```

---

### Task 14: SMS Login 处理器 (待完成)

> **依赖**: 逆向双鱼 AES key 派生问题解决后才能实现
> 基于现有 AccountManager 封装

---

### Task 15: Rongcloud TCP 处理器 (待完成)

> **依赖**: 融云 TCP 协议实现
> rongcloud-tcp 处理器放到 `framework/core/processors/messaging/rongcloud_tcp.py`
