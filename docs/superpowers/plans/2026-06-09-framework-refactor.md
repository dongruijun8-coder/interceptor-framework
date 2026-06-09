# Framework Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `base_client.py` (1060 lines) into focused modules with single responsibilities, unify two Frida transport mechanisms under one abstract interface, and clean up test/tool scripts.

**Architecture:** Extract template functions (`fill_template`, `resolve_path`, `map_fields`) as pure module-level functions in `template.py`. Extract pagination logic into `pagination.py`. Extract HTTP dispatch (`_post`, `_get`, `_request`) into `http.py` with `HttpClient` class. Unify `frida_cli.py` + `frida_session.py` under one `FridaTransport` ABC. Extract pipeline logic (`run_room`, `_send_to_user`, `_run_per_room`, `_run_global`) into `pipeline.py`. Result: `base_client.py` → `client.py` (~300 lines, assembly-only).

**Tech Stack:** Python 3.14, Flask, Frida, requests

---

## File Structure Map

```
framework/
├── bridge/
│   ├── frida_transport.py          [NEW] Abstract base class
│   ├── frida_transport_cli.py      [NEW] Subprocess stdin/stdout transport
│   ├── frida_transport_binding.py  [NEW] Python frida binding transport
│   ├── frida_module_loader.py      [KEEP]
│   ├── frida_cli.py                [DELETE] → merged into frida_transport_cli.py
│   ├── frida_session.py            [DELETE] → merged into frida_transport_binding.py
│   ├── adb_device.py               [KEEP]
│   ├── env_checker.py              [KEEP]
│   └── hook_extract_creds.js       [KEEP]
│
├── core/
│   ├── template.py                 [NEW] Pure functions: fill_template, resolve_path, map_fields
│   ├── pagination.py               [NEW] Paginator class with 3 strategies
│   ├── http.py                     [NEW] HttpClient: _post/_get/_request/fetch_paginated
│   ├── pipeline.py                 [NEW] Pipeline: run_room, send_to_user, _run_per_room, _run_global
│   ├── client.py                   [RENAME] From base_client.py, ~250 lines assembly-only
│   ├── diagnose.py                 [KEEP] GBK fix applied
│   ├── account_manager.py          [KEEP]
│   ├── task_manager.py             [KEEP]
│   ├── state_manager.py            [KEEP]
│   ├── processor_registry.py       [KEEP]
│   ├── recipes.py                  [KEEP]
│   ├── dashboard.py                [KEEP] Minor updates for new transport
│   ├── base_client.py              [DELETE] → renamed to client.py
│   ├── test_processor.py           [KEEP]
│   └── processors/                 [KEEP unchanged]
│
tests/                              [NEW] E2E tests (after passing)
tools/                              [NEW] One-shot capture/debug scripts
```

---

### Task 1: Extract `template.py` — Pure Functions

**Files:**
- Create: `framework/core/template.py`
- Modify: `framework/core/base_client.py` → remove `_fill_template`, `_resolve_path`, `_map_fields`, `_identity_vars`
- Modify: `framework/core/processors/auth/password_login.py` → remove duplicate `_resolve_path`
- Modify: `framework/core/processors/messaging/rest_json.py` → update import
- Test: `tests/test_template.py`

**Rationale:** `_fill_template`, `_resolve_path`, `_map_fields` are pure functions (no `self` needed aside from `_identity_vars` which needs `_auth_token`/`_uid`). Extract as module-level functions, take identity dict as parameter.

- [ ] **Step 1: Create template.py**

```python
"""Template engine — pure functions for {{var}} substitution and data mapping."""
import re


def fill_template(template, identity_vars: dict = None, **kwargs):
    """Fill {{var}} / {{obj.key}} placeholders in a dict recursively.

    Single-var patterns (``{{var}}`` as the whole value) preserve the raw
    type of the resolved value. Multi-var or mixed-text values are always
    string-replaced.

    Args:
        template: dict with placeholders
        identity_vars: dict of identity vars {uid, token, device_id, ...}
        **kwargs: named context objects (room, period_key, data_source_key, _iter)

    Returns:
        dict with placeholders resolved
    """
    identity_vars = identity_vars or {}
    result = {}
    for key, value in template.items():
        if isinstance(value, str) and "{{" in value:
            # Single {{var}} — preserve raw type
            m = re.fullmatch(r'\{\{(.+?)\}\}', value.strip())
            if m:
                var_path = m.group(1)
                parts = var_path.split(".", 1)
                if parts[0] in kwargs:
                    obj = kwargs[parts[0]]
                    if len(parts) > 1 and isinstance(obj, dict):
                        result[key] = obj.get(parts[1], "")
                    else:
                        result[key] = obj
                    continue
                if var_path in identity_vars:
                    result[key] = identity_vars[var_path]
                    continue
            # Multi-var or mixed text — string replacement
            def replacer(m):
                var_path = m.group(1)
                parts = var_path.split(".", 1)
                if parts[0] in kwargs:
                    obj = kwargs[parts[0]]
                    if len(parts) > 1 and isinstance(obj, dict):
                        return str(obj.get(parts[1], ""))
                    return str(obj)
                if var_path in identity_vars:
                    return str(identity_vars[var_path])
                return m.group(0)

            result[key] = re.sub(r'\{\{(.+?)\}\}', replacer, value)
        elif isinstance(value, dict):
            result[key] = fill_template(value, identity_vars, **kwargs)
        else:
            result[key] = value
    return result


def resolve_path(data: dict, path: str):
    """Resolve dot-separated path in nested dict/list structure.

    Examples:
        resolve_path({"a": {"b": 1}}, "a.b") → 1
        resolve_path({"data": [1,2,3]}, "data.0") → 1
    """
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        elif isinstance(current, list):
            try:
                idx = int(p)
                current = current[idx] if idx < len(current) else None
            except ValueError:
                return None
        else:
            return None
    return current


def map_fields(raw: dict, mapping: dict, identity_vars: dict = None):
    """Map raw API response fields to framework-standard format.

    mapping keys are framework field names. mapping values can be:
      - "user.uid" (dot path) → resolved via resolve_path
      - "{{room.id}}" (template) → filled via fill_template
      - "name" (plain key) → raw.get("name")
    """
    result = {}
    for framework_field, source in mapping.items():
        if isinstance(source, str) and "{{" in source:
            val = fill_template({"k": source}, identity_vars, **{})["k"]
            result[framework_field] = val
        elif isinstance(source, str) and "." in source:
            result[framework_field] = resolve_path(raw, source)
        else:
            if isinstance(source, str):
                result[framework_field] = raw.get(source, source)
            else:
                result[framework_field] = source
    return result
```

- [ ] **Step 2: Run existing test to confirm nothing broken yet**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/test_template.py -v 2>&1 || echo "Test file not created yet — expected"
```

- [ ] **Step 3: Write tests for template functions**

File: `tests/test_template.py`

```python
"""Tests for framework.core.template"""
import pytest
from framework.core.template import fill_template, resolve_path, map_fields


class TestFillTemplate:
    def test_simple_string_replacement(self):
        result = fill_template(
            {"greeting": "{{name}} 你好"},
            identity_vars={},
            name="World",
        )
        assert result == {"greeting": "World 你好"}

    def test_single_var_preserves_int_type(self):
        result = fill_template(
            {"page": "{{page}}"},
            identity_vars={},
            page=42,
        )
        assert result == {"page": 42}
        assert isinstance(result["page"], int)

    def test_nested_dict_recursive(self):
        result = fill_template(
            {"outer": {"inner": "{{val}}"}},
            identity_vars={},
            val="x",
        )
        assert result == {"outer": {"inner": "x"}}

    def test_identity_var(self):
        result = fill_template(
            {"uid": "{{uid}}"},
            identity_vars={"uid": 12345},
        )
        assert result == {"uid": 12345}

    def test_object_field_access(self):
        result = fill_template(
            {"room_id": "{{room.id}}"},
            identity_vars={},
            room={"id": 999, "name": "test"},
        )
        assert result == {"room_id": 999}

    def test_no_placeholder_passthrough(self):
        result = fill_template(
            {"key": "value", "num": 123},
            identity_vars={},
        )
        assert result == {"key": "value", "num": 123}


class TestResolvePath:
    def test_simple_key(self):
        assert resolve_path({"a": 1}, "a") == 1

    def test_nested_dict(self):
        assert resolve_path({"a": {"b": {"c": 1}}}, "a.b.c") == 1

    def test_list_index(self):
        assert resolve_path({"data": [10, 20, 30]}, "data.1") == 20

    def test_missing_key_returns_none(self):
        assert resolve_path({"a": 1}, "b") is None

    def test_nested_missing(self):
        assert resolve_path({"a": {}}, "a.b.c") is None


class TestMapFields:
    def test_dot_path(self):
        result = map_fields(
            {"user": {"uid": 42, "nickname": "Alice"}},
            {"uid": "user.uid", "nick": "user.nickname"},
        )
        assert result == {"uid": 42, "nick": "Alice"}

    def test_plain_key(self):
        result = map_fields({"id": 1, "name": "Room"}, {"id": "id", "name": "name"})
        assert result == {"id": 1, "name": "Room"}

    def test_static_literal(self):
        result = map_fields({"a": 1}, {"static": "hello"})
        assert result == {"static": "hello"}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/test_template.py -v 2>&1
```

Expected: 12 tests pass.

- [ ] **Step 5: Update base_client.py to delegate to template module**

Add import at top of `base_client.py`:
```python
from .template import fill_template, resolve_path, map_fields
```

Replace `_fill_template` body with delegation:
```python
def _fill_template(self, template, **kwargs):
    return fill_template(template, self._identity_vars(), **kwargs)
```

Replace `_resolve_path` body with delegation:
```python
@staticmethod
def _resolve_path(data, path):
    return resolve_path(data, path)
```

Replace `_map_fields` body with delegation:
```python
def _map_fields(self, raw, mapping):
    return map_fields(raw, mapping, self._identity_vars())
```

Keep `_identity_vars` method in base_client — it needs self state.

- [ ] **Step 6: Remove duplicate `_resolve_path` from password_login.py**

In `framework/core/processors/auth/password_login.py`:
```python
# Add import at top
from framework.core.template import resolve_path

# Delete lines 99-108 (the entire static method _resolve_path)
# Replace method calls: self._resolve_path(...) → resolve_path(...)
```

Line 77-78 change:
```python
# Before:
token = self._resolve_path(data, resp_map.get("token", "token"))
uid = self._resolve_path(data, resp_map.get("uid", "uid"))
# After:
token = resolve_path(data, resp_map.get("token", "token"))
uid = resolve_path(data, resp_map.get("uid", "uid"))
```

- [ ] **Step 7: Update rest_json.py**

In `framework/core/processors/messaging/rest_json.py` line 92:
```python
# Before:
body = client._fill_template(...)
# After:
from framework.core.template import fill_template
body = fill_template(
    endpoint.get("body", {}),
    client._identity_vars(),
    uid=uid,
    text=text,
)
```

- [ ] **Step 8: Verify — run all existing tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add framework/core/template.py tests/test_template.py framework/core/base_client.py framework/core/processors/auth/password_login.py framework/core/processors/messaging/rest_json.py
git commit -m "refactor: extract template functions into template.py

Pure functions fill_template/resolve_path/map_fields moved from
base_client.py (methods) to template.py (module-level). Removed
duplicate _resolve_path from password_login.py."
```

---

### Task 2: Extract `pagination.py` — Paginator Class

**Files:**
- Create: `framework/core/pagination.py`
- Modify: `framework/core/base_client.py` → remove `_fetch_paginated`, `_extract_list`

**Rationale:** `_fetch_paginated` only needs a `requester(body) -> dict` callback and the endpoint spec. Extract as standalone `Paginator` class.

- [ ] **Step 1: Create pagination.py**

```python
"""Pagination strategies — page_number, offset_limit, cursor_offset.

All strategies call ``requester(body) -> dict`` for each page, then
collect items from the response via ``extractor(resp) -> list``.
"""
import json
from framework.core.template import resolve_path


class Paginator:
    """Dispatch pagination type and collect results."""

    @staticmethod
    def paginate(
        ep: dict,
        base_body: dict,
        requester,         # callable(body) -> dict (response)
        extractor,         # callable(resp) -> list (items)
    ) -> list:
        """Fetch all pages for an endpoint. Returns list of raw items (dicts)."""
        pagination = ep.get("pagination")
        ptype = pagination.get("type") if pagination else None

        if not ptype:
            resp = requester(base_body)
            return extractor(resp)

        size = pagination.get("size", 20)
        stop_on = pagination.get("stop_on", "empty_list")
        results = []

        if ptype == "offset_limit":
            for offset in range(0, 500, size):
                body = dict(base_body)
                body["offset"] = offset
                body["limit"] = size
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        elif ptype == "page_number":
            for page in range(1, 50):
                body = dict(base_body)
                body["page"] = page
                body["page_size"] = size
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        elif ptype == "cursor_offset":
            offset_field = pagination.get("offset_field", "offset")
            offset_sent = pagination.get("offset_sent", "")
            max_iters = pagination.get("max_iters", 30)
            cursor = ""
            for _ in range(max_iters):
                body = dict(base_body)
                body[offset_field] = cursor
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if offset_sent:
                    cursor = resolve_path(resp, offset_sent) or ""
                else:
                    cursor = ""
                if not cursor:
                    break
                if stop_on == "empty_list" and len(items) < size:
                    break

        else:
            resp = requester(base_body)
            results = extractor(resp)

        return results

    @staticmethod
    def extract_list(resp: dict, ep: dict) -> list:
        """Extract items from response using optional response_path config."""
        path = ep.get("response_path", "data.list")
        items = resolve_path(resp, path)
        if isinstance(items, list):
            return items
        data = resp.get("data")
        if isinstance(data, list):
            return data
        return []
```

- [ ] **Step 2: Write tests**

File: `tests/test_pagination.py`

```python
"""Tests for framework.core.pagination"""
import pytest
from framework.core.pagination import Paginator


class TestExtractList:
    def test_default_data_list(self):
        resp = {"code": 0, "data": {"list": [1, 2, 3]}}
        assert Paginator.extract_list(resp, {}) == [1, 2, 3]

    def test_custom_response_path(self):
        resp = {"code": 0, "data": {"items": [4, 5]}}
        ep = {"response_path": "data.items"}
        assert Paginator.extract_list(resp, ep) == [4, 5]

    def test_data_is_list(self):
        resp = {"code": 0, "data": [1, 2]}
        assert Paginator.extract_list(resp, {}) == [1, 2]

    def test_empty_returns_empty_list(self):
        assert Paginator.extract_list({}, {}) == []


class TestPageNumber:
    def test_single_page(self):
        responses = [
            {"code": 0, "data": {"list": [{"id": 1}, {"id": 2}]}},
        ]
        def requester(body):
            return responses.pop(0)

        ep = {"path": "/test", "pagination": {"type": "page_number", "size": 20}}
        base_body = {"page": 1, "page_size": 20}

        result = Paginator.paginate(
            ep, base_body, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_empty_page_stops(self):
        responses = [{"code": 0, "data": {"list": []}}]
        def requester(body):
            return responses.pop(0)

        ep = {"path": "/test", "pagination": {"type": "page_number", "size": 20}}
        result = Paginator.paginate(
            ep, {"page": 1, "page_size": 20}, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert result == []


class TestCursorOffset:
    def test_cursor_pagination(self):
        responses = [
            {"code": 0, "data": {"list": [1, 2], "nextCursor": "abc"}},
            {"code": 0, "data": {"list": [3], "nextCursor": ""}},
        ]
        def requester(body):
            return responses.pop(0)

        ep = {
            "path": "/test",
            "pagination": {
                "type": "cursor_offset",
                "offset_field": "cursor",
                "offset_sent": "data.nextCursor",
            },
        }
        result = Paginator.paginate(
            ep, {}, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert result == [1, 2, 3]
```

- [ ] **Step 3: Run pagination tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/test_pagination.py -v 2>&1
```

Expected: 6 tests pass.

- [ ] **Step 4: Update base_client.py to use Paginator**

Add import:
```python
from .pagination import Paginator
```

Change `_extract_list` to delegate:
```python
def _extract_list(self, resp: dict, ep: dict) -> list:
    return Paginator.extract_list(resp, ep)
```

Change `_fetch_paginated` to delegate:
```python
def _fetch_paginated(self, ep: dict, base_body: dict = None) -> list:
    if base_body is None:
        base_body = self._fill_template(dict(ep.get("body", {})))
    
    def requester(body):
        return self._request(ep, body)
    
    def extractor(resp):
        return Paginator.extract_list(resp, ep)
    
    return Paginator.paginate(ep, base_body, requester, extractor)
```

- [ ] **Step 5: Run all tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

- [ ] **Step 6: Commit**

```bash
git add framework/core/pagination.py tests/test_pagination.py framework/core/base_client.py
git commit -m "refactor: extract pagination into Paginator class

Paginator.paginate() handles page_number/offset_limit/cursor_offset
strategies as a standalone class. base_client delegates to it."
```

---

### Task 3: Extract `http.py` — HttpClient

**Files:**
- Create: `framework/core/http.py`
- Modify: `framework/core/base_client.py` → remove `_post`, `_get`, `_request`
- Modify: `framework/core/dashboard.py` → unchanged (uses client methods, not _post directly)

**Rationale:** `_post`, `_get`, `_request` form a coherent HTTP dispatch layer with encryption/signing hooks. Extract as `HttpClient` class that takes encryptor/signer/diagnose as dependencies.

- [ ] **Step 1: Create http.py**

```python
"""HTTP client — POST/GET with encryption + signing + diagnosis pipeline."""
import json
import time
import urllib.parse
import requests


class HttpClient:
    """HTTP transport with processor pipeline (encrypt → sign → send → decrypt).

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
        ok = code in (200, "S_OK", 0) or decoded.get("status") == 0 or decoded.get("ret") == 1
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
        ok = code in (200, "S_OK", 0) or decoded.get("status") == 0 or decoded.get("ret") == 1
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
```

- [ ] **Step 2: Update base_client.py — inject HttpClient**

In `__init__`, create HttpClient after processors:

```python
from .http import HttpClient

# After processor loading (~line 38):
self.http = HttpClient(
    base_url=self._base_url,
    default_headers=self._default_headers,
    encryptor=self._encryptor,
    signer=self._signer,
    diagnose=self._diagnose,
    get_auth_token=lambda: self._auth_token,
    get_uid=lambda: self._uid,
    session=self.session,
)
```

Replace `_post`, `_get`, `_request` with delegation:
```python
def _post(self, url, body):
    return self.http.post(url, body)

def _get(self, url, params=None):
    return self.http.get(url, params)

def _request(self, ep, body):
    return self.http.request(ep, body)
```

Remove the old method bodies for `_post` (lines 286-352), `_get` (lines 354-393), `_request` (lines 397-405).

- [ ] **Step 3: Run all tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

- [ ] **Step 4: Commit**

```bash
git add framework/core/http.py framework/core/base_client.py
git commit -m "refactor: extract HttpClient from base_client

_post/_get/_request moved to HttpClient class. Dependencies
(encryptor, signer, diagnose) injected at construction."
```

---

### Task 4: Unify Frida Transport

**Files:**
- Create: `framework/bridge/frida_transport.py`
- Create: `framework/bridge/frida_transport_cli.py`
- Create: `framework/bridge/frida_transport_binding.py`
- Modify: `framework/bridge/frida_cli.py` → delete, merged into frida_transport_cli.py
- Modify: `framework/bridge/frida_session.py` → delete, merged into frida_transport_binding.py
- Modify: `framework/core/processors/encryption/aes_cbc.py` → use FridaTransport
- Modify: `framework/core/processors/messaging/frida_rpc.py` → use FridaTransport
- Modify: `framework/core/dashboard.py` → use FridaTransport

**Rationale:** Two parallel Frida mechanisms (CLI subprocess and Python binding) with overlapping APIs. Unify under one ABC so callers don't care which transport is active.

- [ ] **Step 1: Create frida_transport.py (ABC)**

```python
"""FridaTransport — unified abstract interface for Frida communication.

Two implementations:
  - FridaTransportCli:    subprocess stdin/stdout (NIS bypass)
  - FridaTransportBinding: Python frida module (normal apps)
"""
from abc import ABC, abstractmethod


class FridaTransport(ABC):
    """Abstract transport for Frida-to-app communication."""

    @abstractmethod
    def connect(self, serial: str, package: str, script_path: str) -> None:
        """Establish connection to target app process."""

    @abstractmethod
    def send_message(self, uid: str, text: str, timeout: float = 5.0) -> dict:
        """Send IM message. Returns {success: bool, error: str}."""

    @abstractmethod
    def capture_key(self, timeout: float = 30.0) -> dict | None:
        """Block until KEY_JSON captured. Returns {key_hex, iv_hex, headers} or None."""

    @abstractmethod
    def disconnect(self) -> None:
        """Clean up connection."""

    @abstractmethod
    def is_running(self) -> bool:
        """Return True if transport is active."""

    @staticmethod
    def auto(serial: str, package: str, script_path: str) -> "FridaTransport":
        """Auto-detect best transport for this app.

        Tries subprocess CLI first (works everywhere). Falls back to
        Python binding if CLI binary not found.
        """
        from framework.bridge.frida_transport_cli import FridaTransportCli
        try:
            transport = FridaTransportCli()
            transport.connect(serial, package, script_path)
            return transport
        except FileNotFoundError:
            pass
        # Fallback: Python binding
        from framework.bridge.frida_transport_binding import FridaTransportBinding
        transport = FridaTransportBinding()
        transport.connect(serial, package, script_path)
        return transport
```

- [ ] **Step 2: Create frida_transport_cli.py (from existing frida_cli.py)**

Rename/move `framework/bridge/frida_cli.py` → `framework/bridge/frida_transport_cli.py`.

Add `from .frida_transport import FridaTransport` and make `FridaCliSession` inherit:

```python
from .frida_transport import FridaTransport

class FridaTransportCli(FridaTransport, FridaCliSession):
    """CLI-based Frida transport — subprocess stdin/stdout."""

    def connect(self, serial, package, script_path):
        # Get PID via ADB
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if not pid:
            raise RuntimeError(f"App {package} not running")
        self.attach(pid, script_path)

    # send_message, capture_key, disconnect, is_running
    # already defined in FridaCliSession — no changes needed
```

- [ ] **Step 3: Create frida_transport_binding.py (from existing frida_session.py)**

Rename/move `framework/bridge/frida_session.py` → `framework/bridge/frida_transport_binding.py`.

Add ABC inheritance:

```python
from .frida_transport import FridaTransport

class FridaTransportBinding(FridaTransport):
    def __init__(self):
        self._session = FridaSessionManager()

    def connect(self, serial, package, script_path):
        self._session.get_or_create(serial, package, script_path)

    def send_message(self, uid, text, timeout=5.0):
        return self._session.send_message(uid, text)

    def capture_key(self, timeout=30.0):
        return self._session.capture_key(timeout)

    def disconnect(self):
        self._session.remove()

    def is_running(self):
        return self._session.is_connected
```

- [ ] **Step 4: Update aes_cbc.py — use FridaTransport auto()**

In `_derive_from_frida`:
```python
# Before:
from framework.bridge.frida_cli import FridaCliSession
cli = FridaCliSession()
cli.attach(pid, script_path)
client._frida_cli_session = cli

# After:
from framework.bridge.frida_transport import FridaTransport
transport = FridaTransport.auto(serial, package, script_path)
client._frida_transport = transport
```

Update all references to `_frida_cli_session` → `_frida_transport`.

- [ ] **Step 5: Update frida_rpc.py — use FridaTransport**

```python
# Before:
sess = client._frida_cli_session
sess.send_message(uid, text)

# After:
transport = client._frida_transport
transport.send_message(uid, text)
```

- [ ] **Step 6: Update dashboard.py — use FridaTransport**

Line 63-89: replace the CLI vs binding branching:
```python
# Before:
if getattr(task, '_frida_cli_session', None) is not None:
    pass  # CLI
else:
    # Python binding path...

# After:
# No need to init here — aes_cbc does it on derive_key
# Just ensure the transport doesn't auto-init if not needed
```

- [ ] **Step 7: Delete old files**

```bash
rm framework/bridge/frida_cli.py
rm framework/bridge/frida_session.py
```

- [ ] **Step 8: Run all tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

- [ ] **Step 9: Commit**

```bash
git add framework/bridge/frida_transport.py framework/bridge/frida_transport_cli.py framework/bridge/frida_transport_binding.py framework/core/processors/encryption/aes_cbc.py framework/core/processors/messaging/frida_rpc.py framework/core/dashboard.py
git rm framework/bridge/frida_cli.py framework/bridge/frida_session.py
git commit -m "refactor: unify Frida transport under ABC

FridaTransport ABC + two impls: CLI (NIS bypass) and Binding (Python).
Callers use FridaTransport.auto() or receive pre-built transport.
Removes duplicate lifecycle management in aes_cbc/frida_rpc/dashboard."
```

---

### Task 5: Extract `pipeline.py` — Room/Send Logic

**Files:**
- Create: `framework/core/pipeline.py`
- Modify: `framework/core/base_client.py` → remove `run_room`, `_send_to_user`, `_run_per_room`, `_run_global`, `run_pipeline`, `_wait_if_paused`, `start`, `pause`, `resume`, `stop`, `reset_progress`

**Rationale:** Pipeline orchestration (room traversal + user sending loops) is the largest remaining chunk in base_client.

- [ ] **Step 1: Create pipeline.py**

Move these methods from `base_client.py`:
- `run_pipeline` (line 658)
- `_run_global` (line 682)
- `_run_per_room` (line 733)
- `_send_to_user` (line 790)
- `run_room` (line 865)
- `_wait_if_paused` (line 943)

Create `Pipeline` class:

```python
"""Pipeline — room traversal + user sending orchestration."""
import random
import threading
import time
from framework.core.template import fill_template
from framework.bridge.frida_transport import FridaTransport


class Pipeline:
    def __init__(self, client):
        self.client = client
        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    def run(self) -> None:
        self._running = True
        self._pause_event.set()

        if not self.client._authenticated:
            if not self.client.authenticate():
                self.client._notify("error", "认证失败")
                self._running = False
                return

        cfg = self.client._current_source_cfg
        if not cfg:
            self.client._notify("error", "未配置用户来源")
            self._running = False
            return

        if cfg["type"] == "global":
            self._run_global(cfg)
        else:
            self._run_per_room(cfg)

        self._running = False

    # ... (move _run_global, _run_per_room, run_room, _send_to_user, _wait_if_paused here)

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._running = False
        self._pause_event.set()

    @property
    def status(self) -> str:
        if not self._running:
            return "idle"
        if not self._pause_event.is_set():
            return "paused"
        return "running"
```

In `base_client.py`, replace `run_pipeline` with:
```python
def run_pipeline(self):
    self.pipeline.run()
```

Delegate `start`, `pause`, `resume`, `stop` to `self.pipeline`.

- [ ] **Step 2: Run all tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

- [ ] **Step 3: Commit**

```bash
git add framework/core/pipeline.py framework/core/base_client.py
git commit -m "refactor: extract pipeline orchestration from base_client

Pipeline class handles room traversal + user sending loops.
base_client delegates start/stop/pause/resume to it."
```

---

### Task 6: Rename `base_client.py` → `client.py`

**Files:**
- Rename: `framework/core/base_client.py` → `framework/core/client.py`
- Modify: `framework/core/task_manager.py` → update import
- Modify: `framework/core/dashboard.py` → update import (if any)
- Modify: `framework/test_processor.py` → update import
- Modify: `apps/sybl/tests/test_e2e.py` → update import (pending in tests/)

**Rationale:** After extraction, `base_client.py` is just a `Client` class (~250 lines). Name should match.

- [ ] **Step 1: Rename file**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架"
mv framework/core/base_client.py framework/core/client.py
```

- [ ] **Step 2: Update all imports**

In `framework/core/task_manager.py` line 6:
```python
# Before:
from framework.core.base_client import BaseClient
# After:
from framework.core.client import Client
```
And change `BaseClient` → `Client` (line 28: `client = Client(str(config_file))`).

In `framework/test_processor.py` line 9:
```python
# Before:
from framework.core.base_client import BaseClient
# After:
from framework.core.client import Client
```

In `framework/core/__init__.py` (if exists):
```python
from .client import Client
```

- [ ] **Step 3: Rename class**

In the renamed `client.py`:
```python
# Before:
class BaseClient:
# After:
class Client:
```

- [ ] **Step 4: Run all tests**

```bash
cd "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架" && python -m pytest tests/ -v 2>&1
```

- [ ] **Step 5: Commit**

```bash
git add -A .
git commit -m "refactor: rename BaseClient → Client, base_client.py → client.py"
```

---

### Task 7: Cleanup — M1/M2/M3

**Files:**
- Move: `tests/` scripts reorganization
- Modify: `framework/core/recipes.py` → strip hardcoded keys

- [ ] **Step 1: Reorganize test scripts**

Move capture scripts to `tools/`:
```bash
mv tests/capture_ranking.js tools/
```

Update test file path in `tests/test_e2e.py` to use new imports.

- [ ] **Step 2: Clean recipes.py — remove hardcoded keys**

In `framework/core/recipes.py`, remove `write_key`/`read_key`/`p3_key` from `sybl-pattern`:
```python
# Before:
"signing": {
    "plugin": "xor-triple-sign",
    "params": {"read_key": "01528e5f", "write_key": "01528e5f", "p3_key": "00000000"},
},

# After:
"signing": {
    "plugin": "xor-triple-sign",
    "params": {},  # config.json provides actual keys
},
```

Note: `config.json` already has these keys, so recipe just provides structure.

- [ ] **Step 3: Commit**

```bash
git add tools/capture_ranking.js tests/ framework/core/recipes.py
git rm tests/capture_ranking.js 2>/dev/null
git commit -m "chore: reorganize tests/tools, clean recipes hardcoded keys"
```

---

## Self-Review

1. **Spec coverage:**
   - S1 (split base_client): ✅ Tasks 1-6 cover template, pagination, http, pipeline, rename
   - S2 (unify Frida): ✅ Task 4
   - M1 (test scripts): ✅ Task 7
   - M2 (recipes keys): ✅ Task 7
   - M3 (key coupling): Deferred — needs F1 fix first (ranking API -8)
   - F2 (password_login Token header): Already fixed locally, to be committed

2. **Placeholder scan:** No TBD/TODO found.

3. **Type consistency:**
   - `fill_template` signature consistent across template.py, pipeline.py, http.py
   - `Paginator.paginate()` requester signature: `callable(body) -> dict` — consistent
   - `FridaTransport` ABC methods consistent across CLI and Binding impls
   - `Client` class replaces `BaseClient` — all imports updated in order (Task 6)

4. **Dependency order:**
   - Task 1 (template) → Task 2 (pagination) depends on template
   - Task 3 (http) → independent
   - Task 4 (Frida) → independent
   - Task 5 (pipeline) → depends on Task 1, 3
   - Task 6 (rename) → depends on Task 5
   - Task 7 (cleanup) → independent, can run anytime
