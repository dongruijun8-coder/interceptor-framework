// Native hook: EVP_CipherInit_ex in libcrypto.so (BoringSSL/OpenSSL)
// Zero Java.perform — completely bypasses NIS Java-level detection
(function(ctx) {
  var keyHex = null, ivHex = null;

  function tryInstall() {
    var mod = Process.findModuleByName("libcrypto.so");
    if (!mod) {
      return false;
    }
    var evpInit = Module.findExportByName("libcrypto.so", "EVP_CipherInit_ex");
    if (!evpInit) {
      ctx.log("evp_cipher_init", "EVP_CipherInit_ex not found");
      return false;
    }
    Interceptor.attach(evpInit, {
      onEnter: function(args) {
        if (keyHex && ivHex) return;
        var keyPtr = args[3], ivPtr = args[4];
        if (keyPtr.isNull() || ivPtr.isNull()) return;
        try {
          if (!keyHex) {
            var h = "";
            for (var i = 0; i < 32; i++) h += ("0" + keyPtr.add(i).readU8().toString(16)).slice(-2);
            if (h !== "0000000000000000000000000000000000000000000000000000000000000000") {
              keyHex = h;
              ctx.shared.sessionKey = h;
              ctx.log("evp_cipher_init", "AES-256 key captured (native)");
            }
          }
          if (!ivHex) {
            var h = "";
            for (var i = 0; i < 16; i++) h += ("0" + ivPtr.add(i).readU8().toString(16)).slice(-2);
            ivHex = h;
            ctx.shared.sessionIV = h;
            ctx.log("evp_cipher_init", "IV captured (native)");
          }
        } catch(e) {}
      }
    });
    ctx.log("evp_cipher_init", "Hooked EVP_CipherInit_ex");
    return true;
  }

  var installed = false;
  setInterval(function() {
    if (!installed) { installed = tryInstall(); }
  }, 1000);

  ctx.register("evp_cipher_init", {
    install: function() {},
    getState: function() { return {key_hex: keyHex, iv_hex: ivHex}; },
  });
})
