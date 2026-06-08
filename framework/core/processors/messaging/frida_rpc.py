"""Frida RPC 私信 — 统一走 CLI stdin（NIS bypass 通用方案）"""
import json
import time

from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry
from framework.bridge.frida_cli import FridaCliSession, find_frida_binary
from framework.bridge.frida_session import FridaDisconnectedError


class FridaRpcMessaging(MessagingProcessor):
    name = "frida-rpc"

    @classmethod
    def params_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "script_name": {
                    "type": "string",
                    "description": "Frida JS 脚本文件名（位于 app 目录下）",
                    "default": "hook_send_msg.js",
                },
            },
        }

    def send(self, client, uid: str, text: str) -> dict:
        # 1. Try Python Frida binding session (non-NIS apps: hifun, etc.)
        session = getattr(client, '_frida_session', None)
        if session is not None and session.is_connected:
            try:
                return session.send_message(uid, text)
            except FridaDisconnectedError:
                raise

        # 2. Frida CLI session (NIS-bypass: sybl)
        cli = getattr(client, '_frida_cli_session', None)
        if cli is not None:
            # Re-attach if CLI died
            if not cli.is_running:
                print("[frida-rpc] CLI process dead, re-launching...")
                rt = client._load_runtime()
                dev = rt.get("device", {})
                serial = dev.get("serial", "")
                package = dev.get("app_package",
                                  client.config.get("meta", {}).get("package", ""))
                if serial and package:
                    from framework.bridge.adb_device import AdbDevice
                    pid = AdbDevice.get_pid(serial, package)
                    if pid:
                        script_path = client.config_path.parent / "bridge_cli.js"
                        if not script_path.exists():
                            script_path = client.config_path.parent / "frida_key_bridge.js"
                        if script_path.exists():
                            try:
                                cli.attach(pid, script_path)
                            except FileNotFoundError:
                                return {"success": False, "error": "找不到 frida CLI"}
            return cli.send_message(uid, text)

        return {"success": False, "error": "Frida 会话未初始化 — 请先扫描房间获取密钥"}

    def validate(self, client) -> tuple:
        warnings = []
        script = self.params.get("script_name", "")
        if not script:
            warnings.append("frida-rpc 缺少 script_name")
        else:
            sp = client.config_path.parent / script
            if not sp.exists():
                warnings.append(f"frida-rpc 脚本 {script} 不存在")
        return len(warnings) == 0, warnings


ProcessorRegistry.register(FridaRpcMessaging)
