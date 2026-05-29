"""纯 HTTP REST 私信 — preCheck → send 模式"""
from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry


class RestJsonMessaging(MessagingProcessor):
    name = "rest-json"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "precheck_path": {"type": "string"},
                "send_path": {"type": "string"},
            },
            "required": ["precheck_path", "send_path"],
        }

    def send(self, client, uid: str, text: str) -> dict:
        base = client.config["base_url"]
        precheck_path = self.params["precheck_path"]
        send_path = self.params["send_path"]

        try:
            precheck = client._post(
                f"{base}{precheck_path}",
                {"tuids": [uid]},
            )
            if not client.check_response(precheck):
                return {"success": False, "error": f"preCheck: {precheck.get('message', '')}"}

            msg_chat_id = precheck.get("data", {}).get("msgChatId", "")
            if not msg_chat_id:
                return {"success": False, "error": "no msgChatId"}

            resp = client._post(
                f"{base}{send_path}",
                {"tuid": uid, "content": text, "msgChatId": msg_chat_id, "type": "TEXT"},
            )
            if client.check_response(resp):
                return {"success": True, "error": ""}
            return {"success": False, "error": resp.get("message", "send failed")}
        except Exception as e:
            return {"success": False, "error": str(e)}


ProcessorRegistry.register(RestJsonMessaging)
