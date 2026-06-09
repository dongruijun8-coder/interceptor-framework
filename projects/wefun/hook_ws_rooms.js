/** WeFun 房间发现 — 多路径全量拦截

 * 1. Gson.fromJson — 拦截 JSON 反序列化
 * 2. OkHttp ResponseBody.string/source — 拦截所有 HTTP 响应正文
 * 3. OkHttp WebSocket — 拦截 WS 消息
 * 4. 通用：解析所有捕获到的 JSON，扫描房间数据

 * RPC: getRooms() → JSON [{id, name, cover, member_cnt}]
 */

var rooms = {};
var roomCount = 0;

function tryAddRoom(obj) {
    if (!obj || typeof obj !== 'object') return;
    var id = obj.room_id || obj.roomId || obj.id || obj.rid;
    if (!id) return;
    id = String(id);
    var name = obj.room_name || obj.roomName || obj.title || obj.name || obj.room_title;
    if (!name) return;
    if (rooms[id]) return;
    rooms[id] = {
        id: id,
        name: String(name),
        cover: String(obj.cover || obj.room_cover || obj.avatar || ""),
        member_cnt: Number(obj.member_cnt || obj.online_count || obj.members || 0)
    };
    roomCount++;
    console.log("[Room#" + roomCount + "] " + id + " = " + name);
}

function scanJson(text) {
    if (!text || text.length < 10) return;
    try {
        var obj = JSON.parse(text);
        scanObj(obj, 0);
    } catch(e) {}
}

function scanObj(obj, depth) {
    if (!obj || depth > 6) return;
    // Direct hit
    if (typeof obj === 'object' && (obj.room_id || obj.roomId)) { tryAddRoom(obj); depth++; }
    // Array
    if (Array.isArray(obj)) {
        for (var i = 0; i < obj.length && i < 200; i++) scanObj(obj[i], depth + 1);
        return;
    }
    if (typeof obj !== 'object') return;
    // Scan all values
    var keys = Object.keys(obj);
    for (var k = 0; k < keys.length; k++) {
        try { scanObj(obj[keys[k]], depth + 1); } catch(e) {}
    }
}

// ═══ 1. OkHttp ResponseBody — catch ALL HTTP responses ═══
Java.perform(function() {
    try {
        var ResponseBody = Java.use("okhttp3.ResponseBody");
        // Hook string() — most common
        ResponseBody.string.implementation = function() {
            var s = this.string();
            try {
                if (s && s.length > 20) {
                    scanJson(s);
                }
            } catch(e) {}
            return s;
        };
        console.log("[Room Hook] OkHttp ResponseBody.string hooked");
    } catch(e) {
        console.log("[Room Hook] ResponseBody failed: " + e);
    }
});

// ═══ 2. Gson — catch JSON deserialization ═══
Java.perform(function() {
    try {
        var Gson = Java.use("com.google.gson.Gson");
        Gson.fromJson.overload('java.lang.String', 'java.lang.Class').implementation = function(json, cls) {
            var r = this.fromJson(json, cls);
            scanJson(json);
            return r;
        };
        Gson.fromJson.overload('java.lang.String', 'java.lang.reflect.Type').implementation = function(json, type) {
            var r = this.fromJson(json, type);
            scanJson(json);
            return r;
        };
        console.log("[Room Hook] Gson hooked");
    } catch(e) {}
});

// ═══ 3. WebSocket ═══
Java.perform(function() {
    try {
        var WSL = Java.use("okhttp3.WebSocketListener");
        WSL.onMessage.overload('okhttp3.WebSocket', 'java.lang.String').implementation = function(ws, text) {
            scanJson(text);
            this.onMessage(ws, text);
        };
        try {
            WSL.onMessage.overload('okhttp3.WebSocket', 'okio.ByteString').implementation = function(ws, bytes) {
                scanJson(bytes.utf8());
                this.onMessage(ws, bytes);
            };
        } catch(e) {}
        console.log("[Room Hook] WebSocket hooked");
    } catch(e) {}
});

// ═══ RPC ═══
rpc.exports = {
    getRooms: function() {
        var result = [];
        for (var k in rooms) {
            if (rooms.hasOwnProperty(k)) result.push(rooms[k]);
        }
        return JSON.stringify(result);
    },
    getRoomCount: function() {
        return roomCount;
    },
    clearRooms: function() {
        rooms = {};
        roomCount = 0;
        return "ok";
    }
};
