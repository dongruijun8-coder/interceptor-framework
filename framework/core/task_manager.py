"""多 App 任务调度 — 从 apps/ 读取 config.json 发现 App"""
import json
from pathlib import Path
from typing import Optional

from framework.core.client import Client


class TaskManager:
    def __init__(self, apps_dir: str = None):
        if apps_dir is None:
            apps_dir = Path(__file__).resolve().parent.parent.parent / "apps"
        self.apps_dir = Path(apps_dir)
        self._tasks: dict[str, Client] = {}
        self._discover()

    def _discover(self) -> None:
        if not self.apps_dir.exists():
            return
        for item in sorted(self.apps_dir.iterdir()):
            if not item.is_dir():
                continue
            config_file = item / "config.json"
            if not config_file.exists():
                continue
            app_id = item.name
            try:
                client = Client(str(config_file))
                self._tasks[app_id] = client
            except Exception as e:
                print(f"[TaskManager] 加载 {app_id} 失败: {e}")

    def register(self, app_id: str, config_path: str) -> bool:
        """动态注册新 App（Web 上传后调用）"""
        try:
            client = Client(config_path)
            self._tasks[app_id] = client
            return True
        except Exception as e:
            print(f"[TaskManager] 注册 {app_id} 失败: {e}")
            return False

    def unregister(self, app_id: str) -> bool:
        if app_id in self._tasks:
            task = self._tasks.pop(app_id)
            task.stop()
            return True
        return False

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
        task.reset_progress()
        return True

    def rescan_rooms(self, app_id: str):
        task = self._tasks.get(app_id)
        if not task:
            return False, "not found"
        try:
            task.refresh_rooms()
            return True, ""
        except Exception as e:
            print(f"[TaskManager] 重新扫描 {app_id} 失败: {e}")
            return False, str(e)

    # ═══ 查询 ═══

    def get_all_stats(self) -> list:
        return [t.get_stats() for t in self._tasks.values()]

    def get_stats(self, app_id: str) -> Optional[dict]:
        task = self._tasks.get(app_id)
        return task.get_stats() if task else None

    def get_task(self, app_id: str) -> Optional[Client]:
        return self._tasks.get(app_id)

    @property
    def task_ids(self) -> list:
        return list(self._tasks.keys())
