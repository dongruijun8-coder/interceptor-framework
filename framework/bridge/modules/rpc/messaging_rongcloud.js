// rpc/messaging_rongcloud.js — sends message via RongCloud IM SDK
(function(ctx) {
  var rongIMClient = null;

  function install() {
    Java.perform(function() {
      try {
        rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
        ctx.log("messaging_rongcloud", "RongIMClient acquired");
      } catch(e) {
        ctx.log("messaging_rongcloud", "RongIMClient not available yet");
      }
    });
  }

  function send(uid, text) {
    var result = {};
    Java.perform(function() {
      try {
        if (!rongIMClient) {
          rongIMClient = Java.use("io.rong.imlib.RongIMClient").getInstance();
        }
        var msg = Java.use("io.rong.message.TextMessage").$new(text);
        var conv = Java.use("io.rong.imlib.model.Conversation$ConversationType").valueOf("PRIVATE");
        rongIMClient.sendMessage(conv, String(uid), msg, null, null, null);
        result = {success: true, uid: uid, text: text};
      } catch(e) {
        result = {success: false, error: String(e)};
      }
    });
    return JSON.stringify(result);
  }

  ctx.register("messaging_rongcloud", {
    install: install,
    send: send,
    getState: function() { return {ready: rongIMClient !== null}; },
  });
})
