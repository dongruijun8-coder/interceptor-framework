"""SHA1 排序参数签名 — hifun 风格
算法: 请求参数 + 签名参数 → 按 key 字母排序 → K=V 对用 & 拼接 → 追加 &secret_key=XXX → SHA1 → 大写 hex
"""
import hashlib
import random
import string
import time
from ..base import SigningProcessor


class Sha1SortedKvSigning(SigningProcessor):
    name = "sha1-sorted-kv"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "secret_key": {"type": "string"},
                "secret_key_placement": {"type": "string", "default": "append"},
                "secret_key_param": {"type": "string", "default": "secret_key"},
                "hash": {"type": "string", "default": "sha1"},
                "output_format": {"type": "string", "default": "uppercase_hex"},
                "excluded_params": {"type": "array", "default": []},
                "nonce_param": {"type": "string", "default": ""},
                "nonce_length": {"type": "integer", "default": 0},
                "nonce_charset": {"type": "string", "default": "hex_uppercase"},
                "timestamp_param": {"type": "string", "default": ""},
                "access_key_param": {"type": "string", "default": ""},
                "access_key_value": {"type": "string", "default": ""},
                "sign_param": {"type": "string", "default": "sign"},
            },
        }

    def validate(self, client) -> tuple:
        warnings = []
        if not self.params.get("secret") and not self.params.get("secret_key"):
            warnings.append("sha1-sorted-kv 缺少 secret/secret_key 参数")
        return len(warnings) == 0, warnings

    def sign(self, url: str, headers: dict, params: dict = None) -> tuple:
        """返回 (headers, query_params) — 签名加到 query string"""
        secret = self.params.get("secret_key", "")
        excluded = set(self.params.get("excluded_params", []))
        sign_param = self.params.get("sign_param", "sign")

        # Merge request params + signing params
        merged = dict(params or {})

        # nonce
        nonce_param = self.params.get("nonce_param", "")
        nonce_len = self.params.get("nonce_length", 0)
        if nonce_param and nonce_len:
            charset = string.hexdigits.upper() if self.params.get("nonce_charset") == "hex_uppercase" else string.hexdigits
            merged[nonce_param] = ''.join(random.choices(charset, k=nonce_len))

        # timestamp
        ts_param = self.params.get("timestamp_param", "")
        if ts_param:
            merged[ts_param] = str(int(time.time()))

        # access_key
        ak_param = self.params.get("access_key_param", "")
        ak_value = self.params.get("access_key_value", "")
        if ak_param and ak_value:
            merged[ak_param] = ak_value

        # Sort params alphabetically by key, build sign string
        sorted_keys = sorted(merged.keys())
        kv_pairs = [f"{k}={merged[k]}" for k in sorted_keys if k not in excluded]

        # Append secret key
        if self.params.get("secret_key_placement") == "append":
            kv_pairs.append(f"{self.params.get('secret_key_param', 'secret_key')}={secret}")

        sign_string = "&".join(kv_pairs)

        # Hash
        hash_algo = self.params.get("hash", "sha1")
        if hash_algo == "sha1":
            sig = hashlib.sha1(sign_string.encode("utf-8")).hexdigest()
        else:
            sig = hashlib.sha256(sign_string.encode("utf-8")).hexdigest()

        if self.params.get("output_format") == "uppercase_hex":
            sig = sig.upper()

        # Build query params (all signing outputs go to query string)
        merged[sign_param] = sig
        query_params = dict(merged)

        return headers, query_params
