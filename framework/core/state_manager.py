"""每 App 独立 .state/ 目录读写 — rooms_cache / sent_today / progress"""
import json
from datetime import date, datetime
from pathlib import Path


class StateManager:
    def __init__(self, app_dir: str):
        self.state_dir = Path(app_dir) / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._rooms_path = self.state_dir / "rooms_cache.json"
        self._sent_path = self.state_dir / "sent_today.json"
        self._progress_path = self.state_dir / "progress.json"

    def load_rooms(self) -> list:
        if not self._rooms_path.exists():
            return []
        return json.loads(self._rooms_path.read_text(encoding="utf-8"))

    def save_rooms(self, rooms: list) -> None:
        self._rooms_path.write_text(
            json.dumps(rooms, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_sent_today(self) -> dict:
        if not self._sent_path.exists():
            return {"date": str(date.today()), "sent": []}
        data = json.loads(self._sent_path.read_text(encoding="utf-8"))
        if data.get("date") != str(date.today()):
            return {"date": str(date.today()), "sent": []}
        return data

    def is_sent_today(self, uid: str) -> bool:
        data = self.load_sent_today()
        return any(s["uid"] == uid for s in data["sent"])

    def mark_sent(self, uid: str, nick: str, room_name: str) -> None:
        data = self.load_sent_today()
        data["sent"].append({
            "uid": uid, "nick": nick, "room": room_name,
            "time": datetime.now().strftime("%H:%M"),
        })
        self._sent_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_progress(self) -> dict:
        if not self._progress_path.exists():
            return self._default_progress()
        return json.loads(self._progress_path.read_text(encoding="utf-8"))

    def save_progress(self, **kwargs) -> None:
        data = self.load_progress()
        data.update(kwargs)
        self._progress_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def reset_progress(self) -> None:
        self._progress_path.write_text(
            json.dumps(self._default_progress(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _default_progress(self) -> dict:
        return {
            "current_room_index": 0, "current_room_name": "",
            "sent_total": 0, "failed_total": 0,
        }
