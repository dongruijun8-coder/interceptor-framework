"""AES-256-CBC 加密 — 双鱼部落"""
import base64
import json
import re
import time
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from ..base import EncryptionProcessor
from framework.bridge.frida_cli import FridaCliSession


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
                "key_derivation": {"type": ["string", "null"], "enum": [None, "device_token", "clientsession", "session_key"]},
            },
        }

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

    def derive_key(self, client) -> None:
        if self._key is not None:
            return
        method = self.params.get("key_derivation", "device_token")
        if method == "device_token":
            seed = client.config.get("device_token", "")
        elif method == "clientsession":
            seed = client._session_id
        elif method == "session_key":
            self._derive_from_frida(client)
            return
        else:
            return
        if not seed:
            return
        import hashlib
        self._key = hashlib.sha256(seed.encode()).digest()
        self._iv = seed.replace("-", "")[:16].encode() if len(seed.replace("-", "")) >= 16 else b"FCE3F1A4-5DC3-41"

    # ═══ Frida session_key derivation ═══

    def _derive_from_frida(self, client) -> None:
        """Fetch AES key via Frida CLI subprocess (bypasses NIS anti-Frida).

        Caches key per PID — if PID unchanged, reuses cached key+headers.
        Also launches Frida CLI for frida-rpc messaging regardless of cache.
        """
        import subprocess as _subprocess
        from framework.bridge.adb_device import AdbDevice

        rt = client._load_runtime()
        device = rt.get("device", {})
        serial = device.get("serial", "")
        package = device.get("app_package",
                             client.config.get("meta", {}).get("package", ""))

        if not serial or not package:
            print("[aes-cbc] No device configured, skipping session_key derivation")
            return

        # ── 1. Get PID ──
        pid = AdbDevice.get_pid(serial, package)
        if not pid:
            print("[aes-cbc] App not running, attempting to launch...")
            _subprocess.run(
                ["adb", "-s", serial, "shell", "monkey", "-p", package, "1"],
                timeout=15, capture_output=True,
            )
            time.sleep(5)
            pid = AdbDevice.get_pid(serial, package)

        if not pid:
            client._notify("error",
                           f"找不到进程 {package}，请手动打开 App")
            return

        # Script path: bridge_cli.js preferred, frida_key_bridge.js fallback
        script_path = client.config_path.parent / "bridge_cli.js"
        if not script_path.exists():
            script_path = client.config_path.parent / "frida_key_bridge.js"
        if not script_path.exists():
            print(f"[aes-cbc] No Frida script found in {client.config_path.parent}")
            return

        # ── 2. Launch CLI for frida-rpc messaging (always needed) ──
        if client._messenger.name == "frida-rpc":
            cli = FridaCliSession()
            cli.attach(pid, script_path)
            client._frida_cli_session = cli
            client._notify("info", "Frida CLI 已启动")

        # ── 3. Check key cache ──
        cache_file = client.config_path.parent / ".state" / "last_key.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("pid") == pid and cached.get("key_hex"):
                    return self._apply_cached_key(cached, client, pid)
            except Exception:
                pass

        # ── 4. Capture fresh key ──
        # Use shared CLI session if already created, otherwise create new one
        cli = getattr(client, '_frida_cli_session', None)
        if cli is None:
            cli = FridaCliSession()
            cli.attach(pid, script_path)
            client._frida_cli_session = cli

        client._notify("info", "等待密钥... Frida CLI 已启动")
        key_data = cli.capture_key(timeout=30, tap_helper=serial)

        if not key_data:
            client._notify("error",
                           "密钥捕获超时（30s）。请确认 App 已登录并触发网络请求")
            return

        self._apply_fresh_key(key_data, client, pid)

    def _apply_cached_key(self, cached: dict, client, pid: int) -> None:
        """Restore key + headers from cache."""
        print(f"[aes-cbc] Reusing cached key (PID={pid} unchanged)")
        self._key = bytes.fromhex(cached["key_hex"])
        cs = cached.get("headers", {}).get("clientSession", "")
        if cs and len(cs) >= 16:
            self._iv = cs[:16].encode("utf-8")
        else:
            self._iv = b"\x00" * 16

        self._inject_headers(cached.get("headers", {}), client)
        client._frida_authenticated = True
        client._notify("info", f"复用缓存的密钥 (PID={pid})")

    def _apply_fresh_key(self, key_data: dict, client, pid: int) -> None:
        """Parse key_hex/iv/headers from captured data, inject, cache."""
        key_hex = key_data.get("key_hex", "")
        if not key_hex:
            client._notify("error", "Frida 返回空密钥")
            return

        self._key = bytes.fromhex(key_hex)

        headers = key_data.get("headers", {})
        client_session = headers.get("clientSession", "")
        if client_session and len(client_session) >= 16:
            self._iv = client_session[:16].encode("utf-8")
        else:
            self._iv = b"\x00" * 16

        self._inject_headers(headers, client)
        client._frida_authenticated = True

        # Cache
        key_data["pid"] = pid
        cache_file = client.config_path.parent / ".state" / "last_key.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(key_data, ensure_ascii=False, indent=2))

        client._notify("info",
                       f"密钥捕获成功 ({len(self._key)} bytes)")
        print(f"[aes-cbc] Session key loaded via Frida CLI "
              f"({len(self._key)} bytes, IV from clientSession)")

    @staticmethod
    def _inject_headers(headers: dict, client) -> None:
        """Inject captured session headers into client default_headers."""
        for hk in ("deviceToken", "SMDeviceId", "DeviceId",
                    "clientSession", "Token"):
            if hk in headers:
                client._default_headers[hk] = headers[hk]
        if "Token" in headers:
            client._auth_token = headers["Token"]

    # ═══ Crypto ═══

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
