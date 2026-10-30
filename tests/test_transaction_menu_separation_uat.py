from app.screen_catalog import build_catalog, HO_TRANSACTION_ALIASES

EXPECTED_AGENT = [
 "special-rates-request","booking","planning","vessel-lock","cro","trt",
 "export-trt","transshipment-trt","bl","switch-bl","split-bl",
 "import-bl","import-trt","delivery-order","container-activity",
 "detention-collection","storage-cost","agent-receipt-pay","soa",
]
EXPECTED_HO = [
 "Agent Opening","Vendor Opening","Customer Opening","Container Purchase",
 "Container Sale","Purchase Invoice","Purchase Invoice Slot","Sale Invoice",
 "Payment","Receipt","Purchase Invoice Storage","Sale Invoice Detention",
 "Sale Invoice Import","Lease Rental Transaction","Container Exchange",
 "Exchange Rate Update","Third Party Deal Info","Third Party Tracking Info",
]

def test_ho_and_agent_transaction_menus_are_separate_complete_and_ordered():
    catalog=build_catalog()
    by_domain={m["domain"]:m for m in catalog["menu"]}
    ho=next(x for x in by_domain["HO Tasks"]["submenus"] if x["name"]=="Transaction")
    agent=next(x for x in by_domain["Agent Tasks"]["submenus"] if x["name"]=="Transaction")
    assert [x["label"] for x in HO_TRANSACTION_ALIASES]==EXPECTED_HO
    assert [x["label"] for x in ho["items"]]==EXPECTED_HO
    assert not ho["screens"]
    assert [screen.split("::",1)[1] for screen in agent["screens"]]==EXPECTED_AGENT
    assert not agent.get("items")
    assert catalog["screen_count"]==196

def test_transaction_links_use_existing_authoritative_screens():
    catalog=build_catalog()
    screens={s["screen_id"]:s for s in catalog["screens"]}
    assert all(alias["target"] in screens for alias in HO_TRANSACTION_ALIASES)
    assert all(screens[x["screen_id"]]["domain"]=="Agent Tasks"
               for x in catalog["screens"] if x["screen_id"] in
               next(sub["screens"] for menu in catalog["menu"] if menu["domain"]=="Agent Tasks"
                    for sub in menu["submenus"] if sub["name"]=="Transaction"))
