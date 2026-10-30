"""Regression: validated IAM SQLite rows must preserve maker/checker identity."""
import sqlite3

from app import masterdata


def test_authenticated_actor_normalizes_sqlite_row(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE actor (user_id INTEGER, user_ref TEXT, username TEXT)")
    db.execute("INSERT INTO actor VALUES (7,'maker-7','md.maker')")
    row = db.execute("SELECT * FROM actor").fetchone()
    monkeypatch.setattr(masterdata, "iam_session", lambda connection, token: row)
    monkeypatch.setattr(
        masterdata, "iam_roles_for",
        lambda connection, uid: [{"role_code": "MASTER_DATA"}],
    )
    try:
        session, roles = masterdata.authenticated_actor(db, "validated-token")
        assert type(session) is dict
        assert session["user_id"] == 7
        assert session["user_ref"] == "maker-7"
        assert session["username"] == "md.maker"
        assert roles == {"MASTER_DATA"}
    finally:
        db.close()
