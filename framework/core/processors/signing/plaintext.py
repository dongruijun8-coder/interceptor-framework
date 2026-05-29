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
