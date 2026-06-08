"""Flask 统一面板 — REST API + HTML 页面渲染"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import requests
import urllib3

from flask import Flask, jsonify, request, Response
from queue import Empty

from .account_manager import AccountManager
from .task_manager import TaskManager

app = Flask(__name__)
manager = TaskManager()

SPEC_DIR = (Path(__file__).resolve().parent.parent.parent
            / "docs" / "superpowers" / "specs")
HOMEPAGE_HTML = SPEC_DIR / "homepage.html"
DETAIL_HTML = SPEC_DIR / "design-mockup.html"

APPS_DIR = Path(__file__).resolve().parent.parent.parent / "apps"

ALLOWED_CONFIG_SCHEMAS = ["1.0", "2.0"]


def _atomic_write_json(path: Path, data) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, str(path))


# ═══ Task API ═══

@app.route("/api/apps")
def api_apps():
    return jsonify(manager.get_all_stats())


@app.route("/api/app/<app_id>")
def api_app_detail(app_id):
    stats = manager.get_stats(app_id)
    if not stats:
        return jsonify({"error": "not found"}), 404
    return jsonify(stats)


@app.route("/api/app/<app_id>/start", methods=["POST"])
def api_start(app_id):
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    # If messaging is frida-rpc, try to set up Frida session
    if task._messenger.name == "frida-rpc":
        # Already have CLI-based Frida connection (NIS bypass for sybl etc.)
        if getattr(task, '_frida_cli_proc', None) is not None:
            pass  # Use CLI stdin messaging — no Python binding needed
        else:
            runtime_path = APPS_DIR / app_id / "runtime.json"
            if runtime_path.exists():
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
                device_cfg = runtime.get("device", {})
                serial = device_cfg.get("serial", "")
                app_package = device_cfg.get("app_package", "")
                script_name = device_cfg.get("script_name", "hook_send_msg.js")

                if serial and app_package:
                    script_path = str(APPS_DIR / app_id / script_name)
                    from framework.bridge.frida_session import FridaSessionManager
                    try:
                        session_mgr = FridaSessionManager()
                        session = session_mgr.get_or_create(
                            app_id, serial, app_package, script_path
                        )
                        task.set_frida_session(session)
                    except RuntimeError as e:
                        # Python binding might be blocked (NIS) — CLI fallback handles this
                        print(f"[dashboard] Frida Python binding failed (will use CLI): {e}")
                    except Exception as e:
                        print(f"[dashboard] Frida init warning: {e}")

    ok = manager.start(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/pause", methods=["POST"])
def api_pause(app_id):
    ok = manager.pause(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/stop", methods=["POST"])
def api_stop(app_id):
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    task.clear_frida_session()
    from framework.bridge.frida_session import FridaSessionManager
    FridaSessionManager().remove(app_id)

    ok = manager.stop(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/rescan", methods=["POST"])
def api_rescan(app_id):
    ok, err = manager.rescan_rooms(app_id)
    task = manager.get_task(app_id)
    rooms = task._rooms if task else []
    return jsonify({
        "success": ok,
        "error": err,
        "total_rooms": len(rooms),
        "rooms": [{"id": r.get("id",""), "name": r.get("name","")} for r in rooms[:5]]
    })


@app.route("/api/app/<app_id>/settings", methods=["POST"])
def api_app_settings(app_id):
    """Update runtime settings: interval, data_source, period, gender, templates."""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    data = request.get_json() or {}
    runtime_path = APPS_DIR / app_id / "runtime.json"
    runtime = {}
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))

    settings = runtime.setdefault("settings", {})
    if "send_interval" in data:
        settings["send_interval"] = data["send_interval"]
        task._interval = data["send_interval"]
    if "data_sources" in data:
        runtime["data_sources"] = data["data_sources"]
    if "periods" in data:
        runtime["periods"] = data["periods"]
    if "genders" in data:
        runtime["genders"] = data["genders"]
    if "templates" in data:
        runtime["templates"] = data["templates"]
        task._templates = data["templates"]
    # Current selection
    if "data_source" in data:
        runtime["data_source"] = data["data_source"]
        task._data_source = data["data_source"]
    if "period" in data:
        runtime["period"] = data["period"]
        task._period = data["period"]
    if "gender" in data:
        runtime["gender"] = data["gender"]
        task._gender = data["gender"]
    # User source — only allow switch when task is not running
    if "user_source" in data:
        if task.status == "running":
            return jsonify({
                "success": False,
                "error": "请先停止任务再切换用户来源",
            }), 409
        new_source = data["user_source"]
        if new_source not in task._user_sources:
            return jsonify({
                "success": False,
                "error": f"无效的用户来源: {new_source}",
            }), 400
        task._user_source = new_source
        task._current_source_cfg = task._user_sources[new_source]
        runtime["user_source"] = new_source
    # Credentials & profile (token, uid, age, gender, device_id)
    if "credentials" in data:
        runtime["credentials"] = data["credentials"]
    if "profile" in data:
        runtime["profile"] = data["profile"]

    _atomic_write_json(runtime_path, runtime)
    return jsonify({"success": True})


@app.route("/api/app/<app_id>/diagnose/stream")
def api_app_diagnose_stream(app_id):
    """SSE stream of diagnose events for real-time frontend display."""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    q = task._diagnose.subscribe()

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            task._diagnose.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/app/<app_id>/health")
def api_app_health(app_id):
    """设备健康检查 — 探测 ADB、Frida、NIS、平台"""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    runtime = task._load_runtime()
    device = runtime.get("device", {})
    serial = device.get("serial", "")
    package = device.get("app_package",
                         task.config.get("meta", {}).get("package", ""))

    if not serial or not package:
        return jsonify({"error": "请先在 Dashboard 设置设备串号和包名"}), 400

    from framework.bridge.env_checker import EnvChecker
    result = EnvChecker.probe(serial, package)
    return jsonify(result)


# ═══ Account API ═══

def _get_account_manager(app_id: str) -> AccountManager:
    app_dir = APPS_DIR / app_id
    if not app_dir.exists():
        app_dir.mkdir(parents=True, exist_ok=True)
    return AccountManager(str(app_dir))


@app.route("/api/app/<app_id>/accounts")
def api_accounts(app_id):
    am = _get_account_manager(app_id)
    accounts = am.load_accounts()
    active = am.get_active_account()
    return jsonify({
        "accounts": accounts,
        "active_uid": active["uid"] if active else None,
    })


@app.route("/api/app/<app_id>/accounts/login", methods=["POST"])
def api_accounts_login(app_id):
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    sms_code = data.get("sms_code", "").strip()
    if not phone or not sms_code:
        return jsonify({"success": False, "error": "手机号和验证码不能为空"}), 400

    # Load app config for API details
    config_path = APPS_DIR / app_id / "config.json"
    if not config_path.exists():
        return jsonify({"success": False, "error": "App 配置不存在"}), 404

    config = json.loads(config_path.read_text(encoding="utf-8"))
    base_url = config.get("base_url", "")
    if not base_url:
        return jsonify({"success": False, "error": "config.json 缺少 base_url"}), 400

    am = _get_account_manager(app_id)
    result = am.sms_login(base_url, phone, sms_code, config)

    if result.get("success"):
        config["token"] = result["token"]
        config["uid"] = result["uid"]
        _atomic_write_json(config_path, config)

    return jsonify(result)


@app.route("/api/app/<app_id>/accounts/send-sms", methods=["POST"])
def api_accounts_send_sms(app_id):
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    captcha_validate = data.get("authenticate", "").strip()
    if not phone or not captcha_validate:
        return jsonify({"success": False, "error": "手机号和滑块验证不能为空"}), 400

    config_path = APPS_DIR / app_id / "config.json"
    if not config_path.exists():
        return jsonify({"success": False, "error": "App 配置不存在"}), 404

    config = json.loads(config_path.read_text(encoding="utf-8"))
    base_url = config.get("base_url", "")
    if not base_url:
        return jsonify({"success": False, "error": "config.json 缺少 base_url"}), 400

    am = _get_account_manager(app_id)
    result = am.send_sms(base_url, phone, captcha_validate, config)
    return jsonify(result)


@app.route("/api/app/<app_id>/accounts/<uid>", methods=["DELETE"])
def api_accounts_remove(app_id, uid):
    am = _get_account_manager(app_id)
    ok = am.remove_account(uid)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/accounts/<uid>/activate", methods=["POST"])
def api_accounts_activate(app_id, uid):
    am = _get_account_manager(app_id)
    ok = am.activate_account(uid)
    if ok:
        active = am.get_active_account()
        if active:
            config_path = APPS_DIR / app_id / "config.json"
            if config_path.exists():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                config["token"] = active["token"]
                config["uid"] = active["uid"]
                _atomic_write_json(config_path, config)
    return jsonify({"success": ok})


# ═══ Pages ═══

@app.route("/")
def index():
    if HOMEPAGE_HTML.exists():
        return HOMEPAGE_HTML.read_text(encoding="utf-8")
    return "<h1>截流看板</h1><p>homepage.html not found</p>", 404


@app.route("/app/<app_id>")
def app_detail(app_id):
    if DETAIL_HTML.exists():
        return DETAIL_HTML.read_text(encoding="utf-8")
    return f"<h1>{app_id}</h1><p>详情页未找到</p>", 404


@app.route("/apps/manage")
def apps_manage():
    """App 管理页"""
    html = Path(__file__).resolve().parent.parent.parent / "docs" / "superpowers" / "specs" / "app-manage.html"
    if html.exists():
        return html.read_text(encoding="utf-8")
    return "<h1>App 管理</h1><p>app-manage.html not found</p>", 404


@app.route("/api/apps/upload", methods=["POST"])
def api_apps_upload():
    """上传配置 — 支持 JSON body 或 zip 包 (config.json + hook_send_msg.js)"""
    data = None
    hook_js = None

    # Detect: multipart zip upload vs plain JSON body
    if request.content_type and "multipart" in request.content_type:
        file = request.files.get("file")
        if not file or not file.filename.endswith(".zip"):
            return jsonify({"success": False, "error": "请上传 .zip 文件"}), 400

        import zipfile, io
        try:
            with zipfile.ZipFile(io.BytesIO(file.read()), "r") as zf:
                # Extract config.json (required)
                if "config.json" not in zf.namelist():
                    return jsonify({"success": False, "error": "zip 包缺少 config.json"}), 400
                data = json.loads(zf.read("config.json").decode("utf-8"))

                # Extract hook JS (optional)
                for name in zf.namelist():
                    if name.endswith(".js") and name != "config.json":
                        hook_js = zf.read(name).decode("utf-8")
                        break
        except (zipfile.BadZipFile, json.JSONDecodeError) as e:
            return jsonify({"success": False, "error": f"文件解析失败: {e}"}), 400
    else:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "请求体为空 — 请上传 JSON 或 .zip 文件"}), 400

    errors, warnings = _validate_config(data)

    if errors:
        return jsonify({"success": False, "errors": errors, "warnings": warnings}), 400

    app_name = data["meta"]["app_name"]
    app_dir = APPS_DIR / app_name
    app_dir.mkdir(parents=True, exist_ok=True)

    # Save config.json
    config_path = app_dir / "config.json"
    _atomic_write_json(config_path, data)

    # Save hook JS if provided
    if hook_js:
        hook_path = app_dir / "hook_send_msg.js"
        hook_path.write_text(hook_js, encoding="utf-8")

    # Create runtime.json template
    runtime_path = app_dir / "runtime.json"
    if not runtime_path.exists():
        runtime = {
            "credentials": {},
            "settings": data.get("runtime_config", {}).get("settings", {}),
            "data_sources": data.get("runtime_config", {}).get("data_sources", {}),
            "periods": data.get("runtime_config", {}).get("periods", {}),
            "genders": data.get("runtime_config", {}).get("genders", {}),
            "templates": data.get("runtime_config", {}).get("templates", []),
        }
        _atomic_write_json(runtime_path, runtime)

    # Dynamic register
    manager.register(app_name, str(config_path))

    return jsonify({
        "success": True,
        "app_id": app_name,
        "warnings": warnings,
        "has_hook": hook_js is not None,
    })


@app.route("/api/apps/<app_id>/test", methods=["POST"])
def api_apps_test(app_id):
    """测试连接 — 执行 authenticate()"""
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"success": False, "error": "App 未找到"}), 404

    result = {"success": False, "error": ""}

    def _on_test(event, payload):
        result["event"] = event
        if event == "error":
            result["error"] = str(payload)
        elif event == "info":
            result["success"] = True

    task._on_update = _on_test
    ok = task.authenticate()
    result["success"] = ok
    return jsonify(result)


@app.route("/api/apps/<app_id>", methods=["DELETE"])
def api_apps_delete(app_id):
    """删除 App"""
    manager.unregister(app_id)
    app_dir = APPS_DIR / app_id
    if app_dir.exists():
        shutil.rmtree(str(app_dir))
    return jsonify({"success": True})


@app.route("/api/processors")
def api_processors():
    """返回所有已注册处理器列表"""
    from framework.core.processor_registry import ProcessorRegistry
    return jsonify(ProcessorRegistry.list_all())


def _validate_config(data: dict) -> tuple:
    errors = []
    warnings = []

    schema_ver = data.get("meta", {}).get("config_schema") or "1.0"
    if schema_ver not in ALLOWED_CONFIG_SCHEMAS:
        errors.append(f"不支持的 config_schema 版本: {schema_ver} (支持: {ALLOWED_CONFIG_SCHEMAS})")

    if not data.get("meta", {}).get("app_name"):
        errors.append("缺少 meta.app_name")
    if not data.get("server", {}).get("base_url"):
        errors.append("缺少 server.base_url")

    from framework.core.processor_registry import ProcessorRegistry
    pipeline = data.get("pipeline", {})
    for category in ["encryption", "signing", "auth", "messaging"]:
        spec = pipeline.get(category, "plaintext")
        plugin_name = spec if isinstance(spec, str) else spec.get("plugin", "plaintext")
        key = f"{category}/{plugin_name}"
        if key not in ProcessorRegistry._registry:
            errors.append(f"处理器不存在: {key}")

    base_url = data.get("server", {}).get("base_url", "")
    if base_url and not errors:
        try:
            urllib3.disable_warnings()
            r = requests.head(base_url, timeout=5, verify=False)
        except Exception:
            warnings.append(f"base_url 不可达: {base_url}")

    return errors, warnings


# ==================== Device API ====================

@app.route("/api/devices")
def api_devices():
    """List all connected ADB devices"""
    from framework.bridge.adb_device import AdbDevice
    devices = AdbDevice.list_devices()
    return jsonify([d.to_dict() for d in devices])


@app.route("/api/app/<app_id>/device", methods=["GET"])
def api_app_device_get(app_id):
    """Get app device selection from runtime.json"""
    runtime_path = APPS_DIR / app_id / "runtime.json"
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        return jsonify(runtime.get("device", {}))
    return jsonify({})


@app.route("/api/app/<app_id>/device", methods=["POST"])
def api_app_device_set(app_id):
    """Save app device selection to runtime.json"""
    data = request.get_json() or {}
    runtime_path = APPS_DIR / app_id / "runtime.json"
    runtime = {}
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    dev = runtime.setdefault("device", {})
    for key in ["serial", "app_package", "script_name", "device_id", "shumei_device_id"]:
        if key in data:
            dev[key] = data[key]
    _atomic_write_json(runtime_path, runtime)
    return jsonify({"success": True})


@app.route("/api/app/<app_id>/extract-creds", methods=["POST"])
def api_app_extract_creds(app_id):
    """从 App 提取凭据：adb 读 SharedPreferences + Frida 抓 OkHttp headers。

    提取后自动保存 token/uid/device_id 到 runtime.json，更新运行中 task。
    """
    task = manager.get_task(app_id)
    if not task:
        return jsonify({"error": "not found"}), 404

    runtime_path = APPS_DIR / app_id / "runtime.json"
    runtime = {}
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))

    device = runtime.get("device", {})
    config_path = APPS_DIR / app_id / "config.json"
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))

    serial = device.get("serial", "")
    req_data = request.get_json() or {}
    if req_data.get("serial"):
        serial = req_data["serial"]
    package = device.get("app_package") or config.get("meta", {}).get("package", "")

    if not serial or not package:
        return jsonify({"success": False, "error": "请先设置设备串号和包名"}), 400

    import subprocess
    import re
    import xml.etree.ElementTree as ET

    all_entries = {}   # filename → {key: value}
    http_headers = {}
    device_info = {}

    # ═══ Phase 1: adb read SharedPreferences XML ═══
    sp_dir = f"/data/data/{package}/shared_prefs"

    xml_files = []
    # Try su first (MuMu/rooted), fallback to direct.
    # Use single string after "shell" for proper quoting
    for ls_cmd in (f"su -c 'ls {sp_dir}'", f"ls {sp_dir}"):
        try:
            r = subprocess.run(
                ["adb", "-s", serial, "shell", ls_cmd],
                capture_output=True, text=True, timeout=10,
                encoding='utf-8', errors='replace',
            )
            lines = [f.strip() for f in r.stdout.split("\n") if f.strip().endswith(".xml")]
            if lines:
                xml_files = lines
                break
        except Exception:
            continue

    # Read each XML file (try su first, fallback direct)
    for fname in xml_files:
        fpath = f"{sp_dir}/{fname}"
        content = None
        for cat_cmd in (f"su -c 'cat {fpath}'", f"cat {fpath}"):
            try:
                r = subprocess.run(
                    ["adb", "-s", serial, "shell", cat_cmd],
                    capture_output=True, text=True, timeout=10,
                    encoding='utf-8', errors='replace',
                )
                if r.returncode == 0 and r.stdout.strip():
                    content = r.stdout
                    break
            except Exception:
                continue
        if not content:
            continue

        entries = {}
        try:
            root = ET.fromstring(content)
            for el in root:
                key = el.get("name", "")
                if el.tag == "string":
                    entries[key] = el.text or ""
                elif el.tag in ("int", "long", "boolean"):
                    entries[key] = el.get("value", "")
        except ET.ParseError:
            for m in re.finditer(r'<string name="([^"]+)">([^<]*)</string>', content):
                entries[m.group(1)] = m.group(2)
            for m in re.finditer(r'<(int|boolean|long) name="([^"]+)" value="([^"]+)"', content):
                entries[m.group(2)] = m.group(3)

        if entries:
            clean_name = fname.replace(".xml", "")
            all_entries[clean_name] = entries

    # ═══ Phase 2: Frida capture OkHttp headers (best-effort) ═══
    try:
        import frida
        frida_result = {}

        def _frida_capture():
            nonlocal frida_result
            session = None
            script = None
            try:
                dm = frida.get_device_manager()
                try:
                    dev = dm.get_device(serial)
                except Exception:
                    try:
                        dev = dm.get_usb_device()
                    except Exception:
                        return
                try:
                    session = dev.attach(package)
                except frida.ProcessNotFoundError:
                    for p in dev.enumerate_processes():
                        if package.lower() in (p.name or "").lower():
                            session = dev.attach(p.pid)
                            break
                    if not session:
                        return

                script_path = Path(__file__).resolve().parent.parent / "bridge" / "hook_extract_creds.js"
                js_code = script_path.read_text(encoding="utf-8")
                script = session.create_script(js_code)
                script.load()

                for i in range(6):
                    import time as _t
                    _t.sleep(0.5)
                    try:
                        if hasattr(script, 'exports') and script.exports is not None:
                            keys = [k for k in dir(script.exports) if not k.startswith('_')]
                            if 'getCredentials' in keys:
                                raw = script.exports_sync.get_credentials()
                                if isinstance(raw, str):
                                    d = json.loads(raw)
                                else:
                                    d = raw
                                frida_result = {
                                    "headers": d.get("httpHeaders", {}),
                                    "deviceInfo": d.get("deviceInfo", {}),
                                }
                                break
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                try:
                    if script:
                        script.unload()
                except Exception:
                    pass
                try:
                    if session:
                        session.detach()
                except Exception:
                    pass

        import threading
        t = threading.Thread(target=_frida_capture, daemon=True)
        t.start()
        t.join(timeout=8)
        http_headers = frida_result.get("headers", {})
        device_info = frida_result.get("deviceInfo", {})
    except Exception:
        pass

    # ═══ Phase 3: adb device info (backup) ═══
    if not device_info:
        try:
            r = subprocess.run(
                ["adb", "-s", serial, "shell", "settings", "get", "secure", "android_id"],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
            )
            aid = r.stdout.strip()
            if aid and aid != "null":
                device_info["android_id"] = aid
        except Exception:
            pass

    # ═══ Auto-detect & save ═══
    token = None
    uid = None
    device_id = None

    token_patterns = ["access_token", "refresh_token", "auth_token", "tk"]
    uid_patterns = ["uid", "user_id", "userid", "h_m", "member_id", "mid"]
    did_patterns = ["device_id", "device-id", "did", "android_id", "key_did"]

    for filename, entries in all_entries.items():
        for key, value in entries.items():
            key_lower = key.lower()
            val = str(value).strip()

            # Special: AccountData / account-like JSON blobs with tk+mid
            if not token or not uid:
                if val.startswith("{") and ("tk" in val or "mid" in val or "access_token" in val):
                    try:
                        nested = json.loads(val)
                        if not token and "tk" in nested:
                            token = str(nested["tk"])
                        if not token and "access_token" in nested:
                            token = str(nested["access_token"])
                        if not uid and "mid" in nested:
                            uid = str(nested["mid"])
                        if not uid and "uid" in nested:
                            uid = str(nested["uid"])
                        if not uid and "user_id" in nested:
                            uid = str(nested["user_id"])
                    except (json.JSONDecodeError, TypeError):
                        pass

            if not token:
                for p in token_patterns:
                    if p in ("tk",):
                        if (key_lower == p or key_lower.endswith(":" + p) or key_lower.endswith("_" + p)) and len(val) > 8:
                            token = val
                            break
                    elif p in key_lower and len(val) > 8:
                        token = val
                        break
                if not token and key_lower == "token" and len(val) > 12:
                    token = val

            if not uid:
                for p in uid_patterns:
                    # Short patterns need exact match or word boundary
                    if p in ("h_m", "mid", "uid"):
                        if key_lower == p or key_lower.endswith(":" + p) or key_lower.endswith("_" + p):
                            try:
                                int(val)
                                uid = val
                                break
                            except ValueError:
                                pass
                    elif p in key_lower:
                        try:
                            int(val)
                            uid = val
                            break
                        except ValueError:
                            pass

            if not device_id:
                for p in did_patterns:
                    if p in key_lower and len(val) >= 8:
                        device_id = val
                        break

    # Check httpHeaders
    for name, value in http_headers.items():
        name_lower = name.lower()
        val = str(value)
        if not token and ("token" in name_lower or "auth" in name_lower):
            if len(val) > 10:
                token = val
        if not uid and ("uid" in name_lower or "user" in name_lower):
            try:
                int(val)
                uid = val
            except ValueError:
                pass

    # Fallback: device_id from device_info
    if not device_id and device_info.get("android_id"):
        device_id = device_info["android_id"]

    # Parse nested JSON in token values (e.g. refresh_token = {"access_token": "..."})
    if token and token.startswith("{") and "access_token" in token:
        try:
            nested = json.loads(token)
            if nested.get("access_token"):
                token = nested["access_token"]
            if not uid and nested.get("uid"):
                uid = str(nested["uid"])
            if not uid and nested.get("user_id"):
                uid = str(nested["user_id"])
        except (json.JSONDecodeError, TypeError):
            pass

    saved = {}
    if not runtime_path.exists():
        runtime = {}

    if token:
        runtime.setdefault("credentials", {})["token"] = token
        task._auth_token = token
        saved["token"] = token[:20] + "..."

    if uid:
        runtime.setdefault("credentials", {})["uid"] = int(uid)
        task._uid = str(uid)
        saved["uid"] = uid

    if device_id:
        runtime.setdefault("device", {})["device_id"] = device_id
        saved["device_id"] = device_id

    if saved:
        _atomic_write_json(runtime_path, runtime)
        saved["_message"] = f"已自动配置到 {app_id}"
    else:
        saved["_message"] = "未识别到 token/uid，请确认 App 已登录"

    return jsonify({
        "success": True,
        "saved": saved,
        "raw": {
            "sharedPrefs": all_entries,
            "httpHeaders": http_headers,
            "deviceInfo": device_info,
        },
    })


def run_dashboard(host: str = "127.0.0.1", port: int = 3112, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
