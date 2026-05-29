"""处理器注册表 — 单例，按 category/name 索引"""
from .processors.base import BaseProcessor


class ProcessorRegistry:
    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, proc_class):
        key = f"{proc_class.category}/{proc_class.name}"
        cls._registry[key] = proc_class

    @classmethod
    def load(cls, spec, category: str) -> BaseProcessor:
        # spec can be "plaintext" (str shorthand) or {"plugin": "aes-cbc", "params": {...}}
        if isinstance(spec, str):
            plugin_name = spec
            params = {}
        else:
            plugin_name = spec["plugin"]
            params = spec.get("params", {})
        key = f"{category}/{plugin_name}"
        proc_class = cls._registry[key]
        return proc_class(params)

    @classmethod
    def list_all(cls) -> list[dict]:
        return [{"name": p.name, "category": p.category,
                 "schema": p.params_schema()}
                for p in cls._registry.values()]

    @classmethod
    def get_spec(cls, plugin_name: str, category: str) -> dict:
        """返回 {plugin, params} 规格，供 Web UI 渲染"""
        key = f"{category}/{plugin_name}"
        if key in cls._registry:
            return {
                "plugin": plugin_name,
                "params_schema": cls._registry[key].params_schema(),
            }
        return {}
