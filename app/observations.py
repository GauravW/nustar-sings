"""
Observation browsing and product routes for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Reads sequence status, windows, processing runs, and linked blind candidates directly.
- Provides filtered, paginated sequence lists and a selectable window/algorithm detail view.
- Locates overview, shield-rate, Matched Filter, Bayesian Blocks, and log products.
- Serves files only for known sequences/windows and within the configured observation root.
- Shows latest search metrics separately from the latest processing activity.
"""
from pathlib import Path
from flask import Blueprint, abort, current_app, render_template, request, send_file, url_for
from .db import get_db

observations = Blueprint('observations', __name__)


def observation_path(seqid, index, kind):
    db = get_db()
    row = db.execute('SELECT obsid FROM observations WHERE seqid=?', (seqid,)).fetchone()
    if row is None:
        return None
    names = {'overview': 'obs_overview.png', 'log': 'nubs_obs.log'}
    if kind in names:
        if index != -1:
            return None
        relative = names[kind]
    else:
        folders = {'shield': ('windows','window'), 'mf': ('mf_algo','mf'), 'bb': ('bb_algo','bb')}
        if kind not in folders or not db.execute('SELECT 1 FROM observation_windows WHERE seqid=? AND widx=?', (seqid,index)).fetchone():
            return None
        folder,suffix = folders[kind]
        relative = f'{folder}/w{index:02d}_{suffix}.png'
    root = Path(current_app.config['OBS_PRODUCT_ROOT']).resolve()
    path = (root / row['obsid'] / seqid / relative).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def products_for(seqid):
    result = {}
    items = [(-1,'overview'),(-1,'log')]
    items += [(row['widx'],kind) for row in get_db().execute('SELECT widx FROM observation_windows WHERE seqid=?',(seqid,)) for kind in ('shield','mf','bb')]
    for index,kind in items:
        if observation_path(seqid,index,kind):
            result[(index,kind)] = url_for('observations.file',seqid=seqid,index=index,kind=kind)
    return result


@observations.get('/observation-products/<seqid>/<int(signed=True):index>/<kind>')
def file(seqid,index,kind):
    path = observation_path(seqid,index,kind)
    if path is None:
        abort(404)
    return send_file(path,mimetype='text/plain' if kind == 'log' else 'image/png',conditional=True)


@observations.get('/observations')
def listing():
    filters = {key: request.args.get(key, '').strip() for key in ('q', 'start', 'end', 'status', 'has_candidates', 'available')}
    rows = []
    for item in get_db().execute('''SELECT o.*, min(w.t0_utc) start, max(w.t1_utc) end,
        count(w.widx) windows,
        (SELECT count(*) FROM blind_candidate_details b WHERE b.seqid=o.seqid) candidates
        FROM observations o LEFT JOIN observation_windows w ON w.seqid=o.seqid
        GROUP BY o.seqid ORDER BY start DESC, o.seqid'''):
        row = dict(item)
        row['overview'] = url_for('observations.file',seqid=row['seqid'],index=-1,kind='overview') if observation_path(row['seqid'],-1,'overview') else None
        if filters['q'].lower() not in (row['seqid'] + ' ' + row['obsid']).lower():
            continue
        if filters['status'] and row['status'] != filters['status']:
            continue
        if filters['start'] and (not row['end'] or row['end'][:10] < filters['start']):
            continue
        if filters['end'] and (not row['start'] or row['start'][:10] > filters['end']):
            continue
        if filters['has_candidates'] in ('yes', 'no') and bool(row['candidates']) != (filters['has_candidates'] == 'yes'):
            continue
        if filters['available'] in ('yes', 'no') and bool(row['overview']) != (filters['available'] == 'yes'):
            continue
        rows.append(row)
    total = len(rows)
    total_pages = max(1, (total + 9) // 10)
    page = max(1, min(request.args.get('page', 1, type=int), total_pages))
    page_numbers = sorted({1, total_pages, *range(max(1, page-1), min(total_pages, page+1)+1)})
    return render_template('observations.html', page_title='Observation Products', active='observations',
        rows=rows[(page-1)*10:page*10], total=total, page=page, total_pages=total_pages,
        page_numbers=page_numbers, filters=filters, pagination_endpoint='observations.listing')


@observations.get('/observations/<seqid>')
def detail(seqid):
    db = get_db()
    row = db.execute('SELECT * FROM observations WHERE seqid=?', (seqid,)).fetchone()
    if row is None:
        abort(404)
    windows = db.execute('SELECT * FROM observation_windows WHERE seqid=? ORDER BY widx', (seqid,)).fetchall()
    selected = request.args.get('window', type=int)
    window = next((w for w in windows if w['widx'] == selected), windows[0] if windows else None)
    kind = request.args.get('view', 'shield')
    if kind not in ('shield', 'mf', 'bb'):
        kind = 'shield'
    candidates = db.execute('''SELECT c.NuID,c.trigger_time,c.classification_tag,b.widx,b.algos,b.status
        FROM blind_candidate_details b JOIN candidates c ON c.NuID=b.nuid
        WHERE b.seqid=? ORDER BY c.trigger_time''', (seqid,)).fetchall()
    runs = db.execute('SELECT * FROM observation_runs WHERE seqid=? ORDER BY run_utc DESC', (seqid,)).fetchall()
    search = next((r for r in runs if r['mode'] == 'search'), None)
    return render_template('observation-details.html', page_title=seqid, active='observations',
        row=row, windows=windows, window=window, kind=kind, products=products_for(seqid),
        candidates=candidates, runs=runs, search=search,
        coverage_start=min((w['t0_utc'] for w in windows if w['t0_utc']), default=None),
        coverage_end=max((w['t1_utc'] for w in windows if w['t1_utc']), default=None))
