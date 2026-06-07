"""Frida CLI wrapper — WSL-aware subprocess management for Frida CLI tool"""
import subprocess
import sys
from pathlib import Path


class FridaCLI:
    """Manage Frida CLI subprocess with WSL auto-detection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27042):
        self.host = host
        self.port = port
        self._proc = None

    @staticmethod
    def wsl_available() -> bool:
        try:
            r = subprocess.run(
                ["wsl", "echo", "ok"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _get_wsl_host_ip() -> str:
        """Return Windows host IP accessible from WSL."""
        try:
            r = subprocess.run(
                ["wsl", "cat", "/etc/resolv.conf"],
                capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if line.startswith("nameserver"):
                    return line.split()[1]
        except Exception:
            pass
        return "127.0.0.1"

    @staticmethod
    def _to_wsl_path(win_path: str) -> str:
        """Convert Windows path to WSL path: C:\\tmp\\x.js -> /mnt/c/tmp/x.js"""
        p = Path(win_path)
        drive = p.drive.lower().rstrip(":")
        parts = p.parts[1:]
        return "/mnt/" + drive + "/" + "/".join(parts)

    def launch(self, pid: int, script_path: str) -> subprocess.Popen:
        """Launch Frida CLI and attach to pid, inject script. Returns Popen."""
        use_wsl = sys.platform == "win32" and self.wsl_available()

        if use_wsl:
            wsl_path = self._to_wsl_path(script_path)
            host_ip = self._get_wsl_host_ip()
            cmd = f"wsl frida -H {host_ip}:{self.port} -p {pid} -l {wsl_path}"
            print(f"[frida_cli] Using WSL: {cmd}")
        else:
            cmd = f'frida -H {self.host}:{self.port} -p {pid} -l "{script_path}"'

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=True,
        )
        self._proc = proc
        return proc

    def kill(self):
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


def build_frida_command(host: str, port: int, pid: int, script_path: str) -> str:
    """Return the appropriate frida command string for the current environment."""
    use_wsl = sys.platform == "win32" and FridaCLI.wsl_available()
    if use_wsl:
        wsl_path = FridaCLI._to_wsl_path(script_path)
        host_ip = FridaCLI._get_wsl_host_ip()
        return f"wsl frida -H {host_ip}:{port} -p {pid} -l {wsl_path}"
    return f'frida -H {host}:{port} -p {pid} -l "{script_path}"'
