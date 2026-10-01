"""
Algorithms for the NuSTAR SINGS blind search (NUBS).
Author: Gaurav Waratkar

Note:
- To add an algorithm: write detect_x(win, p) -> (triggers, aux) and plot_x(...), then add it to ALGOS
  and give it a block in bs_config. nubs_search.py builds its DB table from ALGOS[x]["cols"].
- detect_x returns trigger dicts with i0, i1, ipk (indices into the window), score, plus its own columns.
"""

import os
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from astropy.stats import bayesian_blocks
from matplotlib import pyplot as plt


def running_mean(x, W):
    W = int(W)
    x = np.asarray(x, float)
    if W <= 1:
        return x.copy()
    xp = np.pad(x, (W // 2, W - 1 - W // 2), mode="edge")
    c = np.cumsum(np.insert(xp, 0, 0.0))
    return (c[W:] - c[:-W]) / W


def running_median(x, W):
    W = int(W)
    x = np.asarray(x, float)
    if W <= 1:
        return x.copy()
    xp = np.pad(x, (W // 2, W - 1 - W // 2), mode="edge")
    return np.median(sliding_window_view(xp, W), axis=-1)


def sliding_count(mask, W):
    W = int(W)
    xp = np.pad(np.asarray(mask, float), (W // 2, W - 1 - W // 2), mode="constant")
    c = np.cumsum(np.insert(xp, 0, 0.0))
    return c[W:] - c[:-W]


def intervals(mask):
    i = np.where(mask)[0]
    if i.size == 0:
        return []
    return [(b[0], b[-1]) for b in np.split(i, np.where(np.diff(i) > 1)[0] + 1)]


def coincidence_gate(sa, sb, floor, nmin):
    return sliding_count((sa >= floor) & (sb >= floor), nmin) >= nmin


def whiten(x):
    med = float(np.median(x))
    mad = float(1.4826 * np.median(np.abs(x - med)))
    return (x - med) / max(mad, 1e-6), med, mad


def finalize(tt, keep, score, edge_pad, merge_gap):
    keep = np.asarray(keep, bool).copy()
    if edge_pad > 0 and len(keep) > 2 * edge_pad:
        keep[:edge_pad] = False
        keep[-edge_pad:] = False
    out = []
    for i0, i1 in intervals(keep):
        j = i0 + int(np.argmax(score[i0 : i1 + 1]))
        if out and tt[i0] <= tt[out[-1][1]] + merge_gap:
            out[-1][1] = i1
            if score[j] > score[out[-1][2]]:
                out[-1][2] = j
        else:
            out.append([i0, i1, j])
    return out


def bb_edges(tt, x, f, p0):
    f = int(f)
    sig = 1.0
    if f > 1:
        n = (len(x) // f) * f
        tt, x, sig = (
            tt[:n].reshape(-1, f).mean(1),
            x[:n].reshape(-1, f).mean(1),
            1 / np.sqrt(f),
        )
    return bayesian_blocks(tt, x, sigma=sig, fitness="measures", p0=p0)


def block_sig(edges, tt, x):
    blk = np.clip(np.searchsorted(edges, tt, side="right") - 1, 0, len(edges) - 2)
    nb = len(edges) - 1
    s = np.bincount(blk, weights=x, minlength=nb)
    n = np.bincount(blk, minlength=nb).astype(float)
    mean = np.divide(s, n, out=np.zeros_like(s), where=n > 0)
    return blk, mean * np.sqrt(n)


def detect_mf(win, p):
    sc = win["sc"]
    ws = np.array([running_mean(sc, W) * np.sqrt(W) for W in p["timescales"]])
    wsig = ws.max(0)
    gate = coincidence_gate(win["sa"], win["sb"], p["floor"], p["nmin"])
    trigs = []
    for i0, i1, j in finalize(
        win["tt"], (wsig >= p["thresh"]) & gate, sc, p["edge_pad"], p["merge_gap"]
    ):
        k = i0 + int(np.argmax(wsig[i0 : i1 + 1]))
        trigs.append(
            dict(
                i0=i0,
                i1=i1,
                ipk=j,
                score=float(sc[j]),
                wsig_pk=float(wsig[k]),
                best_ts=int(p["timescales"][int(np.argmax(ws[:, k]))]),
            )
        )
    return trigs, dict(stat=wsig, gate=gate)


def detect_bb(win, p):
    tt = win["tt"]
    x, med, mad = whiten(win["sc"])
    if p["center"]:
        x = x - x.mean()
    gate = coincidence_gate(win["sa"], win["sb"], p["floor"], p["nmin"])
    if len(x) < p["min_bb_bins"]:
        return [], dict(stat=np.zeros(len(x)), gate=gate, x=x)
    edges = bb_edges(tt, x, p["bb_downsample"], p["p0"])
    blk, sig = block_sig(edges, tt, x)
    trigs = []
    for i0, i1, j in finalize(
        tt, (sig >= p["block_thresh"])[blk] & gate, x, p["edge_pad"], p["merge_gap"]
    ):
        sel = np.where(blk == blk[j])[0]
        trigs.append(
            dict(
                i0=i0,
                i1=i1,
                ipk=j,
                score=float(x[j]),
                rec_sig=float(sig[blk[j]]),
                blk_t0=float(tt[sel[0]]),
                blk_t1=float(tt[sel[-1]]),
                blk_n=int(sel.size),
                n_blocks=int(sig.size),
                w_med=med,
                w_mad=mad,
            )
        )
    return trigs, dict(stat=sig[blk], gate=gate, x=x)


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _axis(ax, win, title, ylabel):
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_xlabel(f"MET - {win['tt'][0]:.0f} (s)")
    ax.set_ylabel(ylabel)
    ax.legend(fontsize=7, loc="upper right")


def _spans(ax, win, trigs, color):
    t0 = win["tt"][0]
    for tr in trigs:
        ax.axvspan(
            win["tt"][tr["i0"]] - t0, win["tt"][tr["i1"]] - t0, color=color, alpha=0.3
        )
        ax.axvline(win["tt"][tr["ipk"]] - t0, color="k", ls="--", lw=0.8)


def plot_common(win, trigs, aux, p, outdir, tag, name, color):
    x = win["tt"] - win["tt"][0]
    title = f"{tag} {name}: {len(trigs)} trigger(s)"
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(x, win["a"], lw=0.4, label="SHLDLO A")
    ax.plot(x, win["b"], lw=0.4, label="SHLDLO B")
    _spans(ax, win, trigs, color)
    _axis(ax, win, title, "counts/s")
    _save(fig, os.path.join(outdir, f"{tag}_raw_triggers.png"))

    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(x, win["a"] - win["ba"], lw=0.4, label="A - baseline")
    ax.plot(x, win["b"] - win["bb"], lw=0.4, label="B - baseline")
    _spans(ax, win, trigs, color)
    _axis(ax, win, title, "detrended counts/s")
    _save(fig, os.path.join(outdir, f"{tag}_det_triggers.png"))

    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(x, win["sa"], lw=0.4, label="sigma A")
    ax.plot(x, win["sb"], lw=0.4, label="sigma B")
    ax.axhline(p["floor"], color="grey", ls=":", label=f"floor {p['floor']}")
    lo, hi = ax.get_ylim()
    ax.fill_between(
        x,
        lo,
        hi,
        where=aux["gate"],
        step="mid",
        color=color,
        alpha=0.3,
        label=f"gate (nmin {p['nmin']})",
    )
    ax.set_ylim(lo, hi)
    _axis(ax, win, f"{tag} {name}: coincidence gate", "sigma / bin")
    _save(fig, os.path.join(outdir, f"{tag}_gate.png"))


def plot_mf(win, trigs, aux, p, outdir, tag):
    plot_common(win, trigs, aux, p, outdir, tag, "MF", "C2")
    x = win["tt"] - win["tt"][0]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(x, win["sc"], lw=0.4, color="0.6", label="sigma comb")
    ax.plot(
        x,
        aux["stat"],
        lw=0.7,
        color="C3",
        label=f"max windowed sigma {p['timescales']}",
    )
    ax.axhline(p["thresh"], color="grey", ls=":", label=f"thresh {p['thresh']}")
    _spans(ax, win, trigs, "C2")
    _axis(ax, win, f"{tag} MF: windowed significance", "sigma")
    _save(fig, os.path.join(outdir, f"{tag}_wsig_thresh.png"))


def plot_bb(win, trigs, aux, p, outdir, tag):
    plot_common(win, trigs, aux, p, outdir, tag, "BB", "C1")
    x = win["tt"] - win["tt"][0]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    ax.plot(x, aux["x"], lw=0.3, color="0.6", label="whitened sigma comb")
    ax.step(
        x, aux["stat"], where="mid", lw=0.9, color="C0", label="block sig (mean*sqrt n)"
    )
    ax.axhline(
        p["block_thresh"],
        color="grey",
        ls=":",
        label=f"block_thresh {p['block_thresh']}",
    )
    _spans(ax, win, trigs, "C1")
    _axis(ax, win, f"{tag} BB: blocks (f={p['bb_downsample']})", "sigma")
    _save(fig, os.path.join(outdir, f"{tag}_blocks.png"))


ALGOS = {
    "mf": dict(
        detect=detect_mf,
        plot=plot_mf,
        rank="wsig_pk",
        color="C2",
        cols=dict(wsig_pk="REAL", best_ts="INTEGER"),
    ),
    "bb": dict(
        detect=detect_bb,
        plot=plot_bb,
        rank="rec_sig",
        color="C1",
        cols=dict(
            rec_sig="REAL",
            blk_t0="REAL",
            blk_t1="REAL",
            blk_n="INTEGER",
            n_blocks="INTEGER",
            w_med="REAL",
            w_mad="REAL",
        ),
    ),
}
