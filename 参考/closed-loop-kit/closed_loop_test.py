"""
WeFun 完整闭环测试 (全 HTTP): 房间列表 → 房间在线用户 → 发私信
2026-06-06: 更新为 /api/app/im/v1/conversation/send 新格式 (从 mitmproxy 抓取)
"""
import json, time, uuid, sys, io
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

TOKEN = 'T2K2N5CQJgSjTSCFlPDEsWTUBgnItXq4_bWxAyaIIitBtJjo0DBopP7ccfuA7wtBZzBjr'
UID = 302207594
DID = '7700f25aec05ac8b'
SECRET = 'a3f7e4c2b9d8a1e5f6c7d8e9a0b1c2d3'
BASE = 'https://api.hiyaparty.com'

COMMON_BODY = {
    "h_m": UID, "token": TOKEN, "h_did": DID,
    "h_os": "32", "h_av": "11.2.6.2381", "h_app": "me-live",
    "h_ch": "office", "h_package": "com.pico.live",
}

# New base_params (from app capture)
BASE_PARAMS = {
    "product_name": "w_project",
    "app_type": "android",
    "package_name": "com.pico.live",
    "version_name": "11.2.6.2381",
    "version_code": 101861,
    "channel": "office",
    "device_language": "zh",
    "user_id": UID,
    "device_id": DID,
    "app_id": "68082pc",
}

HEADERS = {
    'Content-Type': 'application/json; charset=utf-8',
    'User-Agent': 'Mozilla/5.0 (Linux; Android 12; 24031PN0DC) Pico/11.2.6.2381',
    'Origin': 'https://m.hiyaparty.com',
    'X-Requested-With': 'com.pico.live',
}


def api(path, body, timeout=15):
    resp = requests.post(f'{BASE}{path}', headers=HEADERS, json=body, timeout=timeout)
    return resp.json()


def step1_room_list(kind_id=100002, sub_kind_id=None):
    """Step 1: 获取房间列表"""
    body = {**COMMON_BODY, "tab_id": 2, "kind_id": kind_id,
            "offset": "", "direction": "refresh"}
    if sub_kind_id:
        body["sub_kind_id"] = sub_kind_id
    result = api('/recommend_app/app/v1/roomlist', body)
    if result.get('ret') != 1:
        raise Exception(f"房间列表失败: {result.get('msg', result)}")
    return result['data']['list']


def step2_room_users(room_id):
    """Step 2: 获取房间在线用户 (HTTP)"""
    body = {**COMMON_BODY, "room_id": room_id}
    result = api('/room_v2/app/room/room_online_user', body)
    if result.get('ret') != 1:
        raise Exception(f"在线用户失败: {result.get('msg', result)}")
    return result['data']['list']


def step3_send_message(to_uid, content):
    """Step 3: 发私信 (/api/app/im/v1/conversation/send, sign 可选)"""
    conv_id = f"single_chat-{min(UID, to_uid)}-{max(UID, to_uid)}"
    ts_ms = int(time.time() * 1000)
    msg_uuid = str(uuid.uuid4())

    body = {
        "spec_conv": {"conv_type": "single_chat", "spec_conv_id": str(to_uid)},
        "msg": {
            "content_type": "text", "content": {"text": content},
            "interact_type": "user_msg", "client_msg_id": msg_uuid,
            "ct_millis": ts_ms, "from_user_id": str(UID),
            "to_user_id": str(to_uid), "conv_type": "single_chat",
            "page_from": "we_msg_page", "room_id": 0,
            "conv_id": conv_id, "biz_type": "chat",
        },
        "h_m": UID, "h_did": DID, "h_ts": ts_ms, "token": TOKEN,
        "base_params": BASE_PARAMS,
    }

    result = api('/api/app/im/v1/conversation/send', body)
    return result


def step2b_conversation_list(sign=None):
    """获取会话列表 (新 API)"""
    ts_ms = int(time.time() * 1000)
    body = {
        "display_group": "normal",
        "size": 50,
        "cursor": "",
        "h_m": UID,
        "h_did": DID,
        "h_ts": ts_ms,
        "token": TOKEN,
        "base_params": BASE_PARAMS,
    }
    path = '/api/app/im/v1/conversation_list/list'
    if sign:
        path += f'?sign={sign}'
    return api(path, body)


def main():
    print("=" * 60)
    print("WeFun 闭环测试: 房间列表 → 在线用户 → 发私信")
    print("=" * 60)

    # Step 1: Room list
    print("\n[1/3] 获取房间列表 (热门 kind_id=100002)...")
    rooms = step1_room_list(kind_id=100002)
    print(f"  ✅ 获取 {len(rooms)} 个房间")
    for r in rooms[:5]:
        print(f"  [{r['room_id']}] {r.get('title','?')} live={r.get('live_on','?')} mid={r.get('mid','?')}")

    live_rooms = [r for r in rooms if int(r.get('live_on', 0)) == 1]
    if not live_rooms:
        print("  ❌ 没有正在直播的房间")
        return
    target_room = live_rooms[0]
    room_id = target_room['room_id']
    print(f"\n  选择房间: [{room_id}] '{target_room.get('title','?')}' (主播:{target_room.get('mid','?')})")

    # Step 2: Room online users
    print(f"\n[2/3] 获取房间 {room_id} 在线用户...")
    users = step2_room_users(room_id)
    print(f"  ✅ 获取 {len(users)} 个在线用户")
    for u in users[:10]:
        print(f"  [{u['mid']}] {u['name']}")

    others = [u for u in users if str(u.get('mid')) != str(UID)]
    if not others:
        print("  ⚠️ 房间只有自己")
        return

    target_user = others[0]
    to_uid = target_user['mid']
    to_name = target_user['name']
    print(f"\n  选择目标: [{to_uid}] {to_name}")

    # Step 3: Send message
    print(f"\n[3/3] 发送私信给 {to_name} ...")
    stamp = int(time.time())
    result = step3_send_message(to_uid, f"Hello [{stamp}]")
    ret = result.get('ret')
    if ret == 1:
        msg_id = result.get('data', {}).get('msg_id', '?')
        print(f"  ✅ 发送成功! ret=1 msg_id={msg_id}")
    else:
        print(f"  ❌ 发送失败: ret={ret} msg={result.get('msg','?')}")

    print("\n" + "=" * 60)
    print(f"闭环: {room_id} → [{to_uid}]{to_name} → msg_id={msg_id}")
    print(f"链路: all_rooms ✅ → room_users ✅ → send_message {'✅' if ret==1 else '❌'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
