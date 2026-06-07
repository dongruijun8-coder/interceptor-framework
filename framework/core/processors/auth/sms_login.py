"""短信验证码登录 — 封装 AccountManager，由 Dashboard 协调两步流程"""
from ..base import AuthProcessor
from ...processor_registry import ProcessorRegistry


class SmsLoginAuth(AuthProcessor):
    name = "sms-login"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "login_endpoint": {"type": "string", "description": "短信登录 API 路径（可选，默认用 AccountManager 内置）"},
                "response_mapping": {
                    "type": "object",
                    "properties": {
                        "token": {"type": "string", "default": "token"},
                        "uid": {"type": "string", "default": "uid"},
                    },
                },
            },
        }

    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("login_endpoint"):
            warnings.append("sms-login 缺少 login_endpoint")
        return len(warnings) == 0, warnings

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        phone = creds.get("phone", "")
        sms_code = creds.get("sms_code", "")

        if not phone or not sms_code:
            client._notify("error", "缺少 phone 或 sms_code — 请在 Dashboard 中先发送验证码再登录")
            return False

        # Delegate to AccountManager for the full SMS login flow
        from framework.core.account_manager import AccountManager
        import json

        app_dir = str(client.config_path.parent)
        am = AccountManager(app_dir)
        base_url = client._base_url

        result = am.sms_login(base_url, phone, sms_code, client.config)

        if not result.get("success"):
            client._notify("error", f"SMS 登录失败: {result.get('error', '')}")
            return False

        token = result.get("token", "")
        uid = result.get("uid", "")

        client._auth_token = token
        client._uid = str(uid) if uid else ""
        client.config["auth_token"] = token
        client.config["uid"] = client._uid
        client.config_path.write_text(
            json.dumps(client.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        client._notify("info", f"SMS 登录成功 uid={uid}")
        return True


ProcessorRegistry.register(SmsLoginAuth)
