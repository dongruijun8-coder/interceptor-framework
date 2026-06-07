// rpc/key_export.js — exposes getSessionKey + getHeaders via shared state
(function(ctx) {
  ctx.register("key_export", {
    install: function() {},
    getState: function() {
      return {
        key_hex: ctx.shared.sessionKey || null,
        iv_hex: ctx.shared.sessionIV || null,
        headers: ctx.shared.sessionHeaders || {},
      };
    },
  });
})
