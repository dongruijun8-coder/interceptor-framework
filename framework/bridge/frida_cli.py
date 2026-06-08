"""Frida CLI 会话管理 — 统一的 Frida CLI 生命周期管理。

设计原则:
- 一个 FridaCliSession = 一次 attach 的 CLI 进程
- 只通过 stdin/stdout 与 Frida 交互，不依赖 Python frida binding
- 独立于 AES 加密、RPC 消息等上层逻辑
"""

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


def find_frida_binary() -> str | None:
    """统一搜索 frida CLI。

    结果缓存，后续调用直接返回。
    """
    if find_frida_binary._cached:
        return find_frida_binary._cached
    import shutil

    # 1. PATH
    for name in ("frida.exe", "frida"):
        p = shutil.which(name)
        if p:
            find_frida_binary._cached = p
            return p

    # 2. 用户 Python Scripts（pip --user 安装目标，含 %APPDATA%）
    import os as _os
    appdata = _os.environ.get("APPDATA", "")
    if appdata:
        for ver in ("Python314", "Python313", "Python312", "Python311", "Python310"):
            d = Path(appdata) / "Python" / ver / "Scripts"
            for name in ("frida.exe", "frida"):
                c = d / name
                if c.exists():
                    find_frida_binary._cached = str(c)
                    return str(c)

    # 3. 当前 Python Scripts（可能和 site-packages 不同）
    scripts_dir = Path(sys.executable).parent / "Scripts"
    for name in ("frida.exe", "frida"):
        c = scripts_dir / name
        if c.exists():
            find_frida_binary._cached = str(c)
            return str(c)

    # 4. pip show 定位 site-packages → 反推 Scripts
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "show", "frida-tools"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.split("\n"):
            if line.startswith("Location:"):
                loc = line.split(":", 1)[1].strip()
                # site-packages → Python314 → Scripts
                scripts = Path(loc) / ".." / "Scripts"
                for name in ("frida.exe", "frida"):
                    c = (scripts / name).resolve()
                    if c.exists():
                        find_frida_binary._cached = str(c)
                        return str(c)
    except Exception:
        pass

    return None


find_frida_binary._cached = None


class FridaCliSession:
    """Frida CLI 子进程会话。

    用法::

        cli = FridaCliSession(host="127.0.0.1", port=27042)
        cli.attach(pid, script_path)
        key = cli.capture_key(timeout=30)  # 阻塞直到 KEY_JSON 出现
        cli.send_message("uid", "text")    # stdin → _sendMsg()
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 27042):
        self.host = host
        self.port = port
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue = queue.Queue()       # stdout 行队列
        self._msg_queue: queue.Queue = queue.Queue()   # [MSG_SENT] 专用队列
        self._reader_t: threading.Thread | None = None
        self._pid: int | None = None

    # ── attach ──

    def attach(self, pid: int, script_path: str | Path) -> subprocess.Popen:
        """启动 Frida CLI，附加到 PID，注入脚本。返回 Popen。"""
        frida_bin = find_frida_binary()
        if not frida_bin:
            raise FileNotFoundError(
                "找不到 frida CLI。请安装: pip install frida-tools")

        script_path = str(script_path)
        self._pid = pid

        # 构造命令：带引号包裹路径
        if " " in frida_bin:
            # 路径含空格（如 C:/Program Files/...）
            cmd = (f'"{frida_bin}" -H {self.host}:{self.port} '
                   f'-p {pid} -l "{script_path}"')
        else:
            cmd = (f'"{frida_bin}" -H {self.host}:{self.port} '
                   f'-p {pid} -l "{script_path}"')

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )
        self._start_reader()
        return self._proc

    def _start_reader(self):
        """后台线程读取 stdout，分发到行队列和消息队列。"""
        def _reader():
            try:
                for line in iter(self._proc.stdout.readline, ""):
                    self._lines.put(line)
                    if "[MSG_SENT]" in line:
                        self._msg_queue.put(line)
            except Exception:
                pass
            finally:
                self._lines.put(None)  # sentinel

        self._reader_t = threading.Thread(target=_reader, daemon=True)
        self._reader_t.start()

    # ── key capture ──

    def capture_key(self, timeout: float = 30.0,
                    tap_helper: str | None = None) -> dict | None:
        """阻塞等待 KEY_JSON，可选 tap 触发网络请求。

        Returns:
            {"key_hex": ..., "iv_hex": ..., "headers": {...}} or None
        """
        deadline = time.time() + timeout
        hooks_ready = False
        tapped = False

        while time.time() < deadline:
            try:
                line = self._lines.get(timeout=0.5)
            except queue.Empty:
                if self._proc and self._proc.poll() is not None:
                    break
                continue

            if line is None:
                break
            line = line.strip()
            if not line:
                continue

            # 检测 hooks 就绪
            if not hooks_ready and ("Ready" in line or "Hooks installed" in line):
                hooks_ready = True
                print("[frida_cli] Hooks ready")

            # tap 触发（给串号走 adb input tap）
            if hooks_ready and not tapped and tap_helper:
                tapped = True
                self._tap(tap_helper)

            # 解析 KEY_JSON
            if "KEY_JSON:" in line:
                try:
                    return json.loads(line.split("KEY_JSON: ", 1)[1])
                except json.JSONDecodeError:
                    pass
                except Exception:
                    pass

        return None

    @staticmethod
    def _tap(serial: str):
        """adb tap 模拟点击，触发 App 网络请求。"""
        import subprocess as _sp
        for i in range(5):
            _sp.run(
                ["adb", "-s", serial, "shell", "input", "tap",
                 str(400 + i * 40), str(500 + i * 20)],
                timeout=5, capture_output=True,
            )
            time.sleep(0.5)

    # ── messaging ──

    def send_message(self, uid: str, text: str, timeout: float = 5.0) -> dict:
        """通过 CLI stdin 发送 _sendMsg(uid, text)，等待 [MSG_SENT] 确认。

        Fire-and-forget 模式：写入 stdin 后等 1s 快速确认。
        超时也返回 success（RongCloud SDK 异步投递已开始）。
        """
        if not self.is_running:
            return {"success": False, "error": "Frida CLI 未运行"}

        # 清理旧消息
        while not self._msg_queue.empty():
            try:
                self._msg_queue.get_nowait()
            except queue.Empty:
                break

        try:
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            cmd = f'_sendMsg("{uid}", "{escaped}");\n'
            self._proc.stdin.write(cmd)
            self._proc.stdin.flush()
        except (OSError, BrokenPipeError) as e:
            return {"success": False, "error": f"stdin 写入失败: {e}"}

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._msg_queue.get(timeout=1.0)
            except queue.Empty:
                if not self.is_running:
                    return {"success": False, "error": "Frida CLI 已退出"}
                continue
            if "[MSG_SENT]" in line:
                try:
                    return json.loads(line.split("[MSG_SENT] ", 1)[1])
                except (json.JSONDecodeError, ValueError):
                    return {"success": True, "error": ""}
        # Timeout — fire-and-forget, message already queued to RongCloud
        return {"success": True, "error": ""}

    # ── lifecycle ──

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def disconnect(self):
        """清理 CLI 进程。"""
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
