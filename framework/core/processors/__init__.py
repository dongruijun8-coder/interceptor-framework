"""自动发现并注册所有处理器子包 — import side-effect triggers register()"""
# Imports added incrementally as each processor is created (Tasks 2-7)
from .encryption.plaintext import PlaintextEncryption
from .encryption.aes_cbc import AesCbcEncryption
from .signing.plaintext import PlaintextSigning
from .signing.xor_triple import XorTripleSigning
from .auth.manual_token import ManualTokenAuth
from .messaging.rest_json import RestJsonMessaging
from .messaging.none import NoneMessaging
from .auth.password_login import PasswordLoginAuth
