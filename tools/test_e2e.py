"""sybl E2E — cached key + token directly, no login"""
import json, sys, io
sys.path.insert(0, "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架")

# Fix Windows GBK print
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from framework.core.base_client import BaseClient

# Force working directory for BaseClient path resolution
import os
os.chdir("d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架/apps/sybl")

APP_DIR = "d:/360MoveData/Users/DYWH/Desktop/截流框架/截流框架/apps/sybl"
client = BaseClient(APP_DIR + "/config.json")
client._load_runtime()

# Inject cached key + headers from last_key.json
cache = json.loads(open(APP_DIR + "/.state/last_key.json", encoding="utf-8").read())
print(f"Cache: key={cache['key_hex'][:20]}... pid={cache['pid']}")

from framework.core.processors.encryption.aes_cbc import AesCbcEncryption
enc = client._encryptor
enc._key = bytes.fromhex(cache["key_hex"])
enc._iv = bytes.fromhex(cache["iv_hex"])
enc._inject_headers(cache["headers"], client)
client._frida_authenticated = True

# Current PID check
from framework.bridge.adb_device import AdbDevice
pid = AdbDevice.get_pid("127.0.0.1:7555", "com.sybl.voiceroom")
print(f"Current PID: {pid}, cached PID: {cache['pid']}")

# Re-login with cached key (token expired after app crash)
print("\n=== Re-login ===")
# Force re-login: temporarily unset token + frida_authenticated
client._auth_token = ""
client._frida_authenticated = False
ok = client._auth_processor.authenticate(client)
print(f"Login ok: {ok}, token={client._auth_token[:20] if client._auth_token else 'N/A'}...")
print(f"Token header before: {client._default_headers.get('Token','')[:20]}...")
# FIX: update Token header too
client._default_headers["Token"] = client._auth_token
print(f"Token header after: {client._default_headers.get('Token','')[:20]}...")

# Test room list first — maybe need room data
print("\n=== Room list ===")
resp = client._post(client._base_url + "/UI/Room/Home/roomList", {"page": 1, "page_size": 5})
code = resp.get("code")
msg = resp.get("msg", resp.get("message", ""))
print(f"code={code} msg={msg}")
if code == 0:
    rooms = resp.get("data", {}).get("list", [])
    print(f"Rooms: {len(rooms)}")
    for r in rooms[:3]:
        print(f"  id={r.get('id')} name={r.get('name')} tag={r.get('tag')}")

    test_room = rooms[0] if rooms else {"id": 35239646}
    rid = test_room.get("id")

    print(f"\n=== Join room {rid} ===")
    resp = client._post(client._base_url + "/UI/User/joinRoom", {"roomId": rid, "password": ""})
    print(f"Join: code={resp.get('code')} msg={resp.get('msg','')}")

    # Try ranking with ALL param combos
    print(f"\n=== Ranking param matrix for room {rid} ===")
    tests = [
        {"room_id": rid, "mode": "rich", "rank_type": "day", "page": 1, "page_size": 5},
        {"room_id": str(rid), "mode": "rich", "rank_type": "day", "page": 1, "page_size": 5},
        {"room_id": rid, "mode": "rich", "period": "day", "page": 1, "page_size": 5},
        {"room_id": rid, "rank_type": "day", "page": 1, "page_size": 5},
        {"room_id": rid, "mode": "rich", "type": "day", "page": 1, "page_size": 5},
        {"room_id": rid, "page": 1, "page_size": 5},
    ]
    for i, body in enumerate(tests):
        resp = client._post(client._base_url + "/UI/Room/UserRank/list", body)
        c = resp.get("code")
        m = resp.get("msg", resp.get("message", ""))
        data = resp.get("data", {})
        print(f"  [{i+1}] {json.dumps(body)} => code={c} msg={m}")
        if c == 0:
            users = data.get("list", [])
            print(f"      Users: {len(users)}")
