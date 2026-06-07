// CLI-compatible bridge: writes key to file, exports RPC for messaging
var sessionKey = null;
var sessionIV = null;
var sessionHeaders = {};
var rongIMClient = null;
var keyWritten = false;

Java.perform(function() {
    // 1. Capture Key
    var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
    SKS.$init.overload('[B', 'java.lang.String').implementation = function(kb, algo) {
        if (!sessionKey && algo.indexOf("AES") >= 0 && kb.length === 32) {
            var h = ""; for (var i = 0; i < kb.length; i++) h += ("0" + (kb[i] & 0xFF).toString(16)).slice(-2);
            sessionKey = h;
        }
        return this.$init(kb, algo);
    };

    // 2. Capture IV
    var IvSpec = Java.use("javax.crypto.spec.IvParameterSpec");
    IvSpec.$init.overload('[B').implementation = function(iv) {
        if (!sessionIV && iv.length === 16) {
            var ih = ""; for (var i = 0; i < iv.length; i++) ih += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
            sessionIV = ih;
        }
        return this.$init(iv);
    };

    // 3. Capture Headers
    var RB = Java.use("okhttp3.Request$Builder");
    var keys = ["deviceToken", "SMDeviceId", "DeviceId", "clientSession", "Token"];
    RB.header.overload('java.lang.String', 'java.lang.String').implementation = function(k, v) {
        for (var i = 0; i < keys.length; i++) { if (k === keys[i]) sessionHeaders[k] = v; }
        return this.header(k, v);
    };

    // 4. RongCloud
    try { rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance(); } catch(e) {}

    console.log("[bridge] Hooks installed. Waiting for encryption...");

    // 5. Watch for key and write to file
    setInterval(function() {
        if (sessionKey && !keyWritten) {
            keyWritten = true;
            var data = JSON.stringify({
                key_hex: sessionKey,
                iv_hex: sessionIV,
                headers: sessionHeaders
            });
            console.log("[bridge] KEY_JSON: " + data);
        }
    }, 500);

    console.log("[bridge] Ready.");
});

// RPC exports for sendMessage
rpc.exports = {
    sendMessage: function(targetUid, text) {
        var result = {};
        Java.perform(function() {
            try {
                if (!rongIMClient) rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
                var msg = Java.use("io.rong.message.TextMessage").$new(text);
                var conv = Java.use("io.rong.imlib.model.Conversation$ConversationType").valueOf("PRIVATE");
                rongIMClient.sendMessage(conv, targetUid.toString(), msg, null, null, null);
                result = {success: true, uid: targetUid, text: text};
            } catch(e) {
                result = {success: false, error: e.toString()};
            }
        });
        return JSON.stringify(result);
    }
};
