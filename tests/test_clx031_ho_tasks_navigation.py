from app.screen_catalog import build_catalog, HO_TRANSACTION_ALIASES, HO_UTILITY_ALIASES
from app.screen_integration import menu


EXPECTED_TX=[
 "Agent Opening","Vendor Opening","Customer Opening","Container Purchase","Container Sale",
 "Purchase Invoice","Purchase Invoice Slot","Sale Invoice","Payment","Receipt",
 "Purchase Invoice Storage","Sale Invoice Detention","Sale Invoice Import","Lease Rental Transaction",
 "Container Exchange","Exchange Rate Update","Third Party Deal Info","Third Party Tracking Info",
]


def test_ho_tasks_restored_without_screen_duplication():
    c=build_catalog()
    assert c["screen_count"]==196
    assert len(c["screens"])==196
    assert [x["label"] for x in HO_TRANSACTION_ALIASES]==EXPECTED_TX
    assert len(HO_UTILITY_ALIASES)>=10
    ho=next(x for x in c["menu"] if x["domain"]=="HO Tasks")
    assert ho["domain"]=="HO Tasks"
    assert [x["name"] for x in ho["submenus"]]==["Transaction","Utilities"]
    assert ho["navigation_only"] is True
    assert ho["screen_count"]==0
    ids={s["screen_id"] for s in c["screens"]}
    assert all(x["target"] in ids for x in HO_TRANSACTION_ALIASES+HO_UTILITY_ALIASES)


def test_ho_alias_menu_search_and_order():
    r=menu("Third Party")
    ho=next(x for x in r["menu"] if x["domain"]=="HO Tasks")
    tx=next(x for x in ho["submenus"] if x["name"]=="Transaction")
    assert [x["label"] for x in tx["items"]]==["Third Party Deal Info","Third Party Tracking Info"]

    u=menu("Fiscal Year")
    ho=next(x for x in u["menu"] if x["domain"]=="HO Tasks")
    util=next(x for x in ho["submenus"] if x["name"]=="Utilities")
    assert util["items"][0]["target"]=="gl-accounts::fiscal-year"


def test_aliases_reuse_existing_business_logic():
    c=build_catalog()
    by_id={s["screen_id"]:s for s in c["screens"]}
    for alias in HO_TRANSACTION_ALIASES+HO_UTILITY_ALIASES:
        target=by_id[alias["target"]]
        assert target["route"]
        assert target["actions"]
        assert "quick-view" in target["quick_actions"]
