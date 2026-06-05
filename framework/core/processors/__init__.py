"""自动发现并注册所有处理器子包 — import后集中register()"""
from framework.core.processor_registry import ProcessorRegistry

from .encryption.plaintext import PlaintextEncryption
from .encryption.aes_cbc import AesCbcEncryption
from .signing.plaintext import PlaintextSigning
from .signing.xor_triple import XorTripleSigning
from .signing.sha1_sorted_kv import Sha1SortedKvSigning
from .signing.md5_h5_sign import Md5H5Signing
from .auth.manual_token import ManualTokenAuth
from .auth.password_login import PasswordLoginAuth
from .auth.sms_login import SmsLoginAuth
from .auth.plaintext import PlaintextAuth
from .auth.header_token import HeaderTokenAuth
from .messaging.rest_json import RestJsonMessaging
from .messaging.none import NoneMessaging
from .messaging.plaintext import PlaintextMessaging
from .messaging.rongcloud_tcp import RongcloudTcpMessaging
from .messaging.frida_rpc import FridaRpcMessaging

ProcessorRegistry.register(PlaintextEncryption)
ProcessorRegistry.register(AesCbcEncryption)
ProcessorRegistry.register(PlaintextSigning)
ProcessorRegistry.register(XorTripleSigning)
ProcessorRegistry.register(Sha1SortedKvSigning)
ProcessorRegistry.register(Md5H5Signing)
ProcessorRegistry.register(ManualTokenAuth)
ProcessorRegistry.register(PasswordLoginAuth)
ProcessorRegistry.register(SmsLoginAuth)
ProcessorRegistry.register(PlaintextAuth)
ProcessorRegistry.register(HeaderTokenAuth)
ProcessorRegistry.register(RestJsonMessaging)
ProcessorRegistry.register(PlaintextMessaging)
ProcessorRegistry.register(NoneMessaging)
ProcessorRegistry.register(RongcloudTcpMessaging)
ProcessorRegistry.register(FridaRpcMessaging)
