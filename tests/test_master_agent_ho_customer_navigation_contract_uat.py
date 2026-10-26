"""Navigation compatibility: HO, Agent, Customer and Master Data share one authority.

Repeated destinations across menu contexts are intentional aliases, never duplicate
business models. This test does not relax access control or change master records.
"""
from collections import defaultdict

from app.screen_catalog import (
    build_catalog, AGENT_SETUP_ALIASES, HO_TRANSACTION_ALIASES,
    HO_UTILITY_ALIASES, BUSINESS_NAV_ALIASES,
)

MASTER_SHARED = {
    "Agent": "master-data::agents",
    "Agent Opening": "master-data::agents",
    "Customer": "master-data::customers",
    "Customer Opening": "master-data::customers",
    "Common Parties": "master-data::common-partys",
    "Vessel": "master-data::vessels",
    "Voyage Registration": "master-data::voyages",
    "Commodity": "master-data::commoditys",
    "Units": "master-data::units",
    "Sales Person": "master-data::sales-persons",
}

FINANCE_SHARED = {
    "Container Purchase": "gl-accounts::bills",
    "Purchase Invoice": "gl-accounts::bills",
    "Purchase Invoice Slot": "gl-accounts::bills",
    "Container Sale": "gl-accounts::invoice",
    "Sale Invoice": "gl-accounts::invoice",
    "Sale Invoice Import": "gl-accounts::invoice",
}

def test_all_navigation_aliases_resolve_to_existing_screens_and_have_no_local_label_collisions():
    catalog=build_catalog()
    screens={s["screen_id"]:s for s in catalog["screens"]}
    assert len(screens)==catalog["screen_count"]==196
    groups=[
        ("Agent/Setup",AGENT_SETUP_ALIASES),
        ("HO/Transaction",HO_TRANSACTION_ALIASES),
        ("HO/Utilities",HO_UTILITY_ALIASES),
        *[(f"Business/{name}",items) for name,items in BUSINESS_NAV_ALIASES.items()],
    ]
    for group,items in groups:
        labels=[item["label"].strip().casefold() for item in items]
        assert len(labels)==len(set(labels)),f"duplicate label in {group}"
        for item in items:
            assert item["target"] in screens,(group,item["label"],item["target"])

def test_master_agent_and_customer_navigation_share_only_authoritative_targets():
    by_label=defaultdict(set)
    for collection in (AGENT_SETUP_ALIASES,HO_TRANSACTION_ALIASES,
                       HO_UTILITY_ALIASES,*BUSINESS_NAV_ALIASES.values()):
        for item in collection:
            by_label[item["label"]].add(item["target"])
    for label,target in {**MASTER_SHARED,**FINANCE_SHARED}.items():
        assert by_label[label]=={target},(label,by_label[label])

def test_ho_agent_navigation_remains_separate_without_copying_screens():
    catalog=build_catalog()
    menu={x["domain"]:x for x in catalog["menu"]}
    ho=next(x for x in menu["HO Tasks"]["submenus"] if x["name"]=="Transaction")
    agent=next(x for x in menu["Agent Tasks"]["submenus"] if x["name"]=="Transaction")
    setup=next(x for x in menu["Agent Tasks"]["submenus"] if x["name"]=="Setup")
    assert len(ho["items"])==18 and not ho["screens"]
    assert len(agent["screens"])==19 and not agent.get("items")
    assert len(set(agent["screens"]))==19
    assert len(setup["items"])==7 and not setup["screens"]
    assert catalog["extension_count"]==0
