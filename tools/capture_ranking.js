// capture_ranking.js — Hook Crypto to capture request/response plaintext
Java.perform(function() {
    var Cipher = Java.use("javax.crypto.Cipher");

    // 1. Capture encrypt output (request body BEFORE encryption)
    var origDoFinal = Cipher.doFinal;
    Cipher.doFinal.overload('[B').implementation = function(input) {
        var result = origDoFinal.call(this, input);
        var mode = this.getOpMode ? this.getOpMode() : -1;
        if (mode === 1) { // ENCRYPT_MODE
            try {
                var plain = Java.use("java.lang.String").$new(input, "UTF-8");
                console.log("[CRYPTO] ENC PLAIN: " + plain);
            } catch(e) {}
        }
        return result;
    };

    // 2. Capture decrypt input (response body AFTER decryption)
    Cipher.doFinal.overload('[B', 'int', 'int').implementation = function(input, off, len) {
        var result = origDoFinal.call(this, input, off, len);
        var mode = this.getOpMode ? this.getOpMode() : -1;
        if (mode === 2) { // DECRYPT_MODE
            try {
                var plain = Java.use("java.lang.String").$new(result, "UTF-8");
                if (plain.indexOf("UserRank") >= 0 || plain.indexOf("rank") >= 0 ||
                    plain.indexOf("mode") >= 0 || plain.indexOf("data") >= 0 ||
                    plain.indexOf("list") >= 0 || plain.indexOf("room") >= 0 ||
                    plain.indexOf("nick") >= 0 || plain.indexOf("uid") >= 0) {
                    console.log("[CRYPTO] DEC RAW: " + plain.substring(0, 500));
                }
            } catch(e) {}
        }
        return result;
    };

    console.log("[capture] Crypto hooks installed — open ranking in app");
});
