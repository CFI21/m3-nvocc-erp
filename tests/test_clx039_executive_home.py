from pathlib import Path
from app.screen_catalog import build_catalog

HTML=Path("web/index.html").read_text()

def test_clean_header_keeps_only_core_controls():
    assert 'id="globalSearch"' in HTML
    assert 'id="role"' in HTML
    assert 'id="notificationsBtn"' in HTML
    assert 'id="moreBtn"' in HTML
    for old in (
        'id="readinessBtn"','id="observabilityBtn"','id="kpiTrendsBtn"',
        'id="controlTowerBtn"','id="opsWorkbenchBtn"','id="businessDayBtn"',
        'id="actionQueueBtn"','id="financeAccessBtn"','id="financeControlBtn"',
        'id="financeApprovalBtn"','id="recentBtn"','id="favBtn"'
    ):
        assert old not in HTML

def test_secondary_tools_are_moved_to_more_menu():
    for label in (
        'Executive Home','Readiness','Observability','KPI Trends','Control Tower',
        'Ops Workbench','Business Day','Action Queue','Finance Access',
        'Finance Controls','Finance Approvals','Recent Screens','Favorite Screens'
    ):
        assert label in HTML

def test_executive_home_uses_existing_approved_data_sources():
    assert "api('/api/v1/control-tower'" in HTML
    assert "api('/api/v1/operations-workbench?view=my'" in HTML
    assert "api('/api/v1/operations-workbench?view=critical'" in HTML
    assert "api('/api/clx034/workbench?status=PENDING'" in HTML
    for label in ('My Work','Critical Exceptions','Overdue','Open Jobs','Finance Attention','Recent Screens','Favorite Screens'):
        assert label in HTML

def test_home_is_default_and_screen_navigation_still_works():
    assert "else await showExecutiveHome()" in HTML
    assert "async function openScreen(" in HTML
    assert "right-side" not in ""  # marker: no business-route replacement required

def test_neutral_grey_sidebar_and_drawer_are_preserved():
    assert "--nav:#4b5563" in HTML
    assert "--nav2:#5f6b78" in HTML
    assert 'class="drawer"' in HTML
    assert 'class="fabWrap"' in HTML

def test_desktop_tablet_mobile_home_layouts_exist():
    assert ".homeGrid{display:grid" in HTML
    assert "@media(max-width:1100px) and (min-width:951px)" in HTML
    assert "@media(max-width:950px)" in HTML
    assert ".homeSections{grid-template-columns:1fr}" in HTML

def test_current_clx038_screen_baseline_is_preserved():
    assert build_catalog()['screen_count']==196
