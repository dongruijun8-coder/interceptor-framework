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

            # 1. Get Frida device by serial
            try:
                self._device = frida.get_device_manager().get_device(
                    self.device_serial
                )
            except frida.ServerNotAvailableError:
                raise RuntimeError(
                    f"无法连接到设备 ({self.device_serial})。请确认: "
                    f"1) ADB 已连接 "
                    f"2) frida-server 正在设备上运行"
                )
            except frida.TransportError:
                # Fallback: try USB device
                try:
                    self._device = frida.get_usb_device()
                except frida.ServerNotAvailableError:
                    raise RuntimeError(
                        f"无法连接到设备 ({self.device_serial})。请确认 "
                        f"frida-server 正在设备上运行"
                    )

            # 2. Attach to target process — try package name first, then PID lookup
            try:
                self._session = self._device.attach(self.app_package)
            except frida.ProcessNotFoundError:
                # Search by app package in process list and attach by PID
                # On some devices the process name is the display name, not the package name
                found_pid = None
                for p in self._device.enumerate_processes():
                    pname = (p.name or "").lower()
                    if pname == self.app_package.lower() or self.app_package.lower() in pname:
                        found_pid = p.pid
                        break
                # Also try matching segments of package name
                if not found_pid:
                    parts = self.app_package.split(".")
                    # Build candidates from most specific to least: last 2 segments, then each segment
                    candidates = []
                    if len(parts) >= 2:
                        candidates.append(".".join(parts[-2:]))  # e.g. "hifun.android"
                    for part in parts:
                        if part.lower() not in ("android", "chat", "com", "cn", "app", "io"):
                            candidates.append(part)  # e.g. "hifun"
                    for candidate in candidates:
                        for p in self._device.enumerate_processes():
                            if candidate.lower() in (p.name or "").lower():
                                found_pid = p.pid
                                break
                        if found_pid:
                            break
                if found_pid:
                    try:
                        self._session = self._device.attach(found_pid)
                    except Exception as e:
                        raise RuntimeError(
                            f"目标应用 {self.app_package} 正在运行 (PID={found_pid}) "
                            f"但附加失败: {e}"
                        )
                else:
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

            # 4. Load script and wait for exports to be populated
            self._script.load()
            # The JS script watches for IM SDK login (every 200ms) before installing
            # rpc.exports via doInstall(). Wait up to 15s for exports to appear.
            for i in range(30):
                time.sleep(0.5)
                try:
                    if (hasattr(self._script, 'exports')
                            and self._script.exports is not None):
                        export_keys = [k for k in dir(self._script.exports)
                                       if not k.startswith('_')]
                        if any(k in export_keys for k in
                               ('sendMessage', 'send_message', 'sendText', 'send_text')):
                            break
                except Exception:
                    pass
            else:
                # Check one more time — maybe exports exist but without our methods
                if not hasattr(self._script, 'exports') or self._script.exports is None:
                    raise RuntimeError(
                        "Frida 脚本未暴露 rpc.exports。"
                        "请在 hook 脚本中定义: rpc.exports = { sendMessage: function(uid, text) {...} }"
                    )
                export_keys = [k for k in dir(self._script.exports) if not k.startswith('_')]
                raise RuntimeError(
                    f"Frida 脚本 exports 已存在但缺少发送方法。"
                    f"当前 exports: {export_keys}。"
                    f"请确认 IM SDK 已登录且 doInstall() 已执行。"
                )

            self._rpc = self._script.exports_sync
            self._connected = True

    def send_message(self, uid: str, text: str) -> dict:
        """Call rpc.exports.sendMessage/sendText(uid, text) in the injected script.

        Tries multiple JS export names in order:
        sendMessage → send_message → sendText → send_text

        Returns:
            {"success": bool, "error": str}
        Raises:
            FridaDisconnectedError: if the session is gone — caller must stop pipeline
        """
        if not self._connected or not self._rpc:
            raise FridaDisconnectedError("Frida 会话已断开")

        # Try each method name until one works
        method_names = ["sendMessage", "send_message", "sendText", "send_text"]
        result = None
        last_error = None
        export_keys = [k for k in dir(self._rpc) if not k.startswith('_')]

        for name in method_names:
            if name not in export_keys:
                continue
            try:
                result = getattr(self._rpc, name)(uid, text)
                break
            except frida.core.RPCException as e:
                last_error = e
                continue
            except frida.InvalidOperationError:
                self._connected = False
                raise FridaDisconnectedError("Frida 会话在执行 RPC 时断开")
        else:
            if last_error:
                return {"success": False, "error": f"RPC 调用失败: {last_error}"}
            return {"success": False, "error": "RPC exports 未找到 sendMessage/sendText 方法"}

        # Interpret JS return value
        if result is True or result == "ok":
            return {"success": True, "error": ""}
        elif isinstance(result, str):
            # Try to parse JSON string
            try:
                import json
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    # Async queued result — poll for actual outcome
                    if parsed.get("queued") and parsed.get("key"):
                        return self._poll_send_result(parsed["key"])
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return {"success": False, "error": result}
        elif isinstance(result, dict):
            # Async queued result — poll for actual outcome
            if result.get("queued") and result.get("key"):
                return self._poll_send_result(result["key"])
            return result
        else:
            return {"success": True, "error": ""}

    def _poll_send_result(self, key: str, timeout: float = 10.0, interval: float = 0.3) -> dict:
        """Poll rpc.exports.pollResult(key) until the async send completes or timeout."""
        deadline = time.time() + timeout
        # Find poll method
        poll_names = ["poll_result", "pollResult"]
        poll_fn = None
        export_keys = [k for k in dir(self._rpc) if not k.startswith('_')]
        for name in poll_names:
            if name in export_keys:
                poll_fn = getattr(self._rpc, name)
                break
        if not poll_fn:
            return {"success": True, "error": "", "note": "queued but pollResult not found in rpc.exports"}

        while time.time() < deadline:
            try:
                raw = poll_fn(key)
            except (frida.core.RPCException, frida.InvalidOperationError):
                return {"success": False, "error": "Frida RPC poll failed"}
            # Parse poll result
            if isinstance(raw, str):
                try:
                    import json
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    time.sleep(interval)
                    continue
            elif isinstance(raw, dict):
                parsed = raw
            else:
                time.sleep(interval)
                continue

            if parsed.get("status") == "pending":
                time.sleep(interval)
                continue
            if "success" in parsed:
                return parsed
            if "error" in parsed:
                return {"success": False, "error": parsed.get("error", "unknown")}
            time.sleep(interval)

        return {"success": False, "error": f"poll timeout ({timeout}s) for key={key}"}

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
