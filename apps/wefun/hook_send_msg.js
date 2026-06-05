/** WeFun IM stub — 私信走 HTTP (rest-json)，此脚本仅用于建立 Frida 连接
 *
 * 真正的功能通过 hook_ws_rooms.js（WS 房间捕获）加载。
 */
rpc.exports = {
    sendText: function(uid, text) {
        return JSON.stringify({success: false, error: "WeFun uses rest-json, not frida-rpc"});
    },
    sendMessage: function(uid, text) {
        return JSON.stringify({success: false, error: "WeFun uses rest-json, not frida-rpc"});
    }
};
