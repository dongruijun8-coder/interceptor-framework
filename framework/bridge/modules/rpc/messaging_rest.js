// rpc/messaging_rest.js — sends message via HTTP REST (generic OkHttp)
// Note: this module hooks app's own HTTP client, not used standalone
(function(ctx) {
  ctx.register("messaging_rest", {
    install: function() {
      ctx.log("messaging_rest", "REST messaging: use rest-json Python processor instead");
    },
    send: function(uid, text) {
      return JSON.stringify({success: false, error: "REST messaging uses Python processor"});
    },
    getState: function() { return {ready: false}; },
  });
})
