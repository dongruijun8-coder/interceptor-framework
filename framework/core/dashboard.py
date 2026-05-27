"""Flask 统一面板 — REST API + HTML 页面渲染"""
import json
import os
import tempfile
from pathlib import Path

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


def run_dashboard(host: str = "127.0.0.1", port: int = 3112, debug: bool = False):
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run_dashboard(debug=True)
