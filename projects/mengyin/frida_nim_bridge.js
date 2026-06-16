/*
 * 梦音 NIM SDK Bridge — Frida 脚本
 * 用法: frida -U -n com.qiyu.dream:core -l frida_nim_bridge.js
 *
 * 功能:
 *   1. 捕获 session 信息 (ticket, DeviceId, pub_sid)
 *   2. 通过 NIM SDK 发送 P2P 私信
 *   3. 反 Frida 检测绕过
 */

'use strict';

var MSG_SERVICE = null;
var SESSION_INFO = {};

// ── 反检测: 绕过 strstr("frida") ──
(function() {
    try {
        var strstr = Module.getExportByName(null, "strstr");
        Interceptor.attach(strstr, {
            onEnter: function(args) { this.needle = args[1]; },
            onLeave: function(retval) {
                try {
                    if (this.needle && !this.needle.isNull()) {
                        var needle = this.needle.readCString();
                        if (needle && /frida|gum-js|linjector/i.test(needle)) {
                            retval.replace(ptr(0));
                        }
                    }
                } catch(e) {}
            }
        });
        console.log("[bridge] anti-frida: OK");
    } catch(e) {
        console.log("[bridge] anti-frida skip: " + e.message);
    }
})();

// ── 获取 NIM MsgService ──
function findMsgService() {
    Java.perform(function() {
        try {
            var NIMClient = Java.use("com.netease.nimlib.sdk.NIMClient");
            var MsgServiceClass = Java.use("com.netease.nimlib.sdk.msg.MsgService");
            var service = NIMClient.getService(MsgServiceClass.class);
            if (service) {
                MSG_SERVICE = service;
                console.log("[bridge] MsgService: OK");
            }
        } catch(e) {
            console.log("[bridge] NIMClient.getService fail: " + e.message);
        }

        if (!MSG_SERVICE) {
            Java.choose("com.netease.nimlib.sdk.msg.MsgService", {
                onMatch: function(instance) { MSG_SERVICE = instance; },
                onComplete: function() {
                    if (MSG_SERVICE) console.log("[bridge] MsgService via choose: OK");
                }
            });
        }
    });
}

// ── 捕获 session 信息 ──
function captureSessionInfo() {
    Java.perform(function() {
        try {
            // 尝试读取 MMKV ticket
            var MMKV = Java.use("com.tencent.mmkv.MMKV");
            var kv = MMKV.defaultMMKV();
            if (kv) {
                var ticket = kv.decodeString("ticket", "");
                var uid = kv.decodeString("uid", "");
                if (ticket) SESSION_INFO.ticket = ticket;
                if (uid) SESSION_INFO.uid = uid;
            }
        } catch(e) {}

        try {
            // 读 SharedPreferences
            var ctx = Java.use("android.app.ActivityThread").currentApplication().getApplicationContext();
            var sp = ctx.getSharedPreferences("share_data.xml", 0);
            var all = sp.getAll();
            var iter = all.entrySet().iterator();
            while (iter.hasNext()) {
                var entry = iter.next();
                var key = String(entry.getKey());
                var val = String(entry.getValue());
                if (key.indexOf("ticket") >= 0) SESSION_INFO.ticket = val;
                if (key.indexOf("device") >= 0) SESSION_INFO.deviceId = val;
            }
        } catch(e) {}
    });

    if (SESSION_INFO.ticket) {
        console.log("[bridge] ticket: " + SESSION_INFO.ticket);
    }
}

// ── Hook OkHttp headers ──
function hookOkHttp() {
    Java.perform(function() {
        try {
            var Builder = Java.use("okhttp3.Request$Builder");
            var originalBuild = Builder.build;
            Builder.build.implementation = function() {
                var req = originalBuild.call(this);
                try {
                    var headers = req.headers();
                    var sid = headers.get("pub_sid");
                    var ticket = headers.get("pub_ticket");
                    var uid = headers.get("pub_uid");
                    if (sid) SESSION_INFO.pub_sid = sid;
                    if (ticket) SESSION_INFO.ticket = ticket;
                    if (uid) SESSION_INFO.uid = uid;
                } catch(e) {}
                return req;
            };
            console.log("[bridge] OkHttp hook: OK");
        } catch(e) {
            console.log("[bridge] OkHttp hook skip: " + e.message);
        }
    });
}

// ── 发送 P2P 私信 ──
function sendP2PMessage(targetUid, text) {
    return Java.perform(function() {
        try {
            if (!MSG_SERVICE) {
                try {
                    var NIMClient = Java.use("com.netease.nimlib.sdk.NIMClient");
                    var MsgServiceClass = Java.use("com.netease.nimlib.sdk.msg.MsgService");
                    MSG_SERVICE = NIMClient.getService(MsgServiceClass.class);
                } catch(e) {}
            }
            if (!MSG_SERVICE) {
                return JSON.stringify({success: false, error: "MsgService not available"});
            }

            var SessionTypeEnum = Java.use("com.netease.nimlib.sdk.msg.constant.SessionTypeEnum");
            var sessionType;
            var values = SessionTypeEnum.values();
            for (var i = 0; i < values.length; i++) {
                if (values[i].getValue() === 0) { sessionType = values[i]; break; }
            }
            if (!sessionType) {
                return JSON.stringify({success: false, error: "P2P SessionType not found"});
            }

            var MessageBuilder = Java.use("com.netease.nimlib.sdk.msg.MessageBuilder");
            var message = MessageBuilder.createTextMessage(
                String(targetUid), sessionType, String(text)
            );
            MSG_SERVICE.sendMessage(message, false);

            return JSON.stringify({success: true});
        } catch(e) {
            return JSON.stringify({success: false, error: e.message});
        }
    });
}

// ── RPC exports ──
rpc.exports = {
    getSessionInfo: function() {
        captureSessionInfo();
        return JSON.stringify(SESSION_INFO);
    },

    sendMessage: function(uid, text) {
        return sendP2PMessage(uid, text);
    },

    getStatus: function() {
        return JSON.stringify({
            msgServiceReady: MSG_SERVICE !== null,
            hasTicket: !!SESSION_INFO.ticket,
            hasPubSid: !!SESSION_INFO.pub_sid
        });
    }
};

// ── 自动初始化 ──
setTimeout(function() {
    Java.perform(function() {
        hookOkHttp();
        findMsgService();
        captureSessionInfo();
        console.log("[bridge] init done. msgService=" + (MSG_SERVICE !== null) +
                    " ticket=" + !!SESSION_INFO.ticket);
    });
}, 3000);

console.log("[bridge] 梦音 NIM bridge loaded");
