/**
 * Frida Hook 脚本模板
 *
 * 用于截流框架的 frida-rpc 消息发送。
 * 每个 App 在 apps/<app_name>/hook_send_msg.js 放置自定义版本。
 *
 * 必须暴露: rpc.exports.sendMessage(uid, text) -> bool|string|object
 *   - 返回 true / "ok" → 发送成功
 *   - 返回 "error message" → 发送失败，错误信息
 *   - 返回 {success: false, error: "..."} → 同上
 */

// ==== 配置区域 — 替换为实际类名和方法 ====
var TARGET_CLASS = "com.example.im.MessageSender";
var TARGET_METHOD = "sendPrivateMessage";
// ==========================================

rpc.exports = {
    sendMessage: function(uid, text) {
        var result = { success: false, error: "" };

        Java.perform(function() {
            try {
                var MsgSender = Java.use(TARGET_CLASS);

                // 获取实例 — 根据 app 实际情况选择方式:
                // 方式1: 直接 new
                var instance = MsgSender.$new();
                // 方式2: 静态方法获取
                // var instance = MsgSender.getInstance();
                // 方式3: 从已有对象拿
                // var instance = Java.choose(TARGET_CLASS, {onMatch: function(ins) { ... }});

                var resp = instance[TARGET_METHOD](
                    Java.use("java.lang.String").$new(String(uid)),
                    Java.use("java.lang.String").$new(String(text))
                );

                // 判断结果 — 根据 app 实际返回值调整
                var respStr = resp ? resp.toString() : "";
                if (respStr === "ok" || respStr === "0" || respStr === "success") {
                    result.success = true;
                } else {
                    result.error = respStr || "send failed";
                }
            } catch (e) {
                result.error = "Hook error: " + e.toString();
            }
        });

        return result;
    }
};

console.log("[interceptor] Hook loaded: " + TARGET_CLASS + "." + TARGET_METHOD);
