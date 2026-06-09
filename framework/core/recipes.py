"""预置处理器组合配方 — 一个名字展开为完整 pipeline 配置。

只提供结构，密钥等敏感值由 app config.json 提供。
"""
RECIPES = {
    "sybl-pattern": {
        "encryption": {
            "plugin": "aes-cbc",
            "params": {"key": None, "iv": None, "key_derivation": "session_key"},
        },
        "signing": {
            "plugin": "xor-triple-sign",
            "params": {},
        },
        "auth": {
            "plugin": "password-login",
            "params": {
                "endpoint": "/UI/PasswordLoginPage/passwordLogin",
                "fields": {"phone": "phone", "password": "password"},
                "response_mapping": {"token": "token", "uid": "id"},
            },
        },
        "messaging": {
            "plugin": "frida-rpc",
            "params": {"script_name": "bridge.js"},
        },
    },
    "simple-rest": {
        "encryption": "plaintext",
        "signing": "plaintext",
        "auth": "header-token",
        "messaging": "rest-json",
    },
    "rongcloud": {
        "encryption": "plaintext",
        "signing": "plaintext",
        "auth": "header-token",
        "messaging": {
            "plugin": "rongcloud-tcp",
            "params": {"app_key": "", "navi_server": "flse.cn.rongnav.com"},
        },
    },
}


def expand_recipe(pipeline_config: dict) -> dict:
    """如果 pipeline 有 recipe 字段，展开为完整处理器配置。
    显式指定的处理器覆盖 recipe 中的对应项。"""
    recipe_name = pipeline_config.get("recipe")
    if not recipe_name:
        return pipeline_config

    base = RECIPES.get(recipe_name)
    if not base:
        raise ValueError(
            f"未知配方: {recipe_name}。可用: {list(RECIPES.keys())}")

    result = dict(base)
    for category in ["encryption", "signing", "auth", "messaging"]:
        if category in pipeline_config:
            # Deep merge params if both specify them
            user = pipeline_config[category]
            if isinstance(user, dict) and isinstance(base[category], dict):
                merged = dict(base[category])
                merged.update(user)
                # Merge params separately
                if "params" in user and "params" in base[category]:
                    mp = dict(base[category]["params"])
                    mp.update(user["params"])
                    merged["params"] = mp
                result[category] = merged
            else:
                result[category] = user
    return result
