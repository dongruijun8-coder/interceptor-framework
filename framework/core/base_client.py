"""BaseClient — 配置驱动 Pipeline，加载处理器链执行"""
import json
import random
import re
import threading
import time
from pathlib import Path

import requests
import urllib3

from .state_manager import StateManager
from .processor_registry import ProcessorRegistry
from framework.bridge.frida_session import FridaDisconnectedError


class BaseClient:
    def __init__(self, config_path: str):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.config_path = Path(config_path)
        self._runtime_path = self.config_path.parent / "runtime.json"
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

        if "app_name" not in self.config.get("meta", {}):
            raise KeyError(f"[{config_path}] 缺少 meta.app_name")

        self.app_name = self.config["meta"]["app_name"]
        self.state = StateManager(str(self.config_path.parent))

        # Load processors from config
        pipeline = self.config.get("pipeline", {})
        self._encryptor = ProcessorRegistry.load(pipeline.get("encryption", "plaintext"), "encryption")
        self._signer = ProcessorRegistry.load(pipeline.get("signing", "plaintext"), "signing")
        self._auth_processor = ProcessorRegistry.load(pipeline.get("auth", "manual-token"), "auth")
        self._messenger = ProcessorRegistry.load(pipeline.get("messaging", "none"), "messaging")

        self._frida_session = None

        self.session = requests.Session()
        self.session.verify = False
        self._authenticated = False
        self._auth_token = self.config.get("auth_token", "")
        self._uid = str(self.config.get("uid", ""))
        self._session_id = self.config.get("client_session", "")

        # Runtime settings
        rt = self._load_runtime()
        settings = rt.get("settings", {})
        self._interval = settings.get("send_interval", 3)
        self._templates = rt.get("templates", ["{nick} 你好~"])
        self._data_sources = rt.get("data_sources", self.config.get("runtime_config", {}).get("data_sources", {}))
        self._periods = rt.get("periods", self.config.get("runtime_config", {}).get("periods", {}))
        self._genders = rt.get("genders", self.config.get("runtime_config", {}).get("genders", {}))

        keys = list(self._data_sources.keys())
        self._data_source = keys[0] if keys else ""
        keys = list(self._periods.keys())
        self._period = keys[0] if keys else ""
        keys = list(self._genders.keys())
        self._gender = keys[0] if keys else ""

        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._lock = threading.Lock()
        self._rooms = []
        self._progress = {}
        self._on_update = None
        self._current_user = {}          # 当前正在发的用户 {uid, nick, text}
        self._recent_sent: list[dict] = []  # 最近已发 (最多 20 条)
        self._recent_failed: list[dict] = []  # 最近失败
        self._ranking_users: list[dict] = []   # 当前房间排行用户（含发送状态）

        # Header defaults from config
        self._default_headers = self.config.get("server", {}).get("default_headers", {}).copy()
        self._base_url = self.config.get("server", {}).get("base_url", "")

    def _load_runtime(self) -> dict:
        if self._runtime_path.exists():
            return json.loads(self._runtime_path.read_text(encoding="utf-8"))
        return {}

    def _save_runtime(self, data: dict):
        current = self._load_runtime()
        current.update(data)
        self._runtime_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    # ═══ Auth ═══

    def authenticate(self) -> bool:
        if hasattr(self._encryptor, 'derive_key'):
            self._encryptor.derive_key(self)
        return self._auth_processor.authenticate(self)

    # ═══ 3 core methods (config-driven) ═══

    def fetch_all_rooms(self) -> list:
        ep = self.config["endpoints"]["all_rooms"]
        if "steps" in ep:
            return self._execute_steps(ep)
        else:
            rooms = self._fetch_paginated(ep)
            mapping = ep.get("output_mapping", {})
            if mapping:
                rooms = [self._map_fields(r, mapping) for r in rooms]
            return rooms

    def _execute_steps(self, ep: dict) -> list:
        all_rooms = []
        step_results = {}

        for step in ep["steps"]:
            name = step["name"]
            pagination = step.get("pagination")
            iter_source = step.get("iter_source", "")

            if iter_source:
                src_name, src_path = iter_source.split(".", 1)
                src_data = step_results.get(src_name, {})
                items = self._resolve_path(src_data, src_path) or []
                for item in items:
                    body = self._fill_template(step.get("body", {}), _iter=item)
                    results = self._fetch_paginated(step, body)
                    all_rooms.extend(results)
            else:
                body = self._fill_template(step.get("body", {}))
                if pagination:
                    results = self._fetch_paginated(step, body)
                    step_results[name] = {"list": results, "raw": results}
                    all_rooms = results
                else:
                    base_url = self._base_url
                    path = step["path"]
                    resp = self._request(step, body)
                    if self.check_response(resp):
                        items = self._extract_list(resp, step)
                        step_results[name] = {"list": items, "raw": resp.get("data", {})}

        mapping = ep.get("output_mapping", {})
        return [self._map_fields(r, mapping) for r in all_rooms]

    def fetch_room_ranking(self, room: dict, period: str) -> list:
        ep = dict(self.config["endpoints"]["ranking"])
        period_key = self._periods.get(period, "day")
        ds_key = self._data_sources.get(self._data_source, "")

        # Support per-period list selection via output_mapping.lists config
        op = ep.get("output_mapping", {})
        lists_cfg = op.get("lists", {})
        if lists_cfg:
            # Find the list key matching this period (e.g. rich_day, rich_week)
            matched = None
            for list_key, list_cfg in lists_cfg.items():
                if list_key.endswith(f"_{period_key}"):
                    matched = list_cfg
                    break
            if matched:
                ep["response_path"] = matched["response_list"]
                if "amount_field" in matched:
                    op = dict(op)
                    op["amount"] = matched["amount_field"]

        body = self._fill_template(ep.get("body", {}),
                                   room=room, period_key=period_key, data_source_key=ds_key)
        items = self._fetch_paginated(ep, body)
        op.pop("lists", None)
        op.pop("note", None)
        return [self._map_fields(u, op) for u in items]

    def send_message(self, uid: str, text: str) -> dict:
        return self._messenger.send(self, uid, text)

    # ═══ HTTP with processor pipeline ═══

    def _post(self, url: str, body: dict) -> dict:
        try:
            encrypted = self._encryptor.encode(body)
        except Exception as e:
            raise RuntimeError(f"encryption.encode failed: {e}")

        headers = dict(self._default_headers)
        headers["Content-Type"] = "text/plain; charset=UTF-8"
        headers["__auth_token__"] = self._auth_token

        headers, extra_params = self._signer.sign(url, headers, body)

        # Add signing params to URL query string
        if extra_params:
            import urllib.parse
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(extra_params)

        r = self.session.post(url, data=encrypted, headers=headers, timeout=30)
        r.raise_for_status()

        try:
            return self._encryptor.decode(r.content)
        except Exception:
            try:
                return json.loads(r.text)
            except json.JSONDecodeError:
                raise RuntimeError(f"decryption failed: {r.text[:200]}")

    def _get(self, url: str, params: dict = None) -> dict:
        params = dict(params or {})
        headers = dict(self._default_headers)
        headers["__auth_token__"] = self._auth_token

        headers, extra_params = self._signer.sign(url, headers, params)
        params.update(extra_params)

        r = self.session.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        try:
            return self._encryptor.decode(r.content)
        except Exception:
            return json.loads(r.text)

    # ═══ Pagination ═══

    def _request(self, ep: dict, body: dict) -> dict:
        """Dispatch to _get or _post based on endpoint method field (default POST)."""
        method = ep.get("method", "POST").upper()
        path = ep["path"]
        url = f"{self._base_url}{path}"
        if method == "GET":
            return self._get(url, body)
        else:
            return self._post(url, body)

    def _extract_list(self, resp: dict, ep: dict) -> list:
        """Extract list from response using optional response_path config."""
        path = ep.get("response_path", "data.list")
        items = self._resolve_path(resp, path)
        if isinstance(items, list):
            return items
        data = resp.get("data")
        if isinstance(data, list):
            return data
        return []

    def _fetch_paginated(self, ep: dict, base_body: dict = None) -> list:
        path = ep["path"]
        pagination = ep.get("pagination")
        base_url = self._base_url

        if base_body is None:
            base_body = dict(ep.get("body", {}))

        ptype = pagination.get("type") if pagination else None

        # No pagination — single request
        if not ptype:
            resp = self._request(ep, base_body)
            if self.check_response(resp):
                return self._extract_list(resp, ep)
            return []

        size = pagination.get("size", 20)
        stop_on = pagination.get("stop_on", "empty_list")
        results = []

        if ptype == "offset_limit":
            for offset in range(0, 500, size):
                body = dict(base_body)
                body["offset"] = offset
                body["limit"] = size
                resp = self._request(ep, body)
                if not self.check_response(resp):
                    break
                items = self._extract_list(resp, ep)
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
                resp = self._request(ep, body)
                if not self.check_response(resp):
                    break
                items = self._extract_list(resp, ep)
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        else:
            # Unknown pagination type — single request
            resp = self._request(ep, base_body)
            if self.check_response(resp):
                results = self._extract_list(resp, ep)
            return results

    # ═══ Template ═══

    def _fill_template(self, template, **kwargs) -> dict:
        result = {}
        for key, value in template.items():
            if isinstance(value, str) and "{{" in value:
                def replacer(m):
                    var_path = m.group(1)
                    parts = var_path.split(".", 1)
                    if parts[0] in kwargs:
                        obj = kwargs[parts[0]]
                        if len(parts) > 1 and isinstance(obj, dict):
                            return str(obj.get(parts[1], ""))
                        return str(obj)
                    return m.group(0)
                result[key] = re.sub(r'\{\{(.+?)\}\}', replacer, value)
            elif isinstance(value, dict):
                result[key] = self._fill_template(value, **kwargs)
            else:
                result[key] = value
        return result

    @staticmethod
    def _resolve_path(data: dict, path: str):
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

    def _map_fields(self, raw: dict, mapping: dict) -> dict:
        result = {}
        for framework_field, source in mapping.items():
            if isinstance(source, str) and "{{" in source:
                val = self._fill_template({"k": source}, **{})["k"]
                result[framework_field] = val
            elif isinstance(source, str) and "." in source:
                result[framework_field] = self._resolve_path(raw, source)
            else:
                if isinstance(source, str):
                    result[framework_field] = raw.get(source, source)
                else:
                    result[framework_field] = source
        return result

    # ═══ Pipeline (unchanged from original) ═══

    def run_pipeline(self) -> None:
        self._running = True
        self._pause_event.set()

        if not self._authenticated:
            if not self.authenticate():
                self._notify("error", "认证失败")
                self._running = False
                return
            self._authenticated = True

        self._rooms = self.state.load_rooms()
        if not self._rooms:
            self._notify("info", "扫描房间...")
            try:
                self._rooms = self.fetch_all_rooms()
            except Exception as e:
                self._notify("error", f"扫描房间失败: {e}")
                self._running = False
                return
            self.state.save_rooms(self._rooms)
            self._notify("info", f"扫描完成: {len(self._rooms)} 间房")

        with self._lock:
            self._progress = self.state.load_progress()
            start_idx = self._progress.get("current_room_index", 0)

        for idx in range(start_idx, len(self._rooms)):
            if not self._wait_if_paused():
                break
            room = self._rooms[idx]
            self._notify("progress", {"current_room_index": idx, "room": room})
            try:
                self.run_room(room, idx)
            except FridaDisconnectedError:
                self._notify("error", "Frida 会话已断开，请重新连接设备后继续")
                self.pause()
                return
            except Exception as e:
                self._notify("error", f"房间 {room.get('name')} 失败: {e}")

        if self._running:
            self._notify("done", "全部房间完成")
            self.state.reset_progress()
        self._running = False

    def run_room(self, room: dict, idx: int) -> None:
        room_name = room.get("name", "")
        with self._lock:
            self._progress["current_room_index"] = idx
            self._progress["current_room_name"] = room_name
            self.state.save_progress(
                current_room_index=idx,
                current_room_name=room_name,
            )

        try:
            users = self.fetch_room_ranking(room, self._period)
        except Exception as e:
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        gender_target = self._genders.get(self._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        # Store ranking users with initial status for frontend display
        with self._lock:
            self._ranking_users = [dict(u, status="wait") for u in users]

        for user in users:
            if not self._wait_if_paused():
                break

            uid = user.get("uid", "")
            nick = user.get("nick", "")

            if self.state.is_sent_today(uid):
                continue

            template = random.choice(self._templates)
            text = template.replace("{nick}", nick).replace("{room_name}", room.get("name", ""))

            # 记录当前正在发的用户 + 标记排行榜状态
            with self._lock:
                self._current_user = {"uid": uid, "nick": nick, "text": text,
                                       "room": room.get("name", ""),
                                       "time": time.strftime("%H:%M:%S")}
                for ru in self._ranking_users:
                    if str(ru.get("uid")) == str(uid):
                        ru["status"] = "sending"
                        break

            try:
                result = self.send_message(uid, text)
            except Exception as e:
                result = {"success": False, "error": str(e)}

            entry = {"uid": uid, "nick": nick,
                     "room": room.get("name", ""),
                     "time": time.strftime("%H:%M:%S"),
                     "success": result.get("success", False),
                     "error": result.get("error", "")}

            if result.get("success"):
                entry["text"] = text
                self.state.mark_sent(uid, nick, room.get("name", ""))
                with self._lock:
                    sent = self._progress.get("sent_total", 0) + 1
                    self._progress["sent_total"] = sent
                    self.state.save_progress(sent_total=sent)
                    self._current_user = {}
                    self._recent_sent.insert(0, entry)
                    if len(self._recent_sent) > 20:
                        self._recent_sent = self._recent_sent[:20]
                    # Update ranking user status
                    for ru in self._ranking_users:
                        if str(ru.get("uid")) == str(uid):
                            ru["status"] = "sent"
                            break
                self._notify("sent", {"uid": uid, "nick": nick, "text": text})
            else:
                with self._lock:
                    failed = self._progress.get("failed_total", 0) + 1
                    self._progress["failed_total"] = failed
                    self.state.save_progress(failed_total=failed)
                    self._current_user = {}
                    self._recent_failed.insert(0, entry)
                    if len(self._recent_failed) > 20:
                        self._recent_failed = self._recent_failed[:20]
                    # Update ranking user status
                    for ru in self._ranking_users:
                        if str(ru.get("uid")) == str(uid):
                            ru["status"] = "failed"
                            break
                self._notify("failed", {
                    "uid": uid, "nick": nick,
                    "error": result.get("error", "unknown"),
                })

            time.sleep(self._interval)

    # ═══ Control ═══

    def _wait_if_paused(self) -> bool:
        self._pause_event.wait()
        return self._running

    def refresh_rooms(self) -> list:
        if not self._authenticated:
            if not self.authenticate():
                raise RuntimeError("认证失败")
            self._authenticated = True
        self._rooms = self.fetch_all_rooms()
        self.state.save_rooms(self._rooms)
        return self._rooms

    def start(self) -> None:
        t = threading.Thread(target=self.run_pipeline, daemon=True)
        t.start()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        self._running = False
        self._pause_event.set()

    def reset_progress(self) -> None:
        with self._lock:
            self._progress = {}
            self.state.reset_progress()

    def set_frida_session(self, session) -> None:
        """Set the Frida session used by frida-rpc messaging processor"""
        self._frida_session = session

    def clear_frida_session(self) -> None:
        """Clear the Frida session (called on stop)"""
        if self._frida_session:
            try:
                self._frida_session.disconnect()
            except Exception:
                pass
            self._frida_session = None

    @property
    def status(self) -> str:
        if not self._running:
            return "idle"
        if not self._pause_event.is_set():
            return "paused"
        return "running"

    def get_stats(self) -> dict:
        with self._lock:
            progress = dict(self._progress)
            rooms = list(self._rooms)
            current_user = dict(self._current_user)
            recent_sent = list(self._recent_sent)
            recent_failed = list(self._recent_failed)
        total_rooms = len(rooms)
        current_idx = progress.get("current_room_index", 0)
        return {
            "app_name": self.app_name,
            "status": self.status,
            "total_rooms": total_rooms,
            "done_rooms": current_idx,
            "sent": progress.get("sent_total", 0),
            "failed": progress.get("failed_total", 0),
            "current_room": progress.get("current_room_name", ""),
            "current_user": current_user,
            "recent_sent": recent_sent,
            "recent_failed": recent_failed,
            "mode": self.config.get("send_mode", "rest"),
            "interval": self._interval,
            "data_source": self._data_source,
            "period": self._period,
            "gender": self._gender,
            "messaging_type": self._messenger.name,
            "available_data_sources": self._data_sources,
            "available_periods": self._periods,
            "available_genders": self._genders,
            "templates": list(self._templates),
            "ranking_users": [dict(ru) for ru in self._ranking_users],
        }

    def _notify(self, event: str, payload) -> None:
        if self._on_update:
            self._on_update(event, payload)

    def check_response(self, resp_data: dict) -> bool:
        code = resp_data.get("code")
        if code in (200, "S_OK", 0):
            return True
        status = resp_data.get("status")
        if status is not None and status == 0:
            return True
        return False

    def build_headers(self) -> dict:
        return self._default_headers
