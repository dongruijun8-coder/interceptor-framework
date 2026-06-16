"""AES-ECB 加密 — 梦音风格
算法: AES-128-ECB PKCS7, 静态key. 响应两层解密:
  1. body层: Base64 decode → AES-ECB decrypt → JSON
  2. data层: 如果 data 字段是长字符串 (>50 chars), 二次 Base64 decode → AES-ECB decrypt
"""
import base64
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from ..base import EncryptionProcessor


class AesEcbEncryption(EncryptionProcessor):
    name = "aes-ecb"

    def __init__(self, params: dict):
        super().__init__(params)
        self._key = params["key"].encode("utf-8") if isinstance(params["key"], str) else params["key"]
        self._key_size = int(params.get("key_size", 128))

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "AES key (16 bytes for AES-128)"},
                "key_size": {"type": "integer", "default": 128, "enum": [128, 192, 256]},
                "padding": {"type": "string", "default": "pkcs7"},
                "two_layer": {"type": "boolean", "default": True,
                              "description": "响应 data 字段是否二次解密"},
            },
        }

    def validate(self, client) -> tuple:
        warnings = []
        if not self._key:
            warnings.append("aes-ecb 缺少 key 参数")
        if len(self._key) not in (16, 24, 32):
            warnings.append(f"aes-ecb key 长度 {len(self._key)} bytes, 预期 16/24/32")
        return len(warnings) == 0, warnings

    def encode(self, body: dict) -> bytes:
        plain = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = AES.new(self._key, AES.MODE_ECB)
        return base64.b64encode(cipher.encrypt(pad(plain, AES.block_size)))

    def decode(self, raw: bytes) -> dict:
        # 尝试解密
        try:
            decoded = base64.b64decode(raw)
            cipher = AES.new(self._key, AES.MODE_ECB)
            result = json.loads(unpad(cipher.decrypt(decoded), AES.block_size))
        except Exception:
            # 解密失败, 回退到 raw JSON
            result = json.loads(raw.decode("utf-8"))

        # 两层解密: data 字段可能是二次加密的字符串
        if self.params.get("two_layer", True):
            data_val = result.get("data")
            if isinstance(data_val, str) and len(data_val) > 30:
                try:
                    decoded = base64.b64decode(data_val)
                    cipher = AES.new(self._key, AES.MODE_ECB)
                    result["data"] = json.loads(unpad(cipher.decrypt(decoded), AES.block_size))
                except Exception:
                    pass  # 保持原值

        return result
