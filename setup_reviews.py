"""
One-time review-table setup for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Validates the existing triggered and blind databases; preview is the default.
- With --apply, backs up each database and creates ts_reviews/ts_audit or bs_reviews/bs_audit.
- Each database is updated in its own transaction; existing pipeline records are preserved.
- Optional --reviews-from transfers legacy classifications and history into empty review tables.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from app.config import load_settings
from app.db import DATABASES, connect, validate_sources, review_schema


def setup(settings, apply=False):
    validate_sources(settings)
    for kind, (setting, prefix, parent, key) in DATABASES.items():
        path = Path(settings[setting])
        with closing(connect(path)) as source:
            names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = {prefix+'_reviews', prefix+'_audit'} - names
            print(f'{kind}: {path}; missing tables: {sorted(missing)}')
            if not apply:
                continue
            backup = path.with_name(path.name + '.before-reviews-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.db')
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
            print(f'Backup: {backup}')
        with closing(connect(path, writable=True)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                review_schema(db, prefix, parent, key)
        print(f'{kind}: ready')


def migrate_reviews(settings, source_path, apply=False):
    """Optional one-time transfer; never overwrite destination reviews/history."""
    source_path = Path(source_path).resolve()
    for setting, _, _, _ in DATABASES.values():
        destination = Path(settings[setting]).resolve()
        if source_path == destination or source_path.samefile(destination):
            raise ValueError('The old review database must differ from both pipeline databases.')
    with closing(connect(source_path)) as source:
        rows = source.execute('SELECT * FROM candidates').fetchall()
        audit = source.execute('SELECT * FROM candidate_audit ORDER BY id').fetchall()
    batches = []
    for kind, (setting, prefix, parent, key) in DATABASES.items():
        with closing(connect(settings[setting])) as destination:
            ids = {r[0] for r in destination.execute(f'SELECT {key} FROM {parent}')}
            names = {r[0] for r in destination.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in (prefix+'_reviews',prefix+'_audit'):
                if table in names and destination.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                    raise ValueError(f'{table} already contains data; migration refuses to overwrite or duplicate history.')
        candidates = [r for r in rows if r['search_type'] == kind]
        missing = [r['NuID'] for r in candidates if r['NuID'] not in ids]
        if missing:
            raise ValueError(f'{kind}: old candidates absent from destination: {missing}')
        candidates_ids = {r['NuID'] for r in candidates}
        history = [r for r in audit if r['nuid'] in candidates_ids]
        print(f'{kind}: transfer {len(candidates)} review rows and {len(history)} history entries')
        batches.append((setting,prefix,candidates,history))
    if not apply:
        return
    setup(settings, apply=True)
    for setting,prefix,candidates,history in batches:
        with closing(connect(settings[setting], writable=True)) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute(f'SELECT 1 FROM {prefix}_reviews LIMIT 1').fetchone() or db.execute(f'SELECT 1 FROM {prefix}_audit LIMIT 1').fetchone():
                    raise ValueError(f'{prefix}: reviews changed during migration; stopped.')
                db.executemany(f'INSERT INTO {prefix}_reviews VALUES(?,?,?,?,?)',
                    [(r['NuID'],r['classification_tag'],r['classification_version'],r['classification_updated_at'],r['classification_updated_by']) for r in candidates])
                db.executemany(f"""INSERT INTO {prefix}_audit(nuid,action,old_value,new_value,reviewer,created_at,reason,source,batch_id)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    [(r['nuid'],r['action'],r['old_value'],r['new_value'],r['reviewer'],r['created_at'],r['reason'],r['source'] or 'legacy',r['batch_id'] or 'legacy-transfer') for r in history])
        print(f'{prefix}: review transfer committed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--reviews-from', type=Path, help='Optional old interface database; requires empty destination review tables')
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        if args.reviews_from:
            migrate_reviews(settings,args.reviews_from,args.apply)
        else:
            setup(settings,args.apply)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Setup stopped: {error}\n')
