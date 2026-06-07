"""无消息通道 — 私信不可用"""
from ..base import MessagingProcessor


class NoneMessaging(MessagingProcessor):
    name = "none"

    def send(self, client, uid: str, text: str) -> dict:
        return {"success": False, "error": "messaging 未配置 — 私信通道不存在"}

    def validate(self, client) -> tuple:
        warnings = ["messaging=none: 消息发送已禁用"]
        return False, warnings


