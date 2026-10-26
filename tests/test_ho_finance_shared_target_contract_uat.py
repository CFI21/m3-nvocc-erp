"""Guard HO Finance screen contracts and their shared navigation targets.

These are metadata integration tests, not proof of posted-ledger idempotency.
No live database is accessed.
"""
from app.clx044_gl_exact_flow import FLOW
from app.screen_catalog import build_catalog, HO_TRANSACTION_ALIASES

FINANCE_TARGETS={
    "Purchase Invoice":"gl-accounts::bills",
    "Purchase Invoice Slot":"gl-accounts::bills",
    "Container Purchase":"gl-accounts::bills",
    "Sale Invoice":"gl-accounts::invoice",
    "Sale Invoice Import":"gl-accounts::invoice",
    "Container Sale":"gl-accounts::invoice",
    "Payment":"gl-accounts::payment",
    "Receipt":"gl-accounts::receipt",
}

def test_ho_finance_navigation_targets_single_existing_authority():
    aliases={item["label"]:item["target"] for item in HO_TRANSACTION_ALIASES}
    screens={s["screen_id"]:s for s in build_catalog()["screens"]}
    for label,target in FINANCE_TARGETS.items():
        assert aliases[label]==target,(label,aliases[label])
        assert target in screens
    assert len({aliases[label] for label in FINANCE_TARGETS})==4

def test_finance_forms_keep_distinct_business_reference_and_party_fields():
    for module,required in {
        "invoice":{"Invoice No.","Customer / Supplier","Currency","Invoice Amount","Status"},
        "bills":{"Bill No.","Vendor","Currency","Invoice Amount","Status"},
        "payment":{"Voucher No.","Party Account","Payment Amount","Currency","Status"},
        "receipt":{"Voucher No.","Party Account","Receipt Amount","Currency","Status"},
    }.items():
        assert required<=set(FLOW[module]["fields"]),(module,required-set(FLOW[module]["fields"]))
    assert "Bill No." not in FLOW["invoice"]["fields"]
    assert "Invoice No." not in FLOW["bills"]["fields"]

def test_finance_invoice_and_settlement_modules_have_separate_authoritative_metadata():
    for key in ("invoice","bills","payment","receipt"):
        assert key in FLOW
        assert FLOW[key]["fields"]
        assert FLOW[key]["tabs"]
        assert FLOW[key]["columns"]
