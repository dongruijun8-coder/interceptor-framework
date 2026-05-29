"""手动 token 认证 — 直接从 runtime.json 读取 token/uid"""
from ..base import AuthProcessor
from ...processor_registry import ProcessorRegistry


class ManualTokenAuth(AuthProcessor):
    name = "manual-token"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "token_field": {"type": "string", "default": "token"},
                "uid_field": {"type": "string", "default": "uid"},
            },
        }

    def authenticate(self, client) -> bool:
        creds = self.load_credentials(client)
        token = creds.get(self.params.get("token_field", "token"), "")
        uid = creds.get(self.params.get("uid_field", "uid"), "")
        if not token:
            return False
        client._auth_token = token
        client._uid = str(uid) if uid else ""
        client.config["token"] = token
        client.config["uid"] = client._uid
        return True


ProcessorRegistry.register(ManualTokenAuth)
