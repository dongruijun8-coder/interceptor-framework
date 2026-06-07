"""融云 IM TCP 私信 — navi 服务发现 + TCP 协议（骨架）"""
from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry


class RongcloudTcpMessaging(MessagingProcessor):
    name = "rongcloud-tcp"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "app_key": {"type": "string", "description": "融云 App Key"},
                "navi_server": {"type": "string", "default": "flse.cn.rongnav.com"},
            },
        }

    def send(self, client, uid: str, text: str) -> dict:
        rct = client.config.get("rongcloud_token", "")
        if not rct:
            return {"success": False, "error": "rongcloud_token 未配置 — 需通过登录获取"}

        app_key = self.params.get("app_key", "")
        navi_server = self.params.get("navi_server", "flse.cn.rongnav.com")

        # Step 1: Navi discovery
        try:
            resp = client._post(
                f"https://{navi_server}/v2/navi.json",
                {"token": rct, "appId": app_key, "v": "5.36.0", "p": "Android"},
            )
        except Exception as e:
            return {"success": False, "error": f"navi 服务发现失败: {e}"}

        if not client.check_response(resp):
            return {"success": False, "error": f"navi 失败: {resp.get('message', '')}"}

        data = resp.get("data", resp)
        servers = data.get("serverAddr", [])
        if not servers:
            return {"success": False, "error": "navi 未返回 IM 服务器地址"}

        # TODO: TCP connect → auth with rongCloudToken → send private message
        # server = servers[0]  # e.g. "112.126.70.47:443"
        # 1. TCP connect to server
        # 2. Send RongCloud auth packet
        # 3. Send private message via RongCloud protocol
        return {"success": False, "error": f"融云 TCP 待实现 (navi OK, {len(servers)} 节点)"}

    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("app_key"):
            warnings.append("rongcloud-tcp 缺少 app_key")
        return len(warnings) == 0, warnings


ProcessorRegistry.register(RongcloudTcpMessaging)
