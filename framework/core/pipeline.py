"""Pipeline — room traversal + user sending orchestration.

Moved from base_client.py to keep client assembly-only.
"""
import random
import threading
import time

from framework.bridge.frida_session import FridaDisconnectedError
from framework.core.template import fill_template


class Pipeline:
    """Orchestrates room scanning → user collection → message sending."""

    def __init__(self, client):
        self.client = client
        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    # ═══ Public control ═══

    def start(self) -> None:
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def stop(self) -> None:
        self._running = False
        self._pause_event.set()

    @property
    def status(self) -> str:
        if not self._running:
            return "idle"
        if not self._pause_event.is_set():
            return "paused"
        return "running"

    # ═══ Internal ═══

    def _run(self) -> None:
        c = self.client
        self._running = True
        self._pause_event.set()

        if not c._authenticated:
            if not c.authenticate():
                c._notify("error", "认证失败")
                self._running = False
                return

        cfg = c._current_source_cfg
        if not cfg:
            c._notify("error", "未配置用户来源 (user_sources 为空)")
            self._running = False
            return

        if cfg["type"] == "global":
            self._run_global(cfg)
        else:
            self._run_per_room(cfg)

        self._running = False

    def _run_global(self, cfg: dict) -> None:
        c = self.client
        c._notify("info", f"全站模式: {c._user_source}")
        try:
            users = c.fetch_users(c._user_source)
        except Exception as e:
            c._notify("error", f"拉取用户失败: {e}")
            return

        with c._lock:
            c._progress["total_users"] = len(users)
            c._progress["sent_total"] = 0
            c._progress["failed_total"] = 0

        c._notify("info", f"拉取完成: {len(users)} 人")

        gender_target = c._genders.get(c._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]
            with c._lock:
                c._progress["total_users"] = len(users)

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        with c._lock:
            c._ranking_users = [dict(u, status="wait") for u in users]

        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and c.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with c._lock:
                for ru in c._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room=None)

        if self._running:
            c._notify("done", "全站发送完成")

    def _run_per_room(self, cfg: dict) -> None:
        c = self.client
        c._rooms = c.state.load_rooms()
        if not c._rooms:
            c._notify("info", "扫描房间...")
            try:
                c._rooms = c.fetch_all_rooms()
            except Exception as e:
                c._notify("error", f"扫描房间失败: {e}")
                self._running = False
                return
            c.state.save_rooms(c._rooms)
            c._notify("info", f"扫描完成: {len(c._rooms)} 间房")

        with c._lock:
            c._progress = c.state.load_progress()
            start_idx = c._progress.get("current_room_index", 0)

        for idx in range(start_idx, len(c._rooms)):
            if not self._wait_if_paused():
                break
            room = c._rooms[idx]
            c._notify("progress", {"current_room_index": idx, "room": room})
            try:
                self.run_room(room, idx)
            except FridaDisconnectedError:
                rt = c._load_runtime()
                dev = rt.get("device", {})
                serial = dev.get("serial", "")
                package = dev.get("app_package",
                                  c.config.get("meta", {}).get("package", ""))
                if serial and package:
                    new_pid = c._ensure_app_running(serial, package)
                    if new_pid:
                        c._notify("info", f"App 已恢复 (PID={new_pid})，继续...")
                        try:
                            self.run_room(room, idx)
                            continue
                        except Exception:
                            pass
                c._notify("error", "Frida 会话已断开且无法恢复，任务暂停")
                self.pause()
                return
            except Exception as e:
                c._notify("error", f"房间 {room.get('name')} 失败: {e}")

        if self._running:
            with c._lock:
                c._progress["current_room_index"] = len(c._rooms)
                c._progress["current_room_name"] = ""
            c.state.save_progress(
                current_room_index=len(c._rooms),
                current_room_name="",
            )
            c._notify("done", "全部房间完成")

    def run_room(self, room: dict, idx: int) -> None:
        c = self.client
        room_name = room.get("name", "")
        with c._lock:
            c._progress["current_room_name"] = room_name

        join_ep = c.config.get("endpoints", {}).get("join_room")
        if join_ep:
            try:
                body = c._fill_template(join_ep.get("body", {}), room=room)
                resp = c._request(join_ep, body)
                if not c.check_response(resp):
                    c._notify("error", f"进房失败 {room.get('name')}: {resp.get('msg','')}")
            except Exception as e:
                c._notify("error", f"进房异常 {room.get('name')}: {e}")

        try:
            users = c.fetch_users(c._user_source, room)
        except Exception as e:
            with c._lock:
                c._ranking_users = []
            c._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        if not users:
            try:
                import json as _json
                body = c._fill_template(
                    dict(c.config["endpoints"]["ranking"].get("body", {})),
                    room=room,
                    period_key=c._periods.get(c._period, "day"),
                    data_source_key=c._data_sources.get(c._data_source, ""),
                )
                print(f"[diagnose] ranking body: {_json.dumps(body, ensure_ascii=False)}")
                resp = c._request(c.config["endpoints"]["ranking"], body)
                code = resp.get("code", "")
                count = len(c._extract_list(resp, c.config["endpoints"]["ranking"]))
                print(f"[diagnose] ranking resp: code={code} items={count} msg={resp.get('message','')}")
            except Exception:
                pass

        gender_target = c._genders.get(c._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        with c._lock:
            c._ranking_users = [dict(u, status="wait") for u in users]

        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and c.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with c._lock:
                for ru in c._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room)

        with c._lock:
            c._progress["current_room_index"] = idx + 1
            c.state.save_progress(
                current_room_index=idx + 1,
                current_room_name=room_name,
            )

    def _send_to_user(self, user: dict, room: dict = None) -> None:
        c = self.client
        uid = str(user.get("uid", ""))
        nick = user.get("nick", "")
        room_name = room.get("name", "") if room else ""

        if c.state.is_sent_today(uid):
            return

        template = random.choice(c._templates)
        text = template.replace("{nick}", nick).replace("{room_name}", room_name)

        with c._lock:
            c._current_user = {
                "uid": uid, "nick": nick, "text": text,
                "room": room_name,
                "time": time.strftime("%H:%M:%S"),
            }
            for ru in c._ranking_users:
                if str(ru.get("uid")) == uid:
                    ru["status"] = "sending"
                    break

        time.sleep(0.6)

        try:
            result = c.send_message(uid, text)
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
            c.state.mark_sent(uid, nick, room_name)
            with c._lock:
                sent = c._progress.get("sent_total", 0) + 1
                c._progress["sent_total"] = sent
                c.state.save_progress(sent_total=sent)
                c._current_user = {}
                c._recent_sent.insert(0, entry)
                if len(c._recent_sent) > 20:
                    c._recent_sent = c._recent_sent[:20]
                for ru in c._ranking_users:
                    if str(ru.get("uid")) == uid:
                        ru["status"] = "sent"
                        break
            c._notify("sent", {"uid": uid, "nick": nick, "text": text})
        else:
            with c._lock:
                failed = c._progress.get("failed_total", 0) + 1
                c._progress["failed_total"] = failed
                c.state.save_progress(failed_total=failed)
                c._current_user = {}
                c._recent_failed.insert(0, entry)
                if len(c._recent_failed) > 20:
                    c._recent_failed = c._recent_failed[:20]
                for ru in c._ranking_users:
                    if str(ru.get("uid")) == uid:
                        ru["status"] = "failed"
                        break
            c._notify("failed", {
                "uid": uid, "nick": nick,
                "error": result.get("error", "unknown"),
            })

        time.sleep(c._interval)

    def _wait_if_paused(self) -> bool:
        self._pause_event.wait()
        return self._running
