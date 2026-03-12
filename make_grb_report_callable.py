from astropy.io.fits import getdata
from astropy.time import Time
from numpy import where, diff, convolve, ones, array, histogram, polyfit, polyval
from nustar_gen import info, utils
import argparse as ag
from matplotlib import pyplot as plt

from astropy.visualization import time_support
from astropy.coordinates import SkyCoord

import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.path import Path
import matplotlib.patches as patches

import pandas as pd
import os
from astropy import units as u
import numpy as np

from skyfield.api import EarthSatellite, Loader

import nustar_pysolar.io as io
from nusings_config import load_config
import get_nu_obs


def make_report(
    grbtime,
    grb_ra,
    grb_dec,
    name,
    dest="./data/",
    data_path="./data/",
    config_path="./data/",
):
    grb_met = ns.time_to_met(grbtime)
    trange = 200
    lowlim = grb_met - 0.5 * trange
    highlim = grb_met + 0.5 * trange

    tbins = int(trange / 5)  # 5 second bins

    infile = f"{config_path}/aft.txt"
    print(f"Reading AFT file: {infile}")
    with open(infile, "r") as f:
        for line in f:
            if line.startswith(";"):
                continue
            #        print(line)
            fields = line.split("|")[0].split(" ")[1:]
            t0 = Time(fields[0], format="yday")
            t1 = Time(fields[2], format="yday")
            if (grbtime.mjd > t0.mjd) & (grbtime.mjd < t1.mjd):
                break

    seqid = fields[2]
    #    print(seqid)

    infile = f"{config_path}/observing_schedule.txt"
    print(f"Reading observing schedule file: {infile}")
    with open(infile, "r") as f:
        for line in f:
            if seqid in line:
                fields = line.split()
                break
    if fields[4] == "NULL":
        return

    ra_target = float(fields[4])
    dec_target = float(fields[5])
    target_coord = SkyCoord(ra_target, dec_target, unit=(u.deg, u.deg))

    #    print(grb_ra, grb_dec)
    good = True
    set = True
    try:
        coord = SkyCoord(grb_ra, grb_dec, unit=(u.deg, u.deg))
    except:
        print("Skipping visibility check")
        set = False

    if set:
        sep = grb_visibility(grbtime, coord, config_path)
        # print(f"Separation from geocenter: {sep:8.2f}")
        if sep.deg < 45:
            good = False

        boresight_offset = target_coord.separation(coord).deg
    else:
        boresight_offset = -999 * u.deg
        sep = -999 * u.deg
    print(f"GRB offset from boreseight: {boresight_offset:8.2f}")

    seqid = fields[2]
    socname = seqid[0:8] + "_" + fields[3]

    # datpath = f"{data_path}/{socname}"
    # datpath = os.path.join(datadir, socname)
    # seqpath = os.path.join(data_path, seqid)
    # print(seqpath)
    hkdir = os.path.join(data_path, "hk")
    evdir = os.path.join(data_path, "event_cl")

    hka_file = os.path.join(hkdir, f"nu{seqid}A_fpm.hk")
    hkb_file = os.path.join(hkdir, f"nu{seqid}B_fpm.hk")
    attorb_file = os.path.join(evdir, f"nu{seqid}A.attorb")
    print("Looking for the following files:")
    print(f"  {hka_file}")
    print(f"  {hkb_file}")
    print(f"  {attorb_file}")

    if not os.path.exists(hka_file):
        print(f"Missing {hka_file}")
        return

    hka = getdata(hka_file, "HK1FPM")
    hkb = getdata(hkb_file, "HK1FPM")
    # hdr = getheader(hka_file)

    attorb = getdata(attorb_file)

    # Trim to just time in range:

    hka = hka[((hka["TIME"] > lowlim) & (hka["TIME"] < highlim))]
    hkb = hkb[((hkb["TIME"] > lowlim) & (hkb["TIME"] < highlim))]

    if len(hka) < 10:
        print(f"Not enough HK data points for {name}, skipping report.")
        print("This likely means that the data is not yet available.")
        return

    hka_time = ns.met_to_time(hka["TIME"])
    hkb_time = ns.met_to_time(hkb["TIME"])

    # ax.set_ylim([1e-9, 1e-2])
    # y1, y2 = ax.get_ylim()

    kernel_size = 5
    kernel = ones(kernel_size) / kernel_size
    hka_smth = convolve(hka["SHLDLO"], kernel, mode="same")
    hkb_smth = convolve(hkb["SHLDLO"], kernel, mode="same")

    order = 3
    parA = polyfit(hka["TIME"], hka_smth, order)
    parB = polyfit(hkb["TIME"], hkb_smth, order)

    modelA = polyval(parA, hka["TIME"])
    modelB = polyval(parB, hkb["TIME"])

    ymax_a = hka_smth.max()
    ymin_a = hka_smth.min()
    mean_a = hka_smth.mean()

    ymax_b = hkb_smth.max()
    ymin_b = hkb_smth.min()
    mean_b = hkb_smth.mean()

    sub_a = hka_smth - modelA
    sub_b = hkb_smth - modelB
    ysub_max = sub_a.max()

    eva_file = os.path.join(evdir, f"nu{seqid}A_uf.evt")
    evb_file = os.path.join(evdir, f"nu{seqid}B_uf.evt")
    attorb_file = os.path.join(evdir, f"nu{seqid}A.attorb")
    print("Looking for the following files:")
    print(f"  {eva_file}")
    print(f"  {evb_file}")
    print(f"  {attorb_file}")

    eva, hdra = getdata(eva_file, header=True)
    evb = getdata(evb_file)

    eva = eva[(eva["TIME"] > lowlim) & (eva["TIME"] < highlim)]
    evb = evb[(evb["TIME"] > lowlim) & (evb["TIME"] < highlim)]

    ena = array(utils.chan_to_energy(eva["PI"]))
    enb = array(utils.chan_to_energy(evb["PI"]))

    with time_support(format="iso"):
        fig, axs = plt.subplots(nrows=3, ncols=2, figsize=(12, 12))

        #        print(axs.shape)
        ax0 = axs[0, 0]
        ax1 = axs[1, 0]
        ax2 = axs[2, 0]
        ax3 = axs[0, 1]
        ax4 = axs[1, 1]
        ax5 = axs[2, 1]

        grb_utc = ns.met_to_time(grb_met)
        hka_rel = (hka_time - grb_utc).to_value("sec")
        hkb_rel = (hkb_time - grb_utc).to_value("sec")
        print(f"Plotting shield rates")
        ax0.step(
            hka_rel, hka["SHLDLO"], label="SHLDLO_A", linewidth=0.5, color="#d8b365"
        )
        ax0.step(
            hkb_rel, hkb["SHLDLO"], label="SHLDLO_B", linewidth=0.5, color="#5ab4ac"
        )

        ax0.grid()
        ax0.set_ylim(ymin_a * 0.5, ymax_a * 2)
        ax0.plot([0, 0], [ymin_a * 0.5, 10e3], linestyle="dotted", color="#7fbf7b")
        ax0.step(
            hka_rel, hka_smth, label="SHLDLO_A Smoothed", linewidth=0.5, color="#8c510a"
        )
        ax0.step(
            hkb_rel, hkb_smth, label="SHLDLO_B Smoothed", linewidth=0.5, color="#01665e"
        )

        ax0.legend()
        ax0.set_yscale("log")
        ax0.set_title("Shield Rates")
        ax0.set_xlabel("Seconds from GRB_UTC")
        ax0.set_ylabel("Counts/s")

        print(f"Plotting detrended shield rates")
        ax1.grid()
        ax1.step(hka_rel, sub_a, label="SHLDLO A Sub", linewidth=0.5, color="#8c510a")
        ax1.step(hkb_rel, sub_b, label="SHLDLO B Sub", linewidth=0.5, color="#01665e")

        lims = ax1.get_ylim()
        ax1.plot([0, 0], lims, linestyle="dotted", color="#7fbf7b")

        ax1.set_xlabel("Seconds from GRB_UTC")
        ax1.set_title("Shield Rate Detrended")
        ax1.set_ylabel("Counts/s")
        ax1.legend()

        ### Solar stuff
        print("Making GOES plot")
        in_goes = f"{config_path}/xrays-7-day.json"
        # check if this exists or has been downloaded in the last hour, if not download it from NOAA
        if not os.path.exists(in_goes):
            print(f"GOES data file {in_goes} not found. Downloading from NOAA.")
            url = "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"
            os.system(f"wget {url} -O {in_goes}")
        elif (Time.now() - Time(os.path.getmtime(in_goes), format="unix")).sec > 3600:
            print(
                f"GOES data file {in_goes} is older than 1 hour. Downloading new data from NOAA."
            )
            url = "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json"
            os.system(f"wget {url} -O {in_goes}")

        df = pd.read_json(in_goes)
        df["time_tag"] = pd.to_datetime(df["time_tag"], format="%Y-%m-%dT%H:%M:%SZ")
        lowlim_solar = lowlim - 3600
        highlim_solar = highlim + 3600
        tstart_grb = ns.met_to_time(lowlim)
        tend_grb = ns.met_to_time(highlim)
        tstart_solar = ns.met_to_time(lowlim_solar)
        tend_solar = ns.met_to_time(highlim_solar)

        df2 = df[
            (df["energy"] == "0.1-0.8nm")
            & (df["time_tag"] > tstart_solar.datetime)
            & (df["time_tag"] < tend_solar.datetime)
        ]

        ax2.step(df2["time_tag"], df2["flux"], label="GOES-17 XRS 1-min ave")
        # set the xaxis limits to be one hour before tstart and one hour after tend
        ax2.set_xlim(tstart_solar.datetime, tend_solar.datetime)
        # draw a vertical line at grb tstart and tend
        ax2.plot(
            [tstart_grb.datetime, tstart_grb.datetime],
            [1e-7, 1e-3],
            linestyle="dotted",
            color="#7fbf7b",
        )
        ax2.plot(
            [tend_grb.datetime, tend_grb.datetime],
            [1e-7, 1e-3],
            linestyle="dotted",
            color="#7fbf7b",
        )
        # rotate x-axis labels
        plt.setp(ax2.get_xticklabels(), rotation=20, ha="right")

        ax2.set_ylim([1e-7, 1e-3])
        y1, y2 = ax2.get_ylim()
        ax2.set_yscale("log")
        ax22 = ax2.twinx()
        ax22.yaxis.tick_right()
        ax22.set_ylim(y1, y2)

        ax22.set_yscale("log")
        ax22.minorticks_off()

        ax22.set_yticks([1e-8, 1e-7, 1e-6, 1e-5, 1e-4])
        ax22.set_yticklabels(["A", "B", "C", "M", "X"])

        ax2.legend()

        ## Add on when NuSTAR is in sunlight
        in_sun = (where(attorb["SUNSHINE"] == 1))[0]
        di = diff(in_sun)
        transition = (where(di > 1))[0]
        # Check to see if you started in sun:

        lims = ax2.get_ylim()
        height = lims[1] - lims[0]
        left_edge_ind = 0

        for ind, edge in enumerate(transition):
            if ind == 0:
                if in_sun[0] == 0:
                    left_edge = mdates.date2num(
                        ns.met_to_time(attorb["TIME"][0]).datetime
                    )
                    right_edge = mdates.date2num(
                        ns.met_to_time(attorb["TIME"][in_sun[edge]]).datetime
                    )
                else:
                    left_edge = mdates.date2num(
                        ns.met_to_time(attorb["TIME"][in_sun[0]]).datetime
                    )
                    right_edge = mdates.date2num(
                        ns.met_to_time(attorb["TIME"][in_sun[edge]]).datetime
                    )
            else:
                right_edge = mdates.date2num(
                    ns.met_to_time(attorb["TIME"][in_sun[edge]]).datetime
                )
                left_edge = mdates.date2num(
                    ns.met_to_time(attorb["TIME"][left_edge_ind]).datetime
                )

            prev_right_edge = in_sun[edge]
            left_edge_ind = prev_right_edge + di[edge]

            width = right_edge - left_edge
            rect = Rectangle((left_edge, lims[0]), width, 1, color="yellow", alpha=0.5)
            ax2.add_patch(rect)

        left_edge = mdates.date2num(
            ns.met_to_time(attorb["TIME"][left_edge_ind]).datetime
        )
        right_edge = mdates.date2num(
            ns.met_to_time(attorb["TIME"][in_sun].max()).datetime
        )
        width = right_edge - left_edge

        rect = Rectangle((left_edge, lims[0]), width, 1, color="yellow", alpha=0.5)
        ax2.add_patch(rect)
        ax2.set_title("GOES X-ray Flux")

    ### Geographic plot
    print("Making SAA plot")
    # Define vertices here:
    vertices = [[260, -6.25], [350, -6.25], [330, 6.25], [310, 6.25], [260, -6.25]]
    codes = [
        Path.MOVETO,
        Path.LINETO,
        Path.LINETO,
        Path.LINETO,
        Path.CLOSEPOLY,
    ]

    attorb = attorb[(attorb["TIME"] > lowlim) & (attorb["TIME"] < highlim)]

    saa_path = Path(vertices, codes)
    ax3.scatter(attorb["SAT_LON"], attorb["SAT_LAT"], s=1.0)
    ax3.set_xlim(0, 360)
    ax3.set_ylim(-7, 7)
    patch = patches.PathPatch(saa_path, facecolor="blue", lw=2, alpha=0.5)
    ax3.add_patch(patch)
    ax3.set_xlabel("Longitude")
    ax3.set_ylabel("Latitude")
    ax3.set_title("SAA Check")

    ## X-ray counts
    print("Making X-ray count plots")
    with time_support(format="iso"):
        tbins = int(trange / 5)
        hista, edgesa = histogram(
            eva[(ena > 100)]["TIME"], range=(lowlim, highlim), bins=tbins
        )
        histb, edgesb = histogram(
            evb[(enb > 100)]["TIME"], range=(lowlim, highlim), bins=tbins
        )

        widths = edgesa[1] - edgesa[0]
        centers = (edgesa[:-1] + edgesa[1:]) / 2
        ct = ns.met_to_time(centers)
        ct_rel = (ct - grb_utc).to_value("sec")
        ax4.step(ct_rel, hista, label="FPMA", linewidth=0.5, color="#fc8d59")
        ax4.step(ct_rel, histb, label="FPMB", linewidth=0.5, color="#4575b4")
        ax4.set_title(f"E>100 keV, {widths:5.2f}-s bins")
        lims = ax4.get_ylim()

        ax4.plot([0, 0], lims, linestyle="dotted", color="#7fbf7b")
        ax4.legend()
        ax4.set_xlabel("Seconds from GRB_UTC")
        ax4.set_ylabel("Counts per bin")

        print("Making X-ray count plots with finer bins")
        tbins = int(trange / 0.25)
        hista, edgesa = histogram(
            eva[(ena > 100)]["TIME"], range=(lowlim, highlim), bins=tbins
        )
        histb, edgesb = histogram(
            evb[(enb > 100)]["TIME"], range=(lowlim, highlim), bins=tbins
        )

        widths = edgesa[1] - edgesa[0]
        centers = (edgesa[:-1] + edgesa[1:]) / 2
        ct = ns.met_to_time(centers)
        ct_rel = (ct - grb_utc).to_value("sec")
        ax5.step(ct_rel, hista, label="FPMA", linewidth=0.5, color="#fc8d59")
        ax5.step(ct_rel, histb, label="FPMB", linewidth=0.5, color="#4575b4")
        ax5.set_title(f"E>100 keV, {widths:5.2f}-s bins")
        lims = ax5.get_ylim()

        ax5.plot([0, 0], lims, linestyle="dotted", color="#7fbf7b")
        ax5.legend()
        ax5.set_xlabel("Seconds from GRB_UTC")
        ax5.set_ylabel("Counts per bin")
        print(f"Saving report at {dest}/{name}/grb_report_{name}.pdf")

    # grb_number = name.replace("grb", "")
    # set a title for the whole figure with the GRB name and time
    fig.suptitle(f"GRB {name}\n{grbtime.iso} UTC", fontsize=16)
    plt.tight_layout()
    print(f"Saving report at {dest}/{name}/grb_report_{name}.pdf")
    plt.savefig(f"{dest}/{name}/grb_report_{name}.pdf")
    # save a low res version for slack sharing
    plt.savefig(f"{dest}/{name}/grb_report_{name}.png", dpi=100)

    # CsI lightcurve
    print("Making CsI lightcurve PDF")
    ax = plt.figure(figsize=(8, 6)).subplots()
    ax.axvline(0, color="green", linestyle="--", alpha=0.5, label="GRB Time")
    ax.step(hka_rel, hka["SHLDLO"], label="Shield A", where="post")
    ax.step(hkb_rel, hkb["SHLDLO"], label="Shield B", where="post")
    ax.set_ylabel("Shield Count / sec", fontsize=12)
    ax.set_xlabel(f"Seconds since {grbtime.iso} UTC")
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{dest}/{name}/{name}_CsI_lc.pdf", dpi=300)
    plt.savefig(f"{dest}/{name}/{name}_CsI_lc.png", dpi=100)

    # CZT lightcurve (use the following snippet)
    print("Making CZT lightcurve PDF")
    met0 = grb_met - trange * 0.5
    met1 = grb_met + trange * 0.5
    ax = plt.figure(figsize=(8, 6)).subplots()
    ev2B = evb[(evb["TIME"] <= met1) & (evb["TIME"] > met0) & (evb["PI"] > 2460)]
    ev2A = eva[(eva["TIME"] <= met1) & (eva["TIME"] > met0) & (eva["PI"] > 2460)]
    print(f"Events: A={len(ev2A)}, B={len(ev2B)}")
    fig, ax = plt.subplots(figsize=(8, 6))
    bins = np.arange(met0, met1, 1)  # 1 s
    hista, edgesa = histogram(ev2A["TIME"], bins=bins)
    histb, edgesb = histogram(ev2B["TIME"], bins=bins)
    widths = edgesa[1:] - edgesa[:-1]
    centers = (edgesa[:-1] + edgesa[1:]) / 2
    ct = ns.met_to_time(centers)
    ct_rel = (ct - grb_utc).to_value("sec")

    ax.step(
        ct_rel, hista / widths, where="post", color="#fc8d59", alpha=0.7, label="FPMA"
    )
    ax.step(
        ct_rel, histb / widths, where="post", color="#4575b4", alpha=0.7, label="FPMB"
    )
    ax.axvline(0, color="green", linestyle="--", alpha=0.5, label="GRB Time")
    ax.set_ylabel("CZT > 100 keV Counts / sec", fontsize=12)
    ax.set_xlabel(f"Seconds since {grbtime.iso} UTC")
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{dest}/{name}/{name}_CZT_lc.pdf", dpi=300)
    plt.savefig(f"{dest}/{name}/{name}_CZT_lc.png", dpi=100)

    header = "Name, OBSID/SEQID, Start Time, Burst Time, End Time, RA, Dec, Offset to Boresight (deg), Separation from Geocenter (deg), Time of run (UTC)"
    values = f"{name},{socname}/{seqid},{t0.iso},{grbtime.iso},{t1.iso},{grb_ra},{grb_dec},{boresight_offset:8.2f},{sep.value:8.2f}, {Time.now().iso}"
    keys = [k.strip() for k in header.split(",")]
    vals = [v.strip() for v in values.split(",")]

    out = "\n".join(f"{k}: {v}" for k, v in zip(keys, vals))
    print(out)

    # save this to a log file in the name folder inside destination directory
    logfile = os.path.join(dest, name, f"grb_report_{name}.log")
    with open(logfile, "a") as f:
        f.write(out + "\n\n")
    print(f"Run log saved to {logfile}")


def grb_visibility(grbtime, coord, config_path):
    load_path = config_path
    load = Loader(load_path)

    ts = load.timescale()
    t = ts.from_astropy(grbtime)
    planets = load(
        "de436.bsp"
    )  # this just downloads the planetary ephemeris in the config path if not already there
    earth = planets["Earth"]

    # tlefile = io.download_tle(outdir=load_path)
    tlefile = f"{config_path}/NuSTAR.tle"
    mindt, line1, line2 = io.get_epoch_tle(grbtime.datetime, tlefile)
    nustar = EarthSatellite(line1, line2)
    observer = earth + nustar

    astrometric = observer.at(t).observe(earth)
    this_ra, this_dec, dist = astrometric.radec()

    ra_deg = this_ra.to(u.deg)
    dec_deg = this_dec.to(u.deg)

    geocen = SkyCoord(ra_deg, dec_deg, unit=(u.deg, u.deg))
    sep = geocen.separation(coord)

    return sep


if __name__ == "__main__":
    parser = ag.ArgumentParser(
        description="Run the triggered GRB search for NuSTAR SINGS and generate report in the destination directory. \
            Remember to have xrays-7-day.json, aft.txt, observing_schedule.txt, and NuSTAR.tle in the data/ directory."
    )
    parser.add_argument(
        "name",
        type=str,
        help="Name of the GRB (e.g., GRB251007A or NUTSYYYYMMDDTHHMMSS)",
    )
    parser.add_argument(
        "time",
        type=str,
        help="Time of the GRB in ISOT format (e.g., 2025-10-07T19:37:51.50)",
    )
    parser.add_argument(
        "--ra", type=float, help="Right Ascension of the GRB (optional, in degrees)"
    )
    parser.add_argument(
        "--dec", type=float, help="Declination of the GRB (optional, in degrees)"
    )
    parser.add_argument(
        "--dest_path",
        type=str,
        default="./data/",
        help="Destination directory to save the report (default: ./data/)",
    )
    parser.add_argument(
        "--data_path",
        type=str,
        help="Path to data directory. If not provided, it will be determined from the observing schedule. Eg.: /disk/bifrost/nustar/fltops/81202301_GS_1354m64/81202301002",
    )
    parser.add_argument(
        "--config_path",
        type=str,
        default="./data/",
        help="Path to directory containing xrays-7-day.json, aft.txt, observing_schedule.txt, and NuSTAR.tle",
    )
    args = parser.parse_args()
    # add a bunch of print statements throughout the code to help with the flow of the code
    config_path = args.config_path
    ns = info.NuSTAR()
    config_file = load_config(f"{config_path}/nusings_config.yaml")
    # grbtime = Time("2025-10-07 19:37:51.50")
    name = args.name  # NuID
    grbtime = Time(args.time)
    if args.ra is not None and args.dec is not None:
        grb_ra = args.ra
        grb_dec = args.dec
    else:
        print("RA and DEC not provided, skipping visibility check.")
        grb_ra = None
        grb_dec = None
    dest = args.dest_path
    # check if the dest/name directory exists, if not create it
    if not os.path.exists(f"{dest}/{name}"):
        os.makedirs(f"{dest}/{name}")
    if args.data_path is None:
        data_path = get_nu_obs.get_seq(grbtime, config_path)[6]
    else:
        data_path = args.data_path
    print(
        f"Making report for {name} at {grbtime.iso} UTC with RA={grb_ra} and DEC={grb_dec}."
    )
    print(
        f"Provided data path: {data_path}, config path: {config_path}, destination directory: {dest}"
    )
    try:
        make_report(grbtime, grb_ra, grb_dec, name, dest, data_path, config_path)
    except Exception as e:
        print(f"Error making report for {name}: {e}")
