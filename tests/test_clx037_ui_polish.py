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
