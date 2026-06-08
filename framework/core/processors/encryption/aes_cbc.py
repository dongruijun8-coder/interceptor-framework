"""AES-256-CBC 加密 — 双鱼部落"""
import base64
import json
import re
import subprocess
import time
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from ..base import EncryptionProcessor


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

    def _derive_from_frida(self, client) -> None:
        """Fetch AES key via Frida CLI subprocess (bypasses NIS anti-Frida).

        Python Frida binding is detected by NIS → Java bridge blocked.
        Frida CLI (frida -H host:port) is NOT detected.
        """
        rt = client._load_runtime()
        device = rt.get("device", {})
        serial = device.get("serial", "")
        package = device.get("app_package",
                             client.config.get("meta", {}).get("package", ""))

        if not serial or not package:
            print("[aes-cbc] No device configured, skipping session_key derivation")
            return

        # Use bridge_cli.js (SecretKeySpec hooks + RPC exports)
        script_path = client.config_path.parent / "bridge_cli.js"
        if not script_path.exists():
            # Fallback to frida_key_bridge.js
            script_path = client.config_path.parent / "frida_key_bridge.js"
        if not script_path.exists():
            print(f"[aes-cbc] No Frida script found in {client.config_path.parent}")
            return

        # ── 1. Get PID via ADB ──
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if not pid:
            # Try launching the app
            print("[aes-cbc] App not running, attempting to launch...")
            subprocess.run(
                ["adb", "-s", serial, "shell", "monkey", "-p", package, "1"],
                timeout=15, capture_output=True,
            )
            time.sleep(5)
            pid = AdbDevice.get_pid(serial, package)

        if not pid:
            client._notify("error",
                           f"找不到进程 {package}，请手动打开 App")
            return

        print(f"[aes-cbc] App PID={pid}, launching Frida CLI...")

        # ── 2. Launch Frida CLI ──
        # Copy script to temp (avoid Chinese path issues)
        import tempfile as _tempfile
        _tmp_dir = Path(_tempfile.gettempdir()) / "sybl_frida"
        _tmp_dir.mkdir(parents=True, exist_ok=True)
        _tmp_script = _tmp_dir / "bridge_cli.js"
        _tmp_script.write_text(script_path.read_text(encoding="utf-8"),
                               encoding="utf-8")

        cmd = f'frida -H 127.0.0.1:27042 -p {pid} -l "{_tmp_script}"'
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True,
            )
        except FileNotFoundError:
            client._notify("error", "frida CLI 未安装，请确认 PATH 中包含 frida")
            return

        # ── 3. Threaded reader — Windows select() doesn't work on pipes ──
        import threading
        import queue
        client._notify("info", "等待密钥... Frida CLI 已启动")
        line_queue = queue.Queue()

        def _read_frida_stdout():
            try:
                for line in iter(proc.stdout.readline, ''):
                    line_queue.put(line)
            except Exception:
                pass
            finally:
                line_queue.put(None)  # sentinel

        reader_t = threading.Thread(target=_read_frida_stdout, daemon=True)
        reader_t.start()

        deadline = time.time() + 30
        key_data = None
        hooks_ready = False

        while time.time() < deadline:
            try:
                line = line_queue.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            if line is None:  # sentinel — stdout closed
                break

            line = line.strip()
            if not line:
                continue
            print(f"[aes-cbc] {line[:120]}")

            if not hooks_ready and ("Ready" in line or "Hooks installed" in line):
                hooks_ready = True
                print("[aes-cbc] Hooks ready, tapping screen...")
                for i in range(5):
                    subprocess.run(
                        ["adb", "-s", serial, "shell", "input", "tap",
                         str(400 + i * 40), str(500 + i * 20)],
                        timeout=5, capture_output=True,
                    )
                    time.sleep(0.5)

            if "KEY_JSON:" in line:
                try:
                    key_data = json.loads(line.split("KEY_JSON: ", 1)[1])
                    break
                except json.JSONDecodeError:
                    pass

        if proc.poll() is not None and not key_data:
            stderr = proc.stderr.read()
            print(f"[aes-cbc] Frida CLI exited: {stderr[:200]}")

        if not key_data:
            client._notify("error",
                           "密钥捕获超时（30s）。请确认 App 已登录并触发网络请求")
            try:
                proc.kill()
            except Exception:
                pass
            return

        # ── 4. Parse key ──
        key_hex = key_data.get("key_hex", "")
        iv_hex = key_data.get("iv_hex", "")
        headers = key_data.get("headers", {})

        if not key_hex:
            client._notify("error", "Frida 返回空密钥")
            return

        self._key = bytes.fromhex(key_hex)
        # IV = clientSession[:16] (16 bytes ASCII)
        client_session = headers.get("clientSession", "")
        if client_session and len(client_session) >= 16:
            self._iv = client_session[:16].encode("utf-8")
        elif iv_hex:
            self._iv = bytes.fromhex(iv_hex)
        else:
            self._iv = b"\x00" * 16

        # Inject captured session headers into client (required for auth)
        for hk in ("deviceToken", "SMDeviceId", "DeviceId",
                    "clientSession", "Token"):
            if hk in headers:
                client._default_headers[hk] = headers[hk]
        # Store Token for auth flow
        if "Token" in headers:
            client._auth_token = headers["Token"]

        # ── 5. Store Frida process for messaging RPC ──
        # Save as (proc, script_path) tuple for frida-rpc processor
        client._frida_cli_proc = proc
        client._frida_cli_script = str(script_path)

        client._notify("info",
                       f"密钥捕获成功 ({len(self._key)} bytes)")
        print(f"[aes-cbc] Session key loaded via Frida CLI "
              f"({len(self._key)} bytes, IV from clientSession)")

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


