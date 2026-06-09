"""Template engine — pure functions for {{var}} substitution and data mapping."""
import re


def fill_template(template, identity_vars: dict = None, **kwargs):
    """Fill {{var}} / {{obj.key}} placeholders in a dict recursively.

    Single-var patterns (``{{var}}`` as the whole value) preserve the raw
    type of the resolved value. Multi-var or mixed-text values are always
    string-replaced.
    """
    identity_vars = identity_vars or {}
    result = {}
    for key, value in template.items():
        if isinstance(value, str) and "{{" in value:
            m = re.fullmatch(r'\{\{(.+?)\}\}', value.strip())
            if m:
                var_path = m.group(1)
                parts = var_path.split(".", 1)
                if parts[0] in kwargs:
                    obj = kwargs[parts[0]]
                    if len(parts) > 1 and isinstance(obj, dict):
                        result[key] = obj.get(parts[1], "")
                    else:
                        result[key] = obj
                    continue
                if var_path in identity_vars:
                    result[key] = identity_vars[var_path]
                    continue
            def replacer(m):
                var_path = m.group(1)
                parts = var_path.split(".", 1)
                if parts[0] in kwargs:
                    obj = kwargs[parts[0]]
                    if len(parts) > 1 and isinstance(obj, dict):
                        return str(obj.get(parts[1], ""))
                    return str(obj)
                if var_path in identity_vars:
                    return str(identity_vars[var_path])
                return m.group(0)
            result[key] = re.sub(r'\{\{(.+?)\}\}', replacer, value)
        elif isinstance(value, dict):
            result[key] = fill_template(value, identity_vars, **kwargs)
        else:
            result[key] = value
    return result


def resolve_path(data: dict, path: str):
    """Resolve dot-separated path in nested dict/list structure."""
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict):
            current = current.get(p)
        elif isinstance(current, list):
            try:
                idx = int(p)
                current = current[idx] if idx < len(current) else None
            except ValueError:
                return None
        else:
            return None
    return current


def map_fields(raw: dict, mapping: dict, identity_vars: dict = None):
    """Map raw API response fields to framework-standard format."""
    result = {}
    for framework_field, source in mapping.items():
        if isinstance(source, str) and "{{" in source:
            val = fill_template({"k": source}, identity_vars, **{})["k"]
            result[framework_field] = val
        elif isinstance(source, str) and "." in source:
            result[framework_field] = resolve_path(raw, source)
        else:
            if isinstance(source, str):
                result[framework_field] = raw.get(source, source)
            else:
                result[framework_field] = source
    return result
