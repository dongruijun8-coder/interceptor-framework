"""处理器基类 — 4个类别，统一接口"""
from abc import ABC, abstractmethod


class BaseProcessor(ABC):
    name: str = ""
    category: str = ""

    def __init__(self, params: dict):
        self.params = params

    @classmethod
    def params_schema(cls) -> dict:
        return {}

    def validate(self, client) -> tuple:
        """返回 (ok: bool, warnings: list[str])。
        派生类覆盖此方法做自检。默认始终通过。"""
        return True, []


class EncryptionProcessor(BaseProcessor, ABC):
    category = "encryption"

    @abstractmethod
    def encode(self, body: dict) -> bytes:
        """dict → bytes (加密后)"""

    @abstractmethod
    def decode(self, raw: bytes) -> dict:
        """bytes (解密后) → dict"""

    def derive_key(self, client) -> None:
        """key=null 时从 client 上下文派生 key"""


class SigningProcessor(BaseProcessor, ABC):
    category = "signing"

    @abstractmethod
    def sign(self, url: str, headers: dict, params: dict = None) -> tuple:
        """返回 (headers, query_params) — query_params 加到请求 URL query string"""


class AuthProcessor(BaseProcessor, ABC):
    category = "auth"

    @abstractmethod
    def authenticate(self, client) -> bool:
        """执行认证，成功返回 True"""

    def load_credentials(self, client) -> dict:
        """从 runtime.json 读取凭据"""
        runtime_path = client._runtime_path
        if runtime_path.exists():
            import json
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            return runtime.get("credentials", {})
        return {}


class MessagingProcessor(BaseProcessor, ABC):
    category = "messaging"

    @abstractmethod
    def send(self, client, uid: str, text: str) -> dict:
        """返回 {success: bool, error: str}"""
