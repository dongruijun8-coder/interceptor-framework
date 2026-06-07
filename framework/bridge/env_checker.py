"""EnvChecker — probe device environment: ADB, Frida server, NIS detection, platform"""
import os
import subprocess
import sys

try:
    import frida
    HAS_FRIDA_PYTHON = True
except ImportError:
    HAS_FRIDA_PYTHON = False


class EnvChecker:
    """Probe the target device + platform and return a structured health report."""

    @classmethod
    def probe(cls, serial: str, package: str, frida_host: str = "127.0.0.1",
              frida_port: int = 27042) -> dict:
        result = {
            "adb": cls._check_adb(serial),
            "frida_server": cls._check_frida_server(frida_host, frida_port),
            "app": cls._check_app(serial, package),
            "nis": {},
            "platform": cls._check_platform(),
            "overall": "ok",
            "warnings": [],
            "recommendations": [],
        }

        if result["nis"].get("detected"):
            result["warnings"].append("NIS/360 保护检测到进程隐藏，Frida CLI 模式将自动启用")
            result["recommendations"].append("cli_mode")

        if result["platform"]["pipe_risk"]:
            result["warnings"].append(
                "Windows 环境 subprocess 管道可能不稳定。建议配置 WSL 或使用 adb pull 备选方案")
            result["recommendations"].append("configure_wsl_or_file_transfer")

        if result["platform"].get("chinese_path"):
            result["warnings"].append(
                f"项目路径包含中文字符: {result['platform']['cwd']}。"
                f"建议迁移到纯 ASCII 路径 (如 C:\\projects\\)")
            result["recommendations"].append("move_to_ascii_path")

        if result["warnings"]:
            result["overall"] = "ok_with_warnings"
        if not result["adb"]["ok"] or not result["frida_server"]["ok"]:
            result["overall"] = "error"

        return result

    @classmethod
    def _check_adb(cls, serial: str) -> dict:
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            if serial in r.stdout and "device" in r.stdout:
                return {"ok": True, "serial": serial}
        except Exception:
            pass
        return {"ok": False, "serial": serial, "error": "ADB 未连接或设备未识别"}

    @classmethod
    def _check_frida_server(cls, host: str, port: int) -> dict:
        try:
            r = subprocess.run(
                ["frida-ps", "-H", f"{host}:{port}"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                server_type = "hluda (port 27042)" if port == 27042 else "frida-server"
                return {"ok": True, "host": host, "port": port, "type": server_type}
            return {"ok": False, "error": f"frida-ps 返回非零: {r.stderr[:100]}"}
        except FileNotFoundError:
            return {"ok": False, "error": "frida CLI 未安装或不在 PATH 中"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @classmethod
    def _check_app(cls, serial: str, package: str) -> dict:
        from framework.bridge.adb_device import AdbDevice
        pid = AdbDevice.get_pid(serial, package)
        if pid:
            return {"ok": True, "pid": pid, "package": package}
        return {"ok": False, "pid": None, "package": package,
                "error": f"App {package} 未运行"}

    @classmethod
    def _check_nis(cls, serial: str, package: str, host: str, port: int) -> dict:
        if not HAS_FRIDA_PYTHON:
            return {"detected": False, "note": "frida Python 库未安装"}

        try:
            from framework.bridge.adb_device import AdbDevice
            adb_pid = AdbDevice.get_pid(serial, package)
            if not adb_pid:
                return {"detected": False, "note": "App 未运行"}

            dm = frida.get_device_manager()
            try:
                dev = dm.get_device(serial)
            except Exception:
                try:
                    dev = dm.get_usb_device()
                except Exception:
                    return {"detected": False, "note": "Frida 连接失败"}

            found = False
            for p in dev.enumerate_processes():
                if (package.lower() in (p.name or "").lower()
                        or str(adb_pid) == str(p.pid)):
                    found = True
                    break

            if not found:
                return {
                    "detected": True,
                    "pid_hidden": True,
                    "pid_via_adb": adb_pid,
                    "recommendation": (
                        "进程被 NIS/360 隐藏。用 Frida CLI subprocess。"
                        "使用 SecretKeySpec 而非 Cipher.init hook。"
                    ),
                }
            return {"detected": False}
        except Exception as e:
            return {"detected": False, "error": str(e)}

    @classmethod
    def _check_platform(cls) -> dict:
        info = {
            "os": sys.platform,
            "pipe_risk": sys.platform == "win32",
            "wsl_available": False,
            "cwd": os.getcwd(),
            "chinese_path": False,
        }

        if any('一' <= c <= '鿿' for c in info["cwd"]):
            info["chinese_path"] = True

        if sys.platform == "win32":
            try:
                r = subprocess.run(
                    ["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    info["wsl_available"] = True
            except Exception:
                pass

        return info
