"""
Queue script for the NuSTAR SINGS blind search (NUBS).
Author: Gaurav Waratkar

Note:
- Every pass lists sequences from observing_schedule.txt in the last back_search_days and compares
  FITS row counts (headers only) with seq_status in bs_candidates.db.
- HK grew by >= hk_min_new_rows -> full nubs_search.py on the sequence.
- only events grew by >= evt_min_new_rows -> nubs_search.py --reports_only (CZT panels change, triggers don't).
- Slack alerts go out only for new candidates (slack_sent flag), with any NUTS matches.
"""

import os
import sys
import time
import sqlite3
import argparse as ag
import subprocess as subp
import traceback
from astropy.time import Time
import astropy.units as u
from nusings_config import load_config
from nubs_algos import ALGOS
from nubs_search import init_db, upsert, ts_match, seq_files, row_counts, now_utc

HERE = os.path.dirname(os.path.abspath(__file__))
PREV = ["hk_rows_a", "hk_rows_b", "evt_rows_a", "evt_rows_b", "status"]


def list_recent_seqs(cfg, days):
    sched = os.path.join(
        cfg["sings-paths"]["essential-data-path"], "observing_schedule.txt"
    )
    now = Time.now()
    lo, hi = (now - days * u.day).yday, now.yday
    seqs = {}
    with open(sched) as f:
        for line in f.readlines()[20:]:
            parts = line.split()
            if line.startswith(";") or len(parts) < 13 or not parts[2].isdigit():
                continue
            start, end, seqid, name = parts[:4]
            if end >= lo and start <= hi:
                obsid = f"{seqid[:-3]}_{name}"
                seqs[seqid] = (
                    obsid,
                    os.path.join(cfg["nustar-data-dir"], obsid, seqid),
                )
    return seqs


def decide(prev, cnt, c, retry_failed):
    if cnt["hk_rows_a"] is None or cnt["hk_rows_b"] is None:
        return "no_data"
    if prev is None or prev["hk_rows_a"] is None:
        return "search"
    dhk = max(
        cnt["hk_rows_a"] - prev["hk_rows_a"],
        cnt["hk_rows_b"] - (prev["hk_rows_b"] or 0),
    )
    devt = max(
        (cnt["evt_rows_a"] or 0) - (prev["evt_rows_a"] or 0),
        (cnt["evt_rows_b"] or 0) - (prev["evt_rows_b"] or 0),
    )
    if prev["status"] == "failed":
        return "search" if (retry_failed or dhk > 0 or devt > 0) else "skip"
    if dhk >= c["hk_min_new_rows"]:
        return "search"
    if devt >= c["evt_min_new_rows"]:
        return "reports"
    return "skip"


def run_seq(cfg_path, path, act):
    cmd = [
        sys.executable,
        os.path.join(HERE, "nubs_search.py"),
        "--seq_path",
        path,
        "--config",
        cfg_path,
    ]
    if act == "reports":
        cmd.append("--reports_only")
    print(f"  running: {' '.join(cmd)}")
    r = subp.run(cmd, cwd=HERE, capture_output=True, text=True)
    print("\n".join("    " + x for x in r.stdout.strip().splitlines()[-15:]))
    return r.returncode, (r.stderr.strip()[-500:] or r.stdout.strip()[-500:])


def slack_new(cur, c, cand_dir, cutoff, send):
    cols = ", ".join(f"n_{a}, max_{a}" for a in ALGOS)
    rows = cur.execute(
        f"SELECT nuid, obsid, seqid, trigger_utc, duration, {cols} FROM candidates "
        f"WHERE status='active' AND slack_sent=0 AND report_status='done' AND trigger_utc>=? "
        f"ORDER BY trigger_utc",
        (cutoff,),
    ).fetchall()
    for r in rows:
        nuid, obsid, seqid, utc, dur = r[:5]
        stats = "  ".join(
            f"{a}: n {r[5 + 2 * i]} max {r[6 + 2 * i]:.1f}"
            for i, a in enumerate(ALGOS)
            if r[5 + 2 * i]
        )
        m = cur.execute(
            "SELECT nuts_id, dt_s FROM nu_bs_ts_match WHERE nubs_id=?", (nuid,)
        ).fetchall()
        msg = (
            f"New NUBS candidate {nuid}\nUTC {utc}  span {dur:.0f}s  {obsid}/{seqid}\n{stats}\n"
            f"NUTS match: {', '.join(f'{a} ({b:+.0f}s)' for a, b in m) or 'none'}"
        )
        print(msg)
        if not send:
            continue
        from message_slack import send_slack_message, send_slack_files

        d = os.path.join(cand_dir, nuid[4:8], nuid)
        files = [
            p
            for p in (
                os.path.join(d, f"grb_report_{nuid}.png"),
                os.path.join(d, "trigger_map.png"),
            )
            if os.path.exists(p)
        ]
        if files:
            send_slack_files(files, msg, c["slack_bs_reports_id"])
        else:
            send_slack_message(msg, channel_id=c["slack_bs_reports_id"])
        cur.execute("UPDATE candidates SET slack_sent=1 WHERE nuid=?", (nuid,))
        time.sleep(2)
    return len(rows)


def notify(c, msg, send):
    print(msg)
    if send and c["slack_bs_log_status"] and c["slack_bs_log_id"]:
        from message_slack import send_slack_message

        send_slack_message(msg, channel_id=c["slack_bs_log_id"])


def one_pass(args, cfg):
    c, bp = cfg["bs_config"], cfg["bs_paths"]
    days = args.days or c["back_search_days"]
    conn = sqlite3.connect(bp["bs_db"])
    init_db(conn)
    cur = conn.cursor()
    seqs = list_recent_seqs(cfg, days)
    if args.seqid:
        seqs = {k: v for k, v in seqs.items() if k in args.seqid}
    print(f"\n{now_utc()}: {len(seqs)} sequence(s) in the last {days} days")
    tally = dict(search=0, reports=0, skip=0, no_data=0, failed=0)
    for seqid, (obsid, path) in sorted(seqs.items()):
        cnt = row_counts(seq_files(path, seqid))
        r = cur.execute(
            f"SELECT {', '.join(PREV)} FROM seq_status WHERE seqid=?", (seqid,)
        ).fetchone()
        act = decide(dict(zip(PREV, r)) if r else None, cnt, c, args.retry_failed)
        tally[act] += 1
        print(f"{seqid} {obsid}: {act}  {cnt}")
        if args.dry_run:
            continue
        base = dict(seqid=seqid, obsid=obsid, path=path, last_check_utc=now_utc())
        if act in ("skip", "no_data"):
            upsert(
                cur,
                "seq_status",
                dict(base, status="no_data") if act == "no_data" else base,
                "seqid",
            )
            conn.commit()
            continue
        conn.commit()
        rc, err = run_seq(args.config, path, act)
        if rc != 0:
            tally["failed"] += 1
            upsert(
                cur,
                "seq_status",
                dict(base, status="failed", last_error=err, **cnt),
                "seqid",
            )
            conn.commit()
            print(f"  FAILED: {err}")
    cutoff = (Time.now() - days * u.day).isot
    nuids = [
        x[0]
        for x in cur.execute(
            "SELECT nuid FROM candidates WHERE status='active' AND trigger_utc>=?",
            (cutoff,),
        ).fetchall()
    ]
    new_matches = (
        []
        if args.dry_run
        else ts_match(cur, cfg["sings-paths"]["ts-queue-db"], nuids, c["ts_match_s"])
    )
    for nb, nt in new_matches:
        print(f"new match {nb} <-> {nt}")
    send = (
        c["slack_bs_reports_status"]
        and c["slack_bs_reports_id"]
        and not args.no_slack
        and not args.dry_run
    )
    n_alert = slack_new(
        cur, c, os.path.join(bp["bs_products_dir"], bp["cand_dir"]), cutoff, send
    )
    conn.commit()
    conn.close()
    notify(
        c,
        f"NUBS pass {now_utc()}: {tally}  active candidates (last {days} d): {len(nuids)}  "
        f"new matches: {len(new_matches)}  "
        + (
            f"alerts sent: {n_alert}"
            if send
            else f"alerts pending (slack off): {n_alert}"
        ),
        not args.no_slack and not args.dry_run,
    )


def parse_args():
    p = ag.ArgumentParser(description="NuSTAR SINGS blind search queue.")
    p.add_argument("--config", default="nusings_config.yaml")
    p.add_argument("--once", action="store_true", help="single pass then exit")
    p.add_argument("--days", type=float, help="override back_search_days")
    p.add_argument("--seqid", nargs="+", help="restrict to these seqids")
    p.add_argument("--dry_run", action="store_true", help="print decisions only")
    p.add_argument(
        "--retry_failed",
        action="store_true",
        help="rerun failed seqs even without new data",
    )
    p.add_argument("--no_slack", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    cfg = load_config(args.config)
    c = cfg["bs_config"]
    send = not args.no_slack and not args.dry_run
    try:
        notify(
            c, "Starting the NuSTAR SINGS blind search queue.", send and not args.once
        )
        while True:
            try:
                one_pass(args, cfg)
            except Exception as e:
                traceback.print_exc()
                notify(c, f"Error in the NuSTAR SINGS blind search queue: {e}", send)
            if args.once:
                break
            print(f"Sleeping for {c['interval']} seconds...")
            time.sleep(c["interval"])
    except KeyboardInterrupt:
        notify(
            c, "Stopping the NuSTAR SINGS blind search queue.", send and not args.once
        )
