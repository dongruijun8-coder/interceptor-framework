"""Frida Python binding transport — for apps WITHOUT NIS anti-Frida."""
from .frida_transport import FridaTransport
from .frida_session import FridaSessionManager


class FridaTransportBinding(FridaTransport):
    """Python frida binding transport."""

    def __init__(self):
        self._session = None
        self._mgr = FridaSessionManager()
        self._app_id = None

    def connect(self, serial, package, script_path):
        import hashlib
        self._app_id = hashlib.md5(script_path.encode()).hexdigest()[:8]
        self._session = self._mgr.get_or_create(
            self._app_id, serial, package, script_path,
        )

    def send_message(self, uid, text, timeout=5.0):
        if not self._session or not self._session.is_connected:
            return {"success": False, "error": "Not connected"}
        try:
            return self._session.send_message(uid, text)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def capture_key(self, timeout=30.0):
        return None

    def disconnect(self):
        if self._mgr and self._app_id:
            self._mgr.remove(self._app_id)
        self._session = None

    def is_running(self):
        return self._session is not None and self._session.is_connected
