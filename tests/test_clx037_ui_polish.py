from pathlib import Path

HTML=Path("web/index.html").read_text()

def test_sidebar_uses_neutral_grey_palette():
    assert "--nav:#4b5563" in HTML
    assert "--nav2:#5f6b78" in HTML
    assert "#1f2d2a" not in HTML
    assert "#29423b" not in HTML

def test_sidebar_text_and_active_state_remain_readable():
    assert "color:#f9fafb" in HTML
    assert "color:#e5e7eb" in HTML
    assert "color:#f3f4f6" in HTML
    assert "border-left-color:#d1d5db" in HTML


def test_sidebar_spacing_and_focus_are_standardized():
    assert "padding:10px 14px" in HTML
    assert "padding-left:24px" in HTML
    assert "padding:8px 14px 8px 38px" in HTML
    assert "focus-visible" in HTML

def test_tablet_breakpoint_preserves_sidebar_without_overlap():
    assert "@media(max-width:1100px) and (min-width:951px)" in HTML
    assert ".app{grid-template-columns:248px 1fr}" in HTML
    assert ".top{flex-wrap:wrap" in HTML
    assert ".content{padding:12px}" in HTML

def test_mobile_breakpoint_remains_safe():
    assert "@media(max-width:950px)" in HTML
    assert ".side{display:none}" in HTML
    assert ".drawer{width:100%}" in HTML
