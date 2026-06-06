"""纯 HTTP REST 私信 — 支持两种模式：

1. config-driven: endpoints.send_message 有 body 模板 → 直接用
2. preCheck → send: 传统模式（漂漂等）
"""
import uuid
import time as _time
from ..base import MessagingProcessor


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
        }

    def send(self, client, uid: str, text: str) -> dict:
        # ═══ Mode 1: config-driven body template ═══
        ep = client.config.get("endpoints", {}).get("send_message")
        if ep and ep.get("body"):
            return self._send_from_template(client, uid, text, ep)

        # ═══ Mode 2: legacy preCheck → send ═══
        base = client.config["base_url"]
        precheck_path = self.params.get("precheck_path", "")
        send_path = self.params.get("send_path", "")

        if precheck_path:
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
            except Exception as e:
                return {"success": False, "error": str(e)}
        elif send_path:
            try:
                resp = client._post(f"{base}{send_path}", {"tuid": uid, "content": text, "type": "TEXT"})
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            return {"success": False, "error": "no send_path configured"}

        if client.check_response(resp):
            return {"success": True, "error": ""}
        return {"success": False, "error": resp.get("message", "send failed")}

    def _send_from_template(self, client, to_uid: str, text: str, ep: dict) -> dict:
        """使用 config 中 send_message 端点的 body 模板发送私信。

        模板变量：
        - {{uid}}, {{token}}, {{device_id}} — 身份
        - {{to_uid}}, {{message}} — 目标用户 + 消息
        - {{conv_id}} — single_chat-{min}-{max}
        - {{ts_ms}} — 当前毫秒时间戳
        - {{uuid_v4}} — 随机 UUID
        - {{uid_string}}, {{to_uid_string}} — uid 字符串
        """
        uid_int = str(client._uid)
        to_uid_str = str(to_uid)
        conv_id = f"single_chat-{min(int(uid_int), int(to_uid_str))}-{max(int(uid_int), int(to_uid_str))}"
        ts_ms = int(_time.time() * 1000)
        msg_uuid = str(uuid.uuid4())

        body = client._fill_template(
            ep["body"],
            to_uid=to_uid_str,
            message=text,
            conv_id=conv_id,
            ts_ms=ts_ms,
            uuid_v4=msg_uuid,
            uid_string=uid_int,
            to_uid_string=to_uid_str,
        )

        try:
            resp = client._request(ep, body)
        except Exception as e:
            return {"success": False, "error": str(e)}

        if client.check_response(resp):
            return {"success": True, "error": ""}
        return {"success": False, "error": resp.get("msg", resp.get("message", "send failed"))}
