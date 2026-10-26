"""Guard menu target authorization while preserving authoritative master records."""
from app.screen_catalog import (
    build_catalog, AGENT_SETUP_ALIASES, HO_TRANSACTION_ALIASES,
    HO_UTILITY_ALIASES, BUSINESS_NAV_ALIASES,
)

def test_shared_master_targets_keep_original_role_contracts():
    catalog=build_catalog()
    screens={s["screen_id"]:s for s in catalog["screens"]}
    important={
        "master-data::agents","master-data::customers","master-data::common-partys",
        "master-data::vessels","master-data::voyages","master-data::commoditys",
        "master-data::units","master-data::sales-persons",
    }
    assert important<=screens.keys()
    for target in important:
        assert isinstance(screens[target].get("roles"),list)
        assert screens[target]["roles"],target

def test_navigation_shortcuts_do_not_override_underlying_screen_permissions():
    catalog=build_catalog()
    screens={s["screen_id"]:s for s in catalog["screens"]}
    collections=[AGENT_SETUP_ALIASES,HO_TRANSACTION_ALIASES,
                 HO_UTILITY_ALIASES,*BUSINESS_NAV_ALIASES.values()]
    for collection in collections:
        for alias in collection:
            target=screens[alias["target"]]
            if alias.get("roles"):
                assert isinstance(alias["roles"],list)
                assert alias["roles"]
            assert "roles" in target,(alias["label"],alias["target"])

def test_customer_and_agent_opening_always_refer_to_same_master_screens():
    ho={item["label"]:item["target"] for item in HO_TRANSACTION_ALIASES}
    setup={item["label"]:item["target"] for item in AGENT_SETUP_ALIASES}
    business={item["label"]:item["target"] for item in BUSINESS_NAV_ALIASES["Setup"]}
    assert ho["Agent Opening"]==setup["Agent"]=="master-data::agents"
    assert ho["Customer Opening"]==business["Customer"]=="master-data::customers"
