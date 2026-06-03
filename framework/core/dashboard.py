"""Flask 统一面板 — REST API + HTML 页面渲染"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import requests
import urllib3

from flask import Flask, jsonify, request

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
    frida_skipped = False
    if task._messenger.name == "frida-rpc":
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
                    return jsonify({"success": False, "error": str(e)}), 500
                except Exception as e:
                    return jsonify({"success": False, "error": f"Frida 初始化失败: {e}"}), 500
            else:
                frida_skipped = True  # No device configured, will fail at send time

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

    _atomic_write_json(runtime_path, runtime)
    return jsonify({"success": True})


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
    runtime["device"] = {
        "serial": data.get("serial", ""),
        "app_package": data.get("app_package", ""),
        "script_name": data.get("script_name", "hook_send_msg.js"),
    }
    _atomic_write_json(runtime_path, runtime)
    return jsonify({"success": True})


def run_dashboard(host: str = "127.0.0.1", port: int = 3112, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
