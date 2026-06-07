"""AES-256-CBC 加密 — 双鱼部落"""
import base64
import json

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
        """Fetch AES key from Frida RPC (frida_key_bridge.js → getSessionKey)."""
        session = getattr(client, '_frida_session', None)
        if not session or not session.is_connected:
            print("[aes-cbc] Frida session not available, skipping session_key derivation")
            return

        try:
            rpc = session._rpc_second or session._rpc
            raw = rpc.getSessionKey()
            if isinstance(raw, str):
                import json as _json
                data = _json.loads(raw)
            else:
                data = raw

            key_hex = data.get("key_hex", "")
            iv_hex = data.get("iv_hex", "")
            headers = data.get("headers", {})

            if key_hex:
                self._key = bytes.fromhex(key_hex)
                # IV = clientSession[:16] (16 bytes ASCII)
                client_session = headers.get("clientSession", "")
                if client_session and len(client_session) >= 16:
                    self._iv = client_session[:16].encode("utf-8")
                elif iv_hex:
                    self._iv = bytes.fromhex(iv_hex)
                else:
                    self._iv = b"\x00" * 16
                print(f"[aes-cbc] Session key loaded via Frida ({len(self._key)} bytes)")
            else:
                print("[aes-cbc] Frida getSessionKey returned no key_hex")
        except Exception as e:
            print(f"[aes-cbc] Failed to fetch session key from Frida: {e}")

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


