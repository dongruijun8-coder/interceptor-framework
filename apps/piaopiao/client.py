"""漂漂 (Popo Live) 客户端 — 纯 REST API，继承 BaseClient，实现 3 个方法"""
from pathlib import Path

from framework.core.base_client import BaseClient


class PiaopiaoClient(BaseClient):
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = str(Path(__file__).parent / "config.json")
        super().__init__(config_path)

    # ═══ 3 个必须方法 ═══

    def fetch_all_rooms(self) -> list:
        cfg = self.config
        base = cfg["base_url"]
        token = cfg["token"]
        uid = cfg["uid"]
        rooms = []
        rooms += self._paginate_voice_rooms(base, token, uid)
        rooms += self._paginate_video_rooms(base, token, uid)
        return rooms

    def fetch_room_ranking(self, room: dict, period: str) -> list:
        cfg = self.config
        base = cfg["base_url"]
        token = cfg["token"]
        uid = cfg["uid"]
        period_code = {"今日": "day", "本周": "week"}.get(period, "day")
        room_type = room.get("type", "voice")

        if room_type == "voice":
            users = self._fetch_voice_ranking(base, token, uid, room["id"], period_code)
        else:
            users = self._fetch_video_ranking(base, token, uid, room["id"], period_code)
        return [self.parse_user(u) for u in users]

    def send_message(self, target_uid: str, text: str) -> dict:
        cfg = self.config
        base = cfg["base_url"]
        token = cfg["token"]
        uid = cfg["uid"]

        precheck = self._post(f"{base}/plpl/im/msg/preCheck", {
            "app": "plpl", "build": 126, "channel": "plpl_baidu",
            "token": token, "uid": uid, "version": "1.7.40",
            "params": {"tuids": [target_uid]},
        })
        if not self.check_response(precheck):
            return {"success": False, "error": f"preCheck: {precheck.get('message', '')}"}

        msg_chat_id = precheck.get("data", {}).get("msgChatId", "")
        if not msg_chat_id:
            return {"success": False, "error": "no msgChatId"}

        resp = self._post(f"{base}/plpl/im/msg/send", {
            "app": "plpl", "build": 126, "channel": "plpl_baidu",
            "token": token, "uid": uid, "version": "1.7.40",
            "params": {
                "tuid": target_uid,
                "content": text,
                "msgChatId": msg_chat_id,
                "type": "TEXT",
            },
        })
        if self.check_response(resp):
            return {"success": True, "error": ""}
        return {"success": False, "error": resp.get("message", "send failed")}

    # ═══ 内部分页 ═══

    def _build_body(self, token: str, uid: str, params: dict = None) -> dict:
        return {
            "app": "plpl", "build": 126, "channel": "plpl_baidu",
            "token": token, "uid": uid, "version": "1.7.40",
            "params": params or {},
        }

    def _paginate_voice_rooms(self, base: str, token: str, uid: str) -> list:
        rooms = []
        for offset in range(0, 100, 20):
            body = self._build_body(token, uid, {"catId": 1, "offset": offset, "limit": 20})
            resp = self._post(f"{base}/plpl/room/main/listByCat", body)
            items = resp.get("data", {}).get("list", [])
            for r in items:
                rooms.append({
                    "id": str(r.get("unRoomId", "")),
                    "name": r.get("roomName", r.get("name", "")),
                    "type": "voice",
                })
            if len(items) < 20:
                break
        return rooms

    def _paginate_video_rooms(self, base: str, token: str, uid: str) -> list:
        rooms = []
        for cat in (1, 2, 3, 4, 5):
            for offset in range(0, 60, 20):
                body = self._build_body(token, uid, {"categoryId": cat, "offset": offset, "limit": 20})
                resp = self._post(f"{base}/plpl/live/category/live", body)
                items = resp.get("data", {}).get("list", [])
                for r in items:
                    rooms.append({
                        "id": str(r.get("lid", r.get("roomId", ""))),
                        "name": r.get("title", r.get("name", "")),
                        "type": "video",
                    })
                if len(items) < 20:
                    break
        return rooms

    def _fetch_voice_ranking(self, base: str, token: str, uid: str,
                              room_id: str, period: str) -> list:
        users = []
        for offset in range(0, 100, 20):
            body = self._build_body(token, uid, {
                "unRoomId": room_id, "period": period,
                "offset": offset, "limit": 20,
            })
            resp = self._post(f"{base}/room/rank/list/contribute/rank", body)
            items = resp.get("data", {}).get("list", [])
            if not items:
                break
            users.extend(items)
        return users

    def _fetch_video_ranking(self, base: str, token: str, uid: str,
                              room_id: str, period: str) -> list:
        users = []
        for offset in range(0, 100, 20):
            body = self._build_body(token, uid, {
                "tid": room_id, "period": period,
                "offset": offset, "limit": 20,
            })
            resp = self._post(f"{base}/gift/list/contribute/rank", body)
            items = resp.get("data", {}).get("list", [])
            if not items:
                break
            users.extend(items)
        return users
