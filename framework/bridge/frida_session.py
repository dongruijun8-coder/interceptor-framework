"""Frida session lifecycle — attach, inject script, expose RPC, cleanup"""
import threading
import time
from pathlib import Path
from typing import Optional

import frida


class FridaDisconnectedError(Exception):
    """Raised when the Frida session is lost mid-operation.

    The pipeline must stop immediately — no retry, no skip."""


class FridaSession:
    """One Frida attach session for a specific (device, package, script) combo"""

    def __init__(self, device_serial: str, app_package: str, script_path: str):
        self.device_serial = device_serial
        self.app_package = app_package
        self.script_path = script_path
        self._device: Optional[frida.core.Device] = None
        self._session: Optional[frida.core.Session] = None
        self._script: Optional[frida.core.Script] = None
        self._rpc = None
        self._lock = threading.Lock()
        self._connected = False

    def connect(self) -> None:
        """Attach to the target app and inject the Frida script.

        Raises RuntimeError with a clear Chinese message on failure.
        """
        with self._lock:
            if self._connected:
                return

            # 1. Get Frida device — try USB/ADB first, then remote TCP
            try:
                self._device = frida.get_usb_device()
            except frida.ServerNotAvailableError:
                try:
                    self._device = frida.get_device_manager().add_remote_device(
                        self.device_serial
                    )
                except frida.ServerNotAvailableError:
                    raise RuntimeError(
                        f"无法连接到设备。请确认: "
                        f"1) ADB 已连接 ({self.device_serial}) "
                        f"2) frida-server 正在设备上运行"
                    )

            # 2. Attach to target process
            try:
                self._session = self._device.attach(self.app_package)
            except frida.ProcessNotFoundError:
                raise RuntimeError(
                    f"目标应用 {self.app_package} 未运行，请在设备上启动该 App 后重试"
                )
            except frida.TimedOutError:
                raise RuntimeError(f"附加到 {self.app_package} 超时，请重试")

            # 3. Read and inject script
            script_path = Path(self.script_path)
            if not script_path.exists():
                alt = Path("apps") / self.app_package / script_path.name
                if alt.exists():
                    script_path = alt
                else:
                    raise RuntimeError(f"Frida 脚本不存在: {self.script_path}")

            js_code = script_path.read_text(encoding="utf-8")
            try:
                self._script = self._session.create_script(js_code)
            except frida.InvalidOperationError as e:
                raise RuntimeError(f"Frida 脚本语法错误: {e}")

            # 4. Load script
            self._script.load()
            # Wait briefly for exports to become available
            time.sleep(0.3)
            if not hasattr(self._script, 'exports') or self._script.exports is None:
                raise RuntimeError(
                    "Frida 脚本未暴露 rpc.exports。"
                    "请在 hook 脚本中定义: rpc.exports = { sendMessage: function(uid, text) {...} }"
                )

            self._rpc = self._script.exports
            self._connected = True

    def send_message(self, uid: str, text: str) -> dict:
        """Call rpc.exports.sendMessage(uid, text) in the injected script.

        Returns:
            {"success": bool, "error": str}
        Raises:
            FridaDisconnectedError: if the session is gone — caller must stop pipeline
        """
        if not self._connected or not self._rpc:
            raise FridaDisconnectedError("Frida 会话已断开")

        try:
            result = self._rpc.send_message(uid, text)
        except frida.core.RPCException as e:
            return {"success": False, "error": f"RPC 调用失败: {e}"}
        except frida.InvalidOperationError:
            self._connected = False
            raise FridaDisconnectedError("Frida 会话在执行 RPC 时断开")

        # Interpret JS return value
        if result is True or result == "ok":
            return {"success": True, "error": ""}
        elif isinstance(result, str):
            return {"success": False, "error": result}
        elif isinstance(result, dict):
            return result
        else:
            return {"success": True, "error": ""}

    def disconnect(self) -> None:
        """Clean up: unload script, detach session"""
        with self._lock:
            self._connected = False
            self._rpc = None
            try:
                if self._script:
                    self._script.unload()
            except Exception:
                pass
            try:
                if self._session:
                    self._session.detach()
            except Exception:
                pass
            self._script = None
            self._session = None
            self._device = None

    @property
    def is_connected(self) -> bool:
        return self._connected


class FridaSessionManager:
    """Singleton registry of active Frida sessions, keyed by app_id"""

    _instance: Optional["FridaSessionManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: dict[str, FridaSession] = {}
            cls._instance._lock = threading.Lock()
        return cls._instance

    def get_or_create(self, app_id: str, device_serial: str,
                      app_package: str, script_path: str) -> FridaSession:
        """Get existing session or create and connect a new one"""
        with self._lock:
            existing = self._sessions.get(app_id)
            if existing and existing.is_connected:
                return existing
            if existing:
                existing.disconnect()

            session = FridaSession(device_serial, app_package, script_path)
            session.connect()
            self._sessions[app_id] = session
            return session

    def get(self, app_id: str) -> Optional[FridaSession]:
        return self._sessions.get(app_id)

    def remove(self, app_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(app_id, None)
            if session:
                session.disconnect()

    def remove_all(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                session.disconnect()
            self._sessions.clear()
