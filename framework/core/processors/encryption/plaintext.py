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
