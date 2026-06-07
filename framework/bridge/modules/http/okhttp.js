// Hook OkHttp Request.Builder.header() — capture session headers
(function(ctx) {
  var headers = {};

  function install() {
    Java.perform(function() {
      var RB = Java.use("okhttp3.Request$Builder");
      var keys = ["deviceToken", "SMDeviceId", "DeviceId", "clientSession", "Token",
                   "Authorization", "X-Token", "token", "Cookie"];
      RB.header.overload('java.lang.String', 'java.lang.String').implementation = function(k, v) {
        for (var i = 0; i < keys.length; i++) {
          if (k === keys[i]) {
            headers[k] = v;
            ctx.shared.sessionHeaders = headers;
          }
        }
        return this.header(k, v);
      };
    });
    ctx.log("okhttp", "Hooked OkHttp Request.Builder.header");
  }

  ctx.register("okhttp", {
    install: install,
    getState: function() { return {headers: headers}; },
  });
})
