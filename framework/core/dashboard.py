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

ALLOWED_CONFIG_SCHEMAS = ["2.0"]


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
    ok = manager.start(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/pause", methods=["POST"])
def api_pause(app_id):
    ok = manager.pause(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/stop", methods=["POST"])
def api_stop(app_id):
    ok = manager.stop(app_id)
    return jsonify({"success": ok})


@app.route("/api/app/<app_id>/rescan", methods=["POST"])
def api_rescan(app_id):
    ok = manager.rescan_rooms(app_id)
    return jsonify({"success": ok})


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
    """上传配置 JSON → 校验 → 保存"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求体为空"}), 400

    errors, warnings = _validate_config(data)

    if errors:
        return jsonify({"success": False, "errors": errors, "warnings": warnings}), 400

    app_name = data["meta"]["app_name"]
    app_dir = APPS_DIR / app_name
    app_dir.mkdir(parents=True, exist_ok=True)

    # Save config.json
    config_path = app_dir / "config.json"
    _atomic_write_json(config_path, data)

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

    return jsonify({"success": True, "app_id": app_name, "warnings": warnings})


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

    try:
        schema_ver = data.get("meta", {}).get("config_schema", "1.0")
        if schema_ver not in ALLOWED_CONFIG_SCHEMAS:
            errors.append(f"不支持的 config_schema 版本: {schema_ver}")
    except Exception:
        errors.append("meta.config_schema 字段缺失或无效")

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


def run_dashboard(host: str = "127.0.0.1", port: int = 3112, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
