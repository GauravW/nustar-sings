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
from matplotlib.ticker import FixedLocator, NullLocator, FuncFormatter


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


RAW_SPLIT = (None, 2000)
DET_SPLIT = (-500, 1500)
SIG_SPLIT = (-20, 60)
EXTRA = [50, 100, 200, 500, 1e3, 2e3, 5e3, 1e4, 2e4, 5e4, 1e5, 2e5]


def split_scale(ax, lo, hi, y):
    """Linear between lo and hi, log10 beyond (one decade = a third of the linear span)."""
    dec = (hi - (lo or 0)) / 3.0

    def fwd(v):
        v = np.asarray(v, float)
        out = np.where(v > hi, hi + dec * np.log10(np.maximum(v, hi) / hi), v)
        if lo is not None and lo < 0:
            out = np.where(v < lo, lo - dec * np.log10(np.minimum(v, lo) / lo), out)
        return out

    def inv(z):
        z = np.asarray(z, float)
        out = np.where(z > hi, hi * 10 ** ((np.maximum(z, hi) - hi) / dec), z)
        if lo is not None and lo < 0:
            out = np.where(z < lo, lo * 10 ** ((lo - np.minimum(z, lo)) / dec), out)
        return out

    ax.set_yscale("function", functions=(fwd, inv))
    base = lo if lo is not None else 0
    ymin, ymax = np.nanmin(y), np.nanmax(y)
    bot = ymin * 1.2 if ymin < base else base
    top = ymax * 1.2 if ymax > hi else hi
    ax.set_ylim(bot, top)
    ticks = list(np.linspace(base, hi, 5)) + [
        t
        for t in EXTRA + [-e for e in EXTRA]
        if t >= 2 * hi or (base < 0 and t <= 2 * base)
    ]
    ax.yaxis.set_major_locator(FixedLocator([t for t in ticks if bot <= t <= top]))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    for b in [hi] + ([lo] if lo is not None and lo < 0 else []):
        ax.axhline(b, color="k", lw=0.5, ls="-.", alpha=0.4)


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _spans(ax, win, trigs, color, name):
    t0 = win["tt"][0]
    for k, tr in enumerate(trigs):
        ax.axvspan(
            win["tt"][tr["i0"]] - t0,
            win["tt"][tr["i1"]] - t0,
            color=color,
            alpha=0.3,
            label=f"{name} trigger span" if k == 0 else None,
        )
        ax.axvline(
            win["tt"][tr["ipk"]] - t0,
            color="k",
            ls="--",
            lw=0.8,
            label="trigger peak" if k == 0 else None,
        )


def _panel(ax, title, ylabel):
    ax.set_title(title, fontsize=9, loc="left")
    ax.set_ylabel(ylabel, fontsize=9)
    ax.legend(fontsize=7, loc="upper right", ncol=2)


def algo_figure(win, trigs, aux, p, name, color, title):
    x = win["tt"] - win["tt"][0]
    fig, ax = plt.subplots(4, 1, figsize=(12, 14), sharex=True)
    ax[0].plot(x, win["a"], lw=0.4, label="Shield A rate (SHLDLO)")
    ax[0].plot(x, win["b"], lw=0.4, label="Shield B rate (SHLDLO)")
    ax[0].plot(
        x, win["ba"], lw=1.2, color="navy", label="Shield A baseline (running median)"
    )
    ax[0].plot(
        x,
        win["bb"],
        lw=1.2,
        color="darkred",
        label="Shield B baseline (running median)",
    )
    split_scale(ax[0], *RAW_SPLIT, np.r_[win["a"], win["b"]])
    ax[1].plot(x, win["a"] - win["ba"], lw=0.4, label="Shield A rate - baseline")
    ax[1].plot(x, win["b"] - win["bb"], lw=0.4, label="Shield B rate - baseline")
    split_scale(ax[1], *DET_SPLIT, np.r_[win["a"] - win["ba"], win["b"] - win["bb"]])
    ax[2].plot(
        x,
        win["sa"],
        lw=0.4,
        label="Shield A significance: (A - baseline)/sqrt(baseline)",
    )
    ax[2].plot(
        x,
        win["sb"],
        lw=0.4,
        label="Shield B significance: (B - baseline)/sqrt(baseline)",
    )
    ax[2].axhline(
        p["floor"],
        color="grey",
        ls=":",
        label=f"coincidence floor = {p['floor']} sigma",
    )
    split_scale(ax[2], *SIG_SPLIT, np.r_[win["sa"], win["sb"]])
    lo, hi = ax[2].get_ylim()
    ax[2].fill_between(
        x,
        lo,
        hi,
        where=aux["gate"],
        step="mid",
        color="C4",
        alpha=0.25,
        label=f"gate open: both shields >= floor for {p['nmin']} consecutive s",
    )
    ax[2].set_ylim(lo, hi)
    for a in ax:
        _spans(a, win, trigs, color, name)
    _panel(
        ax[0],
        f"Raw shield rates (linear up to {RAW_SPLIT[1]} counts/s, log above)",
        "counts/s",
    )
    _panel(
        ax[1],
        f"Detrended shield rates (linear in {DET_SPLIT}, log outside)",
        "counts/s",
    )
    _panel(
        ax[2],
        "Per-shield significance and two-shield coincidence gate (triggers need the gate open)",
        "sigma per 1-s bin\n(linear -20..60, log beyond)",
    )
    ax[3].set_xlabel(
        f"time since window start (s)    [window start MET {win['tt'][0]:.0f}]"
    )
    fig.suptitle(f"{title}\n{name}: {len(trigs)} trigger(s)", fontsize=11)
    return fig, ax, x


def plot_mf(win, trigs, aux, p, outdir, tag, title):
    fig, ax, x = algo_figure(win, trigs, aux, p, "Matched filter (MF)", "C2", title)
    ax[3].plot(
        x,
        win["sc"],
        lw=0.4,
        color="0.6",
        label="Combined significance: (sigma_A + sigma_B)/sqrt(2)",
    )
    ax[3].plot(
        x,
        aux["stat"],
        lw=0.8,
        color="C3",
        label=f"Max windowed significance over W = {p['timescales']} s: mean(combined) x sqrt(W)",
    )
    ax[3].axhline(
        p["thresh"],
        color="grey",
        ls=":",
        label=f"MF detection threshold = {p['thresh']} sigma",
    )
    split_scale(ax[3], *SIG_SPLIT, np.r_[win["sc"], aux["stat"]])
    _panel(
        ax[3],
        "MF statistic: boxcar-averaged combined significance (trigger = above threshold AND gate open)",
        "sigma (linear -20..60, log beyond)",
    )
    _save(fig, os.path.join(outdir, f"{tag}_mf.png"))


def plot_bb(win, trigs, aux, p, outdir, tag, title):
    fig, ax, x = algo_figure(win, trigs, aux, p, "Bayesian Blocks (BB)", "C1", title)
    ax[3].plot(
        x,
        aux["x"],
        lw=0.3,
        color="0.6",
        label="Whitened combined significance: (combined - median)/MAD, mean removed",
    )
    ax[3].step(
        x,
        aux["stat"],
        where="mid",
        lw=0.9,
        color="C0",
        label="Block significance: block mean x sqrt(bins in block)",
    )
    ax[3].axhline(
        p["block_thresh"],
        color="grey",
        ls=":",
        label=f"BB block threshold = {p['block_thresh']} sigma",
    )
    split_scale(ax[3], *SIG_SPLIT, np.r_[aux["x"], aux["stat"]])
    _panel(
        ax[3],
        f"BB statistic: blocks fitted on whitened combined stream, downsampled x{p['bb_downsample']} "
        f"(trigger = block above threshold AND gate open)",
        "sigma (linear -20..60, log beyond)",
    )
    _save(fig, os.path.join(outdir, f"{tag}_bb.png"))


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
