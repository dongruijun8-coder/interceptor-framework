"""Tests for framework.core.template"""
import pytest
from framework.core.template import fill_template, resolve_path, map_fields


class TestFillTemplate:
    def test_simple_string_replacement(self):
        result = fill_template({"greeting": "{{name}} 你好"}, identity_vars={}, name="World")
        assert result == {"greeting": "World 你好"}

    def test_single_var_preserves_int_type(self):
        result = fill_template({"page": "{{page}}"}, identity_vars={}, page=42)
        assert result == {"page": 42}
        assert isinstance(result["page"], int)

    def test_nested_dict_recursive(self):
        result = fill_template({"outer": {"inner": "{{val}}"}}, identity_vars={}, val="x")
        assert result == {"outer": {"inner": "x"}}

    def test_identity_var(self):
        result = fill_template({"uid": "{{uid}}"}, identity_vars={"uid": 12345})
        assert result == {"uid": 12345}

    def test_object_field_access(self):
        result = fill_template({"room_id": "{{room.id}}"}, identity_vars={}, room={"id": 999, "name": "test"})
        assert result == {"room_id": 999}

    def test_no_placeholder_passthrough(self):
        result = fill_template({"key": "value", "num": 123}, identity_vars={})
        assert result == {"key": "value", "num": 123}

    def test_mixed_text_with_placeholder(self):
        result = fill_template({"msg": "Hello {{name}}!"}, identity_vars={}, name="World")
        assert result == {"msg": "Hello World!"}


class TestResolvePath:
    def test_simple_key(self):
        assert resolve_path({"a": 1}, "a") == 1

    def test_nested_dict(self):
        assert resolve_path({"a": {"b": {"c": 1}}}, "a.b.c") == 1

    def test_list_index(self):
        assert resolve_path({"data": [10, 20, 30]}, "data.1") == 20

    def test_missing_key_returns_none(self):
        assert resolve_path({"a": 1}, "b") is None

    def test_nested_missing(self):
        assert resolve_path({"a": {}}, "a.b.c") is None


class TestMapFields:
    def test_dot_path(self):
        result = map_fields({"user": {"uid": 42, "nickname": "Alice"}}, {"uid": "user.uid", "nick": "user.nickname"})
        assert result == {"uid": 42, "nick": "Alice"}

    def test_plain_key(self):
        result = map_fields({"id": 1, "name": "Room"}, {"id": "id", "name": "name"})
        assert result == {"id": 1, "name": "Room"}

    def test_static_literal(self):
        result = map_fields({"a": 1}, {"static": "hello"})
        assert result == {"static": "hello"}
