"""App 账号管理 — 多账号存储、SMS 登录、激活切换"""
import json
from pathlib import Path
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class AccountManager:
    def __init__(self, app_dir: str):
        self.app_dir = Path(app_dir)
        self.state_dir = self.app_dir / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._accounts_path = self.state_dir / "accounts.json"
        self.session = requests.Session()
        self.session.verify = False

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
        accounts = self.load_accounts()
        account = {
            "phone": phone,
            "token": token,
            "uid": str(uid),
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

    def _build_body(self, app_config: dict, params: dict = None) -> dict:
        """构造标准请求体 — 含所有设备指纹字段"""
        return {
            "app": "plpl",
            "build": app_config.get("build", 126),
            "channel": app_config.get("channel", "plpl_baidu"),
            "version": app_config.get("version", "1.7.40"),
            "platform": "Android",
            "subChannel": "",
            "patchVersion": "",
            "sysVersion": app_config.get("sysVersion", "12"),
            "meid": app_config.get("device_id", ""),
            "device": app_config.get("device", "SM-S9210"),
            "imei": app_config.get("device_id", ""),
            "params": params or {},
        }

    def send_sms(self, base_url: str, phone: str, captcha_validate: str, app_config: dict) -> dict:
        """发送短信验证码 — 需先完成易盾滑块验证"""
        body = self._build_body(app_config, {
            "phone": phone,
            "captcha": captcha_validate,
        })
        resp = self._post(f"{base_url}/plpl/tour/sms", body)
        if self._check(resp):
            return {"success": True, "error": ""}
        return {"success": False, "error": resp.get("message", "发送验证码失败")}

    def sms_login(self, base_url: str, phone: str, sms_code: str, app_config: dict) -> dict:
        """SMS 登录流程：查关联账号 → 登录 → 返回 token+uid"""
        # Step 1: 查询关联账号，获取 tid
        resp1 = self._post(f"{base_url}/plpl/ptl/relation/account/list",
            self._build_body(app_config, {"phone": phone, "smsCode": sms_code}))
        if not self._check(resp1):
            return {"success": False, "error": f"查询账号失败: {resp1.get('message', '')}"}

        accounts_data = resp1.get("data", {})
        account_list = accounts_data.get("list", accounts_data.get("accounts", []))
        if not account_list:
            return {"success": False, "error": "该手机号无关联账号，请先注册"}

        tid = account_list[0].get("uid", account_list[0].get("tid", ""))

        # Step 2: 登录获取 token
        resp2 = self._post(f"{base_url}/plpl/ptl/login/relation/account",
            self._build_body(app_config, {"phone": phone, "smsCode": sms_code, "tid": tid}))
        if not self._check(resp2):
            return {"success": False, "error": f"登录失败: {resp2.get('message', '')}"}

        data = resp2.get("data", {})
        user = data.get("fullUser", data.get("user", data))
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
            "nick": user.get("nick", user.get("nickName", "")),
        }

    def _post(self, url: str, body: dict) -> dict:
        r = self.session.post(
            url, json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        return r.json()

    def _check(self, resp: dict) -> bool:
        return resp.get("code") == "S_OK"
