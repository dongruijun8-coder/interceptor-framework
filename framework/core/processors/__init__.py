"""自动发现并注册所有处理器子包 — import side-effect triggers register()"""
# Imports added incrementally as each processor is created (Tasks 2-7)
from .encryption.plaintext import PlaintextEncryption
from .signing.plaintext import PlaintextSigning
from .auth.manual_token import ManualTokenAuth
from .messaging.rest_json import RestJsonMessaging
from .messaging.none import NoneMessaging
