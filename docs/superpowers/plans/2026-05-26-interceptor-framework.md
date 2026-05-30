# 统一私信截流框架 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建可扩展的多 App 私信截流框架 — BaseClient 继承模式，逐房 Pipeline，Flask Dashboard 统一面板，漂漂为首个 App。

**Architecture:** 自底向上：state_manager（状态持久化）→ base_client（Pipeline 核心）→ piaopiao client（3 个方法）→ task_manager（多任务调度）→ dashboard.py（Flask 面板）。每层只依赖下层，独立可测。

**Tech Stack:** Python 3.12+, Flask, requests, threading, JSON 文件存储

---

## File Structure

```
截流框架/
  framework/
    core/
      __init__.py
      state_manager.py     # .state/ 目录读写，3 个 JSON 文件
      base_client.py       # Pipeline + HTTP 封装
      task_manager.py      # 多 App 任务调度（线程）
      dashboard.py         # Flask 面板 API + 模板渲染
    bridge/
      __init__.py
      nim_bridge.py        # Frida NIM 桥（本计划暂不实现，预留接口）
  apps/
    piaopiao/
      __init__.py
      config.json          # API 端点 + 认证 + 筛选配置
      client.py            # PiaopiaoClient(BaseClient) — 3 个方法
```

---

### Task 1: StateManager — 状态持久化

**Files:**
- Create: `framework/__init__.py`
- Create: `framework/core/__init__.py`
- Create: `framework/core/state_manager.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p framework/core framework/bridge apps/piaopiao
touch framework/__init__.py framework/core/__init__.py
```

- [ ] **Step 2: Write StateManager**

`framework/core/state_manager.py`:

```python
"""每 App 独立 .state/ 目录读写 — rooms_cache / sent_today / progress"""
import json
import os
from datetime import date
from pathlib import Path
from typing import Optional


class StateManager:
    def __init__(self, app_dir: str):
        self.state_dir = Path(app_dir) / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._rooms_path = self.state_dir / "rooms_cache.json"
        self._sent_path = self.state_dir / "sent_today.json"
        self._progress_path = self.state_dir / "progress.json"

    # ── rooms_cache ──

    def load_rooms(self) -> list[dict]:
        if not self._rooms_path.exists():
            return []
        return json.loads(self._rooms_path.read_text(encoding="utf-8"))

    def save_rooms(self, rooms: list[dict]) -> None:
        self._rooms_path.write_text(
            json.dumps(rooms, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── sent_today ──

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
        from datetime import datetime
        data = self.load_sent_today()
        data["sent"].append({
            "uid": uid,
            "nick": nick,
            "room": room_name,
            "time": datetime.now().strftime("%H:%M"),
        })
        self._sent_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── progress ──

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
            encoding="utf-8",
        )

    def _default_progress(self) -> dict:
        return {
            "current_room_index": 0,
            "current_room_name": "",
            "sent_total": 0,
            "failed_total": 0,
        }
```

- [ ] **Step 3: Quick smoke test**

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "
from framework.core.state_manager import StateManager
s = StateManager('apps/test_app')
s.save_rooms([{'id':'1','name':'test'}])
print(s.load_rooms())
s.mark_sent('123','test_user','test_room')
print(s.is_sent_today('123'))
s.save_progress(current_room_index=5, sent_total=10)
print(s.load_progress())
import shutil; shutil.rmtree('apps/test_app/.state')
print('OK')
"
```
Expected: prints rooms list, True, progress dict with sent_total=10, "OK"

- [ ] **Step 4: Commit**

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
git add framework/ framework/core/state_manager.py
git commit -m "feat: StateManager — .state/ 目录读写，3 个 JSON 文件"
```

---

### Task 2: BaseClient — Pipeline 核心

**Files:**
- Create: `framework/core/base_client.py`

- [ ] **Step 1: Write BaseClient**

`framework/core/base_client.py`:

```python
"""BaseClient — Pipeline 核心 + HTTP 封装。App 继承后实现 3 个方法。"""
import json
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import requests
import urllib3

from .state_manager import StateManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BaseClient(ABC):
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.app_name = self.config["app_name"]
        self.state = StateManager(str(self.config_path.parent))

        self.session = requests.Session()
        self.session.verify = False
        self._authenticated = False
        self._running = False
        self._paused = False
        self._interval = self.config.get("send_interval", 3)
        self._templates = self.config.get("templates", ["{nick} 你好~"])
        self._period = self.config.get("period", "今日")
        self._gender = self.config.get("gender", "全部")
        self._data_source = self.config.get("data_source", "贡献榜")
        self._rooms = []
        self._progress = {}
        self._on_update = None  # callback for dashboard

    # ═══ App 必须实现（3 个方法）═══

    @abstractmethod
    def fetch_all_rooms(self) -> list[dict]:
        """返回 [{id, name, type, ...meta}]"""
        ...

    @abstractmethod
    def fetch_room_ranking(self, room: dict, period: str) -> list[dict]:
        """返回 [{uid, nick, amount, gender}]"""
        ...

    @abstractmethod
    def send_message(self, uid: str, text: str) -> dict:
        """返回 {success: bool, error: str}"""
        ...

    # ═══ 可选重写 ═══

    def authenticate(self) -> bool:
        """auto 模式走登录接口，manual 模式返回 True"""
        mode = self.config.get("auth_mode", "manual")
        if mode == "manual":
            return True
        # auto: 默认实现 POST /sms_login
        return False

    def build_headers(self) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }

    def check_response(self, resp_data: dict) -> bool:
        """默认：code == 'S_OK' 表示成功"""
        return resp_data.get("code") == "S_OK"

    def parse_user(self, raw: dict) -> dict:
        """统一为 {uid, nick, amount, gender}"""
        return {
            "uid": str(raw.get("uid", "")),
            "nick": raw.get("nick", raw.get("nickName", "")),
            "amount": raw.get("amount", raw.get("totalAmount", 0)),
            "gender": raw.get("gender", 0),
        }

    # ═══ HTTP 工具 ═══

    def _post(self, url: str, body: dict) -> dict:
        r = self.session.post(url, json=body, headers=self.build_headers(), timeout=30)
        return r.json()

    def _get(self, url: str, params: dict = None) -> dict:
        r = self.session.get(url, params=params, headers=self.build_headers(), timeout=30)
        return r.json()

    # ═══ Pipeline ═══

    def run_pipeline(self) -> None:
        """从断点开始，逐房跑完"""
        self._running = True
        self._paused = False

        if not self._authenticated:
            if not self.authenticate():
                self._notify("error", "认证失败")
                return
            self._authenticated = True

        # Step 1: 房间缓存
        self._rooms = self.state.load_rooms()
        if not self._rooms:
            self._notify("info", "扫描房间...")
            self._rooms = self.fetch_all_rooms()
            self.state.save_rooms(self._rooms)
            self._notify("info", f"扫描完成: {len(self._rooms)} 间房")

        # Step 2: 断点恢复
        self._progress = self.state.load_progress()
        start_idx = self._progress.get("current_room_index", 0)

        # Step 3: 逐房循环
        for idx in range(start_idx, len(self._rooms)):
            if not self._running:
                break
            while self._paused and self._running:
                time.sleep(0.5)
            if not self._running:
                break

            room = self._rooms[idx]
            self._notify("progress", {"current_room_index": idx, "room": room})
            self.run_room(room, idx)

        if self._running:
            self._notify("done", "全部房间完成")
        self._running = False

    def run_room(self, room: dict, idx: int) -> None:
        """单间房完整流程"""
        self.state.save_progress(
            current_room_index=idx,
            current_room_name=room.get("name", ""),
        )

        # 拉排行榜
        try:
            users = self.fetch_room_ranking(room, self._period)
        except Exception as e:
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        # 按贡献降序
        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        for user in users:
            if not self._running:
                break
            while self._paused and self._running:
                time.sleep(0.5)
            if not self._running:
                break

            uid = user.get("uid", "")
            nick = user.get("nick", "")

            # 过滤已发
            if self.state.is_sent_today(uid):
                continue

            # 随机话术
            template = random.choice(self._templates)
            text = template.replace("{nick}", nick).replace("{room_name}", room.get("name", ""))

            # 发送
            result = self.send_message(uid, text)

            if result.get("success"):
                self.state.mark_sent(uid, nick, room.get("name", ""))
                sent = self._progress.get("sent_total", 0) + 1
                self._progress["sent_total"] = sent
                self.state.save_progress(sent_total=sent)
                self._notify("sent", {"uid": uid, "nick": nick, "text": text})
            else:
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
        import threading
        t = threading.Thread(target=self.run_pipeline, daemon=True)
        t.start()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def stop(self) -> None:
        self._running = False
        self._paused = False

    @property
    def status(self) -> str:
        if not self._running:
            return "idle"
        if self._paused:
            return "paused"
        return "running"

    def get_stats(self) -> dict:
        rooms = self._rooms
        progress = self._progress
        total_rooms = len(rooms)
        done = progress.get("current_room_index", 0)
        return {
            "app_name": self.app_name,
            "status": self.status,
            "total_rooms": total_rooms,
            "done_rooms": min(done, total_rooms),
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
```

- [ ] **Step 2: Verify imports**

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "from framework.core.base_client import BaseClient; print('import OK')"
```
Expected: `import OK`

- [ ] **Step 3: Commit**

```bash
git add framework/core/base_client.py
git commit -m "feat: BaseClient — Pipeline 核心 + HTTP 封装，3 个抽象方法"
```

---

### Task 3: 漂漂 config.json

**Files:**
- Create: `apps/piaopiao/config.json`

- [ ] **Step 1: Write config.json**

`apps/piaopiao/config.json`:

```json
{
  "app_name": "漂漂",
  "subtitle": "Popo Live",
  "send_mode": "rest",
  "send_interval": 3,

  "auth_mode": "manual",
  "token": "jFOm3hZAcMy09UW2QiCs8LHs9AWQwDMg",
  "uid": "91769319",
  "device_id": "5977276a-be8b-301b-9be3-f91c1946536e",

  "data_source": "贡献榜",
  "period": "今日",
  "gender": "全部",

  "data_sources": ["贡献榜"],
  "periods": ["今日", "本周"],
  "genders": ["全部", "男", "女"],

  "templates": [
    "{nick}你好，来我房间玩嘛~",
    "{nick}在吗？聊聊呀",
    "{nick}哈喽~ 来听听歌吧"
  ],

  "base_url": "https://api.pp.weimipopo.com",
  "voice_room_count": 66,
  "video_room_count": 126
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/piaopiao/config.json
git commit -m "feat: 漂漂 config.json — REST API + manual auth + 贡献榜"
```

---

### Task 4: PiaopiaoClient — 3 个方法

**Files:**
- Create: `apps/piaopiao/__init__.py`
- Create: `apps/piaopiao/client.py`

- [ ] **Step 1: Write PiaopiaoClient**

`apps/piaopiao/client.py`:

```python
"""漂漂 (Popo Live) 客户端 — 纯 REST API，继承 BaseClient，实现 3 个方法"""
from pathlib import Path

from framework.core.base_client import BaseClient


class PiaopiaoClient(BaseClient):
    def __init__(self):
        config_path = Path(__file__).parent / "config.json"
        super().__init__(str(config_path))

    # ═══ 3 个必须方法 ═══

    def fetch_all_rooms(self) -> list[dict]:
        cfg = self.config
        base = cfg["base_url"]
        token = cfg["token"]
        uid = cfg["uid"]
        rooms = []

        # 语音厅 (66间)
        rooms += self._paginate_voice_rooms(base, token, uid)

        # 视频直播 (126间)
        rooms += self._paginate_video_rooms(base, token, uid)

        return rooms

    def fetch_room_ranking(self, room: dict, period: str) -> list[dict]:
        cfg = self.config
        base = cfg["base_url"]
        token = cfg["token"]
        uid = cfg["uid"]

        period_code = {"今日": "day", "本周": "week"}.get(period, "day")
        room_type = room.get("type", "voice")

        users = []
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

        # Step 1: preCheck 获取 msgChatId
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

        # Step 2: send
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

    # ═══ 认证（manual 模式不需要）═══

    def authenticate(self) -> bool:
        cfg = self.config
        if cfg.get("auth_mode") == "manual":
            return bool(cfg.get("token") and cfg.get("uid"))
        return super().authenticate()

    # ═══ 内部分页 ═══

    def _build_body(self, token: str, uid: str, params: dict = None) -> dict:
        return {
            "app": "plpl", "build": 126, "channel": "plpl_baidu",
            "token": token, "uid": uid, "version": "1.7.40",
            "params": params or {},
        }

    def _paginate_voice_rooms(self, base: str, token: str, uid: str) -> list[dict]:
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

    def _paginate_video_rooms(self, base: str, token: str, uid: str) -> list[dict]:
        rooms = []
        categories = [1, 2, 3, 4, 5]  # 新秀/热舞/金牌/PK/排麦
        for cat in categories:
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
                              room_id: str, period: str) -> list[dict]:
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
                              room_id: str, period: str) -> list[dict]:
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
```

- [ ] **Step 2: Verify imports**

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "from apps.piaopiao.client import PiaopiaoClient; c = PiaopiaoClient(); print(f'OK: {c.app_name} - {c.status}')"
```
Expected: `OK: 漂漂 - idle`

- [ ] **Step 3: Commit**

```bash
git add apps/piaopiao/
git commit -m "feat: PiaopiaoClient — 3 个方法（fetch_all_rooms / fetch_room_ranking / send_message）"
```

---

### Task 5: TaskManager — 多任务调度

**Files:**
- Create: `framework/core/task_manager.py`

- [ ] **Step 1: Write TaskManager**

`framework/core/task_manager.py`:

```python
"""多 App 任务调度 — 启动/暂停/停止/状态查询"""
import json
import os
from pathlib import Path
from typing import Optional

from framework.core.base_client import BaseClient


class TaskManager:
    def __init__(self, apps_dir: str = None):
        if apps_dir is None:
            apps_dir = Path(__file__).resolve().parent.parent.parent / "apps"
        self.apps_dir = Path(apps_dir)
        self._tasks: dict[str, BaseClient] = {}
        self._discover()

    def _discover(self) -> None:
        """扫描 apps/ 目录下所有有 config.json 的 App"""
        if not self.apps_dir.exists():
            return
        for item in self.apps_dir.iterdir():
            if item.is_dir() and (item / "config.json").exists():
                app_id = item.name
                self._tasks[app_id] = self._load_client(app_id)

    def _load_client(self, app_id: str) -> Optional[BaseClient]:
        """动态加载 App client"""
        import importlib
        try:
            mod = importlib.import_module(f"apps.{app_id}.client")
            # 查找继承 BaseClient 的类
            for name in dir(mod):
                obj = getattr(mod, name)
                if (isinstance(obj, type) and issubclass(obj, BaseClient)
                        and obj is not BaseClient):
                    return obj()
        except Exception as e:
            print(f"[TaskManager] 加载 {app_id} 失败: {e}")
            return None
        return None

    # ═══ 控制 ═══

    def start(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task or task.status == "running":
            return False
        task.start()
        return True

    def pause(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task or task.status != "running":
            return False
        task.pause()
        return True

    def stop(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task:
            return False
        task.stop()
        task.state.reset_progress()
        return True

    def rescan_rooms(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task:
            return False
        task._rooms = task.fetch_all_rooms()
        task.state.save_rooms(task._rooms)
        return True

    # ═══ 查询 ═══

    def get_all_stats(self) -> list[dict]:
        return [t.get_stats() for t in self._tasks.values()]

    def get_stats(self, app_id: str) -> Optional[dict]:
        task = self._tasks.get(app_id)
        return task.get_stats() if task else None

    def get_task(self, app_id: str) -> Optional[BaseClient]:
        return self._tasks.get(app_id)

    @property
    def task_ids(self) -> list[str]:
        return list(self._tasks.keys())
```

- [ ] **Step 2: Verify**

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "
from framework.core.task_manager import TaskManager
tm = TaskManager()
print('Apps:', tm.task_ids)
print(tm.get_all_stats())
print('OK')
"
```
Expected: `Apps: ['piaopiao']` + stats list + `OK`

- [ ] **Step 3: Commit**

```bash
git add framework/core/task_manager.py
git commit -m "feat: TaskManager — 多 App 调度（发现/启动/暂停/停止/状态）"
```

---

### Task 6: Flask Dashboard — 统一面板

**Files:**
- Create: `framework/core/dashboard.py`

- [ ] **Step 1: Write dashboard.py**

`framework/core/dashboard.py`:

```python
"""Flask 统一面板 — API + 页面渲染"""
import json
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from .task_manager import TaskManager

app = Flask(__name__)
manager = TaskManager()

HOMEPAGE_HTML = (Path(__file__).resolve().parent.parent.parent
                 / "docs" / "superpowers" / "specs" / "homepage.html")
DETAIL_HTML = (Path(__file__).resolve().parent.parent.parent
               / "docs" / "superpowers" / "specs" / "design-mockup.html")


# ═══ API ═══

@app.route("/api/apps")
def api_apps():
    return jsonify(manager.get_all_stats())


@app.route("/api/app/<app_id>")
def api_app_detail(app_id):
    stats = manager.get_stats(app_id)
    if not stats:
        return jsonify({"error": "not found"}), 404
    return jsonify(stats)


@app.route("/api/app/<app_id>/start", methods=["POST"])
def api_start(app_id):
    ok = manager.start(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/pause", methods=["POST"])
def api_pause(app_id):
    ok = manager.pause(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/stop", methods=["POST"])
def api_stop(app_id):
    ok = manager.stop(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/rescan", methods=["POST"])
def api_rescan(app_id):
    ok = manager.rescan_rooms(app_id)
    return jsonify({"success": ok})


# ═══ Pages ═══

@app.route("/")
def index():
    if HOMEPAGE_HTML.exists():
        return HOMEPAGE_HTML.read_text(encoding="utf-8")
    return "<h1>截流看板</h1><p>homepage.html not found</p>"


@app.route("/app/<app_id>")
def app_detail(app_id):
    if DETAIL_HTML.exists():
        return DETAIL_HTML.read_text(encoding="utf-8")
    return f"<h1>{app_id}</h1>"


def run_dashboard(host: str = "127.0.0.1", port: int = 3112, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
```

- [ ] **Step 2: Install Flask if needed and test**

```bash
pip install flask 2>&1 | tail -1
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "
from framework.core.dashboard import app
client = app.test_client()
r = client.get('/api/apps')
print(r.status_code, r.get_json())
print('OK')
"
```
Expected: `200 [{...漂漂 stats...}] OK`

- [ ] **Step 3: Commit**

```bash
git add framework/core/dashboard.py
git commit -m "feat: Flask Dashboard — /api/* 控制接口 + HTML 页面渲染"
```

---

## Verification

完整启动测试：

```bash
cd d:/360MoveData/Users/DYWH/Desktop/梦音私信脚本/截流框架
python -c "
from framework.core.dashboard import run_dashboard
run_dashboard(debug=True)
"
```

浏览器打开 `http://127.0.0.1:3112`:
1. 首页显示任务看板（from homepage.html）
2. `GET /api/apps` 返回 JSON 统计
3. `POST /api/app/piaopiao/start` 启动漂漂任务
4. 点击卡片进 `/app/piaopiao` 详情页
5. `POST /api/app/piaopiao/pause` 暂停
6. `POST /api/app/piaopiao/stop` 停止
