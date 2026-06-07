// Hook Cronet (Google's HTTP stack) — stub, fill in per-app
(function(ctx) {
  var headers = {};
  ctx.register("cronet", {
    install: function() {
      ctx.log("cronet", "Cronet hooks not yet implemented for this app");
    },
    getState: function() { return {headers: headers}; },
  });
})
