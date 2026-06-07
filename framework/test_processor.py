"""Processor test CLI — python -m framework.test_processor --app <id> --category <cat>"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.core.base_client import BaseClient


def test_encryption(client: BaseClient):
    enc = client._encryptor
    print(f"\n[test] encryption/{enc.name}:")

    ok, warnings = enc.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    if hasattr(enc, '_key') and enc._key:
        print(f"  key: 已设置 ({len(enc._key)} bytes)")
    else:
        method = enc.params.get("key_derivation", "unknown")
        print(f"  key: 未设置 (derivation={method})")

    if hasattr(enc, '_iv') and enc._iv:
        print(f"  IV:  {enc._iv[:20]}...")

    try:
        test_body = {"test": "hello"}
        encoded = enc.encode(test_body)
        print(f"  encode: {json.dumps(test_body)} -> {encoded[:60]}... ({len(encoded)} chars)")
        decoded = enc.decode(encoded)
        assert decoded == test_body, f"往返失败: {decoded} != {test_body}"
        print(f"  ✓ 加密/解密往返成功")
    except Exception as e:
        print(f"  ✗ 往返测试失败: {e}")


def test_signing(client: BaseClient):
    sig = client._signer
    print(f"\n[test] signing/{sig.name}:")

    ok, warnings = sig.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    try:
        headers, params = sig.sign("https://example.com/api/test", {}, {"test": 1})
        print(f"  sign(url, headers, {{}}) -> params={list(params.keys())}")
        print(f"  ✓ 签名生成成功")
    except Exception as e:
        print(f"  ✗ 签名失败: {e}")


def test_auth(client: BaseClient):
    auth = client._auth_processor
    print(f"\n[test] auth/{auth.name}:")

    ok, warnings = auth.validate(client)
    for w in warnings:
        print(f"  ! {w}")

    try:
        result = auth.authenticate(client)
        if result:
            print(f"  ✓ 认证成功 (token={client._auth_token[:20]}..., uid={client._uid})")
        else:
            print(f"  ✗ 认证失败 — 请检查日志")
    except Exception as e:
        print(f"  ✗ 认证异常: {e}")


def test_validate_all(client: BaseClient):
    all_ok = True
    for name, proc in [
        ("encryption", client._encryptor),
        ("signing", client._signer),
        ("auth", client._auth_processor),
        ("messaging", client._messenger),
    ]:
        ok, warnings = proc.validate(client)
        status = "✓" if ok else "✗"
        print(f"\n[{status}] {name}/{proc.name}:")
        if not warnings:
            print(f"  (无问题)")
        for w in warnings:
            print(f"  ! {w}")
            all_ok = False
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Processor test CLI")
    parser.add_argument("--app", required=True, help="App ID (e.g. sybl)")
    parser.add_argument("--category", choices=["encryption", "signing", "auth", "all"],
                        default="all", help="Processor category to test")
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent.parent / "apps" / args.app / "config.json"
    if not config_path.exists():
        print(f"错误: 找不到 config.json ({config_path})")
        sys.exit(1)

    client = BaseClient(str(config_path))

    if args.category == "encryption":
        test_encryption(client)
    elif args.category == "signing":
        test_signing(client)
    elif args.category == "auth":
        test_auth(client)
    else:
        ok = test_validate_all(client)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
