import sqlite3
from pathlib import Path

def test_schema_has_booking_scoped_customer_agent_pair_only():
    schema=Path('app/schema.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS bookings' in schema
    assert 'customer_id INTEGER NOT NULL REFERENCES customers(id)' in schema
    assert 'agent_id INTEGER NOT NULL REFERENCES agents(id)' in schema
    assert 'customer_agent' not in schema.lower()
    assert 'agent_customer' not in schema.lower()

def test_many_to_many_pairing_is_supported_by_independent_bookings():
    c=sqlite3.connect(':memory:')
    c.executescript('''
      CREATE TABLE customers(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT);
      CREATE TABLE agents(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT);
      CREATE TABLE bookings(id INTEGER PRIMARY KEY,booking_ref TEXT UNIQUE,customer_id INTEGER,agent_id INTEGER);
      INSERT INTO customers VALUES(1,'C-A','Customer A'),(2,'C-B','Customer B');
      INSERT INTO agents VALUES(1,'A-X','Agent X'),(2,'A-Y','Agent Y');
      INSERT INTO bookings VALUES
        (1,'B-AX',1,1),
        (2,'B-AY',1,2),
        (3,'B-BX',2,1);
    ''')
    rows=c.execute('SELECT customer_id,agent_id FROM bookings ORDER BY id').fetchall()
    assert rows==[(1,1),(1,2),(2,1)]

def test_runtime_exposes_booking_party_alignment_and_agent_scope_guard():
    src=Path('app/clx049_booking_bl_workspace.py').read_text()
    assert "relationship_model':'BOOKING_JOB_ONLY" in src
    assert "permanent_customer_agent_master_link':False" in src
    assert "agent_users_with_permanent_customer_party_grants" in src
    assert "CUSTOMER_AND_AGENT_RESOLVED_SEPARATELY" in src
    main=Path('app/main.py').read_text()
    assert "AGENT requires X-Agent-Scope" in main
    assert "return ' AND a.code=?',[agent_scope]" in main
