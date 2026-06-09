"""Tests for framework.core.pagination"""
import pytest
from framework.core.pagination import Paginator


class TestExtractList:
    def test_default_data_list(self):
        resp = {"code": 0, "data": {"list": [1, 2, 3]}}
        assert Paginator.extract_list(resp, {}) == [1, 2, 3]

    def test_custom_response_path(self):
        resp = {"code": 0, "data": {"items": [4, 5]}}
        ep = {"response_path": "data.items"}
        assert Paginator.extract_list(resp, ep) == [4, 5]

    def test_data_is_list(self):
        resp = {"code": 0, "data": [1, 2]}
        assert Paginator.extract_list(resp, {}) == [1, 2]

    def test_empty_returns_empty_list(self):
        assert Paginator.extract_list({}, {}) == []


class TestPageNumber:
    def test_single_page(self):
        responses = [{"code": 0, "data": {"list": [{"id": 1}, {"id": 2}]}}]
        def requester(body):
            return responses.pop(0)

        ep = {"path": "/test", "pagination": {"type": "page_number", "size": 20}}
        base_body = {"page": 1, "page_size": 20}

        result = Paginator.paginate(
            ep, base_body, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert len(result) == 2
        assert result[0]["id"] == 1

    def test_empty_page_stops(self):
        responses = [{"code": 0, "data": {"list": []}}]
        def requester(body):
            return responses.pop(0)

        ep = {"path": "/test", "pagination": {"type": "page_number", "size": 20}}
        result = Paginator.paginate(
            ep, {"page": 1, "page_size": 20}, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert result == []


class TestCursorOffset:
    def test_cursor_pagination(self):
        responses = [
            {"code": 0, "data": {"list": [1, 2], "nextCursor": "abc"}},
            {"code": 0, "data": {"list": [3], "nextCursor": ""}},
        ]
        def requester(body):
            return responses.pop(0)

        ep = {
            "path": "/test",
            "pagination": {
                "type": "cursor_offset",
                "offset_field": "cursor",
                "offset_sent": "data.nextCursor",
            },
        }
        result = Paginator.paginate(
            ep, {}, requester,
            extractor=lambda r: r.get("data", {}).get("list", []),
        )
        assert result == [1, 2, 3]
