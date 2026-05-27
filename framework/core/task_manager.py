"""多 App 任务调度 — 自动发现、启动/暂停/停止/状态查询"""
import importlib
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
        if not self.apps_dir.exists():
            return
        for item in sorted(self.apps_dir.iterdir()):
            if item.is_dir() and (item / "config.json").exists():
                app_id = item.name
                client = self._load_client(app_id)
                if client:
                    self._tasks[app_id] = client

    def _load_client(self, app_id: str) -> Optional[BaseClient]:
        config_path = str(self.apps_dir / app_id / "config.json")
        try:
            mod = importlib.import_module(f"apps.{app_id}.client")
            for name in dir(mod):
                obj = getattr(mod, name)
                if (isinstance(obj, type) and issubclass(obj, BaseClient)
                        and obj is not BaseClient):
                    return obj(config_path)
        except Exception as e:
            print(f"[TaskManager] 加载 {app_id} 失败: {e}")
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
        task.reset_progress()
        return True

    def rescan_rooms(self, app_id: str) -> bool:
        task = self._tasks.get(app_id)
        if not task:
            return False
        try:
            task.refresh_rooms()
            return True
        except Exception as e:
            print(f"[TaskManager] 重新扫描 {app_id} 失败: {e}")
            return False

    # ═══ 查询 ═══

    def get_all_stats(self) -> list:
        return [t.get_stats() for t in self._tasks.values()]

    def get_stats(self, app_id: str) -> Optional[dict]:
        task = self._tasks.get(app_id)
        return task.get_stats() if task else None

    def get_task(self, app_id: str) -> Optional[BaseClient]:
        return self._tasks.get(app_id)

    @property
    def task_ids(self) -> list:
        return list(self._tasks.keys())
