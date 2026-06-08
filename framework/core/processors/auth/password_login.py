"""密码登录认证"""
import json

from ..base import AuthProcessor


class PasswordLoginAuth(AuthProcessor):
    name = "password-login"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "登录 API 路径"},
                "fields": {"type": "object", "description": "请求字段映射: {内部名: API字段名}"},
                "response_mapping": {
                    "type": "object",
                    "properties": {
                        "token": {"type": "string"},
                        "uid": {"type": "string"},
                    },
                },
            },
            "required": ["endpoint", "fields", "response_mapping"],
        }

    def validate(self, client) -> tuple:
        warnings = []
        ep = self.params.get("endpoint", "")
        if not ep:
            warnings.append("password-login 缺少 endpoint")
        fields = self.params.get("fields", {})
        if "phone" not in fields or "password" not in fields:
            warnings.append("password-login 缺少 fields.phone 或 fields.password 映射")
        rt = client._load_runtime()
        creds = rt.get("credentials", {})
        if not creds.get("phone"):
            warnings.append("未配置手机号，请在 Dashboard 设置凭据")
        if not creds.get("password"):
            warnings.append("未配置密码，请在 Dashboard 设置凭据")
        return len(warnings) == 0, warnings

    def authenticate(self, client) -> bool:
        # Already authenticated via Frida key capture (Token from session headers)
        if client._auth_token:
            client._notify("info", f"Token 已从 Frida 获取 (uid={client._uid or '?'})")
            return True

        creds = self.load_credentials(client)
        endpoint = self.params["endpoint"]
        field_map = self.params["fields"]
        resp_map = self.params["response_mapping"]

        body = {}
        for internal_key, api_field in field_map.items():
            if internal_key in ("code", "mobile_token"):
                body[api_field] = None
            else:
                body[api_field] = creds.get(internal_key, "")

        try:
            resp = client._post(f"{client._base_url}{endpoint}", body)
        except Exception as e:
            client._notify("error", f"登录请求失败: {e}")
            return False

        if not client.check_response(resp):
            client._notify("error", f"登录失败: {resp.get('message', '')}")
            return False

        data = resp.get("data", {})
        token = self._resolve_path(data, resp_map.get("token", "token"))
        uid = self._resolve_path(data, resp_map.get("uid", "uid"))

        if not token:
            client._notify("error", "登录响应缺少 token")
            return False

        client._auth_token = token
        client._uid = str(uid) if uid else ""

        client.config["auth_token"] = token
        client.config["uid"] = client._uid
        client.config_path.write_text(
            json.dumps(client.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        nick = data.get("nickname", data.get("nick", ""))
        client._notify("info", f"登录成功 uid={uid} nick={nick}")
        return True

    @staticmethod
    def _resolve_path(data: dict, path: str):
        """从嵌套 JSON 取值，支持 'data.user.id' 格式"""
        parts = path.split(".")
        current = data
        for p in parts:
            if isinstance(current, dict):
                current = current.get(p)
            else:
                return None
        return current


