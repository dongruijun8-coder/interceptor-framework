"""BaseClient — Pipeline 核心 + HTTP 封装。App 继承后实现 3 个方法。"""
import json
import random
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests
import urllib3

from .state_manager import StateManager


class BaseClient(ABC):
    def __init__(self, config_path: str):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.app_name = self.config["app_name"]
        self.state = StateManager(str(self.config_path.parent))

        self.session = requests.Session()
        self.session.verify = False
        self._authenticated = False
        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._lock = threading.Lock()
        self._interval = self.config.get("send_interval", 3)
        self._templates = self.config.get("templates", ["{nick} 你好~"])
        self._period = self.config.get("period", "今日")
        self._gender = self.config.get("gender", "全部")
        self._data_source = self.config.get("data_source", "贡献榜")
        self._rooms = []
        self._progress = {}
        self._on_update = None

    # ═══ App 必须实现（3 个方法）═══

    @abstractmethod
    def fetch_all_rooms(self) -> list:
        """返回 [{id, name, type, ...meta}]"""
        ...

    @abstractmethod
    def fetch_room_ranking(self, room: dict, period: str) -> list:
        """返回 [{uid, nick, amount, gender}]"""
        ...

    @abstractmethod
    def send_message(self, uid: str, text: str) -> dict:
        """返回 {success: bool, error: str}"""
        ...

    # ═══ 可选重写 ═══

    def authenticate(self) -> bool:
        mode = self.config.get("auth_mode", "manual")
        if mode == "manual":
            return bool(self.config.get("token") and self.config.get("uid"))
        return False

    def build_headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }

    def check_response(self, resp_data: dict) -> bool:
        return resp_data.get("code") == "S_OK"

    def parse_user(self, raw: dict) -> dict:
        return {
            "uid": str(raw.get("uid", "")),
            "nick": raw.get("nick", raw.get("nickName", "")),
            "amount": raw.get("amount", raw.get("totalAmount", 0)),
            "gender": raw.get("gender", 0),
        }

    # ═══ HTTP 工具 ═══

    def _post(self, url: str, body: dict) -> dict:
        r = self.session.post(url, json=body, headers=self.build_headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _get(self, url: str, params: dict = None) -> dict:
        r = self.session.get(url, params=params, headers=self.build_headers(), timeout=30)
        r.raise_for_status()
        return r.json()

    # ═══ Pipeline ═══

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
            if not self._running:
                break
            self._pause_event.wait()
            if not self._running:
                break
            room = self._rooms[idx]
            self._notify("progress", {"current_room_index": idx, "room": room})
            self.run_room(room, idx)

        if self._running:
            self._notify("done", "全部房间完成")
            self.state.reset_progress()
        self._running = False

    def run_room(self, room: dict, idx: int) -> None:
        with self._lock:
            self.state.save_progress(
                current_room_index=idx,
                current_room_name=room.get("name", ""),
            )

        try:
            users = self.fetch_room_ranking(room, self._period)
        except Exception as e:
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        if self._gender != "全部":
            gender_map = {"男": 1, "女": 2, "男神": 1, "女神": 2}
            target = gender_map.get(self._gender)
            if target:
                users = [u for u in users if u.get("gender") == target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        for user in users:
            if not self._running:
                break
            self._pause_event.wait()
            if not self._running:
                break

            uid = user.get("uid", "")
            nick = user.get("nick", "")

            if self.state.is_sent_today(uid):
                continue

            template = random.choice(self._templates)
            text = template.replace("{nick}", nick).replace("{room_name}", room.get("name", ""))

            try:
                result = self.send_message(uid, text)
            except Exception as e:
                result = {"success": False, "error": str(e)}

            if result.get("success"):
                self.state.mark_sent(uid, nick, room.get("name", ""))
                with self._lock:
                    sent = self._progress.get("sent_total", 0) + 1
                    self._progress["sent_total"] = sent
                    self.state.save_progress(sent_total=sent)
                self._notify("sent", {"uid": uid, "nick": nick, "text": text})
            else:
                with self._lock:
                    failed = self._progress.get("failed_total", 0) + 1
                    self._progress["failed_total"] = failed
                    self.state.save_progress(failed_total=failed)
                self._notify("failed", {
                    "uid": uid, "nick": nick,
                    "error": result.get("error", "unknown"),
                })

            time.sleep(self._interval)

    # ═══ 控制 ═══

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
            "mode": self.config.get("send_mode", "rest"),
            "interval": self._interval,
            "data_source": self._data_source,
            "period": self._period,
            "gender": self._gender,
        }

    def _notify(self, event: str, payload) -> None:
        if self._on_update:
            self._on_update(event, payload)
