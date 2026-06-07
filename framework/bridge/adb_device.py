"""ADB device discovery — runs 'adb devices -l' and parses output"""
import subprocess
from typing import Optional


class AdbDevice:
    """Represents one ADB device"""

    def __init__(self, serial: str, status: str = "device", model: str = "", android_version: str = ""):
        self.serial = serial
        self.status = status
        self.model = model
        self.android_version = android_version

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "status": self.status,
            "model": self.model,
            "android_version": self.android_version,
        }

    @staticmethod
    def list_devices(adb_path: str = "adb") -> list["AdbDevice"]:
        """Run 'adb devices -l' and parse into AdbDevice list"""
        try:
            result = subprocess.run(
                [adb_path, "devices", "-l"],
                capture_output=True, text=True, timeout=10
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        devices = []
        for line in result.stdout.strip().split("\n")[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            status = parts[1]

            model = ""
            for part in parts[2:]:
                if part.startswith("model:"):
                    model = part.split(":", 1)[1]
                    break

            android_version = ""
            if status == "device":
                try:
                    prop = subprocess.run(
                        [adb_path, "-s", serial, "shell", "getprop", "ro.build.version.release"],
                        capture_output=True, text=True, timeout=5
                    )
                    android_version = prop.stdout.strip()
                except Exception:
                    pass

            devices.append(AdbDevice(serial, status, model, android_version))

        return devices

    @staticmethod
    def get_pid(serial: str, package: str) -> Optional[int]:
        """Find process PID via adb shell ps -A.
        Works around NIS packers that hide processes from Frida."""
        try:
            raw = subprocess.check_output(
                ["adb", "-s", serial, "shell", "ps", "-A"],
                timeout=10, text=True, stderr=subprocess.DEVNULL,
            )
            for line in raw.splitlines():
                if package in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        try:
                            return int(parts[1])  # PID is 2nd column
                        except ValueError:
                            pass
        except Exception:
            pass
        return None
