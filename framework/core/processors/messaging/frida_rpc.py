"""Frida RPC 私信 — Python binding (non-NIS) or CLI stdin (NIS bypass)"""
import json
import time

from ..base import MessagingProcessor
from ...processor_registry import ProcessorRegistry
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
        # 1. Try Python Frida binding session (non-NIS apps)
        session = getattr(client, '_frida_session', None)
        if session is not None and session.is_connected:
            try:
                return session.send_message(uid, text)
            except FridaDisconnectedError:
                raise

        # 2. Fallback: Frida CLI stdin (NIS-bypass for sybl etc.)
        cli_proc = getattr(client, '_frida_cli_proc', None)
        if cli_proc is not None:
            # Re-launch if process died
            if cli_proc.poll() is not None:
                print("[frida-rpc] CLI process dead, re-launching...")
                from framework.core.processors.encryption.aes_cbc import AesCbcEncryption
                # Re-launch via _derive_from_frida's launch helper
                # Quick: just call _launch_frida_cli
                script_path = client.config_path.parent / "bridge_cli.js"
                if not script_path.exists():
                    script_path = client.config_path.parent / "frida_key_bridge.js"
                if script_path.exists():
                    rt = client._load_runtime()
                    dev = rt.get("device", {})
                    serial = dev.get("serial", "")
                    from framework.bridge.adb_device import AdbDevice
                    pid = AdbDevice.get_pid(serial,
                        dev.get("app_package", client.config.get("meta",{}).get("package","")))
                    if pid:
                        new_proc = client._encryptor._launch_frida_cli(pid, script_path)
                        if new_proc:
                            client._encryptor._start_cli_monitor(new_proc, client)
                            cli_proc = new_proc

            if cli_proc is not None and cli_proc.poll() is None:
                return self._send_via_cli(client, uid, text)

        return {"success": False, "error": "Frida 会话未初始化 — 请先扫描房间获取密钥"}

    def _send_via_cli(self, client, uid: str, text: str) -> dict:
        """Actual implementation: write to CLI stdin, read from msg_queue."""
        import queue
        proc = client._frida_cli_proc
        msg_queue = getattr(client, '_frida_msg_queue', None)
        if msg_queue is None:
            return {"success": False, "error": "Frida CLI 消息队列未就绪"}

        try:
            escaped = text.replace('\\', '\\\\').replace('"', '\\"')
            cmd = f'_sendMsg("{uid}", "{escaped}");\n'
            proc.stdin.write(cmd)
            proc.stdin.flush()
        except Exception as e:
            return {"success": False, "error": f"CLI stdin 写入失败: {e}"}

        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                line = msg_queue.get(timeout=1.0)
            except queue.Empty:
                if proc.poll() is not None:
                    return {"success": False, "error": "Frida CLI 已退出"}
                continue
            if "[MSG_SENT]" in line:
                try:
                    return json.loads(line.split("[MSG_SENT] ", 1)[1])
                except json.JSONDecodeError:
                    return {"success": True, "error": ""}
        return {"success": False, "error": "CLI 消息发送超时"}

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
