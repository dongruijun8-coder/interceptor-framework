/** WeFun WebSocket 房间列表 Hook
 *
 * Hook okhttp3 WebSocketListener.onMessage 拦截 WS 推送消息，
 * 解析房间数据（id + name），缓存到全局列表中。
 *
 * RPC exports:
 *   getRooms()  → 返回缓存的房间列表 JSON string
 *   clearRooms() → 清空缓存
 *   getRoomCount() → 返回缓存房间数
 */
var rooms = {};
var roomCount = 0;

Java.perform(function() {
    try {
        var WebSocketListener = Java.use("okhttp3.WebSocketListener");

        WebSocketListener.onMessage.overload('okhttp3.WebSocket', 'java.lang.String').implementation = function(ws, text) {
            // Parse incoming WS message — look for room data
            try {
                var msg = JSON.parse(text);

                // Case 1: Direct room list object
                if (msg.room_id || msg.roomId) {
                    addRoom(msg);
                }
                if (msg.room_name || msg.roomName || msg.title) {
                    addRoom(msg);
                }

                // Case 2: Array of rooms
                if (Array.isArray(msg)) {
                    for (var i = 0; i < msg.length; i++) {
                        addRoom(msg[i]);
                    }
                }

                // Case 3: data/rooms/list nested
                var data = msg.data || msg.body || msg.result;
                if (data) {
                    if (Array.isArray(data)) {
                        for (var j = 0; j < data.length; j++) {
                            addRoom(data[j]);
                        }
                    }
                    // rooms / list / items
                    var lists = [data.rooms, data.list, data.items, data.room_list,
                                data.recommend, data.hot, data.nearby];
                    for (var k = 0; k < lists.length; k++) {
                        if (Array.isArray(lists[k])) {
                            for (var m = 0; m < lists[k].length; m++) {
                                addRoom(lists[k][m]);
                            }
                        }
                    }
                    // Single room in data
                    if (data.room_id || data.roomId) addRoom(data);
                }

                // Case 4: WS type-based message (WeFun: type=900 enter, type=801 members)
                if (msg.type && msg.data) {
                    // Room enter/update messages
                    if (msg.data.room || msg.data.room_info) {
                        var r = msg.data.room || msg.data.room_info;
                        addRoom(r);
                    }
                    // Member list may include room context
                    if (msg.data.room_id) addRoom(msg.data);
                }
            } catch(e) {
                // Not JSON or parse error — ignore
            }

            // Call original
            this.onMessage(ws, text);
        };

        console.log("[WS Hook] okhttp3.WebSocketListener.onMessage hooked");
    } catch(e) {
        console.log("[WS Hook] Failed: " + e);
    }
});

function addRoom(obj) {
    // Extract room id
    var id = obj.room_id || obj.roomId || obj.id || obj.rid;
    if (!id) return;
    id = String(id);

    // Extract room name
    var name = obj.room_name || obj.roomName || obj.title || obj.name || obj.room_title;
    if (!name) return;

    // Dedup by id
    if (rooms[id]) return;

    rooms[id] = {
        id: id,
        name: String(name),
        cover: String(obj.cover || obj.room_cover || obj.avatar || ""),
        member_cnt: Number(obj.member_cnt || obj.online_count || obj.members || 0)
    };
    roomCount++;
    console.log("[WS Room #" + roomCount + "] " + id + " = " + name);
}

rpc.exports = {
    getRooms: function() {
        var result = [];
        for (var k in rooms) {
            if (rooms.hasOwnProperty(k)) {
                result.push(rooms[k]);
            }
        }
        return JSON.stringify(result);
    },
    clearRooms: function() {
        rooms = {};
        roomCount = 0;
        return "ok";
    },
    getRoomCount: function() {
        return roomCount;
    }
};
