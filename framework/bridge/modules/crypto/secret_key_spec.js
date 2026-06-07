// Hook SecretKeySpec.$init — captures key BEFORE Cipher.init (bypasses NIS)
(function(ctx) {
  var keyHex = null;

  function install() {
    Java.perform(function() {
      var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
      SKS.$init.overload('[B', 'java.lang.String').implementation = function(kb, algo) {
        if (!keyHex && algo.indexOf("AES") >= 0 && kb.length === 32) {
          var h = ""; for (var i = 0; i < kb.length; i++) h += ("0" + (kb[i] & 0xFF).toString(16)).slice(-2);
          keyHex = h;
          ctx.shared.sessionKey = h;
          ctx.log("secret_key_spec", "AES-256 key captured");
        }
        return this.$init(kb, algo);
      };

      var IvSpec = Java.use("javax.crypto.spec.IvParameterSpec");
      IvSpec.$init.overload('[B').implementation = function(iv) {
        if (!ctx.shared.sessionIV && iv.length === 16) {
          var h = ""; for (var i = 0; i < iv.length; i++) h += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
          ctx.shared.sessionIV = h;
          ctx.log("secret_key_spec", "IV captured");
        }
        return this.$init(iv);
      };
    });
  }

  ctx.register("secret_key_spec", {
    install: install,
    getState: function() { return {key_hex: keyHex, iv_hex: ctx.shared.sessionIV}; },
  });
})
