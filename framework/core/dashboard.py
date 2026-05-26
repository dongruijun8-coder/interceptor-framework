"""Flask 统一面板 — REST API + HTML 页面渲染"""
import json
from pathlib import Path

from flask import Flask, jsonify, request

from .task_manager import TaskManager

app = Flask(__name__)
manager = TaskManager()

SPEC_DIR = (Path(__file__).resolve().parent.parent.parent
            / "docs" / "superpowers" / "specs")
HOMEPAGE_HTML = SPEC_DIR / "homepage.html"
DETAIL_HTML = SPEC_DIR / "design-mockup.html"


# ═══ API ═══

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
