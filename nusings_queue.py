"""
Queue script for the NuSTAR SINGS blind search (NUBS).
Author: Gaurav Waratkar

Note:
- Every pass lists sequences from observing_schedule.txt in the last back_search_days and compares
  FITS row counts (headers only) with seq_status in bs_candidates.db.
- HK on disk but attorb not yet -> waiting (no run, no failure; runs once attorb appears).
- HK grew by >= hk_min_new_rows -> full nubs_search.py on the sequence.
- only events grew by >= evt_min_new_rows -> nubs_search.py --reports_only (CZT panels change, triggers don't).
- Slack (channels in the slack block of the config), each message sent once:
    reports channel: new-candidate alert (slack_sent flag), coincident NUBS-NUTS alert (coinc_sent flag)
    log channel: status only on passes with new data or new candidates, daily summary at daily_update_hour
    (server local time, remembered in queue_state), errors, start/stop.
"""

import os
import sys
import json
import time
import sqlite3
import argparse as ag
import subprocess as subp
import traceback
from datetime import datetime
from astropy.time import Time
import astropy.units as u
from nusings_config import load_config
from nubs_algos import ALGOS
from nubs_search import (
    init_db,
    upsert,
    ts_match,
    seq_files,
    row_counts,
    now_utc,
    met2utc,
)

HERE = os.path.dirname(os.path.abspath(__file__))
PREV = ["hk_rows_a", "hk_rows_b", "evt_rows_a", "evt_rows_b", "status"]


def post(on, ch, msg, files=None):
    print(msg)
    if not (on and ch):
        return False
    from message_slack import send_slack_message, send_slack_files

    if files:
        send_slack_files(files, msg, ch)
    else:
        send_slack_message(msg, channel_id=ch)
    time.sleep(1)
    return True


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
                    start,
                    end,
                )
    return seqs


def decide(prev, cnt, has_att, c, retry_failed):
    if cnt["hk_rows_a"] is None or cnt["hk_rows_b"] is None:
        return "no_data"
    if not has_att:
        return "waiting"
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


def algos_str(s):
    return ", ".join(json.loads(s)) if s else ""


def matches(cur, nuid):
    m = cur.execute(
        "SELECT nuts_id, dt_s, missions FROM nu_bs_ts_match WHERE nubs_id=?", (nuid,)
    ).fetchall()
    return ", ".join(f"{a} ({b:+.0f} s, {c})" for a, b, c in m) or "none"


def alert_new(cur, rep, cand_dir, cutoff):
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
        msg = (
            f"New NuBS candidate {nuid}\nTrigger time (UTC): {utc}  span {dur:.0f} s  {obsid}/{seqid}\n"
            f"{stats}\nNuTS coincidence: {matches(cur, nuid)}"
        )
        d = os.path.join(cand_dir, nuid[4:8], nuid)
        files = [
            p
            for p in (
                os.path.join(d, f"grb_report_{nuid}.png"),
                os.path.join(d, "trigger_map.png"),
            )
            if os.path.exists(p)
        ]
        if post(*rep, msg, files):
            cur.execute("UPDATE candidates SET slack_sent=1 WHERE nuid=?", (nuid,))
    return len(rows)


def alert_coinc(cur, rep, cutoff):
    rows = cur.execute(
        "SELECT nuid, trigger_utc, algos FROM candidates c WHERE status='active' "
        "AND COALESCE(coinc_sent, 0)=0 AND trigger_utc>=? AND EXISTS "
        "(SELECT 1 FROM nu_bs_ts_match m WHERE m.nubs_id=c.nuid) ORDER BY trigger_utc",
        (cutoff,),
    ).fetchall()
    for nuid, utc, algos in rows:
        msg = (
            f"<!channel> - Coincident NuBS-NuTS candidate found!\n{nuid}  Trigger time (UTC): {utc}  "
            f"Algos: {algos_str(algos)}\nNuTS: {matches(cur, nuid)}"
        )
        if post(*rep, msg):
            cur.execute("UPDATE candidates SET coinc_sent=1 WHERE nuid=?", (nuid,))
    return len(rows)


def status_msg(cur, seqs, ran, failed, new, check_utc):
    now = Time.now().yday
    current = [f"{o}/{s}" for s, (o, _, st, en) in seqs.items() if st <= now <= en]
    flowed = cur.execute(
        "SELECT MAX(MAX(COALESCE(last_search_utc, '')), MAX(COALESCE(last_report_utc, ''))) "
        "FROM seq_status"
    ).fetchone()[0]
    last = cur.execute("SELECT seqid, MAX(hk_met1) FROM seq_status").fetchone()
    names = [s for s, _ in ran]
    lines = [
        f"NuBS: new data in {len(ran)} seq(s) "
        f"({', '.join(names[:5])}{' ...' if len(names) > 5 else ''}), {len(new)} new candidate(s)"
        + (
            f" ({sum(st == 'saa_reject' for _, _, st in new)} SAA-rejected)"
            if new
            else ""
        )
        + (f", {len(failed)} failed ({', '.join(failed[:5])})" if failed else ""),
        f"current obs: {', '.join(current) or 'unknown'}",
        f"last data flowed at (UTC): {flowed or 'n/a'}",
        f"last check done at (UTC): {check_utc}",
        f"last data event at (UTC): {met2utc(last[1]) + f' ({last[0]})' if last[1] else 'n/a'}",
    ]
    if new:
        lines.append(
            "new candidates: "
            + ", ".join(
                f"{n} [{algos_str(a)}]"
                + (" (saa_reject)" if st == "saa_reject" else "")
                for n, a, st in new
            )
        )
    return "\n".join(lines)


def daily_msg(cur):
    cut = (Time.now() - 1 * u.day).isot
    rows = cur.execute(
        "SELECT nuid, trigger_utc, algos, status FROM candidates WHERE first_seen_utc>=? "
        "ORDER BY trigger_utc",
        (cut,),
    ).fetchall()
    n_rej = sum(r[3] == "saa_reject" for r in rows)
    lines = [
        "NuBS daily update:",
        f"Processed {len(rows)} candidates in the past 1 day ({n_rej} SAA-rejected):",
    ]
    for nuid, utc, algos, st in rows[:50]:
        lines.append(
            f"NuID: {nuid}; Trigger time (UTC): {utc}; Algos: {algos_str(algos)}; Coincidences: {matches(cur, nuid)};"
            + ("" if st == "active" else f" [{st}]")
        )
    if len(rows) > 50:
        lines.append(f"... and {len(rows) - 50} more")
    n_run, n_seq = cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT seqid) FROM obs_runs WHERE run_utc>=? "
        "AND mode='search'",
        (cut,),
    ).fetchone()
    n_fail = cur.execute(
        "SELECT COUNT(*) FROM seq_status WHERE status='failed'"
    ).fetchone()[0]
    lines.append(
        f"Health: {n_run} searches on {n_seq} seq(s) in the past 1 day; {n_fail} seq(s) currently failed"
    )
    return "\n".join(lines)


def maybe_daily(cur, log, hour, dry):
    today = datetime.now().strftime("%Y-%m-%d")
    r = cur.execute("SELECT value FROM queue_state WHERE key='last_daily'").fetchone()
    if datetime.now().hour < hour or (r and r[0] == today) or dry:
        return
    post(*log, daily_msg(cur))
    upsert(cur, "queue_state", dict(key="last_daily", value=today), "key")


def one_pass(args, cfg):
    c, bp, sl = cfg["bs_config"], cfg["bs_paths"], cfg["slack"]
    send = not args.no_slack and not args.dry_run
    rep = (send and sl["slack_bs_reports_status"], sl["slack_bs_reports_id"])
    log = (send and sl["slack_bs_log_status"], sl["slack_bs_log_id"])
    days = args.days or c["back_search_days"]
    t_pass = now_utc()
    conn = sqlite3.connect(bp["bs_db"])
    init_db(conn)
    cur = conn.cursor()
    seqs = list_recent_seqs(cfg, days)
    if args.seqid:
        seqs = {k: v for k, v in seqs.items() if k in args.seqid}
    print(f"\n{t_pass}: {len(seqs)} sequence(s) in the last {days} days")
    tally = dict(search=0, reports=0, skip=0, no_data=0, waiting=0)
    ran, failed = [], []
    for seqid, (obsid, path, _, _) in sorted(seqs.items()):
        f = seq_files(path, seqid)
        cnt = row_counts(f)
        r = cur.execute(
            f"SELECT {', '.join(PREV)} FROM seq_status WHERE seqid=?", (seqid,)
        ).fetchone()
        act = decide(
            dict(zip(PREV, r)) if r else None,
            cnt,
            os.path.exists(f["att"]),
            c,
            args.retry_failed,
        )
        tally[act] += 1
        print(f"{seqid} {obsid}: {act}  {cnt}")
        if args.dry_run:
            continue
        base = dict(seqid=seqid, obsid=obsid, path=path, last_check_utc=now_utc())
        if act in ("skip", "no_data", "waiting"):
            upsert(
                cur,
                "seq_status",
                base if act == "skip" else dict(base, status=act),
                "seqid",
            )
            conn.commit()
            continue
        conn.commit()
        rc, err = run_seq(args.config, path, act)
        if rc != 0:
            failed.append(seqid)
            upsert(
                cur,
                "seq_status",
                dict(base, status="failed", last_error=err, **cnt),
                "seqid",
            )
            conn.commit()
            print(f"  FAILED: {err}")
        else:
            ran.append((seqid, act))
    cutoff = (Time.now() - days * u.day).isot
    nuids = [
        x[0]
        for x in cur.execute(
            "SELECT nuid FROM candidates WHERE status IN ('active', 'saa_reject') "
            "AND trigger_utc>=?",
            (cutoff,),
        ).fetchall()
    ]
    if not args.dry_run:
        for nb, nt in ts_match(
            cur, cfg["sings-paths"]["ts-queue-db"], nuids, c["ts_match_s"]
        ):
            print(f"new match {nb} <-> {nt}")
    new = cur.execute(
        "SELECT nuid, algos, status FROM candidates WHERE first_seen_utc>=? ORDER BY trigger_utc",
        (t_pass,),
    ).fetchall()
    cand_dir = os.path.join(bp["bs_products_dir"], bp["cand_dir"])
    n_new = alert_new(cur, rep, cand_dir, cutoff)
    n_coinc = alert_coinc(cur, rep, cutoff)
    conn.commit()
    print(
        f"pass {t_pass}: {tally} failed {len(failed)}  active (last {days} d): {len(nuids)}  "
        f"alerts {'sent' if rep[0] else 'pending'}: new {n_new}, coinc {n_coinc}"
    )
    if ran or failed or new:
        post(*log, status_msg(cur, seqs, ran, failed, new, now_utc()))
    maybe_daily(cur, log, c.get("daily_update_hour", 5), args.dry_run)
    conn.commit()
    conn.close()


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
    c, sl = cfg["bs_config"], cfg["slack"]
    log = (
        not args.no_slack and not args.dry_run and sl["slack_bs_log_status"],
        sl["slack_bs_log_id"],
    )
    try:
        if not args.once:
            post(*log, "Starting the NuSTAR SINGS blind search queue.")
        while True:
            try:
                one_pass(args, cfg)
            except Exception as e:
                traceback.print_exc()
                post(*log, f"Error in the NuSTAR SINGS blind search queue: {e}")
            if args.once:
                break
            print(f"Sleeping for {c['interval']} seconds...")
            time.sleep(c["interval"])
    except KeyboardInterrupt:
        if not args.once:
            post(*log, "Stopping the NuSTAR SINGS blind search queue.")
