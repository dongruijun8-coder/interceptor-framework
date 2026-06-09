"""BaseClient — 配置驱动 Pipeline，加载处理器链执行"""
import json
import random
import threading
import time
from pathlib import Path

import requests
import urllib3

from .template import fill_template, resolve_path, map_fields
from .pagination import Paginator
from .template import fill_template, resolve_path, map_fields


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

        # Load processors from config (支持 recipe 展开)
        from .recipes import expand_recipe
        pipeline = expand_recipe(self.config.get("pipeline", {}))
        self._encryptor = ProcessorRegistry.load(pipeline.get("encryption", "plaintext"), "encryption")
        self._signer = ProcessorRegistry.load(pipeline.get("signing", "plaintext"), "signing")
        self._auth_processor = ProcessorRegistry.load(pipeline.get("auth", "manual-token"), "auth")
        self._messenger = ProcessorRegistry.load(pipeline.get("messaging", "none"), "messaging")

        self._diagnose = DiagnoseLogger(
            self.app_name,
            enabled=self.config.get("diagnose", True),
        )

        self._frida_session = None

        self.session = requests.Session()
        self.session.verify = False

        # HttpClient — extracted transport layer
        from .http import HttpClient
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

        self._authenticated = False
        self._frida_authenticated = False
        self._auth_token = self.config.get("auth_token", "")
        self._uid = str(self.config.get("uid", ""))
        self._session_id = self.config.get("client_session", "")

        # Runtime settings
        rt = self._load_runtime()
        settings = rt.get("settings", {})
        self._interval = settings.get("send_interval", 3)
        self._templates = rt.get("templates", self.config.get("runtime_config", {}).get("templates", ["{nick} 你好~"]))
        self._data_sources = rt.get("data_sources", self.config.get("runtime_config", {}).get("data_sources", {}))
        self._periods = rt.get("periods", self.config.get("runtime_config", {}).get("periods", {}))
        self._genders = rt.get("genders", self.config.get("runtime_config", {}).get("genders", {}))

        # User source — persisted selection takes priority, then config default
        self._user_sources = self.config.get("user_sources", {})
        src_keys = list(self._user_sources.keys())
        self._user_source = rt.get("user_source", src_keys[0] if src_keys else "")
        self._current_source_cfg = self._user_sources.get(self._user_source, {})

        keys = list(self._data_sources.keys())
        self._data_source = rt.get("data_source", keys[0] if keys else "")
        keys = list(self._periods.keys())
        self._period = rt.get("period", keys[0] if keys else "")
        keys = list(self._genders.keys())
        self._gender = rt.get("gender", keys[0] if keys else "")

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
        # WebSocket transport — use Frida RPC bridge
        if ep.get("transport") == "ws":
            return self._fetch_rooms_ws(ep)
        if "steps" in ep:
            return self._execute_steps(ep)
        else:
            rooms = self._fetch_paginated(ep)
            mapping = ep.get("output_mapping", {})
            if mapping:
                rooms = [self._map_fields(r, mapping) for r in rooms]
            return rooms

    def _fetch_rooms_ws(self, ep: dict) -> list:
        """通过 Frida RPC 从 WebSocket 连接获取房间列表。

        流程：
        1. 注入 WS hook 脚本（hook_ws_rooms.js）到已连接的 Frida session
        2. 脚本 hook OkHttp WebSocket，拦截/缓存房间数据
        3. 通过 RPC getRooms() 获取缓存结果
        4. 应用 output_mapping 返回统一格式
        """
        ws_cfg = ep.get("ws", {})
        method = ws_cfg.get("method", "getRooms")
        script_name = ws_cfg.get("script", "hook_ws_rooms.js")

        # Auto-connect Frida if not already connected
        if not self._frida_session or not self._frida_session.is_connected:
            rt = self._load_runtime()
            device = rt.get("device", {})
            serial = device.get("serial", "")
            package = device.get("app_package", self.config.get("meta", {}).get("package", ""))
            if not serial or not package:
                raise RuntimeError(
                    "WS 房间列表需要 Frida 连接，但未配置设备。请在 Dashboard 设置设备串号")
            # Auto-connect: use IM script as main, WS hook loaded as second
            main_script = str(self.config_path.parent / device.get("script_name", "hook_send_msg.js"))
            if not Path(main_script).exists():
                main_script = str(self.config_path.parent / "hook_send_msg.js")
            from framework.bridge.frida_session import FridaSessionManager
            try:
                self._frida_session = FridaSessionManager().get_or_create(
                    self.app_name, serial, package, main_script)
            except Exception as e:
                raise RuntimeError(f"Frida 连接失败: {e}")

        # Load the WS hook script into the same Frida session
        script_path = self.config_path.parent / script_name
        if not script_path.exists():
            alt = Path("apps") / self.app_name / script_name
            if alt.exists():
                script_path = alt
        if not script_path.exists():
            raise RuntimeError(f"WS 房间脚本不存在: {script_name}")

        try:
            self._frida_session.load_script(str(script_path))
        except Exception as e:
            raise RuntimeError(f"WS 脚本注入失败: {e}")

        # 通知前端等待
        self._notify("info", "WebSocket 房间扫描中... 请在 App 中进入房间列表页面")

        # 轮询获取房间数据（需要用户在 App 中浏览房间以触发 WS 推送）
        deadline = time.time() + 30  # 最多等 30 秒
        while time.time() < deadline:
            # 检查暂停/停止
            if not self._running:
                return []
            try:
                rpc = self._frida_session._rpc_second or self._frida_session._rpc
                raw = getattr(rpc, method)()
                if isinstance(raw, str):
                    import json as _json
                    raw = _json.loads(raw)
                if isinstance(raw, list) and len(raw) > 0:
                    mapping = ep.get("output_mapping", {})
                    if mapping:
                        return [self._map_fields(r, mapping) for r in raw]
                    return raw
            except Exception:
                pass
            time.sleep(1.0)

        # Timeout — 返回已缓存的数据
        try:
            rpc = self._frida_session._rpc_second or self._frida_session._rpc
            raw = getattr(rpc, method)()
            if isinstance(raw, str):
                import json as _json
                raw = _json.loads(raw)
            if isinstance(raw, list) and len(raw) > 0:
                mapping = ep.get("output_mapping", {})
                if mapping:
                    return [self._map_fields(r, mapping) for r in raw]
                return raw
        except Exception:
            pass

        raise RuntimeError(
            f"WS 房间扫描超时（30s）。请确认："
            f"1) App 已打开并进入房间列表页面 "
            f"2) WS hook 脚本已正确配置"
        )

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
        return self.http.post(url, body)

    def _get(self, url: str, params: dict = None) -> dict:
        return self.http.get(url, params)

    # ═══ Pagination ═══

    def _request(self, ep: dict, body: dict) -> dict:
        """Dispatch to GET or POST based on endpoint method field (default POST)."""
        return self.http.request(ep, body)

    def _extract_list(self, resp: dict, ep: dict) -> list:
        return Paginator.extract_list(resp, ep)

    def _fetch_paginated(self, ep: dict, base_body: dict = None) -> list:
        if base_body is None:
            base_body = self._fill_template(dict(ep.get("body", {})))

        def requester(body):
            return self._request(ep, body)

        def extractor(resp):
            return Paginator.extract_list(resp, ep)

        return Paginator.paginate(ep, base_body, requester, extractor)

    # ═══ Template ═══

    def _identity_vars(self) -> dict:
        """运行时身份变量，所有请求 body 自动注入"""
        import time as _time
        rt = self._load_runtime()
        device = rt.get("device", {})
        profile = rt.get("profile", {})
        did = device.get("device_id",
                self.config.get("server", {}).get("default_headers", {}).get("device-id", ""))
        # uid as int (for APIs that need number), also available as uid_str
        try:
            uid_int = int(self._uid) if self._uid else 0
        except (ValueError, TypeError):
            uid_int = 0
        return {
            "uid": uid_int,
            "uid_str": str(self._uid) if self._uid else "",
            "token": self._auth_token,
            "device_id": did,
            "shumei_device_id": device.get("shumei_device_id", did),
            "h5_ts": str(int(_time.time() * 1000)),
            "timestamp_ms": str(int(_time.time() * 1000)),
            "age": str(profile.get("age", "25")),
            "gender": str(profile.get("gender", "1")),
        }

    def _fill_template(self, template, **kwargs):
        return fill_template(template, self._identity_vars(), **kwargs)

    @staticmethod
    def _ensure_app_running(serial: str, package: str) -> int | None:
        """Ensure the target app is running. Restart via monkey if dead. Returns PID."""
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if pid:
            return pid

        import subprocess as _subprocess
        print(f"[base_client] App {package} 未运行，尝试启动...")
        _subprocess.run(
            ["adb", "-s", serial, "shell", "monkey", "-p", package, "1"],
            timeout=15, capture_output=True,
        )
        time.sleep(5)
        return AdbDevice.get_pid(serial, package)

    @staticmethod
    def _resolve_path(data, path):
        return resolve_path(data, path)

    def _map_fields(self, raw, mapping):
        return map_fields(raw, mapping, self._identity_vars())

    # ═══ Pipeline (unchanged from original) ═══

    def fetch_users(self, source_name: str, room: dict = None) -> list:
        """根据用户来源拉取用户列表。

        模板变量从 self._data_source / self._period 解析，
        与 fetch_room_ranking 保持一致。
        """
        cfg = self._user_sources.get(source_name)
        if not cfg:
            return []

        ep_name = cfg.get("endpoint")
        if not ep_name:
            return []

        ep = dict(self.config["endpoints"][ep_name])

        # Resolve template variables
        ds_key = self._data_sources.get(self._data_source, "")
        period_key = self._periods.get(self._period, "day")

        if cfg["type"] == "global":
            body = self._fill_template(
                ep.get("body", {}),
                data_source_key=ds_key,
                period_key=period_key,
            )
            items = self._fetch_paginated(ep, body)
            mapping = ep.get("output_mapping", {})
            if mapping:
                items = [self._map_fields(u, mapping) for u in items]
            return items

        elif cfg["type"] == "per_room":
            if room is None:
                return []
            # Reuse existing per-room ranking logic
            return self.fetch_room_ranking(room, self._period)

    def run_pipeline(self) -> None:
        self._running = True
        self._pause_event.set()

        if not self._authenticated:
            if not self.authenticate():
                self._notify("error", "认证失败")
                self._running = False
                return
            self._authenticated = True

        cfg = self._current_source_cfg
        if not cfg:
            self._notify("error", "未配置用户来源 (user_sources 为空)")
            self._running = False
            return

        if cfg["type"] == "global":
            self._run_global(cfg)
        else:
            self._run_per_room(cfg)

        self._running = False

    def _run_global(self, cfg: dict) -> None:
        """全站榜单模式：不扫描房间，直接拉取用户列表发送。"""
        self._notify("info", f"全站模式: {self._user_source}")
        try:
            users = self.fetch_users(self._user_source)
        except Exception as e:
            self._notify("error", f"拉取用户失败: {e}")
            return

        with self._lock:
            self._progress["total_users"] = len(users)
            self._progress["sent_total"] = 0
            self._progress["failed_total"] = 0

        self._notify("info", f"拉取完成: {len(users)} 人")

        # Gender filter
        gender_target = self._genders.get(self._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]
            with self._lock:
                self._progress["total_users"] = len(users)

        # Sort by amount descending
        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        # Store for frontend display
        with self._lock:
            self._ranking_users = [dict(u, status="wait") for u in users]

        # Batch-mark already-sent
        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and self.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with self._lock:
                for ru in self._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        # Send loop (no room context)
        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room=None)

        if self._running:
            self._notify("done", "全站发送完成")

    def _run_per_room(self, cfg: dict) -> None:
        """房间遍历模式：现有逻辑不变。"""
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
                # Try recovery: restart app + reconnect once
                rt = self._load_runtime()
                dev = rt.get("device", {})
                serial = dev.get("serial", "")
                package = dev.get("app_package",
                                  self.config.get("meta", {}).get("package", ""))
                if serial and package:
                    new_pid = self._ensure_app_running(serial, package)
                    if new_pid:
                        self._notify("info", f"App 已恢复 (PID={new_pid})，继续...")
                        try:
                            self.run_room(room, idx)
                            continue
                        except Exception:
                            pass
                self._notify("error", "Frida 会话已断开且无法恢复，任务暂停")
                self.pause()
                return
            except Exception as e:
                self._notify("error", f"房间 {room.get('name')} 失败: {e}")

        if self._running:
            with self._lock:
                self._progress["current_room_index"] = len(self._rooms)
                self._progress["current_room_name"] = ""
            self.state.save_progress(
                current_room_index=len(self._rooms),
                current_room_name="",
            )
            self._notify("done", "全部房间完成")

    def _send_to_user(self, user: dict, room: dict = None) -> None:
        """发送消息给单个用户。global 和 per_room 共用。"""
        uid = str(user.get("uid", ""))
        nick = user.get("nick", "")
        room_name = room.get("name", "") if room else ""

        if self.state.is_sent_today(uid):
            return

        template = random.choice(self._templates)
        text = template.replace("{nick}", nick).replace("{room_name}", room_name)

        with self._lock:
            self._current_user = {
                "uid": uid, "nick": nick, "text": text,
                "room": room_name,
                "time": time.strftime("%H:%M:%S"),
            }
            for ru in self._ranking_users:
                if str(ru.get("uid")) == uid:
                    ru["status"] = "sending"
                    break

        # 短暂延迟让前端轮询捕获"sending"状态
        time.sleep(0.6)

        try:
            result = self.send_message(uid, text)
        except Exception as e:
            result = {"success": False, "error": str(e)}

        entry = {
            "uid": uid, "nick": nick,
            "room": room_name,
            "time": time.strftime("%H:%M:%S"),
            "success": result.get("success", False),
            "error": result.get("error", ""),
        }

        if result.get("success"):
            entry["text"] = text
            self.state.mark_sent(uid, nick, room_name)
            with self._lock:
                sent = self._progress.get("sent_total", 0) + 1
                self._progress["sent_total"] = sent
                self.state.save_progress(sent_total=sent)
                self._current_user = {}
                self._recent_sent.insert(0, entry)
                if len(self._recent_sent) > 20:
                    self._recent_sent = self._recent_sent[:20]
                for ru in self._ranking_users:
                    if str(ru.get("uid")) == uid:
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
                for ru in self._ranking_users:
                    if str(ru.get("uid")) == uid:
                        ru["status"] = "failed"
                        break
            self._notify("failed", {
                "uid": uid, "nick": nick,
                "error": result.get("error", "unknown"),
            })

        time.sleep(self._interval)

    def run_room(self, room: dict, idx: int) -> None:
        room_name = room.get("name", "")
        with self._lock:
            self._progress["current_room_name"] = room_name

        # Join room if endpoint configured (required for some apps like sybl)
        join_ep = self.config.get("endpoints", {}).get("join_room")
        if join_ep:
            try:
                body = self._fill_template(join_ep.get("body", {}), room=room)
                resp = self._request(join_ep, body)
                if not self.check_response(resp):
                    self._notify("error", f"进房失败 {room.get('name')}: {resp.get('msg','')}")
            except Exception as e:
                self._notify("error", f"进房异常 {room.get('name')}: {e}")

        try:
            users = self.fetch_users(self._user_source, room)
        except Exception as e:
            with self._lock:
                self._ranking_users = []
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        if not users:
            # Diagnostic: try one manual ranking request to see response
            try:
                body = self._fill_template(
                    dict(self.config["endpoints"]["ranking"].get("body", {})),
                    room=room,
                    period_key=self._periods.get(self._period, "day"),
                    data_source_key=self._data_sources.get(self._data_source, ""),
                )
                print(f"[diagnose] ranking body: {json.dumps(body, ensure_ascii=False)}")
                resp = self._request(self.config["endpoints"]["ranking"], body)
                code = resp.get("code", "")
                count = len(self._extract_list(resp, self.config["endpoints"]["ranking"]))
                print(f"[diagnose] ranking resp: code={code} items={count} msg={resp.get('message','')}")
            except Exception:
                pass

        gender_target = self._genders.get(self._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        # Store ranking users with initial status for frontend display
        with self._lock:
            self._ranking_users = [dict(u, status="wait") for u in users]

        # Batch-mark already-sent users so UI shows "sent" not "wait"
        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and self.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with self._lock:
                for ru in self._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room)

        # Room fully processed — save progress
        with self._lock:
            self._progress["current_room_index"] = idx + 1
            self.state.save_progress(
                current_room_index=idx + 1,
                current_room_name=room_name,
            )

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
            self._rooms = []
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
            ranking_users = [dict(ru) for ru in self._ranking_users]
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
            "user_source": self._user_source,
            "available_user_sources": self._user_sources,
            "current_source_cfg": self._current_source_cfg,
            "total_users": self._progress.get("total_users", 0),
            "messaging_type": self._messenger.name,
            "available_data_sources": self._data_sources,
            "available_periods": self._periods,
            "available_genders": self._genders,
            "templates": list(self._templates),
            "ranking_users": ranking_users,
            "sent_today_total": len(sent_today_data := self.state.load_sent_today().get("sent", [])),
            "rooms_today_total": len(set(s.get("room", "") for s in sent_today_data if s.get("room"))),
            "sent_today_data": sent_today_data,  # avoid double read
            "credentials": (rt := self._load_runtime()).get("credentials", {}),
            "profile": rt.get("profile", {}),
            "device": rt.get("device", {}),
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
        # WeFun-style: ret=1
        ret = resp_data.get("ret")
        if ret is not None and int(ret) == 1:
            return True
        return False

    def build_headers(self) -> dict:
        return self._default_headers
