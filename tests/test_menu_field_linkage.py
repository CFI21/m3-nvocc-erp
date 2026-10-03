from app.screen_catalog import build_catalog
from app.screen_integration import menu, field_contract

def test_all_196_screens_have_route_and_related_field_contract():
    c=build_catalog()
    assert c['screen_count']==196
    for s in c['screens']:
        fc=field_contract(s['screen_id'])
        assert fc['screen_id']==s['screen_id']
        assert fc['route']
        assert fc['fields'], s['screen_id']
        assert fc['columns'], s['screen_id']
        assert fc['field_contract_api'].startswith('/api/clx011/field-contract?screen_id=')
        assert fc['screen_data_api'].startswith('/api/clx011/screen-data?screen_id=')
        assert fc['related_records_api']=='/api/clx011/related/{job_ref}'
        assert fc['authoritative'] is True
        assert fc['parallel_model'] is False

def test_active_menu_entries_expose_target_route_and_fields():
    m=menu()
    assert m['screen_count']==196
    linked=0
    aliases=0
    for domain in m['menu']:
        for sub in domain['submenus']:
            for link in sub.get('links',[]):
                linked+=1
                assert link['route']
                assert link['fields']
                assert link['field_contract_api']
                assert link['screen_data_api']
            for item in sub.get('items',[]):
                aliases+=1
                assert item['target']
                assert item['route']
                assert item['fields']
                assert item['field_contract_api']
                assert item['screen_data_api']
    assert linked==196
    assert aliases>0

def test_menu_search_preserves_field_links():
    m=menu('booking')
    assert m['menu']
    found=False
    for domain in m['menu']:
        for sub in domain['submenus']:
            for link in sub.get('links',[]):
                assert link['fields']
                found=True
            for item in sub.get('items',[]):
                assert item['fields']
                found=True
    assert found
