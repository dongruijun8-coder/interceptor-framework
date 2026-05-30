"""双鱼部落 (Shuangyu) 客户端 — AES-256-CBC 加密 + p1/p2/p3 签名 + 密码登录"""
import base64
import gzip
import json
import random
import time
import uuid
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from framework.core.base_client import BaseClient

# — AES-256-CBC constants (reverse-engineered) —
AES_KEY = b"Yn9jsLRbHk0o6YykRJ8ILoVd1ygqkAMK"  # 32 bytes
AES_IV = b"FCE3F1A4-5DC3-41"  # 16 bytes

# — p1/p2/p3 XOR signature keys —
XOR_KEY_READ = bytes.fromhex("01528e5f")
XOR_KEY_WRITE = bytes.fromhex("015357de")
XOR_KEY_P3 = bytes.fromhex("0001d981")

WRITE_ENDPOINTS = {
    "passwordLogin", "joinRoom", "room/config",
    "UserRank/index", "sideRoomList", "connectSuccess",
    "RoomPage/leave",
}


def _is_write(path: str) -> bool:
    return any(w in path for w in WRITE_ENDPOINTS)


def _xor_hex(h: str, key: bytes) -> str:
    b = bytes.fromhex(h)
    repeats = (len(b) + len(key) - 1) // len(key)
    extended = (key * repeats)[:len(b)]
    return bytes(a ^ b for a, b in zip(b, extended)).hex()


def _make_signature(path: str, authenticated: bool = False) -> tuple:
    """Generate p1, p2, p3 — pre-login: all equal; post-login: XOR-derived."""
    p1 = "".join(random.choices("0123456789abcdef", k=32))
    if not authenticated:
        return p1, p1, p1
    if _is_write(path):
        p2 = _xor_hex(p1, XOR_KEY_WRITE)
        p3 = _xor_hex(p2, XOR_KEY_P3)
    else:
        p2 = _xor_hex(p1, XOR_KEY_READ)
        p3 = p2
    return p1, p2, p3


def _encrypt_body(data: dict) -> str:
    plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return base64.b64encode(cipher.encrypt(pad(plain, AES.block_size))).decode("ascii")


def _decrypt_body(text: str) -> bytes:
    raw = base64.b64decode(text)
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return unpad(cipher.decrypt(raw), AES.block_size)


class ShuangyuClient(BaseClient):
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = str(Path(__file__).parent / "config.json")
        super().__init__(config_path)
        self._auth_token = self.config.get("auth_token", "") or ""
        cs = self.config.get("client_session", "") or str(uuid.uuid4()).upper()
        self._session_id = cs
        self.config["client_session"] = cs

        dt = self.config.get("device_token", "") or str(uuid.uuid4())
        self.config["device_token"] = dt

    # ═══ 3 required methods ═══

    def fetch_all_rooms(self) -> list:
        cfg = self.config
        base = cfg["base_url"]

        cat_resp = self._encrypted_post(f"{base}/UI/Room/Home/categoryList", {})
        if cat_resp.get("code") != 200:
            self._notify("error", f"获取房间分类失败: {cat_resp.get('message', '')}")
            return []

        categories = cat_resp.get("data", {}).get("list", [])
        if not categories:
            self._notify("error", "房间分类为空")
            return []

        rooms = []
        for cat in categories:
            cat_id = cat.get("id", cat.get("category_id", 0))
            cat_name = cat.get("name", cat.get("category_name", str(cat_id)))
            for page in range(1, 50):
                resp = self._encrypted_post(f"{base}/UI/Room/Home/roomList", {
                    "id": cat_id, "page": page, "page_size": 20,
                })
                if resp.get("code") != 200:
                    break
                items = resp.get("data", {}).get("list", [])
                if not items:
                    break
                for r in items:
                    rooms.append({
                        "id": str(r.get("id", r.get("room_id", ""))),
                        "name": r.get("name", r.get("room_name", r.get("title", ""))),
                        "type": cat_name,
                    })
                if len(items) < 20:
                    break
        return rooms

    def fetch_room_ranking(self, room: dict, period: str) -> list:
        cfg = self.config
        base = cfg["base_url"]

        period_map = {"今日": "day", "本周": "week", "本月": "month"}
        mode_map = {"贡献榜": "rich", "魅力榜": "charm", "财富榜": "wealth"}
        rank_type = period_map.get(period, "day")
        mode = mode_map.get(self._data_source, "rich")

        room_id = room.get("id", "")
        try:
            room_id = int(room_id)
        except (ValueError, TypeError):
            pass

        users = []
        for offset in range(0, 100, 20):
            resp = self._encrypted_post(f"{base}/UI/Room/UserRank/list", {
                "room_id": room_id,
                "mode": mode,
                "rank_type": rank_type,
            })
            if resp.get("code") != 200:
                break
            items = resp.get("data", {}).get("list", [])
            if not items:
                break
            users.extend(items)
        return [self.parse_user(u) for u in users]

    def send_message(self, target_uid: str, text: str) -> dict:
        """
        私信走融云 IM TCP 协议，HTTP API 无发消息端点。
        TODO: RongCloud TCP client
          1. Navi: POST flse.cn.rongnav.com/v2/navi.json
          2. TCP connect 112.126.70.47:443
          3. Auth with rongCloudToken
          4. Send via RongCloud private protocol
        """
        return {"success": False, "error": "私信需融云 TCP，待实现"}

    # ═══ Auth ═══

    def authenticate(self) -> bool:
        cfg = self.config
        base = cfg["base_url"]
        phone = cfg.get("phone", "")
        password = cfg.get("password", "")

        if not phone or not password:
            self._notify("error", "config.json 缺少 phone/password")
            return False

        resp = self._encrypted_post(
            f"{base}/UI/PasswordLoginPage/passwordLogin",
            {"phone": phone, "password": password, "code": None, "mobile_token": None},
        )

        if resp.get("code") != 200:
            self._notify("error", f"登录失败: {resp.get('message', '')}")
            return False

        data = resp.get("data", {})
        token = data.get("token", "")
        uid = data.get("id", data.get("uid", ""))

        if not token:
            self._notify("error", "登录响应缺少 token")
            return False

        self._auth_token = token
        self.config["auth_token"] = token
        self.config["uid"] = str(uid)

        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        rct = data.get("rongCloudToken", "")
        if rct:
            self.config["rongcloud_token"] = rct

        nick = data.get("nickname", data.get("nick", ""))
        self._notify("info", f"登录成功 uid={uid} nick={nick}")
        return True

    # ═══ Encrypted HTTP ═══

    def _encrypted_post(self, url: str, body: dict) -> dict:
        path = url.split("/UI/")[-1] if "/UI/" in url else url.split(".com/")[-1]
        p1, p2, p3 = _make_signature(path, authenticated=bool(self._auth_token))

        headers = {
            "p1": p1, "p2": p2, "p3": p3,
            "clienttype": self.config.get("platform", "Android"),
            "deviceid": self.config.get("device_id", ""),
            "token": self._auth_token or self.config.get("device_token", ""),
            "timestamp": str(int(time.time())),
            "clientsession": self._session_id,
            "isemulator": "true",
            "isrooted": "false",
            "hasfrida": "false",
            "hasxposed": "false",
            "isrunninginmultiaccount": "false",
            "isaccessibilityenabled": "false",
            "accessibilityservices": "[]",
            "appversion": self.config.get("version", "2.47.1"),
            "devicetype": self.config.get("device", "Samsung SM-S9280"),
            "build": str(self.config.get("build", 334)),
            "channel": self.config.get("channel", "oppo"),
            "Content-Type": "text/plain; charset=UTF-8",
        }
        devicetoken = self.config.get("devicetoken", "")
        if devicetoken:
            headers["devicetoken"] = devicetoken
        smdeviceid = self.config.get("smdeviceid", "")
        if smdeviceid:
            headers["smdeviceid"] = smdeviceid

        r = self.session.post(url, data=_encrypt_body(body).encode("ascii"),
                              headers=headers, timeout=30)
        r.raise_for_status()
        return self._decrypt_response(r.text)

    def _decrypt_response(self, text: str) -> dict:
        if not text or not text.strip():
            return {"code": 200, "data": {}, "message": ""}
        try:
            data = _decrypt_body(text.strip())
        except Exception:
            try:
                return json.loads(text.strip())
            except json.JSONDecodeError:
                return {"code": -1, "data": {}, "message": f"解密失败: {text[:100]}"}

        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)

        return json.loads(data.decode("utf-8"))

    # ═══ Override BaseClient HTTP ═══

    def _post(self, url: str, body: dict) -> dict:
        return self._encrypted_post(url, body)

    def _get(self, url: str, params: dict = None) -> dict:
        params = params or {}
        return self._encrypted_post(url, params)

    def check_response(self, resp_data: dict) -> bool:
        return resp_data.get("code") in (200, "S_OK")

    def parse_user(self, raw: dict) -> dict:
        return {
            "uid": str(raw.get("uid", raw.get("user_id", raw.get("id", "")))),
            "nick": raw.get("nickname", raw.get("nick", raw.get("name", ""))),
            "amount": raw.get("amount", raw.get("score", raw.get("total", raw.get("contribute", 0)))),
            "gender": raw.get("gender", raw.get("sex", 0)),
        }
