"""Frida RPC 私信 — 通过注入的 Frida 脚本发送消息"""
from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry
from framework.bridge.frida_session import FridaDisconnectedError


class FridaRpcMessaging(MessagingProcessor):
    name = "frida-rpc"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "script_name": {
                    "type": "string",
                    "description": "Frida JS 脚本文件名（位于 app 目录下）",
                    "default": "hook_send_msg.js",
                },
            },
        }

    def send(self, client, uid: str, text: str) -> dict:
        """Call rpc.exports.sendMessage(uid, text) via the pre-established Frida session."""
        session = getattr(client, '_frida_session', None)
        if session is None:
            return {"success": False, "error": "Frida 会话未初始化"}

        try:
            return session.send_message(uid, text)
        except FridaDisconnectedError:
            raise  # Re-raise — BaseClient.run_room catches this to stop pipeline


ProcessorRegistry.register(FridaRpcMessaging)
