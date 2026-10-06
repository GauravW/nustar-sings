"""
NuSTAR SINGS blind search (NUBS) for one sequence, end to end.
Author: Gaurav Waratkar

Note:
- Every run reprocesses the full sequence: windows, detrending, triggers and DB rows for this seq are
  replaced by the latest data. Candidate NUIDs are never renamed once created.
- trigger = one algorithm detection. candidate = triggers chained with t0_new - t1_prev <= group_gap_s.
- --set changes thresholds; it needs --db or --dry_run so live candidates stay on the adopted config.
- --windows is a debug mode (plots + log only, no DB/reports).
"""

import os
import sys
import glob
import json
import time
import hashlib
import logging
import sqlite3
import argparse as ag
import subprocess as subp
import numpy as np
import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.path import Path
import matplotlib.patches as patches
from astropy.io import fits
from astropy.time import Time
from nustar_gen import info
from nusings_config import load_config
import get_nu_obs
from nubs_algos import (
    ALGOS,
    running_median,
    intervals,
    split_scale,
    bottom_legend,
    RAW_SPLIT,
    DET_SPLIT,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ns = info.NuSTAR()
log = logging.getLogger("nubs")

SEQ_COLS = dict(
    seqid="TEXT",
    obsid="TEXT",
    path="TEXT",
    hk_rows_a="INTEGER",
    hk_rows_b="INTEGER",
    evt_rows_a="INTEGER",
    evt_rows_b="INTEGER",
    hk_met0="REAL",
    hk_met1="REAL",
    status="TEXT",
    n_runs="INTEGER",
    last_check_utc="TEXT",
    last_search_utc="TEXT",
    last_report_utc="TEXT",
    last_error="TEXT",
)
RUN_COLS = dict(
    seqid="TEXT",
    run_utc="TEXT",
    obsid="TEXT",
    mode="TEXT",
    args="TEXT",
    git_hash="TEXT",
    tag="TEXT",
    params_json="TEXT",
    param_hashes="TEXT",
    hk_rows_a="INTEGER",
    hk_rows_b="INTEGER",
    evt_rows_a="INTEGER",
    evt_rows_b="INTEGER",
    n_bins="INTEGER",
    met0="REAL",
    met1="REAL",
    saa_frac="REAL",
    n_windows="INTEGER",
    exp_s="REAL",
    n_triggers="TEXT",
    n_new="INTEGER",
    n_updated="INTEGER",
    n_unchanged="INTEGER",
    n_gone="INTEGER",
    elapsed_s="REAL",
)
WIN_COLS = dict(
    seqid="TEXT",
    widx="INTEGER",
    obsid="TEXT",
    t0_met="REAL",
    t1_met="REAL",
    t0_utc="TEXT",
    t1_utc="TEXT",
    n_bins="INTEGER",
    dur_s="REAL",
    base_med_a="REAL",
    base_med_b="REAL",
    sc_med="REAL",
    sc_std="REAL",
    param_hash="TEXT",
    run_utc="TEXT",
)
CAND_COLS = dict(
    nuid="TEXT",
    obsid="TEXT",
    seqid="TEXT",
    widx="INTEGER",
    trigger_met="REAL",
    trigger_utc="TEXT",
    t_start_met="REAL",
    t_stop_met="REAL",
    t_start_utc="TEXT",
    t_stop_utc="TEXT",
    duration="REAL",
    n_triggers="INTEGER",
    algos="TEXT",
    trigger_ids="TEXT",
    signature="TEXT",
    status="TEXT",
    merged_into="TEXT",
    report_status="TEXT",
    report_utc="TEXT",
    slack_sent="INTEGER",
    coinc_sent="INTEGER",
    first_seen_utc="TEXT",
    last_updated_utc="TEXT",
    tag="TEXT",
    git_hash="TEXT",
)
TRIG_COLS = dict(
    trigger_id="TEXT",
    nuid="TEXT",
    obsid="TEXT",
    seqid="TEXT",
    widx="INTEGER",
    win_t0_met="REAL",
    t0_met="REAL",
    t1_met="REAL",
    tpeak_met="REAL",
    t0_utc="TEXT",
    t1_utc="TEXT",
    tpeak_utc="TEXT",
    duration="REAL",
    score="REAL",
    sc_pk="REAL",
    sa_pk="REAL",
    sb_pk="REAL",
    param_hash="TEXT",
    tag="TEXT",
    git_hash="TEXT",
    run_utc="TEXT",
)
MATCH_COLS = dict(
    nubs_id="TEXT",
    nuts_id="TEXT",
    dt_s="REAL",
    nubs_trigger_utc="TEXT",
    nuts_trigger_time="TEXT",
    missions="TEXT",
    matched_utc="TEXT",
)


def now_utc():
    return Time.now().isot


def met2utc(met):
    return ns.met_to_time(met).isot


def nubs_name(met):
    return f"NUBS{ns.met_to_time(met).strftime('%Y%m%dT%H%M%S')}"


def phash(d):
    return hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]


def git_hash():
    r = subp.run(
        ["git", "-C", HERE, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() or "na"


def setup_log(path):
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(path)):
        h.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(h)


def ensure_table(cur, name, cols, pk):
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {name} "
        f"({', '.join(f'{k} {v}' for k, v in cols.items())}, PRIMARY KEY ({pk}))"
    )
    have = {r[1] for r in cur.execute(f"PRAGMA table_info({name})")}
    for k, v in cols.items():
        if k not in have:
            cur.execute(f"ALTER TABLE {name} ADD COLUMN {k} {v}")


def upsert(cur, table, row, pk):
    keys = list(row)
    pks = [k.strip() for k in pk.split(",")]
    sets = ", ".join(f"{k}=excluded.{k}" for k in keys if k not in pks)
    cur.execute(
        f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {sets}",
        [row[k] for k in keys],
    )


def init_db(conn):
    cur = conn.cursor()
    ensure_table(cur, "seq_status", SEQ_COLS, "seqid")
    ensure_table(cur, "obs_runs", RUN_COLS, "seqid, run_utc")
    ensure_table(cur, "windows", WIN_COLS, "seqid, widx")
    cand = dict(CAND_COLS)
    for a in ALGOS:
        cand[f"n_{a}"] = "INTEGER"
        cand[f"max_{a}"] = "REAL"
    ensure_table(cur, "candidates", cand, "nuid")
    ensure_table(cur, "nu_bs_ts_match", MATCH_COLS, "nubs_id, nuts_id")
    ensure_table(cur, "queue_state", dict(key="TEXT", value="TEXT"), "key")
    conn.commit()


def algo_table(cur, a, p):
    cols = dict(TRIG_COLS, **ALGOS[a]["cols"])
    for k, v in p.items():
        cols[f"p_{k}"] = "TEXT" if isinstance(v, (list, dict, str)) else "REAL"
    ensure_table(cur, f"{a}_triggers", cols, "trigger_id")


def seq_files(path, seqid):
    return dict(
        hka=os.path.join(path, "hk", f"nu{seqid}A_fpm.hk"),
        hkb=os.path.join(path, "hk", f"nu{seqid}B_fpm.hk"),
        att=os.path.join(path, "event_cl", f"nu{seqid}A.attorb"),
        eva=os.path.join(path, "event_cl", f"nu{seqid}A_uf.evt"),
        evb=os.path.join(path, "event_cl", f"nu{seqid}B_uf.evt"),
    )


def file_rows(path, ext):
    try:
        return int(fits.getheader(path, ext)["NAXIS2"])
    except Exception:
        return None


def row_counts(f):
    return dict(
        hk_rows_a=file_rows(f["hka"], "HK1FPM"),
        hk_rows_b=file_rows(f["hkb"], "HK1FPM"),
        evt_rows_a=file_rows(f["eva"], 1),
        evt_rows_b=file_rows(f["evb"], 1),
    )


def _uniq(tab):
    _, i = np.unique(np.asarray(tab["TIME"], float), return_index=True)
    return tab[i]


def load_data(f):
    hka = _uniq(fits.getdata(f["hka"], "HK1FPM"))
    hkb = _uniq(fits.getdata(f["hkb"], "HK1FPM"))
    att = _uniq(fits.getdata(f["att"], 1))
    t = np.asarray(hka["TIME"], float)
    return dict(
        t=t,
        a=np.asarray(hka["SHLDLO"], float),
        b=np.interp(
            t, np.asarray(hkb["TIME"], float), np.asarray(hkb["SHLDLO"], float)
        ),
        lat=np.interp(
            t, np.asarray(att["TIME"], float), np.asarray(att["SAT_LAT"], float)
        ),
        lon=np.interp(
            t, np.asarray(att["TIME"], float), np.asarray(att["SAT_LON"], float)
        ),
    )


def build_windows(d, c):
    inside = Path(c["saa_polygon"]).contains_points(
        np.column_stack([d["lon"], d["lat"]])
    )
    idx = np.where(~inside)[0]
    wins = []
    if idx.size == 0:
        return inside, wins
    brk = np.where((np.diff(idx) > 1) | (np.diff(d["t"][idx]) > c["max_gap_s"]))[0] + 1
    for blk in np.split(idx, brk):
        if blk.size < c["min_bins"]:
            continue
        blk = blk[:-1]
        a, b = d["a"][blk], d["b"][blk]
        ba, bb = running_median(a, c["w_base"]), running_median(b, c["w_base"])
        sa = (a - ba) / np.sqrt(np.maximum(ba, 1))
        sb = (b - bb) / np.sqrt(np.maximum(bb, 1))
        wins.append(
            dict(
                idx=len(wins),
                tt=d["t"][blk],
                a=a,
                b=b,
                ba=ba,
                bb=bb,
                sa=sa,
                sb=sb,
                sc=(sa + sb) / np.sqrt(2),
            )
        )
    return inside, wins


def run_algos(wins, algos, c, hashes, seqid):
    trigs, aux = [], {}
    for a in algos:
        for w in wins:
            found, aux[(a, w["idx"])] = ALGOS[a]["detect"](w, c[a])
            tt = w["tt"]
            for tr in found:
                tr.update(
                    algo=a,
                    widx=w["idx"],
                    win_t0_met=float(tt[0]),
                    t0_met=float(tt[tr["i0"]]),
                    t1_met=float(tt[tr["i1"]]),
                    tpeak_met=float(tt[tr["ipk"]]),
                    sc_pk=float(w["sc"][tr["ipk"]]),
                    sa_pk=float(w["sa"][tr["ipk"]]),
                    sb_pk=float(w["sb"][tr["ipk"]]),
                    param_hash=hashes[a],
                )
                tr["duration"] = tr["t1_met"] - tr["t0_met"]
                tr["trigger_id"] = f"{a}_{seqid}_{tr['tpeak_met']:.0f}_{hashes[a][:6]}"
                trigs.append(tr)
            log.info(f"  {a} w{w['idx']:02d}: {len(found)} trigger(s)")
            for tr in found:
                extra = "  ".join(
                    f"{k} {tr[k]:.2f}" if isinstance(tr[k], float) else f"{k} {tr[k]}"
                    for k in ALGOS[a]["cols"]
                )
                log.info(
                    f"      {met2utc(tr['tpeak_met'])}  MET {tr['tpeak_met']:.0f}  "
                    f"dur {tr['duration']:.0f}s  score {tr['score']:.1f}  "
                    f"sa {tr['sa_pk']:.1f}  sb {tr['sb_pk']:.1f}  {extra}"
                )
    return trigs, aux


def group(trigs, gap):
    chains = []
    for tr in sorted(trigs, key=lambda x: x["t0_met"]):
        if chains and tr["t0_met"] - max(x["t1_met"] for x in chains[-1]) <= gap:
            chains[-1].append(tr)
        else:
            chains.append([tr])
    return chains


def signature(chain):
    return ",".join(
        f"{a}:{sum(x['algo'] == a for x in chain)}"
        for a in sorted({x["algo"] for x in chain})
    )


def new_nuid(cur, met, taken):
    while True:
        nuid = nubs_name(met)
        if (
            nuid not in taken
            and not cur.execute(
                "SELECT 1 FROM candidates WHERE nuid=?", (nuid,)
            ).fetchone()
        ):
            return nuid
        met += 1


def assign(cur, chains, seqid, gap):
    old = cur.execute(
        "SELECT nuid, t_start_met, t_stop_met, signature, status FROM candidates "
        "WHERE seqid=? ORDER BY t_start_met",
        (seqid,),
    ).fetchall()
    used, cands = set(), []
    for ch in chains:
        t0, t1 = min(x["t0_met"] for x in ch), max(x["t1_met"] for x in ch)
        hits = [
            o for o in old if o[0] not in used and o[1] - gap <= t1 and o[2] + gap >= t0
        ]
        sig = signature(ch)
        if hits:
            nuid = hits[0][0]
            action = (
                "unchanged"
                if (sig == hits[0][3] and hits[0][4] == "active" and len(hits) == 1)
                else "updated"
            )
            used.update(o[0] for o in hits)
        else:
            nuid = new_nuid(cur, t0, used | {cd["nuid"] for cd in cands})
            action = "new"
        cands.append(
            dict(
                nuid=nuid,
                chain=ch,
                t0=t0,
                t1=t1,
                sig=sig,
                action=action,
                merged=[o[0] for o in hits[1:]],
            )
        )
    gone = [o[0] for o in old if o[0] not in used and o[4] == "active"]
    return cands, gone


def cand_row(cd, obsid, seqid, run_utc, tag, git):
    ch = sorted(cd["chain"], key=lambda x: x["t0_met"])
    row = dict(
        nuid=cd["nuid"],
        obsid=obsid,
        seqid=seqid,
        widx=ch[0]["widx"],
        trigger_met=cd["t0"],
        trigger_utc=met2utc(cd["t0"]),
        t_start_met=cd["t0"],
        t_stop_met=cd["t1"],
        t_start_utc=met2utc(cd["t0"]),
        t_stop_utc=met2utc(cd["t1"]),
        duration=cd["t1"] - cd["t0"],
        n_triggers=len(ch),
        algos=json.dumps(sorted({x["algo"] for x in ch})),
        trigger_ids=json.dumps([x["trigger_id"] for x in ch]),
        signature=cd["sig"],
        status="active",
        merged_into=None,
        last_updated_utc=run_utc,
        tag=tag,
        git_hash=git,
    )
    for a in ALGOS:
        sel = [x[ALGOS[a]["rank"]] for x in ch if x["algo"] == a]
        row[f"n_{a}"] = len(sel)
        row[f"max_{a}"] = max(sel) if sel else None
    if cd["action"] == "new":
        row.update(
            first_seen_utc=run_utc, report_status="pending", slack_sent=0, coinc_sent=0
        )
    elif cd["action"] == "updated":
        row["report_status"] = "pending"
    return row


def write_db(cur, ctx, wins, trigs, cands, gone, c, algos, hashes):
    seqid, obsid, run_utc = ctx["seqid"], ctx["obsid"], ctx["run_utc"]
    nuid_of = {x["trigger_id"]: cd["nuid"] for cd in cands for x in cd["chain"]}
    cur.execute("DELETE FROM windows WHERE seqid=?", (seqid,))
    for w in wins:
        upsert(
            cur,
            "windows",
            dict(
                seqid=seqid,
                widx=w["idx"],
                obsid=obsid,
                t0_met=float(w["tt"][0]),
                t1_met=float(w["tt"][-1]),
                t0_utc=met2utc(w["tt"][0]),
                t1_utc=met2utc(w["tt"][-1]),
                n_bins=len(w["tt"]),
                dur_s=float(w["tt"][-1] - w["tt"][0]),
                base_med_a=float(np.median(w["ba"])),
                base_med_b=float(np.median(w["bb"])),
                sc_med=float(np.median(w["sc"])),
                sc_std=float(np.std(w["sc"])),
                param_hash=ctx["common_hash"],
                run_utc=run_utc,
            ),
            "seqid, widx",
        )
    for a in algos:
        algo_table(cur, a, c[a])
        cur.execute(
            f"DELETE FROM {a}_triggers WHERE seqid=? AND param_hash=?",
            (seqid, hashes[a]),
        )
        for tr in [x for x in trigs if x["algo"] == a]:
            row = {k: tr[k] for k in TRIG_COLS if k in tr}
            row.update({k: tr[k] for k in ALGOS[a]["cols"]})
            row.update(
                nuid=nuid_of[tr["trigger_id"]],
                obsid=obsid,
                seqid=seqid,
                t0_utc=met2utc(tr["t0_met"]),
                t1_utc=met2utc(tr["t1_met"]),
                tpeak_utc=met2utc(tr["tpeak_met"]),
                tag=ctx["tag"],
                git_hash=ctx["git"],
                run_utc=run_utc,
            )
            row.update(
                {
                    f"p_{k}": json.dumps(v) if isinstance(v, (list, dict)) else v
                    for k, v in c[a].items()
                }
            )
            upsert(cur, f"{a}_triggers", row, "trigger_id")
    for cd in cands:
        upsert(
            cur,
            "candidates",
            cand_row(cd, obsid, seqid, run_utc, ctx["tag"], ctx["git"]),
            "nuid",
        )
        for m in cd["merged"]:
            cur.execute(
                "UPDATE candidates SET status='merged', merged_into=?, last_updated_utc=? WHERE nuid=?",
                (cd["nuid"], run_utc, m),
            )
    for g in gone:
        cur.execute(
            "UPDATE candidates SET status='gone', last_updated_utc=? WHERE nuid=?",
            (run_utc, g),
        )
    dead = gone + [m for cd in cands for m in cd["merged"]]
    if dead:
        cur.execute(
            f"DELETE FROM nu_bs_ts_match WHERE nubs_id IN ({','.join('?' * len(dead))})",
            dead,
        )


def update_seq_status(cur, ctx, counts, status, d=None):
    prev = cur.execute(
        "SELECT n_runs FROM seq_status WHERE seqid=?", (ctx["seqid"],)
    ).fetchone()
    row = dict(
        seqid=ctx["seqid"],
        obsid=ctx["obsid"],
        path=ctx["path"],
        status=status,
        n_runs=(prev[0] or 0) + 1 if prev else 1,
        last_check_utc=ctx["run_utc"],
        last_error=None,
        **counts,
    )
    if d is not None:
        row.update(
            hk_met0=float(d["t"][0]),
            hk_met1=float(d["t"][-1]),
            last_search_utc=ctx["run_utc"],
        )
    else:
        row["last_report_utc"] = ctx["run_utc"]
    upsert(cur, "seq_status", row, "seqid")


def ts_match(cur, ts_db, nuids, win_s):
    if not nuids:
        return []
    try:
        con = sqlite3.connect(ts_db)
        rows = con.execute(
            "SELECT NuID, trigger_time, missions_list FROM ts_queue"
        ).fetchall()
        con.close()
    except Exception as e:
        log.info(f"ts_match: cannot read {ts_db}: {e}")
        return []
    ts = []
    for nid, tt, miss in rows:
        try:
            ts.append((nid, Time(str(tt).replace("Z", "")).unix, tt, miss))
        except Exception:
            continue
    q = ",".join("?" * len(nuids))
    old = set(
        cur.execute(
            f"SELECT nubs_id, nuts_id FROM nu_bs_ts_match WHERE nubs_id IN ({q})", nuids
        ).fetchall()
    )
    cur.execute(f"DELETE FROM nu_bs_ts_match WHERE nubs_id IN ({q})", nuids)
    cands = cur.execute(
        f"SELECT nuid, trigger_utc, t_start_utc, t_stop_utc FROM candidates "
        f"WHERE nuid IN ({q})",
        nuids,
    ).fetchall()
    new = []
    for nuid, tu, su, eu in cands:
        t0, t1, tr = Time(su).unix, Time(eu).unix, Time(tu).unix
        for nid, tsu, tts, miss in ts:
            if t0 - win_s <= tsu <= t1 + win_s:
                upsert(
                    cur,
                    "nu_bs_ts_match",
                    dict(
                        nubs_id=nuid,
                        nuts_id=nid,
                        dt_s=tsu - tr,
                        nubs_trigger_utc=tu,
                        nuts_trigger_time=tts,
                        missions=miss,
                        matched_utc=now_utc(),
                    ),
                    "nubs_id, nuts_id",
                )
                if (nuid, nid) not in old:
                    new.append((nuid, nid))
    return new


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def clear_pngs(out, algos):
    for d in [out, os.path.join(out, "windows")] + [
        os.path.join(out, f"{a}_algo") for a in algos
    ]:
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(d, "*.png")):
            os.remove(f)


def plot_obs(d, inside, wins, c, out, title):
    t, t0 = d["t"], d["t"][0]
    fig, ax = plt.subplots(3, 1, figsize=(14, 12))
    ax[0].plot(t - t0, d["a"], lw=0.3, label="Shield A rate (SHLDLO)")
    ax[0].plot(t - t0, d["b"], lw=0.3, label="Shield B rate (SHLDLO)")
    ax[0].set_title("Raw shield rates, full sequence", fontsize=9, loc="left")
    (
        ax[0].set_xlabel(f"time since sequence start (s)    [MET {t0:.0f}]"),
        ax[0].set_ylabel("counts/s"),
    )

    ax[1].plot(t - t0, d["a"], lw=0.3, color="C0", label="Shield A rate (SHLDLO)")
    for k, (i0, i1) in enumerate(intervals(inside)):
        ax[1].axvspan(
            t[i0] - t0,
            t[i1] - t0,
            color="r",
            alpha=0.15,
            label="SAA pass (removed)" if k == 0 else None,
        )
    for w in wins:
        ax[1].axvspan(
            w["tt"][0] - t0,
            w["tt"][-1] - t0,
            color="C2",
            alpha=0.08 + 0.07 * (w["idx"] % 2),
            label="good window (searched), labelled wXX" if w["idx"] == 0 else None,
        )
        ax[1].text(
            0.5 * (w["tt"][0] + w["tt"][-1]) - t0,
            0.95,
            f"w{w['idx']:02d}",
            fontsize=7,
            ha="center",
            transform=ax[1].get_xaxis_transform(),
        )
    ax[1].set_title(
        f"SAA cut in time: {len(wins)} windows (min {c['min_bins']} bins; split at HK gaps > "
        f"{c['max_gap_s']} s)",
        fontsize=9,
        loc="left",
    )
    (
        ax[1].set_xlabel(f"time since sequence start (s)    [MET {t0:.0f}]"),
        ax[1].set_ylabel("counts/s"),
    )

    ax[2].scatter(
        d["lon"][~inside],
        d["lat"][~inside],
        s=1,
        c="C0",
        label="orbit outside SAA (kept)",
    )
    ax[2].scatter(
        d["lon"][inside],
        d["lat"][inside],
        s=1,
        c="r",
        label="orbit inside SAA (removed)",
    )
    ax[2].add_patch(
        patches.PathPatch(
            Path(c["saa_polygon"]), fc="r", alpha=0.15, label="SAA polygon"
        )
    )
    ax[2].set_title(
        f"SAA cut on the ground track: {100 * inside.mean():.1f}% of the sequence in SAA",
        fontsize=9,
        loc="left",
    )
    (
        ax[2].set_xlabel("satellite longitude (deg)"),
        ax[2].set_ylabel("satellite latitude (deg)"),
    )
    for a in ax:
        bottom_legend(a)
    fig.suptitle(f"{title}  {met2utc(t0)} -> {met2utc(t[-1])}", fontsize=11)
    _save(fig, os.path.join(out, "obs_overview.png"))


def plot_windows(wins, out, title):
    for w in wins:
        x, tag = w["tt"] - w["tt"][0], f"w{w['idx']:02d}"
        raw = [
            ("Shield A rate (SHLDLO)", w["a"], 0.4, "C0"),
            ("Shield B rate (SHLDLO)", w["b"], 0.4, "C1"),
            ("Shield A baseline (running median)", w["ba"], 1.2, "navy"),
            ("Shield B baseline (running median)", w["bb"], 1.2, "darkred"),
        ]
        det = [
            ("Shield A rate - baseline", w["a"] - w["ba"], 0.4, "C0"),
            ("Shield B rate - baseline", w["b"] - w["bb"], 0.4, "C1"),
        ]
        fig, ax = plt.subplots(4, 1, figsize=(14, 14), sharex=True)
        for k, (series, split, what) in enumerate(
            [
                (raw, None, "Raw shield rates with baseline: full linear scale"),
                (
                    raw,
                    RAW_SPLIT,
                    f"Raw shield rates with baseline: linear up to "
                    f"{RAW_SPLIT[1]} counts/s, log above",
                ),
                (det, None, "Detrended shield rates: full linear scale"),
                (
                    det,
                    DET_SPLIT,
                    f"Detrended shield rates: linear in {DET_SPLIT} "
                    f"counts/s, log beyond",
                ),
            ]
        ):
            for lab, y, lw, col in series:
                ax[k].plot(x, y, lw=lw, color=col, label=lab)
            if split:
                split_scale(ax[k], *split, np.concatenate([y for _, y, _, _ in series]))
            ax[k].set_title(what, fontsize=9, loc="left")
            ax[k].set_ylabel("counts/s")
            bottom_legend(ax[k])
        ax[3].set_xlabel(
            f"time since window start (s)    [window start MET {w['tt'][0]:.0f}]"
        )
        fig.suptitle(
            f"{title}  {tag}  start {met2utc(w['tt'][0])}  ({len(x)} bins, {x[-1]:.0f} s)",
            fontsize=11,
        )
        _save(fig, os.path.join(out, "windows", f"{tag}_window.png"))


def plot_trigger_map(cd, d, path, gap):
    tref = cd["t0"]
    m = (d["t"] >= cd["t0"] - gap - 300) & (d["t"] <= cd["t1"] + gap + 300)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(d["t"][m] - tref, d["a"][m], lw=0.5, label="SHLDLO A")
    ax.plot(d["t"][m] - tref, d["b"][m], lw=0.5, label="SHLDLO B")
    for a in sorted({x["algo"] for x in cd["chain"]}):
        for k, tr in enumerate([x for x in cd["chain"] if x["algo"] == a]):
            col = ALGOS[a]["color"]
            ax.axvspan(
                tr["t0_met"] - tref,
                tr["t1_met"] - tref,
                color=col,
                alpha=0.25,
                label=f"{a} trigger span" if k == 0 else None,
            )
            ax.axvline(tr["tpeak_met"] - tref, color=col, lw=1)
    ax.axvline(
        cd["t0"] - gap - tref,
        color="grey",
        ls=":",
        label=f"grouping zone (+/-{gap:.0f} s)",
    )
    ax.axvline(cd["t1"] + gap - tref, color="grey", ls=":")
    ax.set_xlabel(f"seconds from {met2utc(tref)}"), ax.set_ylabel("counts/s")
    ax.set_title(
        f"{cd['nuid']}  {len(cd['chain'])} trigger(s)  {cd['sig']}",
        fontsize=9,
        loc="left",
    )
    bottom_legend(ax)
    _save(fig, path)


def run_report(nuid, utc, seq_path, year_dir, essential):
    cdir = os.path.join(year_dir, nuid)
    for f in glob.glob(os.path.join(cdir, f"grb_report_{nuid}.p*")):
        os.remove(f)
    cmd = [
        sys.executable,
        os.path.join(HERE, "make_grb_report_callable.py"),
        nuid,
        utc,
        "--data_path",
        seq_path,
        "--dest_path",
        year_dir,
        "--config_path",
        essential,
    ]
    log.info(f"  report: {' '.join(cmd)}")
    r = subp.run(cmd, cwd=HERE, capture_output=True, text=True)
    with open(os.path.join(cdir, f"{nuid}_report_stdout.log"), "w") as f:
        f.write(r.stdout + r.stderr)
    return (
        "done"
        if os.path.exists(os.path.join(cdir, f"grb_report_{nuid}.pdf"))
        else "failed"
    )


def reports_only(cur, ctx, cand_dir, essential):
    rows = cur.execute(
        "SELECT nuid, trigger_utc FROM candidates WHERE seqid=? AND status='active'",
        (ctx["seqid"],),
    ).fetchall()
    log.info(f"reports_only: {len(rows)} active candidate(s)")
    for nuid, utc in rows:
        year_dir = os.path.join(cand_dir, nuid[4:8])
        os.makedirs(os.path.join(year_dir, nuid), exist_ok=True)
        st = run_report(nuid, utc, ctx["path"], year_dir, essential)
        cur.execute(
            "UPDATE candidates SET report_status=?, report_utc=? WHERE nuid=?",
            (st, now_utc(), nuid),
        )
        log.info(f"  {nuid}: report {st}")


def apply_overrides(c, sets):
    for s in sets or []:
        k, v = s.split("=", 1)
        try:
            v = json.loads(v)
        except ValueError:
            pass
        node, keys = c, k.split(".")
        for kk in keys[:-1]:
            node = node[kk]
        node[keys[-1]] = v


def resolve_seq(args, cfg):
    if args.seq_path:
        p = os.path.abspath(args.seq_path)
        return os.path.basename(os.path.dirname(p)), os.path.basename(p), p
    if args.obsid and args.seqid:
        return (
            args.obsid,
            args.seqid,
            os.path.abspath(
                os.path.join(cfg["nustar-data-dir"], args.obsid, args.seqid)
            ),
        )
    if args.time:
        out = get_nu_obs.get_seq(args.time, cfg["sings-paths"]["essential-data-path"])
        p = os.path.abspath(out[6])
        return os.path.basename(os.path.dirname(p)), out[2], p
    sys.exit("give --seq_path, or --obsid and --seqid, or --time")


def parse_args():
    p = ag.ArgumentParser(description="NuSTAR SINGS blind search on one sequence.")
    p.add_argument(
        "--seq_path",
        help="e.g. /disk/bifrost/nustar/fltops/61201018_4FGL_J1548d8m2250/61201018002",
    )
    p.add_argument("--obsid", help="e.g. 61201018_4FGL_J1548d8m2250")
    p.add_argument("--seqid", help="e.g. 61201018002")
    p.add_argument("--time", help="any UTC inside the sequence; resolved with get_seq")
    p.add_argument(
        "--algos", nargs="+", help="subset of algos (default: bs_config algos)"
    )
    p.add_argument(
        "--windows",
        nargs="+",
        type=int,
        help="debug: only these windows, no DB/reports",
    )
    p.add_argument(
        "--set",
        nargs="+",
        help="override config, e.g. mf.floor=7 bb.block_thresh=5 w_base=200",
    )
    p.add_argument("--config", default="nusings_config.yaml")
    p.add_argument("--db", help="alternate DB (paper reruns / tests)")
    p.add_argument("--products_dir", help="alternate bs_products_dir")
    p.add_argument("--tag", default="live", help="label stored with every row")
    p.add_argument("--no_plots", action="store_true")
    p.add_argument("--no_report", action="store_true")
    p.add_argument(
        "--force_report",
        action="store_true",
        help="rerun reports for all candidates in this seq",
    )
    p.add_argument(
        "--reports_only",
        action="store_true",
        help="skip search, rerun reports of active candidates",
    )
    p.add_argument(
        "--no_seq_status",
        action="store_true",
        help="do not update seq_status (wrapper may rerun)",
    )
    p.add_argument("--dry_run", action="store_true", help="no DB writes, no reports")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = load_config(args.config)
    c, bp = cfg["bs_config"], cfg["bs_paths"]
    apply_overrides(c, args.set)
    if args.set and not (args.db or args.dry_run):
        sys.exit("--set needs --db or --dry_run")
    obsid, seqid, seq_path = resolve_seq(args, cfg)
    base = args.products_dir or bp["bs_products_dir"]
    out = os.path.join(base, bp["obs_dir"], obsid, seqid)
    cand_dir = os.path.join(base, bp["cand_dir"])
    essential = cfg["sings-paths"]["essential-data-path"]
    os.makedirs(out, exist_ok=True)
    setup_log(os.path.join(out, "nubs_obs.log"))
    t_run = time.time()
    algos = args.algos or c["algos"]
    common = {k: c[k] for k in ("saa_polygon", "min_bins", "max_gap_s", "w_base")}
    hashes = {a: phash(dict(common, algo=a, **c[a])) for a in algos}
    dry = args.dry_run or args.windows is not None
    mode = "reports_only" if args.reports_only else ("dry_run" if dry else "search")
    ctx = dict(
        seqid=seqid,
        obsid=obsid,
        path=seq_path,
        run_utc=now_utc(),
        tag=args.tag,
        git=git_hash(),
        common_hash=phash(common),
    )

    log.info("=" * 90)
    log.info(
        f"NUBS run {ctx['run_utc']}  mode {mode}  git {ctx['git']}  tag {args.tag}"
    )
    log.info(f"seq {obsid}/{seqid}  path {seq_path}")
    log.info(f"args {vars(args)}")
    for a in algos:
        log.info(f"{a} params {c[a]}  hash {hashes[a]}")
    log.info(f"common {common}  group_gap_s {c['group_gap_s']}")

    f = seq_files(seq_path, seqid)
    counts = row_counts(f)
    log.info(f"rows {counts}")
    if (
        counts["hk_rows_a"] is None
        or counts["hk_rows_b"] is None
        or not os.path.exists(f["att"])
    ):
        log.info("missing HK or attorb, exiting")
        sys.exit(2)

    conn = sqlite3.connect(args.db or bp["bs_db"])
    init_db(conn)
    cur = conn.cursor()

    if args.reports_only:
        reports_only(cur, ctx, cand_dir, essential)
        if not args.no_seq_status:
            update_seq_status(cur, ctx, counts, "reports_only")
        upsert(
            cur,
            "obs_runs",
            dict(
                seqid=seqid,
                run_utc=ctx["run_utc"],
                obsid=obsid,
                mode=mode,
                args=json.dumps(vars(args)),
                git_hash=ctx["git"],
                tag=args.tag,
                elapsed_s=time.time() - t_run,
                **counts,
            ),
            "seqid, run_utc",
        )
        conn.commit()
        conn.close()
        log.info(f"done in {time.time() - t_run:.1f}s")
        return

    d = load_data(f)
    inside, wins = build_windows(d, c)
    exp = sum(w["tt"][-1] - w["tt"][0] for w in wins)
    log.info(
        f"data MET {d['t'][0]:.0f}-{d['t'][-1]:.0f}  ({met2utc(d['t'][0])} -> {met2utc(d['t'][-1])})  "
        f"{len(d['t'])} bins  SAA {100 * inside.mean():.1f}%  {len(wins)} windows  exp {exp / 1e3:.1f} ks"
    )
    for w in wins:
        log.info(
            f"  w{w['idx']:02d}  MET {w['tt'][0]:.0f}-{w['tt'][-1]:.0f}  {met2utc(w['tt'][0])}  "
            f"dur {w['tt'][-1] - w['tt'][0]:.0f}s  base A {np.median(w['ba']):.0f}  B {np.median(w['bb']):.0f}  "
            f"sc std {np.std(w['sc']):.2f}"
        )
    run_wins = [w for w in wins if args.windows is None or w["idx"] in args.windows]

    trigs, aux = run_algos(run_wins, algos, c, hashes, seqid)
    chains = group(trigs, c["group_gap_s"])
    cands, gone = assign(cur, chains, seqid, c["group_gap_s"])
    log.info(f"{len(trigs)} trigger(s) -> {len(cands)} candidate(s)")
    for cd in cands:
        log.info(
            f"  {cd['nuid']}  {cd['action']:<9}  {cd['sig']:<10}  {met2utc(cd['t0'])}  "
            f"span {cd['t1'] - cd['t0']:.0f}s  w{cd['chain'][0]['widx']:02d}"
            + (f"  merged {cd['merged']}" if cd["merged"] else "")
        )
    for g in gone:
        log.info(f"  {g}  gone (no triggers in latest data)")

    if not args.no_plots:
        if args.windows is None:
            clear_pngs(out, algos)
        os.makedirs(os.path.join(out, "windows"), exist_ok=True)
        plot_obs(d, inside, wins, c, out, f"{obsid}/{seqid}")
        plot_windows(run_wins, out, f"{obsid}/{seqid}")
        for a in algos:
            adir = os.path.join(out, f"{a}_algo")
            os.makedirs(adir, exist_ok=True)
            for w in run_wins:
                ALGOS[a]["plot"](
                    w,
                    [x for x in trigs if x["algo"] == a and x["widx"] == w["idx"]],
                    aux[(a, w["idx"])],
                    c[a],
                    adir,
                    f"w{w['idx']:02d}",
                    f"{obsid}/{seqid}  w{w['idx']:02d}  start {met2utc(w['tt'][0])}",
                )
        log.info(f"plots in {out}")

    if dry:
        log.info("dry run: no DB writes, no reports")
        conn.close()
        log.info(f"done in {time.time() - t_run:.1f}s")
        return

    write_db(cur, ctx, wins, trigs, cands, gone, c, algos, hashes)
    new = ts_match(
        cur,
        cfg["sings-paths"]["ts-queue-db"],
        [cd["nuid"] for cd in cands],
        c["ts_match_s"],
    )
    for nb, nt in new:
        log.info(f"  match {nb} <-> {nt}")

    for cd in cands:
        year_dir = os.path.join(cand_dir, cd["nuid"][4:8])
        os.makedirs(os.path.join(year_dir, cd["nuid"]), exist_ok=True)
        plot_trigger_map(
            cd,
            d,
            os.path.join(year_dir, cd["nuid"], "trigger_map.png"),
            c["group_gap_s"],
        )
        st = cur.execute(
            "SELECT report_status FROM candidates WHERE nuid=?", (cd["nuid"],)
        ).fetchone()[0]
        if args.no_report or not (
            args.force_report or cd["action"] != "unchanged" or st != "done"
        ):
            continue
        st = run_report(cd["nuid"], met2utc(cd["t0"]), seq_path, year_dir, essential)
        cur.execute(
            "UPDATE candidates SET report_status=?, report_utc=? WHERE nuid=?",
            (st, now_utc(), cd["nuid"]),
        )
        log.info(f"  {cd['nuid']}: report {st}")

    if not args.no_seq_status:
        update_seq_status(cur, ctx, counts, "searched", d)
    acts = [cd["action"] for cd in cands]
    upsert(
        cur,
        "obs_runs",
        dict(
            seqid=seqid,
            run_utc=ctx["run_utc"],
            obsid=obsid,
            mode=mode,
            args=json.dumps(vars(args)),
            git_hash=ctx["git"],
            tag=args.tag,
            params_json=json.dumps(c),
            param_hashes=json.dumps(hashes),
            n_bins=len(d["t"]),
            met0=float(d["t"][0]),
            met1=float(d["t"][-1]),
            saa_frac=float(inside.mean()),
            n_windows=len(wins),
            exp_s=float(exp),
            n_triggers=json.dumps(
                {a: sum(x["algo"] == a for x in trigs) for a in algos}
            ),
            n_new=acts.count("new"),
            n_updated=acts.count("updated"),
            n_unchanged=acts.count("unchanged"),
            n_gone=len(gone),
            elapsed_s=time.time() - t_run,
            **counts,
        ),
        "seqid, run_utc",
    )
    conn.commit()
    conn.close()
    log.info(f"done in {time.time() - t_run:.1f}s")


if __name__ == "__main__":
    main()
