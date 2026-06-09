"""HTTP client — POST/GET with encryption + signing + diagnosis pipeline."""
import json
import time
import urllib.parse
import requests


class HttpClient:
    """HTTP transport with processor pipeline (encrypt -> sign -> send -> decrypt).

    Dependencies injected at construction — no direct coupling to Client.
    """

    def __init__(
        self,
        base_url: str,
        default_headers: dict,
        encryptor,        # has .encode(body) -> bytes, .decode(raw) -> dict
        signer,            # has .sign(url, headers, body/params) -> (headers, extra_params)
        diagnose,          # has .log(method, path, step, detail, ms)
        get_auth_token,    # callable() -> str
        get_uid,           # callable() -> str
        session=None,
    ):
        self._base_url = base_url
        self._default_headers = default_headers
        self._encryptor = encryptor
        self._signer = signer
        self._diagnose = diagnose
        self._get_auth_token = get_auth_token
        self._get_uid = get_uid
        self.session = session or requests.Session()
        self.session.verify = False

    def post(self, url: str, body: dict) -> dict:
        path = url.replace(self._base_url, "") if self._base_url else url
        _d = self._diagnose

        # Encrypt
        t0 = time.time()
        try:
            encrypted = self._encryptor.encode(body)
        except Exception as e:
            _d.log("POST", path, "encrypt", f"FAILED: {e}")
            raise RuntimeError(f"encryption.encode failed: {e}")
        t1 = time.time()
        _d.log("POST", path, "encrypt",
               f"{self._encryptor.name} | body {len(json.dumps(body))}B -> {len(encrypted)}B",
               (t1 - t0) * 1000)

        # Headers
        headers = dict(self._default_headers)
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json; charset=utf-8"
        headers["__auth_token__"] = self._get_auth_token()

        # Sign
        t0 = time.time()
        headers, extra_params = self._signer.sign(url, headers, body)
        t1 = time.time()
        _d.log("POST", path, "sign",
               f"{self._signer.name}", (t1 - t0) * 1000)

        auth_token = self._get_auth_token()
        if auth_token:
            _d.log("POST", path, "auth", f"token={auth_token[:12]}... uid={self._get_uid()}")
        else:
            _d.log("POST", path, "auth", "skip (未认证)")

        if extra_params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(extra_params)

        # Send
        t0 = time.time()
        r = self.session.post(url, data=encrypted, headers=headers, timeout=30)
        t1 = time.time()
        r.raise_for_status()
        _d.log("POST", path, "send",
               f"{r.status_code} | {len(r.content)}B", (t1 - t0) * 1000)

        # Decrypt
        t0 = time.time()
        try:
            decoded = self._encryptor.decode(r.content)
        except Exception as e:
            _d.log("POST", path, "decrypt", f"FAILED: {e}. 回退到 raw text")
            try:
                decoded = json.loads(r.text)
            except json.JSONDecodeError:
                raise RuntimeError(f"decryption failed: {r.text[:200]}")
        t1 = time.time()
        _d.log("POST", path, "decrypt",
               f"{self._encryptor.name} | {len(r.content)}B -> {len(json.dumps(decoded))}B",
               (t1 - t0) * 1000)

        code = decoded.get("code", "?")
        ok = code in (200, "S_OK", 0) or decoded.get("status") == 0 or int(decoded.get("ret", 1)) == 1
        if ok:
            _d.log("POST", path, "business", f"code={code} | OK")
        else:
            msg = decoded.get("msg", decoded.get("message", ""))
            _d.log("POST", path, "business", f"code={code} | msg={msg} | ✗ 业务错误")

        return decoded

    def get(self, url: str, params: dict = None) -> dict:
        path = url.replace(self._base_url, "") if self._base_url else url
        _d = self._diagnose
        params = dict(params or {})

        headers = dict(self._default_headers)
        headers["__auth_token__"] = self._get_auth_token()

        t0 = time.time()
        headers, extra_params = self._signer.sign(url, headers, params)
        t1 = time.time()
        _d.log("GET", path, "sign", f"{self._signer.name}", (t1 - t0) * 1000)

        auth_token = self._get_auth_token()
        if auth_token:
            _d.log("GET", path, "auth", f"token={auth_token[:12]}...")
        else:
            _d.log("GET", path, "auth", "skip")

        params.update(extra_params)

        t0 = time.time()
        r = self.session.get(url, params=params, headers=headers, timeout=30)
        t1 = time.time()
        r.raise_for_status()
        _d.log("GET", path, "send", f"{r.status_code} | {len(r.content)}B", (t1 - t0) * 1000)

        try:
            decoded = self._encryptor.decode(r.content)
        except Exception:
            _d.log("GET", path, "decrypt", "no encryption / raw JSON")
            decoded = json.loads(r.text)

        code = decoded.get("code", "?")
        ok = code in (200, "S_OK", 0) or decoded.get("status") == 0 or int(decoded.get("ret", 1)) == 1
        if ok:
            _d.log("GET", path, "business", f"code={code} | OK")
        else:
            _d.log("GET", path, "business", f"code={code} | ✗")

        return decoded

    def request(self, ep: dict, body: dict) -> dict:
        """Dispatch to GET or POST based on endpoint method field."""
        method = ep.get("method", "POST").upper()
        path = ep["path"]
        url = f"{self._base_url}{path}"
        if method == "GET":
            return self.get(url, body)
        else:
            return self.post(url, body)
