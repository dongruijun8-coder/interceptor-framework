#!/usr/bin/env python
"""Send private messages via hifun TencentIM SDK using Frida RPC.

Usage:
    python im_send.py <target_user_id> <message_text>
    python im_send.py 1130356 "哥哥在吗"
    python im_send.py --list-contacts
    python im_send.py --interactive

Requirements:
    - frida 16.5.9 (Python package)
    - frida-server-16 running on device
    - hifun app installed and running on device
"""

import frida
import sys
import time
import json
import logging
import os.path

logging.getLogger('frida').setLevel(logging.WARNING)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(SCRIPT_DIR, 'hooks', 'im_rpc.js')
PACKAGE = 'chat.hifun.android'
WAIT_IM_READY = 30   # max seconds to wait for IM SDK login
WAIT_SEND = 15       # max seconds to wait for send result
POLL_INTERVAL = 0.5
DEFAULT_MESSAGE = "哥哥在吗"


class ImClient:
    """Frida-backed TencentIM client for hifun."""

    def __init__(self):
        self.device = frida.get_usb_device()
        self.session = None
        self.script = None
        self._pid = None

    def connect(self, spawn=True, pid=None):
        """Connect to app's IM SDK.

        Args:
            spawn: If True, spawn fresh process. If False, attach to running app.
            pid: Specific PID to attach to (overrides spawn).
        """
        if pid:
            print(f"[*] Attaching to PID {pid}...", flush=True)
            self._pid = pid
            self.session = self.device.attach(pid)
        elif spawn:
            print(f"[*] Spawning {PACKAGE}...", flush=True)
            self._pid = self.device.spawn([PACKAGE])
            self.session = self.device.attach(self._pid)
        else:
            # Try package name first, then find by PID
            try:
                self.session = self.device.attach(PACKAGE)
            except frida.ProcessNotFoundError:
                # Process name might be garbled (Chinese app names)
                # Find by enumerating
                for proc in self.device.enumerate_processes():
                    if proc.pid > 100 and 'hifun' in proc.name.lower():
                        self.session = self.device.attach(proc.pid)
                        self._pid = proc.pid
                        print(f"[*] Found process: {proc.name} (PID={proc.pid})", flush=True)
                        break
                else:
                    raise frida.ProcessNotFoundError(f"cannot find process for {PACKAGE}")
            print(f"[*] Attaching to {PACKAGE}...", flush=True)

        with open(HOOK_SCRIPT, 'r', encoding='utf-8') as f:
            code = f.read()

        self.script = self.session.create_script(code)

        def on_msg(msg, data):
            """Forward JS console.log to Python stdout."""
            if msg.get('type') == 'send':
                payload = msg.get('payload', '')
                # Filter out noisy Frida internals, keep [IM] logs
                if '[IM]' in str(payload):
                    print(f"  {payload}", flush=True)
            elif msg.get('type') == 'error':
                desc = str(msg.get('description', '')[:300])
                # Don't print Frida framework errors unless relevant
                if desc and 'Script' not in desc and 'Error: ' not in desc[:10]:
                    print(f"[!] JS: {desc}", flush=True)

        self.script.on('message', on_msg)
        self.script.load()

        if spawn:
            self.device.resume(self._pid)

        print(f"[*] Waiting for IM SDK (up to {WAIT_IM_READY}s)...", flush=True)

        waited = 0
        while waited < WAIT_IM_READY * 2:
            time.sleep(0.5)
            waited += 1
            try:
                if self.script.exports_sync.is_ready():
                    user = self.script.exports_sync.get_login_user()
                    print(f"[+] IM Ready! LoginUser={user}", flush=True)
                    return user
            except frida.core.RPCException:
                # RPC not installed yet, keep waiting
                pass
            except Exception as e:
                print(f"[*] Wait error: {e}", flush=True)

        raise RuntimeError(f"IM SDK init timeout after {WAIT_IM_READY}s")

    def send_text(self, target_id: str, text: str, wait: bool = True) -> dict:
        """Send a text message. Returns result dict."""
        if not target_id or not text:
            return {'success': False, 'error': 'target_id and text required'}

        # Queue send via RPC
        try:
            raw = self.script.exports_sync.send_text(target_id, text)
        except Exception as e:
            # Fallback: try camelCase name
            try:
                raw = self.script.exports_sync.sendText(target_id, text)
            except Exception:
                return {'success': False, 'error': f'RPC call failed: {e}'}

        try:
            queued = json.loads(raw)
        except json.JSONDecodeError as e:
            return {'success': False, 'error': f'Invalid JSON response: {raw[:100]}', 'raw': raw}

        if not queued.get('queued'):
            error_info = queued.get('error', 'unknown error')
            # Include debug info if available
            extra = ''
            if queued.get('existingKeys'):
                extra = f" | existing keys: {queued['existingKeys']}"
            return {'success': False, 'error': f'{error_info}{extra}'}

        pending_id = queued.get('pendingMsgId', '?')
        print(f"[*] Queued: {pending_id}", flush=True)

        if not wait:
            return queued

        # Poll for result
        key = queued['key']
        print(f"[*] Waiting for callback (up to {WAIT_SEND}s)...", flush=True)
        waited = 0
        while waited < WAIT_SEND * 2:
            time.sleep(POLL_INTERVAL)
            waited += 1
            try:
                raw = self.script.exports_sync.poll_result(key)
            except Exception:
                # Fallback: try camelCase
                try:
                    raw = self.script.exports_sync.pollResult(key)
                except Exception as e:
                    return {'success': False, 'error': f'poll RPC failed: {e}'}

            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Success/failure determined by the callback
            success_val = result.get('success')
            if success_val is True or success_val == 'true' or success_val == True:
                print(f"[+] SENT! msgId={result.get('msgId', '?')}", flush=True)
                return {'success': True, 'msgId': result.get('msgId', '')}
            elif success_val is False or success_val == 'false' or success_val == False:
                print(f"[-] FAILED: {result.get('error', 'unknown')}", flush=True)
                return {'success': False, 'error': result.get('error', 'unknown')}
            elif result.get('error'):
                # Non-terminal error (e.g. "key not found" mid-poll)
                if 'key not found' in str(result.get('error', '')):
                    # This is a real error — the result was never stored
                    keys = result.get('existingKeys', result.get('allKeys', []))
                    print(f"[-] Key not found: {key}. Have: {keys}", flush=True)
                    return {'success': False, 'error': f'key not found: {key}'}
                # Other errors: retry
                if waited % 4 == 0:
                    print(f"[*] Poll: {result.get('error')}", flush=True)

        return {'success': False, 'error': 'send timeout'}

    def list_contacts(self) -> list:
        """Get recent conversations."""
        try:
            raw = self.script.exports_sync.get_conversations()
        except Exception:
            try:
                raw = self.script.exports_sync.getConversations()
            except Exception as e:
                return [{'error': str(e)}]

        contacts = []
        for item in raw:
            try:
                contacts.append(json.loads(item) if isinstance(item, str) else item)
            except json.JSONDecodeError:
                contacts.append(item)
        return contacts

    def disconnect(self):
        """Clean up Frida session."""
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass
        print("[*] Disconnected", flush=True)


def cmd_list_contacts(pid=None):
    """List recent IM contacts."""
    client = ImClient()
    try:
        client.connect(spawn=(pid is None), pid=pid)
        print("\n[*] Fetching contacts...", flush=True)
        contacts = client.list_contacts()
        print(f"\n{'='*50}")
        print(f"{'UserID':<12} {'Name':<20} {'Unread':>6}  Last Message")
        print(f"{'='*50}")
        for c in contacts:
            if isinstance(c, dict) and 'userId' in c:
                uid = c.get('userId', '?')[:10]
                name = c.get('showName', '?')[:18]
                unread = c.get('unread', 0)
                last = c.get('lastMsg', '')[:40]
                print(f"{uid:<12} {name:<20} {unread:>6}  {last}")
            else:
                print(f"  {c}")
        print(f"{'='*50}")
        print(f"Total: {len(contacts)} conversations")
    finally:
        client.disconnect()


def cmd_send(target_id: str, text: str, pid=None):
    """Send a message and wait for result."""
    client = ImClient()
    try:
        client.connect(spawn=(pid is None), pid=pid)
        print(f"\n[*] Sending to {target_id}: \"{text}\"", flush=True)
        result = client.send_text(target_id, text, wait=True)
        print(f"\n{'='*40}")
        print(f"Result: {'SUCCESS' if result.get('success') else 'FAILED'}")
        if result.get('msgId'):
            print(f"  msgId: {result['msgId']}")
        if result.get('error'):
            print(f"  error: {result['error']}")
        print(f"{'='*40}")
    finally:
        client.disconnect()


def cmd_interactive(pid=None):
    """Interactive mode: pick contact and send message."""
    client = ImClient()
    try:
        client.connect(spawn=(pid is None), pid=pid)

        print("\n[*] Fetching contacts...", flush=True)
        contacts = client.list_contacts()
        c2c = [c for c in contacts if isinstance(c, dict) and c.get('userId') and c.get('type') == 0]

        if not c2c:
            print("[!] No C2C conversations found. Enter target user ID manually.")
            target = input("Target user ID: ").strip()
        else:
            print(f"\nRecent C2C chats:")
            for i, c in enumerate(c2c[:20]):
                print(f"  [{i}] {c.get('showName', '?')} (uid={c.get('userId', '?')})")
            choice = input(f"\nPick [0-{min(len(c2c)-1, 19)}] or enter user ID: ").strip()
            try:
                idx = int(choice)
                target = c2c[idx]['userId']
            except (ValueError, IndexError):
                target = choice

        text = input(f"Message [{DEFAULT_MESSAGE}]: ").strip()
        if not text:
            text = DEFAULT_MESSAGE

        print(f"\n[*] Sending to {target}: \"{text}\"", flush=True)
        result = client.send_text(target, text, wait=True)
        print(f"\nResult: {'SUCCESS' if result.get('success') else 'FAILED'}")
        if result.get('msgId'):
            print(f"  msgId: {result['msgId']}")
        if result.get('error'):
            print(f"  error: {result['error']}")
    finally:
        client.disconnect()


if __name__ == '__main__':
    # Parse flags
    pid = None
    args = sys.argv[1:]

    # Handle --pid N
    for flag in ('--pid', '-p'):
        if flag in args:
            idx = args.index(flag)
            pid = int(args[idx + 1])
            args = args[:idx] + args[idx + 2:]
            break

    # Handle --attach / -a (attach to running app, not spawn)
    if '--attach' in args or '-a' in args:
        if not pid:
            # Auto-find hifun process
            import frida as _frida
            device = _frida.get_usb_device()
            for proc in device.enumerate_processes():
                if 'hifun' in proc.name.lower():
                    pid = proc.pid
                    print(f"[*] Auto-found: {proc.name} (PID={pid})", flush=True)
                    break
            else:
                print("[-] Cannot find running hifun process. Use --pid N")
                sys.exit(1)
        # Remove --attach from args
        args = [a for a in args if a not in ('--attach', '-a')]

    sys.argv = [sys.argv[0]] + args

    if '--list-contacts' in args or '-l' in args:
        cmd_list_contacts(pid=pid)
    elif '--interactive' in args or '-i' in args:
        cmd_interactive(pid=pid)
    elif len(args) >= 2:
        target_id = args[0]
        text = ' '.join(args[1:])
        cmd_send(target_id, text, pid=pid)
    else:
        print(__doc__)
        print(f"\nExamples:")
        print(f"  python im_send.py 1130356 {DEFAULT_MESSAGE}")
        print(f"  python im_send.py --list-contacts")
        print(f"  python im_send.py --interactive")
        print(f"  python im_send.py --attach 1130356 {DEFAULT_MESSAGE}")
        print(f"  python im_send.py --pid 3992 --list-contacts")
        sys.exit(1)
