# nustar-sings

# NuSTAR SINGS Interface

A Flask application for reviewing triggered and blind GRB candidates and browsing observation products.

## Features

- Home dashboard with filters and classification counts.
- Candidate scanning with thumbnails and bulk classification.
- NuID details with reports, matching candidates, and review history.
- Observation products with sequence, window, and algorithm views.

## Installation

From the repository root, using Python 3.9 or later:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Configuration

Check that the database and product paths in `nusings_config.yaml` match your environment.

```bash
export NUSINGS_CONFIG="$PWD/nusings_config.yaml"
```

The app reads directly from `ts-queue.db` and `bs_candidates.db`. No separate interface database or recurring imports are needed.

## Session Secret

Flask requires a stable secret to sign sessions and review forms, including session information used for CSRF protection. Create it once outside the repository:

```bash
GRB_STATE_DIR="$HOME/.config/nustar-sings-interface"
mkdir -p "$GRB_STATE_DIR"
chmod 700 "$GRB_STATE_DIR"

if [ ! -f "$GRB_STATE_DIR/session-secret.txt" ]; then
  (umask 077; python -c 'import secrets; print(secrets.token_hex(32))' \
    > "$GRB_STATE_DIR/session-secret.txt")
fi

export GRB_SECRET_KEY="$(cat "$GRB_STATE_DIR/session-secret.txt")"
```

Do not commit the secret to Git.

## One-Time Database Setup

Preview the changes, then apply:

```bash
python setup_reviews.py
python setup_reviews.py --apply
```

Setup backs up each database and adds:

| Database | New tables |
|---|---|
| `ts-queue.db` | `ts_reviews`, `ts_audit` |
| `bs_candidates.db` | `bs_reviews`, `bs_audit` |

Existing pipeline records remain unchanged. Setup and review actions require write access to the databases and their parent directories.

## Run Locally

```bash
python run.py
```

Open http://127.0.0.1:5001. Stop with **Ctrl+C**.

In a new terminal session, reactivate the virtual environment and set `NUSINGS_CONFIG` and `GRB_SECRET_KEY` again. Database setup does not need to be repeated.

For remote server testing, use an SSH tunnel. `run.py` uses Flask’s development server; persistent deployment requires an appropriate application server.

## Review Behavior

- Candidates without a review display as **Unclassified**.
- Classification changes record the reviewer, reason, and audit history.
- Pipeline statuses and scientific fields are not changed by classification.
- Mixed triggered/blind bulk updates save separately and report any failed group.
- Reviewer selection records attribution; it is not user authentication.

## Repository Contents

Keep databases, scientific products, backups, local configuration, and secrets outside Git. Only application code, dependencies, and required assets belong in the repository.