"""每 App 独立 .state/ 目录读写 — rooms_cache / sent_today / progress"""
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path


class StateManager:
    def __init__(self, app_dir: str):
        self.state_dir = Path(app_dir) / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._rooms_path = self.state_dir / "rooms_cache.json"
        self._sent_path = self.state_dir / "sent_today.json"
        self._progress_path = self.state_dir / "progress.json"
        self._sent_today_cache: dict | None = None  # 内存缓存，避免重复读盘

    def _read_json(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            print(f"[StateManager] 警告: {path.name} 损坏，使用默认值", file=sys.stderr)
            return default

    def _write_json(self, path: Path, data) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def load_rooms(self) -> list:
        if not self._rooms_path.exists():
            return []
        return self._read_json(self._rooms_path, [])

    def save_rooms(self, rooms: list) -> None:
        self._write_json(self._rooms_path, rooms)

    def load_sent_today(self) -> dict:
        default = {"date": str(date.today()), "sent": []}
        if not self._sent_path.exists():
            return default
        data = self._read_json(self._sent_path, default)
        if "date" not in data or data["date"] != str(date.today()):
            return default
        if "sent" not in data:
            data["sent"] = []
        return data

    def _get_sent_today_cache(self) -> dict:
        if self._sent_today_cache is None:
            self._sent_today_cache = self.load_sent_today()
        return self._sent_today_cache

    def is_sent_today(self, uid: str) -> bool:
        uid = str(uid)
        data = self._get_sent_today_cache()
        return any(s["uid"] == uid for s in data["sent"])

    def mark_sent(self, uid: str, nick: str, room_name: str) -> None:
        uid = str(uid)
        data = self._get_sent_today_cache()
        data["sent"].append({
            "uid": uid, "nick": nick, "room": room_name,
            "time": datetime.now().strftime("%H:%M"),
        })
        self._write_json(self._sent_path, data)

    def load_progress(self) -> dict:
        if not self._progress_path.exists():
            return self._default_progress()
        return self._read_json(self._progress_path, self._default_progress())

    def save_progress(self, **kwargs) -> None:
        data = self.load_progress()
        data.update(kwargs)
        self._write_json(self._progress_path, data)

    def reset_progress(self) -> None:
        self._write_json(self._progress_path, self._default_progress())

    def _default_progress(self) -> dict:
        return {
            "current_room_index": 0, "current_room_name": "",
            "sent_total": 0, "failed_total": 0,
        }
