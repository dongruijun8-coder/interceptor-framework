"""明文认证 — 直接从 runtime.json 读取 token，不做任何处理"""
from ..base import AuthProcessor


class PlaintextAuth(AuthProcessor):
    name = "plaintext"

    @classmethod
    def params_schema(cls) -> dict:
        return {"type": "object", "properties": {}}

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        token = creds.get("token", "") or client.config.get("token", "")
        uid = creds.get("uid", "") or client.config.get("uid", "")
        client._auth_token = token
        client._uid = str(uid) if uid else ""
        return True
