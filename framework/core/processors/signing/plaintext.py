"""无签名 — 透传 headers"""
from ..base import SigningProcessor


class PlaintextSigning(SigningProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def validate(self, client) -> tuple:
        return True, []

    def sign(self, url: str, headers: dict, params: dict = None) -> tuple:
        return headers, {}


