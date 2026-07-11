"""Tests for cherrypick.core.viz — the declarative dashboard-section contract.

The client renderer is JS (exercised end-to-end by the umbrella/live); here we cover the server-side
skeleton and that the shared style/script constants are present and non-empty.
"""

from cherrypick.core import viz


def test_card_skeleton_has_section_hooks():
    html = viz.card_skeleton_html("gex", "GEX — SPX", "/api/section/gex", refresh=15)
    assert 'data-cp-section="gex"' in html
    assert 'data-endpoint="/api/section/gex"' in html
    assert 'data-refresh="15"' in html
    # the containers the client renderer fills
    for cls in ("cpsub", "cpmetrics", "cpchart", "cpnote"):
        assert cls in html


def test_card_skeleton_escapes_untrusted_title_and_id():
    html = viz.card_skeleton_html("x<i>", "<script>alert(1)</script>", "/api/section/x", refresh=30)
    assert "<script>" not in html and "<i>" not in html
    assert "&lt;script&gt;" in html and "&lt;i&gt;" in html
    assert 'data-refresh="30"' in html


def test_style_and_script_constants_present():
    assert viz.SECTION_STYLE and ".cpmetrics" in viz.SECTION_STYLE and ".cpbar" in viz.SECTION_STYLE
    assert viz.SECTION_JS and "data-cp-section" in viz.SECTION_JS and "data-endpoint" in viz.SECTION_JS
