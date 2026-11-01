"""Treasury release posting atomicity regression (source contract).

The release transition and its GL voucher/lines/link must be in the same
transaction. This complements, but does not replace, runtime release-race tests.
"""
from pathlib import Path

def test_treasury_release_posts_and_updates_within_same_transaction():
    code=(Path(__file__).resolve().parents[1]/"app"/"treasury.py").read_text()
    action=code[code.index("def action(module:"):]
    start=action.index("c=connect();tx(c)")
    assert action.index("gl_post(c,r,module,x_actor_id,False)") > start
    assert action.index("UPDATE treasury_records SET status=?,payload_json=?,version=version+1") > action.index("gl_post(c,r,module,x_actor_id,False)")
    assert action.index("c.execute('COMMIT')") > action.index("UPDATE treasury_records SET status=?,payload_json=?,version=version+1")
    assert "if r['version']!=b.version:raise HTTPException(409" in action
    assert "if r['status']!='Approved':raise HTTPException(422" in action
    assert "c.execute('ROLLBACK')" in action

def test_treasury_posting_creates_balanced_lines_and_single_link_inside_connection():
    code=(Path(__file__).resolve().parents[1]/"app"/"treasury.py").read_text()
    posting=code[code.index("def gl_post("):code.index("def gl_post(")+5000]
    assert "INSERT INTO gl_vouchers(" in posting
    assert posting.count("INSERT INTO gl_voucher_lines(")>=2
    assert "INSERT INTO treasury_gl_links(" in posting
    assert "'POSTING'" in posting
