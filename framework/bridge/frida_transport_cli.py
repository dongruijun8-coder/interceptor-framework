"""Frida CLI transport — subprocess stdin/stdout (NIS bypass).

Wraps FridaCliSession behind the FridaTransport ABC.
"""
import subprocess
import time

from .frida_transport import FridaTransport
from .frida_cli import FridaCliSession
from framework.bridge.adb_device import AdbDevice


class FridaTransportCli(FridaTransport):
    """CLI subprocess transport for NIS-bypass apps."""

    def __init__(self, host="127.0.0.1", port=27042):
        self._cli = FridaCliSession(host=host, port=port)

    def connect(self, serial, package, script_path):
        pid = AdbDevice.get_pid(serial, package)
        if not pid:
            subprocess.run(
                ["adb", "-s", serial, "shell", "monkey", "-p", package, "1"],
                timeout=15, capture_output=True,
            )
            time.sleep(5)
            pid = AdbDevice.get_pid(serial, package)
        if not pid:
            raise RuntimeError(f"App {package} not running on {serial}")
        self._cli.attach(pid, script_path)

    def send_message(self, uid, text, timeout=5.0):
        return self._cli.send_message(uid, text, timeout)

    def capture_key(self, timeout=30.0):
        return self._cli.capture_key(timeout)

    def disconnect(self):
        self._cli.disconnect()

    def is_running(self):
        return self._cli.is_running
