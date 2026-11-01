"""Regression for Treasury replay request-hash validation.

A retry may reuse a key only for the identical request. A changed amount,
party or Job must be rejected, rather than returning an unrelated posting.
"""
from pathlib import Path

def test_treasury_replay_checks_hash_before_returning_saved_response():
    source=(Path(__file__).resolve().parents[1]/"app"/"treasury.py").read_text()
    create=source[source.index("def create(module:"):source.index("@router.put('/{module}/{rid}')")]
    assert "request_hash=hashlib.sha256(json.dumps(b.model_dump(),sort_keys=True).encode()).hexdigest()" in create
    assert "if old['request_hash']!=request_hash:" in create
    assert "raise HTTPException(409,'IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST')" in create
    assert create.index("if old['request_hash']!=request_hash:") < create.index("return json.loads(old['response_json'])")
    assert "(role,idempotency_key,request_hash,json.dumps(out),now())" in create
    assert "DUPLICATE_TREASURY_SOURCE" in create
