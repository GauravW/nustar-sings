"""
Candidate queries and review operations for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Validates filters and retrieves combined candidate counts, chart totals, and paginated results.
- Saves classification changes with reviewer, reason, version checks, and audit entries.
- Triggered and blind bulk groups commit separately and report failures independently.
- Reads stored pipeline crossmatches for links between NuTS and NuBS records.
- Leaves scientific metadata and pipeline processing statuses unchanged.
"""
from datetime import date, timedelta, datetime, timezone
from uuid import uuid4

import sqlite3
from contextlib import closing
from flask import abort, current_app

from .constants import TAGS
from .db import get_db, connect, DATABASES
from .products import candidate_products


def read_filters(arguments):
    filters = {
        'start': arguments.get('start', ''),
        'end': arguments.get('end', ''),
        'tag': arguments.get('tag', ''),
        'nuid': arguments.get('nuid', '').strip(),
        'owner': arguments.get('owner', ''),
        'mission': arguments.get('mission', ''),
        'status': arguments.get('status', ''),
        'search_type': arguments.get('search_type', ''),
        'observation': arguments.get('observation', '').strip(),
    }

    for field in ['start', 'end']:
        if filters[field]:
            try:
                parsed = date.fromisoformat(filters[field])
                filters[field] = parsed.isoformat()
                if field == 'end':
                    parsed + timedelta(days=1)
            except (ValueError, OverflowError):
                abort(400, 'Please enter a valid date.')

    if filters['start'] and filters['end'] and filters['start'] > filters['end']:
        abort(400, 'Start date must come before end date.')

    if filters['tag'] and filters['tag'] not in TAGS:
        abort(400, 'Unknown classification.')

    if filters['search_type'] not in ('', 'triggered', 'blind'):
        abort(400, 'Unknown search type.')

    return filters


def build_filter_query(filters):
    conditions = []
    values = []

    if filters['start']:
        conditions.append('trigger_time >= ?')
        values.append(filters['start'] + 'T00:00:00.000')

    if filters['end']:
        next_day = date.fromisoformat(filters['end']) + timedelta(days=1)
        conditions.append('trigger_time < ?')
        values.append(next_day.isoformat() + 'T00:00:00.000')

    if filters['tag']:
        conditions.append('classification_tag = ?')
        values.append(filters['tag'])

    if filters['nuid']:
        conditions.append('instr(lower(NuID), lower(?)) > 0')
        values.append(filters['nuid'])
    if filters['owner'] == '__unknown__':
        conditions.append("(position_owner IS NULL OR trim(position_owner) = '')")
    elif filters['owner']:
        conditions.append('position_owner = ?')
        values.append(filters['owner'])
    if filters['mission']:
        conditions.append("has_mission(missions_list, ?) = 1")
        values.append(filters['mission'])
    if filters['status']:
        conditions.append('queue_status = ?')
        values.append(filters['status'])

    if filters['search_type']:
        conditions.append('search_type = ?')
        values.append(filters['search_type'])
    if filters['observation']:
        conditions.append("""EXISTS (SELECT 1 FROM blind_candidate_details AS b
            WHERE b.nuid=candidates.NuID AND
            (instr(lower(b.obsid),lower(?)) > 0 OR instr(lower(b.seqid),lower(?)) > 0))""")
        values.extend([filters['observation'], filters['observation']])

    if conditions:
        return ' WHERE ' + ' AND '.join(conditions), values
    return '', values


def count_candidates(filters):
    where, values = build_filter_query(filters)
    row = get_db().execute('SELECT COUNT(*) FROM candidates' + where, values).fetchone()
    return row[0]


def get_candidates(filters, page, per_page=10):
    where, values = build_filter_query(filters)
    offset = (page - 1) * per_page
    return get_db().execute(
        'SELECT * FROM candidates' + where
        + ' ORDER BY trigger_time DESC, NuID DESC LIMIT ? OFFSET ?',
        values + [per_page, offset],
    ).fetchall()


def get_candidate(nuid):
    return get_db().execute(
        'SELECT * FROM candidates WHERE NuID = ?', (nuid,)
    ).fetchone()


def filter_options():
    rows = get_db().execute('SELECT position_owner, missions_list, queue_status FROM candidates').fetchall()
    return {
        'owners': sorted({r['position_owner'] for r in rows if r['position_owner'] and r['position_owner'].strip()}),
        'missions': sorted({m.strip() for r in rows for m in (r['missions_list'] or '').split(',') if m.strip()}),
        'statuses': sorted({r['queue_status'] for r in rows if r['queue_status']}),
    }


def classification_counts(filters):
    where, values = build_filter_query(filters)
    rows = get_db().execute('SELECT classification_tag, COUNT(*) FROM candidates' + where + ' GROUP BY classification_tag', values)
    counts = dict(rows)
    return [{'tag': tag, 'label': info[0], 'color': info[2], 'count': counts.get(tag, 0)} for tag, info in TAGS.items()]


def update_classifications(selected, versions, tag, reviewer, reason, source='scanning'):
    """Each database commits its review group independently, including its audit."""
    if tag not in TAGS:
        abort(400, 'Unknown classification.')
    groups = {'triggered': [], 'blind': []}
    for nuid in selected:
        row = get_candidate(nuid)
        if row is None:
            abort(409, 'Candidate no longer exists; reload the page.')
        groups[row['search_type']].append(nuid)
    result = {'changed': 0, 'errors': []}
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
    batch_id = str(uuid4())
    for kind, nuids in groups.items():
        if not nuids:
            continue
        setting, prefix, parent, key = DATABASES[kind]
        count = 0
        try:
            with closing(connect(current_app.config[setting], writable=True)) as db:
                ready = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {prefix+'_reviews', prefix+'_audit'} <= ready:
                    raise ValueError('Review tables are missing. Run setup_reviews.py --apply first.')
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    for nuid in nuids:
                        if not db.execute(f'SELECT 1 FROM {parent} WHERE {key}=?', (nuid,)).fetchone():
                            raise ValueError('A candidate disappeared. Reload before retrying.')
                        old = db.execute(f'SELECT * FROM {prefix}_reviews WHERE nuid=?', (nuid,)).fetchone()
                        version = old['version'] if old else 0
                        classification = old['classification'] if old else 'unclassified'
                        if version != versions.get(nuid):
                            raise ValueError('A review changed since the page loaded. Reload before retrying.')
                        if classification == tag:
                            continue
                        db.execute(f"""INSERT INTO {prefix}_reviews(nuid,classification,version,updated_at,updated_by)
                            VALUES(?,?,?,?,?) ON CONFLICT(nuid) DO UPDATE SET
                            classification=excluded.classification,version=excluded.version,
                            updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                            (nuid, tag, version+1, timestamp, reviewer))
                        db.execute(f"""INSERT INTO {prefix}_audit
                            (nuid,old_value,new_value,reviewer,created_at,reason,source,batch_id)
                            VALUES(?,?,?,?,?,?,?,?)""", (nuid,classification,tag,reviewer,timestamp,reason,source,batch_id))
                        count += 1
            result['changed'] += count
        except (sqlite3.Error, ValueError) as error:
            message = str(error) if isinstance(error, ValueError) else 'Database busy or unavailable; reload and retry this group.'
            result['errors'].append(f'{kind.title()} group was not saved: {message}')
    return result


def candidate_related(nuid):
    db = get_db()
    candidate = get_candidate(nuid)
    prefix = 'ts' if candidate['search_type'] == 'triggered' else 'bs'
    exists = db.execute(f"SELECT 1 FROM {prefix}.sqlite_master WHERE name=?", (prefix+'_audit',)).fetchone()
    history = db.execute(f'SELECT * FROM {prefix}.{prefix}_audit WHERE nuid=? ORDER BY id DESC', (nuid,)).fetchall() if exists else []
    return {
        'products': candidate_products(nuid),
        'blind': db.execute('SELECT * FROM blind_candidate_details WHERE nuid=?', (nuid,)).fetchone() if prefix == 'bs' else None,
        'events': [], 'history': history,
        'merged_available': db.execute('SELECT 1 FROM candidates WHERE NuID=(SELECT merged_into FROM bs.candidates WHERE nuid=?)',(nuid,)).fetchone(),
    }


def search_type_counts(filters):
    where, values = build_filter_query(filters)
    return dict(get_db().execute('SELECT search_type, COUNT(*) FROM candidates' + where + ' GROUP BY search_type', values))


def parse_trigger_time(value):
    """Compare source UTC strings, including optional offsets and trailing Z."""
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError, OverflowError):
        return None


def matching_candidates(candidate):
    """Use pipeline candidate-span matches; dt_s is triggered minus blind time."""
    triggered = candidate['search_type'] == 'triggered'
    own, other = ('nuts_id','nubs_id') if triggered else ('nubs_id','nuts_id')
    rows = get_db().execute(f'SELECT * FROM bs.nu_bs_ts_match WHERE {own}=?', (candidate['NuID'],)).fetchall()
    matches = []
    for match in rows:
        linked = get_candidate(match[other])
        item = dict(linked) if linked else dict(NuID=match[other], trigger_time=match['nubs_trigger_utc'] if triggered else match['nuts_trigger_time'], classification_tag='unclassified', queue_status=None)
        details = get_db().execute('SELECT duration,algos,status FROM bs.candidates WHERE nuid=?', (match['nubs_id'],)).fetchone()
        item.update(duration=details['duration'] if details else None, algos=details['algos'] if details else None,
                    pipeline_status=details['status'] if details else None,
                    delta_seconds=(-match['dt_s'] if triggered else match['dt_s']), available=linked is not None)
        matches.append(item)
    return sorted(matches, key=lambda row: (abs(row['delta_seconds']), row['NuID']))
