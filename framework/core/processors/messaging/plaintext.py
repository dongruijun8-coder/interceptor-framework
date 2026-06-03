"""明文消息 — 直接 HTTP POST 发送（无加密无签名），适用于 API 直接调用"""
from ..base import MessagingProcessor


class PlaintextMessaging(MessagingProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {}

    def send(self, client, uid: str, text: str) -> dict:
        return {"success": False, "error": "plaintext messaging: 请在 config pipeline.messaging 指定具体的消息处理器"}
