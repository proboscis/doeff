"""Traceback dedup rendering — defense-in-depth for #386.

For non-passthrough handlers where Resume IS genuinely needed, consecutive
identical frames are compressed in collect_rich_context_from with a count
field, and format_default renders a multiplication-sign count marker.
"""
from doeff.traceback import format_default


def test_format_default_renders_repeat_count():
    """format_default should render a count marker for frames with count > 1."""
    exc = KeyError("test")
    exc.__doeff_traceback__ = [
        ["frame", "my_handler", "/fake/handler.py", 42, 15],
        ["frame", "my_body", "/fake/body.py", 10],
    ]
    rendered = format_default(exc)
    assert rendered is not None
    assert "[×15]" in rendered  # noqa: RUF001 - test fixture intentionally matches the literal rendered glyph
    for line in rendered.split("\n"):
        if "my_body" in line:
            assert "[×" not in line  # noqa: RUF001 - test fixture intentionally matches the literal rendered glyph


def test_format_default_omits_count_for_single():
    """Frames with count=1 or no count field should not show a count marker."""
    exc = KeyError("test")
    exc.__doeff_traceback__ = [
        ["frame", "fn_a", "/fake/a.py", 1, 1],
        ["frame", "fn_b", "/fake/b.py", 2],
    ]
    rendered = format_default(exc)
    assert rendered is not None
    assert "[×" not in rendered  # noqa: RUF001 - test fixture intentionally matches the literal rendered glyph
