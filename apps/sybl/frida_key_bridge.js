// frida_key_bridge.js — SYBL Session Key + Auth + Messaging RPC
// Target: com.sybl.voiceroom  |  Packer: NIS (hluda required)
// Hooks: SecretKeySpec.$init, IvParameterSpec, OkHttp headers

var sessionKey = null;
var sessionIV = null;
var sessionHeaders = {};
var rongIMClient = null;
var installed = false;

function tryInstallHooks() {
    if (installed) return;
    try {
        Java.perform(function() {
            // ============================================
            // 1. Capture Key via SecretKeySpec (most reliable)
            // ============================================
            var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
            SKS.$init.overload('[B', 'java.lang.String').implementation = function(keyBytes, algo) {
                if (!sessionKey && algo.indexOf("AES") >= 0 && keyBytes.length === 32) {
                    var hex = "";
                    for (var i = 0; i < keyBytes.length; i++) {
                        hex += ("0" + (keyBytes[i] & 0xFF).toString(16)).slice(-2);
                    }
                    sessionKey = hex;
                }
                return this.$init(keyBytes, algo);
            };

            // ============================================
            // 2. Capture IV via IvParameterSpec
            // ============================================
            var IvSpec = Java.use("javax.crypto.spec.IvParameterSpec");
            IvSpec.$init.overload('[B').implementation = function(iv) {
                if (!sessionIV && iv.length === 16) {
                    var ih = "";
                    for (var i = 0; i < iv.length; i++) {
                        ih += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
                    }
                    sessionIV = ih;
                }
                return this.$init(iv);
            };

            // ============================================
            // 3. Capture Headers
            // ============================================
            var RB = Java.use("okhttp3.Request$Builder");
            var headerKeys = ["deviceToken", "SMDeviceId", "DeviceId", "clientSession", "Token"];
            RB.header.overload('java.lang.String', 'java.lang.String').implementation = function(k, v) {
                for (var i = 0; i < headerKeys.length; i++) {
                    if (k === headerKeys[i]) sessionHeaders[k] = v;
                }
                return this.header(k, v);
            };

            // ============================================
            // 4. Access RongCloud IM Client
            // ============================================
            try {
                rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
            } catch(e) {}

            installed = true;
            console.log("[bridge] Hooks installed. Key=" + (sessionKey ? "yes" : "pending"));
        });
    } catch(e) {
        console.log("[bridge] Install error (will retry): " + e);
    }
}

// Try immediately — NIS-delayed classes may fail, retry via interval
tryInstallHooks();
setInterval(tryInstallHooks, 1000);

// ============================================
// RPC Exports (always available, outside Java.perform)
// ============================================
rpc.exports = {
    getSessionKey: function() {
        if (!sessionKey) return JSON.stringify({error: "key not yet captured"});
        return JSON.stringify({
            key_hex: sessionKey,
            iv_hex: sessionIV,
            headers: sessionHeaders
        });
    },

    getStatus: function() {
        return JSON.stringify({
            key_captured: sessionKey !== null,
            rong_available: rongIMClient !== null,
            installed: installed
        });
    },

    sendMessage: function(uid, text) {
        var result = {};
        Java.perform(function() {
            try {
                if (!rongIMClient) {
                    rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
                }
                var msgContent = Java.use("io.rong.message.TextMessage").$new(text);
                var privateType = Java.use("io.rong.imlib.model.Conversation$ConversationType").valueOf("PRIVATE");
                rongIMClient.sendMessage(privateType, uid.toString(), msgContent, null, null, null);
                result = {success: true, uid: uid, text: text};
            } catch(e) {
                result = {success: false, error: e.toString()};
            }
        });
        return JSON.stringify(result);
    }
};
