"""MD5 排序KV签名 — 梦音风格
算法: 请求参数 + {pub_timestamp: ms_ts} → 按key字母排序 → K=V用&拼接
→ 追加 &key=secret_key → MD5 → 大写hex → 输出到HTTP header pub_sign
签名输出到headers(sign_param, timestamp_param), 不放到query string.
"""
import hashlib
import time
import uuid

from ..base import SigningProcessor

# 梦音默认排除参数 (16项)
DEFAULT_EXCLUDED = [
    "pub_sign", "pub_uid", "pub_ticket",
    "appVersion", "appVersionCode", "channel",
    "deviceId", "ispType", "model", "netType",
    "os", "osVersion", "app", "ticket",
    "smDeviceId", "newDeviceId",
]


class Md5SortedKvSigning(SigningProcessor):
    name = "md5-sorted-kv"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "secret_key": {"type": "string", "description": "签名密钥, 可为空字符串"},
                "excluded_params": {
                    "type": "array", "items": {"type": "string"},
                    "default": DEFAULT_EXCLUDED,
                },
                "timestamp_param": {"type": "string", "default": "pub_timestamp"},
                "sign_param": {"type": "string", "default": "pub_sign"},
                "hash": {"type": "string", "default": "md5"},
                "output_format": {"type": "string", "default": "uppercase_hex"},
            },
        }

    def validate(self, client) -> tuple:
        warnings = []
        sk = self.params.get("secret_key")
        if sk is None:
            warnings.append("md5-sorted-kv 缺少 secret_key 参数 (空字符串合法)")
        return len(warnings) == 0, warnings

    def sign(self, url: str, headers: dict, params: dict = None) -> tuple:
        secret = self.params.get("secret_key", "")
        excluded = set(self.params.get("excluded_params", DEFAULT_EXCLUDED))
        sign_param = self.params.get("sign_param", "pub_sign")
        ts_param = self.params.get("timestamp_param", "pub_timestamp")

        # 合并参数 + 时间戳
        merged = dict(params or {})
        ts = str(int(time.time() * 1000))
        merged[ts_param] = ts

        # 过滤排除键, 按key排序
        sorted_keys = sorted(merged.keys())
        kv_pairs = [f"{k}={merged[k]}" for k in sorted_keys if k not in excluded]

        # 追加 secret key
        kv_pairs.append(f"key={secret}")
        sign_string = "&".join(kv_pairs)

        # Hash
        hash_algo = self.params.get("hash", "md5")
        if hash_algo == "sha1":
            sig = hashlib.sha1(sign_string.encode("utf-8")).hexdigest()
        elif hash_algo == "sha256":
            sig = hashlib.sha256(sign_string.encode("utf-8")).hexdigest()
        else:
            sig = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

        if self.params.get("output_format", "uppercase_hex") == "uppercase_hex":
            sig = sig.upper()

        # 签名输出到 headers, timestamp也到headers, skip excluded参数不追加到url
        headers[sign_param] = sig
        headers[ts_param] = ts

        # 不返回 query_params — 签名只在headers里
        return headers, {}
