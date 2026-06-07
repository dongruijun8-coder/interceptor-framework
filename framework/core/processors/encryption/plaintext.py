"""明文透传 — 不加密"""
import json
from ..base import EncryptionProcessor


class PlaintextEncryption(EncryptionProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def validate(self, client) -> tuple:
        return True, []

    def encode(self, body: dict) -> bytes:
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def decode(self, raw: bytes) -> dict:
        return json.loads(raw.decode("utf-8"))


