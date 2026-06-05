/** 通用凭据提取器 v2 — 主动扫描模式
 *
 * 注入后主动读取：
 *   1. /data/data/<pkg>/shared_prefs/*.xml → 解析所有键值对
 *   2. OkHttp 请求头（被动捕获后续请求）
 *   3. Android 设备标识符
 *
 * RPC exports:
 *   getCredentials() → 返回全部捕获值 JSON string
 */

var captured = {
    sharedPrefs: {},    // filename → {key: value}
    httpHeaders: {},    // header-name → value
    deviceInfo: {}
};

// ═══ 1. Active: scan SharedPreferences XML files ═══
function scanSharedPrefs() {
    try {
        var ActivityThread = Java.use("android.app.ActivityThread");
        var ctx = ActivityThread.currentApplication().getApplicationContext();
        var prefsDir = ctx.getApplicationInfo().dataDir + "/shared_prefs";

        // List XML files via Java File API
        var File = Java.use("java.io.File");
        var dir = File.$new(prefsDir);
        var files = dir.listFiles();
        if (files) {
            for (var i = 0; i < files.length; i++) {
                var name = String(files[i].getName());
                if (name.endsWith(".xml")) {
                    try {
                        var content = readFileContent(String(files[i].getAbsolutePath()));
                        parseXmlPrefs(name, content);
                    } catch(e) {
                        console.log("[Creds] read failed: " + name + " - " + e);
                    }
                }
            }
        }
        console.log("[Creds] Scanned " + (files ? files.length : 0) + " SP files");
    } catch(e) {
        console.log("[Creds] SP scan failed: " + e);
    }
}

function readFileContent(path) {
    var FileInputStream = Java.use("java.io.FileInputStream");
    var ByteArrayOutputStream = Java.use("java.io.ByteArrayOutputStream");
    var fis = FileInputStream.$new(path);
    var bos = ByteArrayOutputStream.$new();
    var buffer = Java.array('byte', new Array(4096).fill(0));
    var len;
    while ((len = fis.read(buffer)) !== -1) {
        bos.write(buffer, 0, len);
    }
    fis.close();
    var bytes = bos.toByteArray();
    bos.close();
    var String = Java.use("java.lang.String");
    return String.$new(bytes, "UTF-8");
}

function parseXmlPrefs(filename, content) {
    // Simple XML parser for SharedPreferences format:
    // <string name="key">value</string>
    // <int name="key" value="123" />
    // <boolean name="key" value="true" />
    var entries = {};
    var patterns = [
        [/<string name="([^"]+)">([^<]*)<\/string>/g, 'string'],
        [/<int name="([^"]+)" value="([^"]+)"\/>/g, 'int'],
        [/<boolean name="([^"]+)" value="([^"]+)"\/>/g, 'bool'],
        [/<long name="([^"]+)" value="([^"]+)"\/>/g, 'long'],
        [/<float name="([^"]+)" value="([^"]+)"\/>/g, 'float'],
    ];

    for (var p = 0; p < patterns.length; p++) {
        var regex = patterns[p][0];
        var m;
        while ((m = regex.exec(content)) !== null) {
            entries[m[1]] = m[2];
        }
    }

    // Only keep if it has credential-like keys
    var hasCred = false;
    var keys = Object.keys(entries);
    for (var k = 0; k < keys.length; k++) {
        if (isCredentialKey(keys[k].toLowerCase())) {
            hasCred = true;
            break;
        }
    }

    if (hasCred && keys.length > 0) {
        var cleanName = filename.replace(".xml", "");
        captured.sharedPrefs[cleanName] = entries;
        console.log("[Creds SP] " + cleanName + ": " + keys.length + " keys");
        // Print credential keys
        for (var k = 0; k < keys.length; k++) {
            if (isCredentialKey(keys[k].toLowerCase())) {
                var val = String(entries[keys[k]]);
                console.log("[Creds SP/" + cleanName + "] " + keys[k] + " = " + val.substring(0, 40));
            }
        }
    }
}

// ═══ 2. Passive: OkHttp Headers ═══
Java.perform(function() {
    try {
        var RequestBuilder = Java.use("okhttp3.Request$Builder");

        RequestBuilder.addHeader.overload('java.lang.String', 'java.lang.String').implementation = function(name, value) {
            if (name && value && value.length > 0) {
                var nameLower = name.toLowerCase();
                if (isAuthHeader(nameLower)) {
                    captured.httpHeaders[name] = String(value);
                    console.log("[Creds HTTP] " + name + " = " + value.substring(0, 40) + "...");
                }
            }
            return this.addHeader(name, value);
        };

        RequestBuilder.header.overload('java.lang.String', 'java.lang.String').implementation = function(name, value) {
            if (name && value && value.length > 0) {
                var nameLower = name.toLowerCase();
                if (isAuthHeader(nameLower)) {
                    captured.httpHeaders[name] = String(value);
                }
            }
            return this.header(name, value);
        };

        console.log("[Creds Extractor] OkHttp hooked");
    } catch(e) {
        console.log("[Creds Extractor] OkHttp hook failed: " + e);
    }
});

// ═══ 3. Device Info ═══
Java.perform(function() {
    try {
        var SettingsSecure = Java.use("android.provider.Settings$Secure");
        var ActivityThread = Java.use("android.app.ActivityThread");
        var ctx = ActivityThread.currentApplication().getApplicationContext();
        var cr = ctx.getContentResolver();

        captured.deviceInfo.android_id = SettingsSecure.getString(cr, "android_id");
        console.log("[Creds Device] android_id = " + captured.deviceInfo.android_id);
    } catch(e) {
        console.log("[Creds Device] failed: " + e);
    }

    try {
        var Build = Java.use("android.os.Build");
        captured.deviceInfo.model = Build.MODEL.value;
        captured.deviceInfo.brand = Build.BRAND.value;
        captured.deviceInfo.serial = Build.SERIAL.value;
    } catch(e) {}
});

// ═══ 4. Also try to get token via Context.getSharedPreferences ═══
Java.perform(function() {
    try {
        var ActivityThread = Java.use("android.app.ActivityThread");
        var ctx = ActivityThread.currentApplication().getApplicationContext();

        // Try common SP names
        var names = ["user_info", "account", "auth", "token", "config",
                     "app_data", "preferences", "user_prefs", "login",
                     "session", "storage", "settings"];
        for (var n = 0; n < names.length; n++) {
            try {
                var sp = ctx.getSharedPreferences(names[n], 0); // MODE_PRIVATE
                var all = sp.getAll();
                var keys = all.keySet().toArray();
                if (keys.length > 0) {
                    var entries = {};
                    for (var k = 0; k < keys.length; k++) {
                        var key = String(keys[k]);
                        entries[key] = String(all.get(key));
                    }
                    captured.sharedPrefs[names[n]] = entries;
                    console.log("[Creds SP/api] " + names[n] + ": " + keys.length + " keys");
                }
            } catch(e) {}
        }
    } catch(e) {
        console.log("[Creds] SP api scan failed: " + e);
    }
});

// ═══ Helpers ═══
function isCredentialKey(key) {
    var patterns = ["token", "access_token", "refresh_token", "auth", "secret",
                    "device_id", "device-id", "did", "android_id", "uid", "user_id",
                    "userid", "sign_key", "signkey", "api_key", "apikey",
                    "session", "cookie", "jwt", "bearer", "user_info", "login",
                    "password", "mobile", "phone"];
    for (var i = 0; i < patterns.length; i++) {
        if (key.indexOf(patterns[i]) >= 0) return true;
    }
    return false;
}

function isAuthHeader(name) {
    var patterns = ["token", "auth", "authorization", "access-token", "access_token",
                    "cookie", "x-api-key", "api-key", "x-auth", "x-token",
                    "x-user", "x-uid", "x-device", "device-id", "x-device-id",
                    "x-session", "session", "bearer", "jwt", "fk-channel",
                    "client-platform", "app-version"];
    for (var i = 0; i < patterns.length; i++) {
        if (name.indexOf(patterns[i]) >= 0) return true;
    }
    return false;
}

// ═══ Execute active scan ═══
Java.perform(function() {
    scanSharedPrefs();
});

// ═══ RPC ═══
rpc.exports = {
    getCredentials: function() {
        return JSON.stringify(captured);
    },
    rescan: function() {
        Java.perform(function() {
            scanSharedPrefs();
        });
        return "ok";
    }
};
