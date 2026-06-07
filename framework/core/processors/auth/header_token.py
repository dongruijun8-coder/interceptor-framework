"""自定义 Header Token 认证 — 从 runtime.json 读 token，注入到指定 HTTP header"""
from ..base import AuthProcessor


class HeaderTokenAuth(AuthProcessor):
    name = "header-token"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "header_name": {"type": "string", "default": "access-token"},
                "token_field": {"type": "string", "default": "token"},
                "uid_field": {"type": "string", "default": "uid"},
            },
        }

    def validate(self, client) -> tuple:
        warnings = []
        if not client._auth_token:
            warnings.append("header-token 需要 auth_token，但当前为空")
        return len(warnings) == 0, warnings

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        header_name = self.params.get("header_name", "access-token")
        token = creds.get(self.params.get("token_field", "token"), "") or client.config.get("token", "")
        uid = creds.get(self.params.get("uid_field", "uid"), "") or client.config.get("uid", "")

        if token:
            client._auth_token = token
            client._default_headers[header_name] = token
        client._uid = str(uid) if uid else ""
        return True
