from pathlib import Path
import json,re

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')
NGINX=(ROOT/'web/nginx.conf').read_text(encoding='utf-8')
WF=(ROOT/'.github/workflows/m3-ghcr-web.yml').read_text(encoding='utf-8')

def test_visible_version_and_build_sha_placeholders():
    assert 'CLX-080 · Live Web Hardening · Build' in HTML
    assert '__M3_BUILD_SHA__' in HTML
    assert '__M3_BUILD_SHA_SHORT__' in HTML
    assert 'meta name="m3-build-sha"' in HTML
    assert 'meta name="m3-ui-baseline" content="CLX-080"' in HTML

def test_main_workflow_stamps_exact_sha_and_only_main_publishes():
    assert 'branches: [main]' in WF
    assert 'Stamp immutable build version' in WF
    assert 'GITHUB_SHA' in WF
    assert 'type=raw,value=latest' in WF
    assert 'type=sha' in WF

def test_html_is_explicitly_non_cacheable():
    assert 'Cache-Control "no-store, no-cache, must-revalidate, max-age=0"' in NGINX
    assert 'location = / {' in NGINX
    assert 'location = /index.html {' in NGINX
    assert 'Pragma "no-cache"' in NGINX
    assert 'Expires "0"' in NGINX
    assert 'etag off' in NGINX

def test_equipment_and_screen_deep_links():
    assert "history.pushState(null,'','#equipment='+encodeURIComponent(view))" in HTML
    assert "history.pushState(null,'','#screen='+encodeURIComponent(id))" in HTML
    assert "raw.match(/equipment=([^&]+)/)" in HTML
    assert "raw.match(/screen=([^&]+)/)" in HTML

def test_back_forward_uses_popstate_without_rewriting_history():
    assert "window.addEventListener('popstate',()=>routeFromLocation('none'))" in HTML
    assert "showEquipmentWorkspace(eq,historyMode)" in HTML
    assert "openScreen(screen,null,historyMode)" in HTML

def test_role_change_does_not_reset_current_route():
    m=re.search(r"\$\('role'\)\.addEventListener\('change'.{0,1000}",HTML,re.S)
    assert m
    assert 'showExecutiveHome' not in m.group(0)
    assert 'location.hash' not in m.group(0)

def test_clx079_tower_preserved_not_old_duplicate_nav():
    assert 'LIVE M3 EQUIPMENT CONTROL — AUTHORITATIVE CONTAINER MASTER' in HTML
    assert 'Global Stock Position' in HTML
    assert 'Recommended Actions' in HTML
    assert 'Operational Exceptions' in HTML
    shell=HTML[HTML.index('function equipmentShell(view)'):HTML.index('function equipHeaders()')]
    assert 'Allocation & Shortage</button>' not in shell
    assert 'Work Items</button>' not in shell
    assert '>Global Equipment Tower</button><button' not in shell

def test_safety_manifest():
    x=json.loads((ROOT/'CLX080_WEB_HARDENING_ACCEPTANCE.json').read_text())
    assert x['backend_model_changed'] is False
    assert x['database_changed'] is False
    assert x['REAL_MONEY_READY'] is False
    assert x['REAL_PROVIDER_READY'] is False
