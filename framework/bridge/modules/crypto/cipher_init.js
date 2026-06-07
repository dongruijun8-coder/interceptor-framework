// Hook Cipher.init( opmode, Key key ) — captures AES key+IV
(function(ctx) {
  var keyHex = null, ivHex = null;

  function install() {
    Java.perform(function() {
      var Cipher = Java.use("javax.crypto.Cipher");
      Cipher.init.overload('int', 'java.security.Key').implementation = function(opmode, key) {
        if (!keyHex && key) {
          var encoded = key.getEncoded();
          if (encoded && encoded.length === 32) {
            var h = ""; for (var i = 0; i < encoded.length; i++) h += ("0" + (encoded[i] & 0xFF).toString(16)).slice(-2);
            keyHex = h;
            ctx.shared.sessionKey = h;
            ctx.log("cipher_init", "AES-256 key captured via Cipher.init");
          }
        }
        return this.init(opmode, key);
      };
      Cipher.init.overload('int', 'java.security.cert.Certificate').implementation = function(opmode, cert) {
        return this.init(opmode, cert);
      };
      Cipher.init.overload('int', 'java.security.Key', 'java.security.spec.AlgorithmParameterSpec').implementation = function(opmode, key, spec) {
        if (!keyHex && key) {
          var encoded = key.getEncoded();
          if (encoded && encoded.length === 32) {
            var h = ""; for (var i = 0; i < encoded.length; i++) h += ("0" + (encoded[i] & 0xFF).toString(16)).slice(-2);
            keyHex = h;
            ctx.shared.sessionKey = h;
            ctx.log("cipher_init", "AES-256 key captured via Cipher.init(3-arg)");
          }
        }
        if (!ivHex && spec && spec.getIV) {
          var iv = spec.getIV();
          if (iv && iv.length === 16) {
            var h = ""; for (var i = 0; i < iv.length; i++) h += ("0" + (iv[i] & 0xFF).toString(16)).slice(-2);
            ivHex = h;
            ctx.shared.sessionIV = h;
            ctx.log("cipher_init", "IV captured via IvParameterSpec");
          }
        }
        return this.init(opmode, key, spec);
      };
    });
  }

  ctx.register("cipher_init", {
    install: install,
    getState: function() { return {key_hex: keyHex, iv_hex: ivHex}; },
  });
})
