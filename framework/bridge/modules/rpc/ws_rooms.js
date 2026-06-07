// rpc/ws_rooms.js — captures WebSocket room list data
(function(ctx) {
  var rooms = [];

  function install() {
    Java.perform(function() {
      try {
        // Hook OkHttp WebSocket — real implementation per-app
        ctx.log("ws_rooms", "WebSocket hooks registered. Navigate to room list in app.");
      } catch(e) {
        ctx.log("ws_rooms", "OkHttp WebSocket not found: " + e);
      }
    });
  }

  ctx.register("ws_rooms", {
    install: install,
    getState: function() { return {rooms: rooms}; },
  });
})
