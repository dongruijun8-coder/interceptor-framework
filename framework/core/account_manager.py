"""App 账号管理 — 多账号存储、SMS 登录、激活切换"""
import base64
import json
from pathlib import Path
from typing import Optional

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ENCRYPT_KEY = "popokey202200000"
AES_IV = "\x00" * 16
ENCRYPT_PREFIX = "encrypt-"


def encrypt_phone(phone: str) -> str:
    cipher = AES.new(ENCRYPT_KEY.encode("utf-8"), AES.MODE_CBC, AES_IV.encode("utf-8"))
    padded = pad(phone.encode("utf-8"), AES.block_size)
    encrypted = cipher.encrypt(padded)
    return ENCRYPT_PREFIX + base64.b64encode(encrypted).decode("utf-8")


class AccountManager:
    def __init__(self, app_dir: str):
        self.app_dir = Path(app_dir)
        self.state_dir = self.app_dir / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._accounts_path = self.state_dir / "accounts.json"
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/3.14.9",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
        })

    # ═══ Account CRUD ═══

    def load_accounts(self) -> list[dict]:
        if not self._accounts_path.exists():
            return []
        try:
            return json.loads(self._accounts_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_accounts(self, accounts: list[dict]) -> None:
        self._accounts_path.write_text(
            json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_account(self, phone: str, token: str, uid: str, label: str = "") -> dict:
        uid = str(uid)
        accounts = self.load_accounts()
        for a in accounts:
            if a["uid"] == uid:
                a["token"] = token
                a["phone"] = phone
                a["label"] = label or phone
                self._save_accounts(accounts)
                return a
        account = {
            "phone": phone,
            "token": token,
            "uid": uid,
            "label": label or phone,
            "active": False,
        }
        accounts.append(account)
        self._save_accounts(accounts)
        return account

    def remove_account(self, uid: str) -> bool:
        accounts = self.load_accounts()
        new_list = [a for a in accounts if a["uid"] != str(uid)]
        if len(new_list) == len(accounts):
            return False
        self._save_accounts(new_list)
        return True

    def activate_account(self, uid: str) -> bool:
        accounts = self.load_accounts()
        found = False
        for a in accounts:
            a["active"] = (a["uid"] == str(uid))
            if a["active"]:
                found = True
        if found:
            self._save_accounts(accounts)
        return found

    def get_active_account(self) -> Optional[dict]:
        accounts = self.load_accounts()
        for a in accounts:
            if a.get("active"):
                return a
        # fallback: first account
        return accounts[0] if accounts else None

    def _make_public_body(self, app_config: dict, params: dict = None) -> dict:
        """构造公开请求体 — 不含 token/uid，用于短信/登录"""
        return {
            "app": app_config.get("app", "plpl"),
            "build": app_config.get("build", 126),
            "channel": app_config.get("channel", "plpl_baidu"),
            "meid": app_config.get("device_id", ""),
            "device": app_config.get("device", "SM-S9210"),
            "platform": app_config.get("platform", "Android"),
            "subChannel": "",
            "token": "",
            "uid": 0,
            "version": app_config.get("version", "1.7.40"),
            "patchVersion": "",
            "sysVersion": app_config.get("sysVersion", "12"),
            "params": params or {},
        }

    def send_sms(self, base_url: str, phone: str, captcha_validate: str, app_config: dict) -> dict:
        """发送短信验证码 — 需先完成易盾滑块验证"""
        body = self._make_public_body(app_config, {
            "authenticate": captcha_validate,
            "contryCode": "+86",
            "phoneNumber": encrypt_phone(phone),
        })
        resp = self._post(f"{base_url}/plpl/tour/sms", body)
        if self._check(resp):
            return {"success": True, "error": ""}
        return {"success": False, "error": resp.get("message", "发送验证码失败")}

    def sms_login(self, base_url: str, phone: str, sms_code: str, app_config: dict) -> dict:
        """SMS 登录流程：查关联账号 → 登录 → 返回 token+uid"""
        meid = app_config.get("device_id", "")
        enc_phone = encrypt_phone(phone)

        def _post_login(path: str, params: dict) -> dict:
            body = self._make_public_body(app_config, params)
            body["imei"] = meid
            r = self.session.post(f"{base_url}{path}", json=body, timeout=30)
            return r.json()

        # Step 1: 查询关联账号，获取 tid
        tid = 0
        resp1 = _post_login("/plpl/ptl/relation/account/list", {
            "contryCode": "+86",
            "phoneNumber": enc_phone,
            "smsCode": sms_code,
            "inviteRedpackCode": "",
            "tid": 0,
        })
        if self._check(resp1):
            accts = resp1.get("data", {}).get("list", [])
            if accts:
                tid = accts[0].get("uid", 0)

        # Step 2: 登录获取 token
        resp2 = _post_login("/plpl/ptl/login/relation/account", {
            "contryCode": "+86",
            "phoneNumber": enc_phone,
            "smsCode": sms_code,
            "inviteRedpackCode": "",
            "tid": tid,
        })
        if not self._check(resp2):
            code = resp2.get("code", "")
            if code == "F_NEED_REG":
                return {"success": False, "error": "该号码未注册，请先在 App 上注册"}
            return {"success": False, "error": f"登录失败: {resp2.get('message', code)}"}

        data = resp2.get("data", {})
        user = data.get("fullUser", {}).get("user", data.get("user", data))
        token = data.get("token", "")
        uid = user.get("uid", tid)

        if not token or not uid:
            return {"success": False, "error": "登录返回缺少 token 或 uid"}

        self.add_account(phone, token, str(uid), label=phone)
        self.activate_account(str(uid))

        return {
            "success": True,
            "token": token,
            "uid": str(uid),
            "nick": user.get("nick", user.get("nickName", user.get("name", ""))),
        }

    def _post(self, url: str, body: dict) -> dict:
        r = self.session.post(url, json=body, timeout=30)
        return r.json()

    def _check(self, resp: dict) -> bool:
        return resp.get("code") == "S_OK"
