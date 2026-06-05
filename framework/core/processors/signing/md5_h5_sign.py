"""MD5 H5 签名 — WeFun 风格
算法: MD5({uid}{secret}{ts}) → lowercase hex
签名加到 URL query string: ?ts={ts}&h_sn={sign}
"""
import hashlib
import time
from ..base import SigningProcessor


class Md5H5Signing(SigningProcessor):
    name = "md5-h5-sign"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "secret": {"type": "string", "description": "MD5 密钥"},
                "uid_field": {"type": "string", "default": "h_m", "description": "UID 参数名"},
                "ts_field": {"type": "string", "default": "ts", "description": "时间戳参数名"},
                "sign_field": {"type": "string", "default": "h_sn", "description": "签名参数名"},
                "ts_ms_field": {"type": "string", "default": "h_ts", "description": "毫秒时间戳参数名（body 里用 {{h5_ts}}）"},
                "algorithm": {"type": "string", "default": "MD5"},
                "pattern": {"type": "string", "default": "{uid}{secret}{ts}",
                            "description": "签名串拼接模式，{uid}/{secret}/{ts} 占位"},
            },
        }

    def sign(self, url: str, headers: dict, params: dict = None) -> tuple:
        """返回 (headers, query_params) — h_sn 签名加到 query string"""
        secret = self.params.get("secret", "")
        uid_field = self.params.get("uid_field", "h_m")
        sign_field = self.params.get("sign_field", "h_sn")
        ts_field = self.params.get("ts_field", "ts")
        ts_ms_field = self.params.get("ts_ms_field", "h_ts")
        pattern = self.params.get("pattern", "{uid}{secret}{ts}")

        merged = dict(params or {})

        # Read uid from headers (injected by auth processor or default_headers)
        uid = merged.get(uid_field, "") or headers.get("__auth_token_uid__", "")

        # Seconds timestamp for signing
        ts = str(int(time.time()))
        merged[ts_field] = ts

        # Build sign string
        sign_string = pattern.replace("{uid}", str(uid)).replace("{secret}", secret).replace("{ts}", ts)

        # Hash
        algo = self.params.get("algorithm", "MD5").upper()
        if algo == "MD5":
            sig = hashlib.md5(sign_string.encode("utf-8")).hexdigest()
        elif algo == "SHA1":
            sig = hashlib.sha1(sign_string.encode("utf-8")).hexdigest()
        elif algo == "SHA256":
            sig = hashlib.sha256(sign_string.encode("utf-8")).hexdigest()
        else:
            sig = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

        merged[sign_field] = sig

        # Also add millisecond timestamp if configured (body 用 {{h5_ts}} 模板变量)
        if ts_ms_field:
            merged[ts_ms_field] = str(int(time.time() * 1000))

        return headers, merged
