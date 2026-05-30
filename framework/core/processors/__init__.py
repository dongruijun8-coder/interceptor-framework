"""自动发现并注册所有处理器子包 — import后集中register()"""
from framework.core.processor_registry import ProcessorRegistry

from .encryption.plaintext import PlaintextEncryption
from .encryption.aes_cbc import AesCbcEncryption
from .signing.plaintext import PlaintextSigning
from .signing.xor_triple import XorTripleSigning
from .auth.manual_token import ManualTokenAuth
from .auth.password_login import PasswordLoginAuth
from .messaging.rest_json import RestJsonMessaging
from .messaging.none import NoneMessaging

ProcessorRegistry.register(PlaintextEncryption)
ProcessorRegistry.register(AesCbcEncryption)
ProcessorRegistry.register(PlaintextSigning)
ProcessorRegistry.register(XorTripleSigning)
ProcessorRegistry.register(ManualTokenAuth)
ProcessorRegistry.register(PasswordLoginAuth)
ProcessorRegistry.register(RestJsonMessaging)
ProcessorRegistry.register(NoneMessaging)
