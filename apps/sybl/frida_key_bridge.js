// frida_key_bridge.js — SYBL Session Key + Auth + Messaging RPC
// Target: com.sybl.voiceroom  |  Packer: NIS (hluda required)
// Safe hooks only — NO reflection, NO enumeration, NO Java.cast

var sessionKey = null;
var sessionIV = null;
var sessionHeaders = {};
var rongIMClient = null;
var installed = false;

function tryInstall() {
    if (installed) return;
    Java.perform(function() {
        try {
            // ============================================
            // 1. Capture AES Key + IV via Cipher.init
            // ============================================
            var Cipher = Java.use("javax.crypto.Cipher");
            Cipher.init.overload('int', 'java.security.Key', 'java.security.spec.AlgorithmParameterSpec').implementation = function(opmode, key, spec) {
                var algo = this.getAlgorithm();
                if (algo.indexOf("AES") >= 0 && !sessionKey) {
                    try {
                        var encoded = key.getEncoded();
                        var hex = "";
                        for (var i = 0; i < encoded.length; i++) hex += ("0" + (encoded[i] & 0xFF).toString(16)).slice(-2);
                        sessionKey = hex;
                    } catch(e) {}
                    try {
                        var iv = spec.getIV();
                        var ih = "";
                        for (var i = 0; i < iv.length; i++) ih += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
                        sessionIV = ih;
                    } catch(e) {}
                }
                return this.init(opmode, key, spec);
            };

            // ============================================
            // 2. Capture Headers
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
            // 3. Access RongCloud IM Client
            // ============================================
            try {
                var RongIM = Java.use("io.rong.imlib.RongIMClient");
                rongIMClient = RongIM.getInstance();
            } catch(e) {}

            installed = true;
            console.log("[bridge] Installed. Key=" + (sessionKey ? "yes" : "pending"));
        } catch(e) {
            console.log("[bridge] Install error: " + e);
        }
    });
}

// Poll until installed (360-style delayed class loading)
setInterval(function() {
    tryInstall();
}, 1000);

// ============================================
// RPC Exports
// ============================================
rpc.exports = {
    // Get current session encryption parameters
    getSessionKey: function() {
        if (!sessionKey) return JSON.stringify({error: "key not yet captured"});
        return JSON.stringify({
            key_hex: sessionKey,
            iv_hex: sessionIV,
            headers: sessionHeaders
        });
    },

    // Login — Python handles HTTP encryption, this provides key
    login: function(credentials) {
        return JSON.stringify({
            session_key_available: sessionKey !== null,
            key_hex: sessionKey,
            note: "Use Python login_test.py pattern with this key + IV=clientSession[:16]"
        });
    },

    // Send text message via RongCloud IM (verified 2026-06-07)
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
    },

    // Get connection status
    getStatus: function() {
        return JSON.stringify({
            key_captured: sessionKey !== null,
            rong_available: rongIMClient !== null,
            installed: installed
        });
    }
};
