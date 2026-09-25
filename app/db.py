from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parent.parent / 'm3_clx013_test.db'
DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
APPROVED_PROJECT_REF = 'ozupgknqaqgvprliewxe'

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - sqlite test/dev fallback
    psycopg = None
    dict_row = None


def using_postgres() -> bool:
    return bool(DATABASE_URL)


def approved_database_target() -> bool:
    return (not DATABASE_URL) or (APPROVED_PROJECT_REF in DATABASE_URL)


def assert_approved_database_target() -> None:
    if DATABASE_URL and APPROVED_PROJECT_REF not in DATABASE_URL:
        raise RuntimeError('DATABASE_URL_TARGET_REJECTED: only M3-NVOCC-PROD ozupgknqaqgvprliewxe is approved for CLX-016')


def backend_name() -> str:
    return 'postgres' if using_postgres() else 'sqlite-test'


def database_label() -> str:
    return 'postgres' if using_postgres() else DB_PATH.name


def table_exists(conn, name: str) -> bool:
    if using_postgres():
        return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (name,)).fetchone())
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def trigger_exists(conn, name: str) -> bool:
    if using_postgres():
        return bool(conn.execute("SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname=?", (name,)).fetchone())
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone())


def list_public_tables(conn) -> list[str]:
    if using_postgres():
        return [r['name'] for r in conn.execute("SELECT table_name AS name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
    return [r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def list_public_triggers(conn) -> list[str]:
    if using_postgres():
        return [r['name'] for r in conn.execute("SELECT DISTINCT t.tgname AS name FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY t.tgname")]
    return [r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")]


def database_health(conn) -> dict:
    if using_postgres():
        row = conn.execute("SELECT current_database() AS database, current_schema() AS schema, version() AS version").fetchone()
        return {'status':'ok','backend':'postgres','database':row['database'],'schema':row['schema'],'server_version':row['version']}
    quick=conn.execute('PRAGMA quick_check').fetchone()[0]
    fk=list(conn.execute('PRAGMA foreign_key_check'))
    return {'status':'ok' if quick=='ok' and not fk else 'fail','backend':'sqlite-test','quick_check':quick,'foreign_key_violations':len(fk)}


def _qmark_to_pyformat(sql: str) -> str:
    """Translate sqlite qmark placeholders to psycopg %s outside SQL string literals."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                # SQL escapes quotes by doubling them.
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == '?':
                out.append('%s')
            else:
                out.append(ch)
        i += 1
    return ''.join(out)


_LASTROWID_TABLES = {
    'transaction_records', 'gl_records', 'gl_vouchers', 'gl_bank_statements',
    'gl_bank_statement_items', 'treasury_records', 'treasury_batches',
    'treasury_payment_batches',
    'integration_events', 'bank_import_batches', 'sandbox_payment_requests',
    'clx012_runs', 'clx012_stage_events', 'clx012_exceptions',
    'clx012_action_queue', 'clx012_events',
}


def _translate_insert_or_replace(sql: str) -> str:
    # The accepted CLX runtime only uses OR REPLACE for singleton config rows keyed by id.
    m = re.match(
        r'\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$',
        sql,
        flags=re.I | re.S,
    )
    if not m:
        return sql
    table, columns_raw, values_raw = m.groups()
    columns = [c.strip() for c in columns_raw.split(',')]
    if 'id' not in [c.lower() for c in columns]:
        raise RuntimeError(f'Unsupported INSERT OR REPLACE without id key: {table}')
    updates = ','.join(f'{c}=EXCLUDED.{c}' for c in columns if c.lower() != 'id')
    return f"INSERT INTO {table} ({','.join(columns)}) VALUES ({values_raw}) ON CONFLICT (id) DO UPDATE SET {updates}"


def _translate_sql(sql: str) -> tuple[str, bool]:
    s = sql.strip().rstrip(';')
    s = _translate_insert_or_replace(s)
    ignore = bool(re.match(r'^INSERT\s+OR\s+IGNORE\s+INTO\b', s, flags=re.I))
    if ignore:
        s = re.sub(r'^INSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT INTO', s, count=1, flags=re.I)
        if not re.search(r'\bON\s+CONFLICT\b', s, flags=re.I):
            s += ' ON CONFLICT DO NOTHING'
    # SQLite date arithmetic used by AR/AP aging.
    s = re.sub(
        r"julianday\('([^']+)'\)\s*-\s*julianday\(([^)]+)\)",
        r"(CAST('\1' AS date)-CAST(\2 AS date))",
        s,
        flags=re.I,
    )
    # SQLite json_set used by the GL voucher write-through. payload_json is TEXT
    # in the preserved M3 PostgreSQL schema, so cast through jsonb and back to text.
    s = re.sub(
        r"json_set\(payload_json,\s*'\$\.\"Voucher No\.\"',\s*\?\)",
        "jsonb_set(COALESCE(payload_json::jsonb,'{}'::jsonb), '{Voucher No.}', to_jsonb(?::text), true)::text",
        s,
        flags=re.I,
    )
    # Translate the remaining SQLite date(expr) calls used by the frozen runtime.
    s = re.sub(r'\bdate\(([^()]+)\)', r'CAST(\1 AS date)', s, flags=re.I)
    s = _qmark_to_pyformat(s)

    wants_lastrowid = False
    m = re.match(r'^INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\b', s, flags=re.I)
    if m and m.group(1).lower() in _LASTROWID_TABLES and not re.search(r'\bRETURNING\b', s, flags=re.I):
        s += ' RETURNING id'
        wants_lastrowid = True
    return s, wants_lastrowid


class PostgresCursorCompat:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)

    @property
    def rowcount(self):
        return self._cursor.rowcount


class PostgresConnectionCompat:
    backend = 'postgres'

    def __init__(self, url: str):
        if psycopg is None:
            raise RuntimeError('DATABASE_URL is set but psycopg is unavailable')
        # autocommit matches the historical sqlite isolation_level=None behavior.
        self._conn = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=15)

    def execute(self, sql: str, params: Iterable[Any] | None = None):
        translated, wants_lastrowid = _translate_sql(sql)
        cur = self._conn.cursor()
        cur.execute(translated, tuple(params or ()))
        lastrowid = None
        if wants_lastrowid:
            row = cur.fetchone()
            if row:
                lastrowid = row.get('id') if isinstance(row, dict) else row[0]
        return PostgresCursorCompat(cur, lastrowid)

    def executescript(self, script: str):
        # Production schema is migration-managed in Supabase. Runtime CREATE-only
        # bootstrap scripts are intentionally skipped to avoid SQLite DDL on Postgres.
        statements = [s.strip() for s in script.split(';') if s.strip()]
        unsafe = [s for s in statements if not re.match(r'^(CREATE\s+(TABLE|INDEX|TRIGGER)|--)', s, flags=re.I | re.S)]
        if unsafe:
            raise RuntimeError('PostgreSQL runtime refuses non-DDL executescript; use migrations instead')
        return self

    def close(self):
        self._conn.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc:
            try:
                self.rollback()
            except Exception:
                pass
        self.close()


def connect():
    if using_postgres():
        assert_approved_database_target()
        return PostgresConnectionCompat(DATABASE_URL)
    c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA busy_timeout=15000')
    return c


def tx(conn):
    conn.execute('BEGIN' if using_postgres() else 'BEGIN IMMEDIATE')
    return conn


if psycopg is not None:
    IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    IntegrityError = (sqlite3.IntegrityError,)