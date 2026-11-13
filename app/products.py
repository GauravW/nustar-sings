"""
Candidate product discovery for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Derives report, CsI, CZT, and blind trigger-map filenames from pipeline candidate identifiers.
- Uses the configured triggered or blind-candidate product root.
- Checks record existence, supported formats, file availability, and path containment.
- Returns product links directly without a product registry or import step.
"""
from pathlib import Path
from flask import current_app, url_for
from .db import get_db

LABELS = {'report': 'Combined report', 'csi': 'CsI shield light curve',
          'czt': 'CZT detector light curve', 'trigger_map': 'Trigger map'}


def candidate_product_path(nuid, kind, format):
    if kind not in LABELS or format not in ('png','pdf') or (kind == 'trigger_map' and format != 'png'):
        return None
    row = get_db().execute('SELECT search_type FROM candidates WHERE NuID=?', (nuid,)).fetchone()
    if row is None or (kind == 'trigger_map' and row['search_type'] != 'blind'):
        return None
    # Pipeline IDs contain the event year at offsets 4..7.
    if len(nuid) < 8 or not nuid[4:8].isdigit() or '/' in nuid or '\\' in nuid:
        return None
    root = Path(current_app.config['TS_PRODUCT_ROOT' if row['search_type'] == 'triggered' else 'BS_CANDIDATE_ROOT']).resolve()
    stem = {'report': f'grb_report_{nuid}', 'csi': f'{nuid}_CsI_lc', 'czt': f'{nuid}_CZT_lc', 'trigger_map': 'trigger_map'}[kind]
    path = (root / nuid[4:8] / nuid / f'{stem}.{format}').resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def candidate_products(nuid):
    products = []
    for kind, label in LABELS.items():
        image = candidate_product_path(nuid,kind,'png')
        pdf = candidate_product_path(nuid,kind,'pdf')
        if image or pdf:
            products.append(dict(kind=kind,label=label,provenance='pipeline',
                thumbnail_url=url_for('product_file',nuid=nuid,kind=kind,format='png') if image else None,
                url=url_for('product_file',nuid=nuid,kind=kind,format='pdf') if pdf else None,
                link_label='Open PDF'))
    return products
