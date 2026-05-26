"""每 App 独立 .state/ 目录读写 — rooms_cache / sent_today / progress"""
import json
import sys
from datetime import date, datetime
from pathlib import Path


class StateManager:
    def __init__(self, app_dir: str):
        self.state_dir = Path(app_dir) / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._rooms_path = self.state_dir / "rooms_cache.json"
        self._sent_path = self.state_dir / "sent_today.json"
        self._progress_path = self.state_dir / "progress.json"

    def _read_json(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            print(f"[StateManager] 警告: {path.name} 损坏，使用默认值", file=sys.stderr)
            return default

    def _write_json(self, path: Path, data) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        if data.get("date") != str(date.today()):
            return default
        return data

    def is_sent_today(self, uid: str) -> bool:
        uid = str(uid)
        data = self.load_sent_today()
        return any(s["uid"] == uid for s in data["sent"])

    def mark_sent(self, uid: str, nick: str, room_name: str) -> None:
        uid = str(uid)
        data = self.load_sent_today()
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
