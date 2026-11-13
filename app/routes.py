"""
Candidate page and form routes for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Serves Home, Scanning, NuID Details, and candidate image/PDF endpoints.
- Uses signed form snapshots and CSRF checks for classification submissions.
- Restricts bulk selection to the candidates displayed on the current page.
- Validates reviewer and reason fields and reports partial bulk-update outcomes.
- Product files are resolved through the controlled product-path helper.
"""
import secrets
from urllib.parse import urlsplit

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for, send_file
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .constants import TAGS, BLIND_SUMMARY_FIELDS, BLIND_CONTEXT_FIELDS
from .db import get_db
from .products import candidate_product_path, candidate_products
from .services import (count_candidates, get_candidate, get_candidates, read_filters,
                       filter_options, classification_counts, update_classifications, candidate_related, search_type_counts, matching_candidates)


def page_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='scanning-page')


def safe_product_url(value):
    """Only expose ordinary web links or local static assets."""
    value = (value or '').strip()
    if value.startswith(('/static/', '/products/')) and '\\' not in value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme in ('https', 'http') and parsed.netloc:
        return value
    return None


REVIEWERS = {'kush': 'Kush Rao', 'gaurav': 'Gaurav', 'brian': 'Brian'}


def read_review():
    """Validate reviewer and reason for either classification form."""
    choice = request.form.get('reviewer', '')
    reviewer = request.form.get('reviewer_name', '').strip() if choice == 'other' else REVIEWERS.get(choice, '')
    if not reviewer or len(reviewer) > 100 or any(ord(c) < 32 for c in reviewer):
        abort(400, 'Choose a reviewer or enter a name (up to 100 characters).')
    reason = request.form.get('reason', '').strip()
    if len(reason) > 500:
        abort(400, 'Keep the reason under 500 characters.')
    return reviewer, reason


def remember_reviewer(reviewer):
    session['reviewer_choice'] = request.form['reviewer']
    session['reviewer_name'] = reviewer if request.form['reviewer'] == 'other' else ''


def check_csrf():
    token = request.form.get('csrf_token', '')
    if not session.get('csrf_token') or not secrets.compare_digest(token.encode(), session['csrf_token'].encode()):
        abort(400, 'This form expired. Reload the page and try again.')


def register_routes(app):
    app.jinja_env.filters['safe_product_url'] = safe_product_url

    @app.get('/products/<nuid>/<kind>/<format>')
    def product_file(nuid, kind, format):
        path = candidate_product_path(nuid, kind, format)
        if path is None:
            abort(404, 'Product file unavailable.')
        return send_file(path, mimetype='image/png' if format == 'png' else 'application/pdf', conditional=True)

    @app.get('/')
    def home():
        filters = read_filters(request.args)
        counts = classification_counts(filters)
        for item in counts:
            item['url'] = url_for('scan', **dict(filters, tag=item['tag']))
        return render_template('home.html', page_title='Candidate overview', active='home',
                               filters=filters, options=filter_options(), counts=counts, search_counts=search_type_counts(filters),
                               total=sum(item['count'] for item in counts))

    @app.get('/scan')
    def scan():
        filters = read_filters(request.args)
        total = count_candidates(filters)
        total_pages = max(1, (total + 9) // 10)
        page = max(1, min(request.args.get('page', 1, type=int), total_pages))
        rows = get_candidates(filters, page)
        thumbnails = {row['NuID']: {p['kind']: p['thumbnail_url'] for p in candidate_products(row['NuID']) if p['kind'] in ('csi', 'report') and p['thumbnail_url']} for row in rows}
        # Keep the first/last pages and a small window around the current page.
        window_start = max(1, min(page - 1, total_pages - 2))
        page_numbers = sorted({1, total_pages, *range(window_start, min(total_pages, window_start + 2) + 1)})
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        # Signed page contents prevent selecting hidden/off-page candidates.
        page_token = page_signer().dumps({'versions': {r['NuID']: r['classification_version'] for r in rows},
                                         'filters': filters, 'page': page})
        return render_template('scanning.html', page_title='Scan candidates', active='scan',
                               filters=filters, options=filter_options(), rows=rows, total=total,
                               page=page, total_pages=total_pages, page_numbers=page_numbers, page_token=page_token, thumbnails=thumbnails)

    @app.post('/scan/classify')
    def classify():
        check_csrf()
        try:
            snapshot = page_signer().loads(request.form.get('page_token', ''), max_age=3600)
        except (BadSignature, SignatureExpired):
            abort(400, 'This page expired. Reload Scanning and try again.')
        selected = list(dict.fromkeys(request.form.getlist('selected')))
        if not selected or len(selected) > 10 or any(n not in snapshot['versions'] for n in selected):
            abort(400, 'Select candidates from the displayed page only.')
        tag = 'discard' if request.form.get('action') == 'discard' else request.form.get('classification')
        if tag not in TAGS:
            abort(400, 'Choose a valid classification.')
        reviewer, reason = read_review()
        changed = update_classifications(selected, snapshot['versions'], tag, reviewer, reason)
        remember_reviewer(reviewer)
        flash(f"{changed['changed']} candidate(s) updated to {TAGS[tag][0]}. "
              'Candidates that no longer match your filters will leave this list.')
        for error in changed['errors']:
            flash(error)
        return redirect(url_for('scan', page=snapshot['page'], **snapshot['filters']), code=303)

    @app.get('/candidate/<candidate_id>')
    def candidate(candidate_id):
        nuid = candidate_id
        row = get_candidate(nuid)
        if row is None:
            abort(404, 'Candidate not found.')
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        filters = read_filters(request.args)
        return_page = max(1, request.args.get('page', 1, type=int))
        detail_token = page_signer().dumps({'nuid': nuid, 'version': row['classification_version'],
                                          'filters': filters, 'page': return_page})
        return render_template('nu-details.html' , page_title=nuid, active='scan', row=row,
                               blind_summary_fields=BLIND_SUMMARY_FIELDS, blind_context_fields=BLIND_CONTEXT_FIELDS,
                               filters=filters, return_page=return_page, detail_token=detail_token,
                               matches=matching_candidates(row),
                               observation_available=get_db().execute('SELECT 1 FROM observations o JOIN blind_candidate_details b ON b.seqid=o.seqid WHERE b.nuid=?', (nuid,)).fetchone(),
                               **candidate_related(nuid))


    @app.post('/candidate/<candidate_id>/classify')
    def classify_candidate(candidate_id):
        check_csrf()
        try:
            snapshot = page_signer().loads(request.form.get('detail_token', ''), max_age=3600)
        except (BadSignature, SignatureExpired):
            abort(400, 'This page expired. Reload the candidate and try again.')
        if snapshot.get('nuid') != candidate_id:
            abort(400, 'Candidate form does not match this record.')
        tag = request.form.get('classification')
        if tag not in TAGS:
            abort(400, 'Choose a valid classification.')
        reviewer, reason = read_review()
        changed = update_classifications([candidate_id], {candidate_id: snapshot['version']},
                                         tag, reviewer, reason, source='details')
        remember_reviewer(reviewer)
        flash('Classification and history updated.' if changed['changed'] else
              ('No changes saved.' if changed['errors'] else 'Classification unchanged; no new history or standalone note was saved.'))
        for error in changed['errors']:
            flash(error)
        return redirect(url_for('candidate', candidate_id=candidate_id, page=snapshot['page'],
                                **snapshot['filters']), code=303)
