"""FridaModuleLoader — concatenates JS modules and builds rpc.exports glue"""
import json
from pathlib import Path
from typing import Optional


MODULES_DIR = Path(__file__).resolve().parent / "modules"

_GLUE_HEADER = """
// === framework glue: shared context ===
var ctx = {
  shared: {},
  modules: {},
  moduleParams: {module_params_json},
  _keyWritten: false,
  register: function(name, mod) { this.modules[name] = mod; },
  log: function(src, msg) { console.log("[module:" + src + "]", msg); },
};
"""

_GLUE_KEY_WATCHER = """
// === framework glue: key watcher ===
setInterval(function() {
  var key = ctx.shared.sessionKey;
  if (key && !ctx._keyWritten) {
    ctx._keyWritten = true;
    var data = JSON.stringify({
      key_hex: key,
      iv_hex: ctx.shared.sessionIV || null,
      headers: ctx.shared.sessionHeaders || {},
    });
    console.log("[bridge] KEY_JSON: " + data);
  }
}, 500);
"""

_GLUE_RPC_HEADER = """
// === framework glue: rpc.exports ===
rpc.exports = {
"""

_GLUE_RPC_FOOTER = """
};
console.log("[bridge] Ready.");
"""


class FridaModuleLoader:
    """Load and concatenate JS modules into a single injectable Frida script."""

    def __init__(self, module_specs: list, rpc_methods: Optional[list] = None):
        self.module_specs = module_specs
        self.rpc_methods = rpc_methods or []

    def build_script(self) -> str:
        parts = []

        module_params = {}
        for spec in self.module_specs:
            module_params[spec["name"]] = spec.get("params", {})

        parts.append(_GLUE_HEADER.replace(
            "{module_params_json}", json.dumps(module_params)))

        for spec in self.module_specs:
            mod_name = spec["name"]
            mod_path = self._find_module(mod_name)
            if not mod_path:
                raise FileNotFoundError(f"Frida module not found: {mod_name}")
            js = mod_path.read_text(encoding="utf-8")
            parts.append(f"\n// === module: {mod_name} ===\n")
            parts.append(js)
            parts.append("\n")

        parts.append("\n// === framework glue: install ===\n")
        install_order = self._install_order()
        for mod_name in install_order:
            parts.append(
                f'if (ctx.modules["{mod_name}"] && ctx.modules["{mod_name}"].install) '
                f'{{ ctx.modules["{mod_name}"].install(); }}\n'
            )

        parts.append(_GLUE_KEY_WATCHER)
        parts.append(_GLUE_RPC_HEADER)

        for method in self.rpc_methods:
            parts.append(self._build_rpc_method(method))

        for spec in self.module_specs:
            name = spec["name"]
            if name.startswith("messaging_") and "sendMessage" not in self.rpc_methods:
                parts.append(
                    f'  sendMessage: function(uid, text) {{ '
                    f'return ctx.modules["{name}"].send(uid, text); '
                    f'}},\n'
                )

        parts.append(_GLUE_RPC_FOOTER)
        return "".join(parts)

    def _find_module(self, name: str) -> Optional[Path]:
        for subdir in ["crypto", "http", "rpc"]:
            candidate = MODULES_DIR / subdir / f"{name}.js"
            if candidate.exists():
                return candidate
        return None

    def _install_order(self) -> list:
        native_first = ["evp_cipher_init"]
        ordered = []
        for spec in self.module_specs:
            if spec["name"] in native_first:
                ordered.insert(0, spec["name"])
            else:
                ordered.append(spec["name"])
        return ordered

    def _build_rpc_method(self, method: str) -> str:
        mappings = {
            "getSessionKey": '    getSessionKey: function() { return ctx.modules["key_export"].getState().key_hex; },\n',
            "getHeaders": '    getHeaders: function() { return ctx.modules["key_export"].getState().headers; },\n',
            "getStatus": '    getStatus: function() { var s = {}; for (var k in ctx.modules) { s[k] = ctx.modules[k].getState(); } return JSON.stringify(s); },\n',
            "sendMessage": '',
        }
        return mappings.get(method, f'    {method}: function() {{ return null; }},\n')
