from app.screen_catalog import build_catalog
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_legacy_reference_flow_is_visible_without_duplicate_screens():
    c=build_catalog()
    assert c['screen_count']==196
    ho=next(x for x in c['menu'] if x['domain']=='HO Tasks')
    assert [x['name'] for x in ho['submenus']]==['Transaction','Utilities']
    assert '▾ Setup <span class=count>2</span>' in HTML
    assert 'Account Setup' in HTML
    assert "openScreen('gl-accounts::account-integration'" in HTML
    assert 'Container Coding' in HTML
    assert "showEquipmentWorkspace('containers')" in HTML

def test_reference_transaction_names_remain_visible():
    c=build_catalog()
    ho=next(x for x in c['menu'] if x['domain']=='HO Tasks')
    tx=next(x for x in ho['submenus'] if x['name']=='Transaction')
    labels=[x['label'] for x in tx.get('items',[])]
    for label in ['Agent Opening','Vendor Opening','Customer Opening','Container Purchase','Container Sale','Purchase Invoice','Sale Invoice','Payment','Receipt']:
        assert label in labels
