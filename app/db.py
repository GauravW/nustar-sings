"""
Database connections and review schema definitions for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Attaches the triggered and blind databases read-only for page queries.
- Uses temporary views to normalize candidate fields without copying source records.
- Provides separate writable connections for short review transactions.
- Defines review tables and append-only audit triggers used by the explicit setup command.
- Does not create a third persistent database or alter pipeline journal settings.
"""
import sqlite3
from contextlib import closing
from pathlib import Path
from flask import current_app, g
from .constants import TAGS

DATABASES = {
    'triggered': ('TS_SOURCE_DATABASE', 'ts', 'ts_queue', 'NuID'),
    'blind': ('BS_SOURCE_DATABASE', 'bs', 'candidates', 'nuid'),
}


def connect(path, writable=False):
    db = sqlite3.connect(Path(path).resolve().as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def validate_sources(settings):
    required = {
        'triggered': {'ts_queue': {'NuID','trigger_time','ra','dec','missions_list','queue_status','position_owner','position_owner_notice_time'}},
        'blind': {'candidates': {'nuid','trigger_utc','status','obsid','seqid','widx','merged_into'},
                  'seq_status': {'seqid','obsid'}, 'windows': {'seqid','widx','t0_utc','t1_utc'},
                  'obs_runs': {'seqid','run_utc'}, 'nu_bs_ts_match': {'nubs_id','nuts_id','dt_s'}},
    }
    for kind, tables in required.items():
        with closing(connect(settings[DATABASES[kind][0]])) as db:
            for table, columns in tables.items():
                found = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                if not columns <= found:
                    raise ValueError(f'{kind}: missing {table} columns: {sorted(columns - found)}')


def review_schema(db, prefix, parent, key):
    """Only the explicit setup command calls this function."""
    tags = ','.join("'" + tag + "'" for tag in TAGS)
    db.execute(f'''CREATE TABLE IF NOT EXISTS {prefix}_reviews (
        nuid TEXT PRIMARY KEY REFERENCES {parent}({key}),
        classification TEXT NOT NULL CHECK(classification IN ({tags})),
        version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
        updated_at TEXT, updated_by TEXT)''')
    db.execute(f'''CREATE TABLE IF NOT EXISTS {prefix}_audit (
        id INTEGER PRIMARY KEY, nuid TEXT NOT NULL REFERENCES {parent}({key}),
        action TEXT NOT NULL DEFAULT 'classified', old_value TEXT, new_value TEXT NOT NULL,
        reviewer TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT,
        source TEXT NOT NULL, batch_id TEXT NOT NULL)''')
    db.execute(f'CREATE INDEX IF NOT EXISTS {prefix}_audit_nuid ON {prefix}_audit(nuid,id)')
    for operation in ('UPDATE','DELETE'):
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS {prefix}_audit_no_{operation.lower()}
            BEFORE {operation} ON {prefix}_audit BEGIN
            SELECT RAISE(ABORT, 'Review history is append-only'); END''')


def get_db():
    """Temporary views normalize the two formats without copying source rows."""
    if 'db' in g:
        return g.db
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    try:
        db.create_function('has_mission', 2, lambda missions, name: int(name in [m.strip() for m in (missions or '').split(',')]))
        for kind, (setting, prefix, parent, key) in DATABASES.items():
            uri = Path(current_app.config[setting]).resolve().as_uri() + '?mode=ro'
            db.execute(f'ATTACH DATABASE ? AS {prefix}', (uri,))
            exists = db.execute(f"SELECT 1 FROM {prefix}.sqlite_master WHERE type='table' AND name=?", (prefix+'_reviews',)).fetchone()
            if exists:
                db.execute(f'CREATE TEMP VIEW {prefix}_review_view AS SELECT * FROM {prefix}.{prefix}_reviews')
            else:
                db.execute(f'''CREATE TEMP VIEW {prefix}_review_view AS SELECT
                    NULL nuid, NULL classification, 0 version, NULL updated_at, NULL updated_by WHERE 0''')
        db.execute('''CREATE TEMP VIEW candidates AS
            SELECT t.NuID, 'triggered' search_type,t.trigger_time,t.ra,t.dec,t.missions_list,
                t.queue_status,t.position_owner,t.position_owner_notice_time,
                coalesce(r.classification,'unclassified') classification_tag,
                coalesce(r.version,0) classification_version,r.updated_at classification_updated_at,
                r.updated_by classification_updated_by
            FROM ts.ts_queue t LEFT JOIN ts_review_view r ON r.nuid=t.NuID
            UNION ALL
            SELECT b.nuid,'blind',b.trigger_utc,NULL,NULL,NULL,b.status,NULL,NULL,
                coalesce(r.classification,'unclassified'),coalesce(r.version,0),r.updated_at,r.updated_by
            FROM bs.candidates b LEFT JOIN bs_review_view r ON r.nuid=b.nuid''')
        for alias, source in [('blind_candidate_details','candidates'),('observations','seq_status'),
                               ('observation_windows','windows'),('observation_runs','obs_runs')]:
            db.execute(f'CREATE TEMP VIEW {alias} AS SELECT * FROM bs.{source}')
        db.execute('PRAGMA query_only=ON')
    except Exception:
        db.close()
        raise
    g.db = db
    return db
