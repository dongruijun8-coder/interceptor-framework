"""p1/p2/p3 XOR 签名 — 双鱼部落"""
import random
import time

from ..base import SigningProcessor

WRITE_ENDPOINTS = {
    "passwordLogin", "joinRoom", "room/config",
    "UserRank/index", "sideRoomList", "connectSuccess", "RoomPage/leave",
}


class XorTripleSigning(SigningProcessor):
    name = "xor-triple-sign"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "read_key": {"type": "string", "description": "4-byte hex for read requests"},
                "write_key": {"type": "string", "description": "4-byte hex for write requests"},
                "p3_key": {"type": "string", "description": "4-byte hex for p3 XOR (write only)"},
            },
            "required": ["read_key", "write_key", "p3_key"],
        }

    def sign(self, url: str, headers: dict) -> dict:
        read_key = bytes.fromhex(self.params["read_key"])
        write_key = bytes.fromhex(self.params["write_key"])
        p3_key = bytes.fromhex(self.params["p3_key"])

        path = url.split("/UI/")[-1] if "/UI/" in url else url.split(".com/")[-1]
        is_write = any(w in path for w in WRITE_ENDPOINTS)
        authenticated = bool(headers.get("__auth_token__"))

        p1 = "".join(random.choices("0123456789abcdef", k=32))

        if not authenticated:
            p2 = p3 = p1
        else:
            key = write_key if is_write else read_key
            p2 = self._xor_hex(p1, key)
            p3 = self._xor_hex(p2, p3_key) if is_write else p2

        headers["p1"] = p1
        headers["p2"] = p2
        headers["p3"] = p3
        headers["timestamp"] = str(int(time.time()))
        return headers

    @staticmethod
    def _xor_hex(h: str, key: bytes) -> str:
        b = bytes.fromhex(h)
        repeats = (len(b) + len(key) - 1) // len(key)
        extended = (key * repeats)[:len(b)]
        return bytes(a ^ b for a, b in zip(b, extended)).hex()


