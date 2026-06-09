"""Pagination strategies — page_number, offset_limit, cursor_offset.
All strategies call ``requester(body) -> dict`` for each page, then
collect items via ``extractor(resp) -> list``.
"""
from framework.core.template import resolve_path


class Paginator:
    """Dispatch pagination type and collect results."""

    @staticmethod
    def paginate(
        ep: dict,
        base_body: dict,
        requester,         # callable(body) -> dict (response)
        extractor,         # callable(resp) -> list (items)
    ) -> list:
        """Fetch all pages for an endpoint. Returns list of raw items (dicts)."""
        pagination = ep.get("pagination")
        ptype = pagination.get("type") if pagination else None

        if not ptype:
            resp = requester(base_body)
            return extractor(resp)

        size = pagination.get("size", 20)
        stop_on = pagination.get("stop_on", "empty_list")
        results = []

        if ptype == "offset_limit":
            for offset in range(0, 500, size):
                body = dict(base_body)
                body["offset"] = offset
                body["limit"] = size
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        elif ptype == "page_number":
            for page in range(1, 50):
                body = dict(base_body)
                body["page"] = page
                body["page_size"] = size
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if stop_on == "empty_list" and len(items) < size:
                    break

        elif ptype == "cursor_offset":
            offset_field = pagination.get("offset_field", "offset")
            offset_sent = pagination.get("offset_sent", "")
            max_iters = pagination.get("max_iters", 30)
            cursor = ""
            for _ in range(max_iters):
                body = dict(base_body)
                body[offset_field] = cursor
                resp = requester(body)
                items = extractor(resp)
                if not items:
                    break
                results.extend(items)
                if offset_sent:
                    cursor = resolve_path(resp, offset_sent) or ""
                else:
                    cursor = ""
                if not cursor:
                    break
                if stop_on == "empty_list" and len(items) < size:
                    break

        else:
            resp = requester(base_body)
            results = extractor(resp)

        return results

    @staticmethod
    def extract_list(resp: dict, ep: dict) -> list:
        """Extract items from response using optional response_path config."""
        path = ep.get("response_path", "data.list")
        items = resolve_path(resp, path)
        if isinstance(items, list):
            return items
        data = resp.get("data")
        if isinstance(data, list):
            return data
        return []
