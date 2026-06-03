// TencentIM RPC bridge for hifun
var IM_READY = false;
var LOGIN_USER = '';
var _results = {};   // JS-level result store (cross-thread safe via Frida bridge)
var _sendSeq = 0;

function doInstall() {
    Java.perform(function () {
        var V2TIMManager = Java.use('com.tencent.imsdk.v2.V2TIMManager');
        var msgMgr = Java.use('com.tencent.imsdk.v2.V2TIMMessageManager').getInstance();
        var V2TIMMessage = Java.use('com.tencent.imsdk.v2.V2TIMMessage');

        rpc.exports = {
            isReady: function () {
                return IM_READY && LOGIN_USER.length > 0;
            },

            getLoginUser: function () {
                return LOGIN_USER;
            },

            // snake_case alias — matches Python exports_sync.send_text()
            send_text: function (targetUserId, text) {
                return rpc.exports.sendText(targetUserId, text);
            },

            sendText: function (targetUserId, text) {
                if (!IM_READY) return JSON.stringify({ success: false, error: 'IM not ready' });

                _sendSeq++;
                var seq = _sendSeq;

                // Default: error state in case Java.perform fails entirely
                _results['r_' + seq] = { success: false, error: 'sendText: Java.perform did not run' };

                try {
                    Java.perform(function () {
                        try {
                            var msg = msgMgr.createTextMessage(String(text));
                            if (!msg) {
                                _results['r_' + seq] = { success: false, error: 'createTextMessage returned null' };
                                return;
                            }

                            _results['r_' + seq] = { status: 'pending' };

                            var Cb = Java.registerClass({
                                name: 'com.frida.ImCb' + seq + '_' + Date.now(),
                                implements: [Java.use('com.tencent.imsdk.v2.V2TIMSendCallback')],
                                methods: {
                                    onSuccess: function (message) {
                                        try {
                                            var cls = message.getClass();
                                            var getMsgID = cls.getMethod('getMsgID', []);
                                            var getTimestamp = cls.getMethod('getTimestamp', []);
                                            _results['r_' + seq] = {
                                                success: true,
                                                msgId: String(getMsgID.invoke(message, [])),
                                                timestamp: String(getTimestamp.invoke(message, []))
                                            };
                                        } catch(e) {
                                            _results['r_' + seq] = { success: true, note: 'sent (detail err: ' + e + ')' };
                                        }
                                        console.log('[IM] SEND OK seq=' + seq);
                                    },
                                    onProgress: function (m, p) { },
                                    onError: function (message, code, desc) {
                                        try {
                                            _results['r_' + seq] = {
                                                success: false,
                                                error: 'code=' + code + ' ' + desc
                                            };
                                        } catch(e) {
                                            _results['r_' + seq] = { success: false, error: 'err: ' + e };
                                        }
                                        console.log('[IM] SEND FAIL seq=' + seq + ' code=' + code + ' ' + desc);
                                    }
                                }
                            });

                            var msgId = msgMgr.sendMessage(
                                msg, String(targetUserId), null,
                                V2TIMMessage.V2TIM_PRIORITY_DEFAULT.value,
                                false, null, Cb.$new()
                            );

                            _results['r_' + seq].pendingMsgId = String(msgId);
                            console.log('[IM] Queued seq=' + seq + ' msgId=' + msgId);
                        } catch (e) {
                            _results['r_' + seq] = { success: false, error: 'Inner: ' + e.toString() };
                            console.log('[IM] sendText inner error: ' + e);
                        }
                    });
                } catch (e) {
                    _results['r_' + seq] = { success: false, error: 'Java.perform: ' + e.toString() };
                    console.log('[IM] sendText Java.perform error: ' + e);
                }

                return JSON.stringify({ queued: true, key: 'r_' + seq });
            },

            // snake_case alias for Python
            poll_result: function (key) {
                return rpc.exports.pollResult(key);
            },

            pollResult: function (key) {
                var entry = _results[key];
                if (!entry) {
                    var keys = [];
                    for (var k in _results) keys.push(k);
                    console.log('[IM] pollResult: key=' + key + ' NOT FOUND. Existing keys: ' + JSON.stringify(keys));
                    return JSON.stringify({ error: 'key not found', key: key, existingKeys: keys });
                }
                var out = JSON.stringify(entry);
                if (entry.success !== undefined) {
                    delete _results[key];
                }
                return out;
            },

            // snake_case alias
            get_conversations: function () {
                return rpc.exports.getConversations();
            },

            getConversations: function () {
                var result = [];
                Java.perform(function () {
                    try {
                        var V2TIMConversationManager = Java.use('com.tencent.imsdk.v2.V2TIMConversationManager');
                        var convMgr = V2TIMConversationManager.getInstance();

                        // Store raw conversation objects — extract data OUTSIDE callback
                        // because direct method calls on Java objects fail inside Java.registerClass callbacks
                        var rawConvs = null;
                        var cbError = null;

                        var latch = Java.use('java.util.concurrent.CountDownLatch').$new(1);
                        convMgr.getConversationList(0, 50, Java.registerClass({
                            name: 'com.frida.ConvCb' + Date.now(),
                            implements: [Java.use('com.tencent.imsdk.v2.V2TIMValueCallback')],
                            methods: {
                                onSuccess: function (obj) {
                                    try {
                                        // Store raw list reference — don't call methods here
                                        var V2TIMConversationResult = Java.use('com.tencent.imsdk.v2.V2TIMConversationResult');
                                        var res = Java.cast(obj, V2TIMConversationResult);
                                        rawConvs = res.getConversationList();
                                    } catch (e2) {
                                        cbError = 'onSuccess cast: ' + e2;
                                    }
                                    latch.countDown();
                                },
                                onError: function (code, desc) {
                                    cbError = 'code=' + code + ' ' + desc;
                                    latch.countDown();
                                }
                            }
                        }).$new());

                        // Wait for callback (10 seconds max)
                        latch.await(10, Java.use('java.util.concurrent.TimeUnit').SECONDS.value);

                        if (cbError) {
                            result.push(JSON.stringify({ error: cbError }));
                            console.log('[IM] getConversations error: ' + cbError);
                        } else if (rawConvs) {
                            // Extract data OUTSIDE callback — method calls work fine here
                            var size = rawConvs.size();
                            console.log('[IM] getConversations: ' + size + ' raw conversations');
                            for (var i = 0; i < size; i++) {
                                try {
                                    var c = rawConvs.get(i);
                                    // Use reflection for safety (known working pattern)
                                    var cCls = c.getClass();
                                    var info = {
                                        userId: String(cCls.getMethod('getUserID', []).invoke(c, []) || ''),
                                        showName: String(cCls.getMethod('getShowName', []).invoke(c, []) || ''),
                                        unread: parseInt(String(cCls.getMethod('getUnreadCount', []).invoke(c, [])) || '0'),
                                        type: parseInt(String(cCls.getMethod('getType', []).invoke(c, [])) || '-1')
                                    };
                                    // Try to get last message
                                    try {
                                        var lastMsg = cCls.getMethod('getLastMessage', []).invoke(c, []);
                                        if (lastMsg) {
                                            var lmCls = lastMsg.getClass();
                                            var textElem = lmCls.getMethod('getTextElem', []).invoke(lastMsg, []);
                                            if (textElem) {
                                                var teCls = textElem.getClass();
                                                info.lastMsg = String(teCls.getMethod('getText', []).invoke(textElem, []) || '');
                                            }
                                        }
                                    } catch (e) { /* no last message */ }
                                    result.push(JSON.stringify(info));
                                } catch (e) {
                                    result.push(JSON.stringify({ error: 'conv[' + i + ']: ' + e }));
                                }
                            }
                        } else {
                            result.push(JSON.stringify({ error: 'no conversations (rawConvs=null)' }));
                        }
                    } catch (e) {
                        result.push('ERROR: ' + e.toString());
                        console.log('[IM] getConversations error: ' + e);
                    }
                });
                return result;
            }
        };

        console.log('[IM] RPC installed. Exports: ' + Object.keys(rpc.exports).join(', '));
    });
}

// Watch for IM SDK init
var checks = 0;
var watch = setInterval(function () {
    checks++;
    if (!Java.available) return;

    Java.perform(function () {
        try {
            var V2TIMManager = Java.use('com.tencent.imsdk.v2.V2TIMManager');
            var lu = String(V2TIMManager.getInstance().getLoginUser());
            if (lu && lu.length > 0 && lu !== 'null') {
                LOGIN_USER = lu;
                if (!IM_READY) {
                    IM_READY = true;
                    clearInterval(watch);
                    console.log('[IM] SDK ready. LoginUser=' + lu);
                    doInstall();
                }
            }
        } catch (e) { }
    });

    if (checks > 300) {
        clearInterval(watch);
        console.log('[IM] Timeout (300 checks) — SDK did not login');
    }
}, 200);
