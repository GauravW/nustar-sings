"""
Shared classification definitions and detail labels for the NuSTAR SINGS interface.
Author: Kush Rao

Note:
- Defines review classification keys, display labels, descriptions, and chart colors.
- Provides field lists used to display blind-search metadata and SAA context.
- Human review classifications are separate from pipeline status and pipeline tags.
"""

# Each tag stores: (display name, description, chart color).
TAGS = {
    "unclassified": ("Unclassified", "Not yet scanned", "#94a3b8"),
    "grb": ("GRB", "Real gamma-ray burst candidate", "#14b8a6"),
    "missed": (
        "Missed",
        "Externally detected GRB missed because of sensitivity",
        "#f472b6",
    ),
    "eo": ("EO", "GRB missed because of Earth occultation", "#8b5cf6"),
    "saa_missed": ("SAA-missed", "GRB missed while NuSTAR was in SAA", "#65a884"),
    "saa_tentacles": (
        "SAA-tentacles",
        "Trigger associated with SAA tentacles",
        "#d99a45",
    ),
    "cp": ("CP", "Charged-particle trigger", "#5094e8"),
    "ambiguous": ("Ambiguous", "Needs investigation or discussion", "#e8ad32"),
    "discard": ("Discard", "Bad candidate", "#ee7580"),
}

# Change these labels/order to adjust the blind details without editing SQL.
BLIND_SUMMARY_FIELDS = [
    ('obsid', 'Observation ID'), ('seqid', 'Sequence ID'),
    ('trigger_utc', 'Trigger time (UTC)'), ('t_start_utc', 'Start (UTC)'),
    ('t_stop_utc', 'Stop (UTC)'), ('duration', 'Duration (s)'),
    ('algos', 'Algorithms'), ('n_triggers', 'Trigger count'),
    ('n_mf', 'MF count'), ('max_mf', 'max_mf (pipeline value)'),
    ('n_bb', 'BB count'), ('max_bb', 'max_bb (pipeline value)'),
    ('status', 'Pipeline status'), ('report_status', 'Report status'),
    ('reject_reason', 'Rejection reason'), ('merged_into', 'Merged into'),
]
BLIND_CONTEXT_FIELDS = [
    ('saa_dist_deg', 'SAA distance (deg)'), ('saa_dt_s', 'SAA time offset (s)'),
    ('lon_start', 'Start longitude'), ('lat_start', 'Start latitude'),
    ('lon_stop', 'Stop longitude'), ('lat_stop', 'Stop latitude'),
]
