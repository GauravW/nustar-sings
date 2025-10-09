from astropy.time import Time
import astropy.units as u
from astropy.table import Table, vstack
import os
import sys
import numpy as np
from nustar_gen import info, utils

ns = info.NuSTAR()

import warnings
from astropy.utils.exceptions import AstropyWarning

warnings.simplefilter("ignore", category=AstropyWarning)
warnings.simplefilter("ignore", category=RuntimeWarning)
warnings.simplefilter("ignore", np.RankWarning)


class GRBReports:
    """
    Class for GRB reports
    """

    def __init__(
        self, path="/disk/bifrost/bwgref/grb_search", dbase="nustar_grbs.fits"
    ):
        self._proto = {
            "Name": ["NYYMMDD"],
            "Type": ["NULL"],
            "Time": [Time("2012-06-12T00:00:00")],
            "DiscoveryReport": ["null"],
            "GRBReport": ["null"],
            "HEALPIX": ["null"],
            "LocalizationMap": ["null"],
        }

        self.set_path(path)
        self.set_dbase_file(dbase)
        self.dbase = []

    def set_path(self, path):
        """
        Sets the path. Makes sure that path ends with a '/'
        """
        assert os.path.exists(path), f"Path does not exist: {path}"
        path = os.path.abspath(path)
        self._path = path
        return

    def set_dbase_file(self, dbase):
        """
        Sets the path. Makes sure that path ends with a '/'
        """
        self._dbase_file = os.path.join(self._path, dbase)
        return

    @property
    def dbase_file(self):
        """
        Returns the path to the database
        """
        return self._dbase_file

    def make_entry(self, time):
        """
        Make an Astropy Table entry based on the time of the GRB

        Parameters
        ----------
        time: Astropy.Time object

        Returns
        ----------

        entry : Table entry with the form of self._proto

        """

        disc_path = "https://nustarsoc.caltech.edu/NuSTAR_Public/grbs/potentials"

        year = time.ymdhms["year"] - 2000
        month = f"{time.ymdhms['month']}".zfill(2)
        day = f"{time.ymdhms['day']}".zfill(2)
        grbname = f"{year}{month}{day}"

        html_path = os.path.join(disc_path, grbname)

        proto = self._proto.copy()

        # See if there's already one with this name
        alpha = ["A", "B", "C", "D", "E", "F", "G"]
        a = 0
        sname = f"N{grbname}{alpha[a]}"
        while sname in self.dbase["Name"]:
            a += 1
            sname = f"N{grbname}{alpha[a]}"

        proto["Name"] = [sname]
        proto["Type"] = ["Long GRB"]
        proto["Time"] = [time]
        proto["DiscoveryReport"] = [f'<a href="{html_path}"> Discovery </a>']
        return Table(proto)

    def write_dbase(self):
        """
        Write the database to a FITS file
        """
        self.dbase.write(self.dbase_file, overwrite=True)
        return

    def read_dbase(self):
        """
        Read the database from the FITS file
        """
        if os.path.exists(self.dbase_file):
            self.dbase = Table.read(self.dbase_file, astropy_native=True)
        else:
            self.dbase = Table(self._proto.copy())
        return

    def grb_exists(self, time):
        """
        Check to see if this time is already in the database.
        """

        dt = abs(self.dbase["Time"] - time).to(u.hr).value
        if min(dt) < 1:
            # Too close to prior entry
            return True
        else:
            return False

    def add_entry(self, entry):
        """
        Adds an entry to the databsae
        """

        # Check to see if the database has been set yet
        if len(self.dbase) == 0:
            self.dbase = Table(entry)
        else:
            self.dbase = vstack([self.dbase, entry])

    def write_html(self):
        """
        Write an output html table based on dbase
        """

        tab_out = self.dbase.copy()
        tab_out.sort("Name", reverse=True)
        tab_out["Time"] = [x.iso for x in tab_out["Time"][:]]
        htmldict = {
            "raw_html_cols": [
                "DiscoveryReport",
                "GRBReport",
                "HEALPIX",
                "LocalizationMap",
            ]
        }

        html_file = os.path.splitext(self.dbase_file)[0] + ".html"
        tab_out.write(html_file, format="jsviewer", overwrite=True, htmldict=htmldict)

    def clean_init(self):
        """
        Remove the initial stub
        """
        self.dbase = self.dbase[(self.dbase["Name"] != "NYYMMDD")]

    def update_entry(self, entry):
        """
        Update a row
        """
        self.dbase[(self.dbase["Name"] == entry["Name"])] = entry
        return

    def update_reports(self):
        self.read_dbase()
        for row in self.dbase:
            # Check to see if the output report exists already
            outpath = os.path.join(self._path, row["Name"])
            outcheck = os.path.join(outpath, "grb_report.pdf")
            if not os.path.exists(outcheck):
                #                print(row['Name'], row['Time'].iso)
                make_report(row["Time"], outpath=outpath)

        return


def make_saa_path():
    """
    Generates the SAA path to use for filtering
    """
    from matplotlib.path import Path

    # Define vertices here:
    vertices = [[260, -6.25], [350, -6.25], [330, 6.25], [310, 6.25], [260, -6.25]]
    codes = [
        Path.MOVETO,
        Path.LINETO,
        Path.LINETO,
        Path.LINETO,
        Path.CLOSEPOLY,
    ]

    saa_path = Path(vertices, codes)
    return saa_path


def load_shield_data(evdir, seqid, filter_saa=True):
    """
    Loads the shield data and applies the SAA filter

    Parameters
    ----------

    evdir : str
        Full path to the sequence ID directory (the one above event_cl)

    seqid : str
        The numeric sequence ID

    filter_saa : boolean [True]
        Apply the SAA filtering on these data


    Returns
    --------

    hka : Astropy Table

    """
    import numpy as np

    hka_f = os.path.join(evdir, f"nu{seqid}A_fpm.hk")
    hkb_f = os.path.join(evdir, f"nu{seqid}B_fpm.hk")
    attorb_f = os.path.join(evdir, f"nu{seqid}A.attorb")
    if not os.path.exists(hka_f):
        return [-1]

    hka = Table.read(hka_f, hdu="HK1FPM")
    hka4 = Table.read(hka_f, hdu="HK4FPM")
    hkb = Table.read(hkb_f, hdu="HK1FPM")
    attorb = Table.read(attorb_f)

    hka["LAT"] = np.interp(hka["TIME"], attorb["TIME"], attorb["SAT_LAT"])
    hka["LON"] = np.interp(hka["TIME"], attorb["TIME"], attorb["SAT_LON"])
    hka["SHLDLOB"] = np.interp(hka["TIME"], hkb["TIME"], hkb["SHLDLO"])

    if filter_saa:
        saa_path = make_saa_path()

        points = [[x, y] for x, y in zip(hka["LON"], hka["LAT"])]
        saa_check = saa_path.contains_points(points)
        hka_nosaa = hka[(~saa_check)]
    else:
        hka_nosaa = hka

    if len(hka_nosaa) == 0:
        return [-1]
    else:
        return hka_nosaa


def load_evt_data(evdir, seqid, filter_saa=True):
    """
    Loads the shield data and applies the SAA filter

    Parameters
    ----------

    evdir : str
        Full path to the sequence ID directory (the one above event_cl)

    seqid : str
        The numeric sequence ID

    filter_saa : boolean [True]
        Apply the SAA filtering on these data

    Returns
    --------

    evA, evB : Astropy Tables

    """

    eva_file = os.path.join(evdir, f"nu{seqid}A_uf.evt")
    evb_file = os.path.join(evdir, f"nu{seqid}B_uf.evt")
    attorb_f = os.path.join(evdir, f"nu{seqid}A.attorb")

    if not os.path.exists(eva_file):
        return [-1], [-1]
    assert os.path.exists(eva_file), f"Missing event file {eva_file}"

    hka_f = os.path.join(evdir, f"nu{seqid}A_fpm.hk")
    hkb_f = os.path.join(evdir, f"nu{seqid}B_fpm.hk")

    evA = Table.read(eva_file)
    evB = Table.read(evb_file)
    attorb = Table.read(attorb_f)

    evA["LAT"] = np.interp(evA["TIME"], attorb["TIME"], attorb["SAT_LAT"])
    evA["LON"] = np.interp(evA["TIME"], attorb["TIME"], attorb["SAT_LON"])

    evB["LAT"] = np.interp(evB["TIME"], attorb["TIME"], attorb["SAT_LAT"])
    evB["LON"] = np.interp(evB["TIME"], attorb["TIME"], attorb["SAT_LON"])

    if filter_saa:
        saa_path = make_saa_path()
        points = [[x, y] for x, y in zip(evA["LON"], evA["LAT"])]
        saa_check = saa_path.contains_points(points)
        evA = evA[(~saa_check)]

        points = [[x, y] for x, y in zip(evB["LON"], evB["LAT"])]
        saa_check = saa_path.contains_points(points)
        evB = evB[(~saa_check)]

    return evA, evB


def shield_block(
    blocka, outpdf="block.pdf", sm_size=20, win_size=300, sig_limit=8, show_plot=False
):
    """
    Make the shield plot

    Parameters
    ----------
    blocka : Astropy Table for time block
    show_plot : boolean
        Make the output PDF [False]
    outpdf : str
        Path to PDF location ['block.pdf']

    Optional Parameters
    --------------------
    sm_size : float
        Size of smoothing function to use [20]
    win_size : float
        Size of window function to use [300]
    sig_limit : float
        Significance to use to test for potential GRB
    Returns
    -------
    potentials : array
        List of potential MET times where
    """
    from scipy.ndimage import uniform_filter1d
    import matplotlib.pyplot as plt
    #     out_pdf = os.path.join(outpath, f'{seqid}_block{blockind}_AB.pdf')
    #     out_txt = os.path.join(outpath, f'{seqid}_block{blockind}_AB.txt')

    # Search parameters (all in seconds)
    block_size = 1000.0  # Chunk to analyze

    smA = uniform_filter1d(blocka["SHLDLO"], sm_size)
    smB = uniform_filter1d(blocka["SHLDLOB"], sm_size)

    winA = uniform_filter1d(blocka["SHLDLO"], win_size)
    winB = uniform_filter1d(blocka["SHLDLOB"], win_size)

    sigA = (smA - winA) / np.sqrt(smA)
    sigB = (smB - winB) / np.sqrt(smB)

    # Potentials are where *both* A and B look high

    potentials = (np.where((sigA > sig_limit) & (sigB > sig_limit)))[0]

    if show_plot:
        from astropy.visualization import time_support

        with time_support(format="iso"):
            t = ns.met_to_time(blocka["TIME"])
            ax = plt.figure(figsize=(8, 8)).subplots(nrows=2)
            ax[0].step(t, blocka["SHLDLO"], alpha=0.5)
            ax[0].step(t, blocka["SHLDLOB"], alpha=0.5)
            ax[0].step(t, smA, alpha=0.5)
            ax[0].step(t, smB, alpha=0.5)
            ax[0].step(t, winA, alpha=0.5)
            ax[0].step(t, winB, alpha=0.5)
            ax[0].set_ylim(500, smA.max() * 3)
            ax[0].set_ylabel("SHLDLO Rate")
            ax[1].step(t, sigA, alpha=0.5)
            ax[1].step(t, sigB, alpha=0.5)
            ax[1].set_ylabel("Significance")
            ax[1].plot(
                ax[1].get_xlim(), (8, 8), linestyle="--", alpha=0.5, color="green"
            )
            for ii in potentials:
                ax[0].plot(
                    ns.met_to_time([blocka["TIME"][ii], blocka["TIME"][ii]]),
                    [500, smA.max() * 3],
                    alpha=0.5,
                    linestyle="--",
                )
                ax[1].plot(
                    ns.met_to_time([blocka["TIME"][ii], blocka["TIME"][ii]]),
                    ax[1].get_ylim(),
                    alpha=0.5,
                    linestyle="--",
                )

            plt.savefig(outpdf)
    return potentials


def shield_block_fit(
    blocka, outpdf="block.pdf", sm_size=20, win_size=300, sig_limit=9, show_plot=False
):
    """
    Make the shield plot

    Parameters
    ----------
    blocka : Astropy Table for time block
    show_plot : boolean
        Make the output PDF [False]
    outpdf : str
        Path to PDF location ['block.pdf']

    Optional Parameters
    --------------------
    sm_size : float
        Size of smoothing function to use [20]
    win_size : float
        Size of window function to use [300]
    sig_limit : float
        Significance to use to test for potential GRB
    Returns
    -------
    potentials : array
        List of potential MET times where
    """
    from numpy import convolve, ones, polyfit, polyval

    import matplotlib.pyplot as plt
    #     out_pdf = os.path.join(outpath, f'{seqid}_block{blockind}_AB.pdf')
    #     out_txt = os.path.join(outpath, f'{seqid}_block{blockind}_AB.txt')

    # Search parameters (all in seconds)
    block_size = 1000.0  # Chunk to analyze

    kernel_size = 21
    kernel = ones(kernel_size) / kernel_size
    smA = convolve(blocka["SHLDLO"], kernel, mode="same")
    smB = convolve(blocka["SHLDLOB"], kernel, mode="same")

    order = 3
    parA = polyfit(blocka["TIME"], smA, order)
    parB = polyfit(blocka["TIME"], smB, order)

    modelA = polyval(parA, blocka["TIME"])
    modelB = polyval(parB, blocka["TIME"])

    subA = smA - modelA
    subB = smB - modelB

    sigA = (subA) / np.sqrt(smA)
    sigB = (subB) / np.sqrt(smB)

    # Potentials are where *both* A and B look high

    potentials = (
        np.where(
            (sigA > sig_limit)
            & (sigB > sig_limit)
            & (blocka["TIME"] > (blocka["TIME"].min() + 60))
            & (blocka["TIME"] < (blocka["TIME"].max() - 60))
        )
    )[0]

    if show_plot:
        from astropy.visualization import time_support

        with time_support(format="iso"):
            t = ns.met_to_time(blocka["TIME"])
            ax = plt.figure(figsize=(8, 8)).subplots(nrows=2)
            ax[0].step(t, blocka["SHLDLO"], alpha=0.5)
            ax[0].step(t, blocka["SHLDLOB"], alpha=0.5)
            ax[0].step(t, smA, alpha=0.5)
            ax[0].step(t, smB, alpha=0.5)
            ax[0].step(t, smA, alpha=0.5)
            ax[0].step(t, smB, alpha=0.5)
            ax[0].set_ylim(500, smA.max() * 3)
            ax[0].set_ylabel("SHLDLO Rate")
            ax[1].step(t, sigA, alpha=0.5)
            ax[1].step(t, sigB, alpha=0.5)
            ax[1].set_ylabel("Significance")
            ax[1].plot(
                ax[1].get_xlim(), (8, 8), linestyle="--", alpha=0.5, color="green"
            )
            for ii in potentials:
                ax[0].plot(
                    ns.met_to_time([blocka["TIME"][ii], blocka["TIME"][ii]]),
                    [500, smA.max() * 3],
                    alpha=0.5,
                    linestyle="--",
                )
                ax[1].plot(
                    ns.met_to_time([blocka["TIME"][ii], blocka["TIME"][ii]]),
                    ax[1].get_ylim(),
                    alpha=0.5,
                    linestyle="--",
                )

            plt.savefig(outpdf)
    return potentials


def long_grb_search(path, seqid, block_size=1000, shift_size=0, outpath="./"):
    """
    Run the long GRB search on a sequence ID

    Parameters
    -----------
    path : str
        Path to the level above the sequence ID (e.g., SOCNAME directory)

    seqid : str
        Numeric sequence ID

    Optional Parameters
    --------------------
    block_size : int
        Length of individual blocks [1000]
    shift_size : int
        Number of seconds to offset the search windows from the first event [0.0]
    """
    found = False
    evdir = os.path.join(path, f"{seqid}/event_cl")
    hka = load_shield_data(evdir, seqid)

    if len(hka) == 1:
        #        print(f'Bad shield data {seqid}')
        return

    # Load the existing GRB database
    grbs = GRBReports(path=outpath)
    grbs.read_dbase()

    saa_path = make_saa_path()

    t0 = hka["TIME"].min() + shift_size
    t1 = t0 + block_size
    blockind = 0
    found = False
    while t1 < hka["TIME"].max() - block_size:
        blocka = hka[(hka["TIME"] > t0) & (hka["TIME"] < t1)]

        if len(blocka) < 100:
            t0 = t1
            t1 = t0 + block_size
            blockind += 1
            continue
        potentials = shield_block_fit(blocka)

        if len(potentials) > 0:
            for ii in potentials:
                # Check to see if potential is in the SAA:

                # Check to see if you're already in the GRB list
                grb_time = ns.met_to_time(blocka["TIME"][ii])
                if not grbs.grb_exists(grb_time):
                    entry = grbs.make_entry(grb_time)
                    entry["Type"] = ["Long GRB"]

                    outdir = os.path.join(outpath, f"{entry['Name'][0]}")
                    if not os.path.isdir(outdir):
                        os.mkdir(outdir)
                    pdfname = f"{entry['Name'][0]}_long_discovery.pdf"
                    outpdf = os.path.join(outdir, pdfname)
                    tmp = shield_block_fit(blocka, show_plot=True, outpdf=outpdf)

                    relative_dir = f"{entry['Name'][0]}"
                    # proto['DiscoveryReport'] = [f'<a href="{html_path}"> Discovery </a>']

                    entry["DiscoveryReport"] = [
                        f'<a href="{os.path.join(relative_dir, pdfname)}"> Discovery </a>'
                    ]
                    entry["GRBReport"] = [
                        f'<a href="{os.path.join(relative_dir, "grb_report.pdf")}"> Summary </a>'
                    ]
                    grbs.add_entry(entry)
                    grbs.write_dbase()
                    print("")
                    print("******")
                    print(f"Potential Long GRB at {grb_time.iso}")
                    print("******")
                    print()
                    found = True
        t0 = t1
        t1 = t0 + block_size
        blockind += 1
    return found


def short_block(
    inA, inB, outpdf="short_block.pdf", binsize=0.25, win_size_sec=60.0, show_plot=False
):
    """
    Perform the short GRB search on a given block

    Inputs
    ------

    evA, evB : Astropy Table for FPMA, and FPMB

    Optional Parameters
    --------------------
    binsize : float
        Time bin to use in seconds [0.25]
    win_size_sec : float
        Window function to use in seconds [60]

    Returns
    -------
    potentials : array
        List of potential MET times where
    """
    from scipy.ndimage import uniform_filter1d
    from scipy.stats import poisson

    win_size = int(win_size_sec / binsize)

    tlow = inA["TIME"].min()
    thigh = inA["TIME"].max()

    bins = int((thigh - tlow) / binsize)

    valsA, edgesA = np.histogram(inA["TIME"], bins=bins, range=(tlow, thigh))
    valsB, edgesB = np.histogram(inB["TIME"], bins=bins, range=(tlow, thigh))

    winA = uniform_filter1d(valsA.astype("float"), win_size)
    winB = uniform_filter1d(valsB.astype("float"), win_size)

    sigA = (valsA - winA) / np.sqrt(winA)
    sigB = (valsB - winB) / np.sqrt(winB)

    probA = 1.0 - poisson.cdf(valsA, winA)
    probB = 1.0 - poisson.cdf(valsB, winB)

    # Account for "holes":
    probA[(winA == 0)] = 1.0
    probB[(winB == 0)] = 1.0

    # Set the false alarm rate to be something like 1/100 per sample
    nsamples = bins
    prob_limit = (30.0 / 100) / nsamples
    cenA = ns.met_to_time((edgesA[:-1] + edgesA[1:]) / 2)

    potentials = cenA[(np.where((probA < prob_limit) & (probB < prob_limit)))[0]]

    if show_plot:
        from astropy.visualization import time_support
        import matplotlib.pyplot as plt

        with time_support(format="iso"):
            axs = plt.figure(figsize=(10, 12)).subplots(nrows=3, ncols=1)
            axs[0].step(cenA, valsA, alpha=0.5)
            axs[0].step(cenA, valsB, alpha=0.5)
            axs[0].set_title(f"Counts in {binsize} bins")
            axs[0].set_ylabel("Counts")

            enA = utils.chan_to_energy(inA["PI"])
            enB = utils.chan_to_energy(inB["PI"])
            axs[1].scatter(ns.met_to_time(inA["TIME"]), enA, alpha=0.5)
            axs[1].scatter(ns.met_to_time(inB["TIME"]), enB, alpha=0.5)
            axs[1].set_ylim(3, 500)
            axs[1].set_ylabel("Energy")

            axs[2].scatter(cenA, 1.0 / probA, alpha=0.5)
            axs[2].scatter(cenA, 1.0 / probB, alpha=0.5)

            axs[2].plot(
                ns.met_to_time([edgesA.min(), edgesA.max()]),
                [1.0 / prob_limit, 1.0 / prob_limit],
                alpha=0.5,
                linestyle="--",
                color="green",
            )
            axs[2].set_ylabel("Chance")
            axs[2].set_yscale("log")
            for grb_time in potentials:
                t = ns.time_to_met(grb_time)
                axs[0].plot(
                    ns.met_to_time([t, t]), axs[0].get_ylim(), alpha=0.5, linestyle="--"
                )
                axs[1].plot(
                    ns.met_to_time([t, t]), axs[1].get_ylim(), alpha=0.5, linestyle="--"
                )
                axs[2].plot(
                    ns.met_to_time([t, t]), axs[2].get_ylim(), alpha=0.5, linestyle="--"
                )

        plt.savefig(outpdf)

    return potentials


def short_grb_search(path, seqid, block_size=1000, shift_size=0, outpath="./"):
    """
    Run the long GRB search on a sequence ID

    Parameters
    -----------
    path : str
        Path to the level above the sequence ID (e.g., SOCNAME directory)

    seqid : str
        Numeric sequence ID

    Optional Parameters
    --------------------
    block_size : int
        Length of individual blocks [1000]
    shift_size : int
        Number of seconds to offset the search windows from the first event [60]
    """

    # Load the existing GRB database
    grbs = GRBReports(path=outpath)
    grbs.read_dbase()

    found = False
    evdir = os.path.join(path, f"{seqid}/event_cl")
    evA, evB = load_evt_data(evdir, seqid)

    if (len(evA) == 1) | (len(evB) == 1):
        #         print(f'Bad events {B}')
        return

    chan_lim = utils.energy_to_chan(100.0)
    evA = evA[(evA["PI"] > chan_lim)]
    evB = evB[(evB["PI"] > chan_lim)]

    tstart = evA["TIME"].min() + shift_size
    tstop = evA["TIME"].max()
    ind = 0
    tshift = tstart + block_size * ind

    while tshift < (tstop - block_size):
        tlow = tshift
        thigh = tshift + block_size

        inA = evA[(evA["TIME"] > tlow) & (evA["TIME"] < thigh)]
        inB = evB[(evB["TIME"] > tlow) & (evB["TIME"] < thigh)]

        ind += 1
        tshift = tstart + block_size * ind

        if (len(inA) < 10) | (len(inB) < 10):
            continue

        potentials = short_block(inA, inB)
        if len(potentials) > 0:
            for grb_time in potentials:
                # Check to see if you're already in the GRB list
                #                grb_time = ns.met_to_time(blocka['TIME'][ii])
                if not grbs.grb_exists(grb_time):
                    entry = grbs.make_entry(grb_time)
                    entry["Type"] = ["Long GRB"]

                    outdir = os.path.join(outpath, f"{entry['Name'][0]}")
                    if not os.path.isdir(outdir):
                        os.mkdir(outdir)
                    pdfname = f"{entry['Name'][0]}_short_discovery.pdf"
                    outpdf = os.path.join(outdir, pdfname)
                    tmp = short_block(inA, inB, show_plot=True, outpdf=outpdf)

                    relative_dir = f"{entry['Name'][0]}"
                    # proto['DiscoveryReport'] = [f'<a href="{html_path}"> Discovery </a>']

                    entry["DiscoveryReport"] = [
                        f'<a href="{os.path.join(relative_dir, pdfname)}"> Discovery </a>'
                    ]
                    entry["GRBReport"] = [
                        f'<a href="{os.path.join(relative_dir, "grb_report.pdf")}"> Summary </a>'
                    ]
                    entry["Type"] = ["Short"]
                    grbs.add_entry(entry)
                    grbs.write_dbase()

                    print("")
                    print("******")
                    print(f"Potential short GRB at {grb_time.iso}")
                    print("******")
                    print()

                    found = True


def run_grb_search(infile="NuSTAR.aft", seqid="None", latency=7, outpath="./"):
    """
    Run the long GRB search. Loops over the AFT to look for any sequence ID
    within `latency` time of the current time.

    Optional Parameters
    -------------------
    infile : str
        Path to the AFT
    seqid : str
        If set, run the search on a specific sequence ID rather than looping over the
        AFT ['None']
    latency : int
        Number of days in the past to look for
    outpath : str
        Path to the output directory ['./']
    """

    datadir = "/disk/bifrost/nustar/fltops/"

    nt = Time.now()
    infile = "NuSTAR.aft"
    with open(infile, "r") as f:
        for line in f:
            if line.startswith(";"):
                continue
            if "Slew" in line:
                continue

            #         if seqid in line:
            #             break
            fields = line.split("|")
            this_seqid = fields[2]
            socname = this_seqid[0:8] + "_" + fields[3]

            if fields[7] == "NULL":
                continue

            if seqid == "None":
                date = fields[0]
                yt = Time(date, format="yday")
                dt = (nt - yt).to(u.d).value
                if dt > latency:
                    continue

                year = (date.split(":"))[0]
            else:
                if this_seqid != seqid:
                    continue

            datpath = os.path.join(datadir, socname)
            date = fields[0]
            tstart = Time(date, format="yday")
            date = fields[1]
            tend = Time(date, format="yday")

            print(f"Running: {datpath} covers {tstart.iso} to {tend.iso}")
            for shift_size in [0, 500]:
                found_l = long_grb_search(
                    datpath, this_seqid, outpath=outpath, shift_size=shift_size
                )
            for shift_size in [0, 0.125]:
                found_s = short_grb_search(
                    datpath, this_seqid, outpath=outpath, shift_size=shift_size
                )
            grbs = GRBReports(path=outpath)
            grbs.read_dbase()
            grbs.update_reports()
            grbs.write_html()

    return


def make_report(grbtime, outpath="./"):
    """
    Generates the GRB reports for a given GRB time
    """
    import matplotlib

    font = {"size": 8}
    matplotlib.rc("font", **font)

    import pandas as pd

    in_goes = "xrays-7-day.json"

    from sunpy import timeseries as ts
    from sunpy.net import Fido
    from sunpy.net import attrs as a
    import matplotlib.dates as mdates
    from matplotlib.patches import Rectangle
    from matplotlib.path import Path
    import matplotlib.patches as patches
    from astropy.io.fits import getdata, getheader
    from astropy.time import Time
    from numpy import where, diff, convolve, ones, array, histogram, polyfit, polyval

    from nustar_gen import utils
    from astropy.visualization import time_support
    import matplotlib.pyplot as plt

    grb_met = ns.time_to_met(grbtime)
    trange = 600
    lowlim = grb_met - 0.5 * trange
    highlim = grb_met + 0.5 * trange

    tstart = ns.met_to_time(lowlim)
    tend = ns.met_to_time(highlim)
    tbins = int(trange / 5)  # 5 second bins

    infile = "NuSTAR.aft"
    with open(infile, "r") as f:
        for line in f:
            if line.startswith(";"):
                continue
            fields = line.split("|")
            t0 = Time(fields[0], format="yday")
            t1 = Time(fields[1], format="yday")
            if (grbtime.mjd > t0.mjd) & (grbtime.mjd < t1.mjd):
                break
    seqid = fields[2]
    socname = seqid[0:8] + "_" + fields[3]

    datadir = "/disk/bifrost/nustar/fltops/"
    datpath = os.path.join(datadir, socname)
    seqpath = os.path.join(datpath, seqid)
    hkdir = os.path.join(seqpath, "hk")
    evdir = os.path.join(seqpath, "event_cl")

    hka_file = os.path.join(hkdir, f"nu{seqid}A_fpm.hk")
    hkb_file = os.path.join(hkdir, f"nu{seqid}B_fpm.hk")
    attorb_file = os.path.join(evdir, f"nu{seqid}A.attorb")

    if not os.path.exists(hka_file):
        return

    hka = getdata(hka_file, "HK1FPM")
    hkb = getdata(hkb_file, "HK1FPM")
    hdr = getheader(hka_file)

    attorb = getdata(attorb_file)

    # Trim to just time in range:

    hka = hka[((hka["TIME"] > lowlim) & (hka["TIME"] < highlim))]
    hkb = hkb[((hkb["TIME"] > lowlim) & (hkb["TIME"] < highlim))]

    hka_time = ns.met_to_time(hka["TIME"])
    hkb_time = ns.met_to_time(hkb["TIME"])

    kernel_size = 10
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

        ax0.step(
            hka_time, hka["SHLDLO"], label="SHLDLO_A", linewidth=0.5, color="#d8b365"
        )
        ax0.step(
            hkb_time, hkb["SHLDLO"], label="SHLDLO_B", linewidth=0.5, color="#5ab4ac"
        )

        ax0.grid()
        ax0.set_ylim(ymin_a * 0.5, 10e3)
        ax0.plot(
            ns.met_to_time([grb_met, grb_met]),
            [ymin_a * 0.5, 10e3],
            linestyle="dotted",
            color="#7fbf7b",
        )
        ax0.step(
            hka_time,
            hka_smth,
            label="SHLDLO_A Smoothed",
            linewidth=0.5,
            color="#8c510a",
        )
        ax0.step(
            hkb_time,
            hkb_smth,
            label="SHLDLO_B Smoothed",
            linewidth=0.5,
            color="#01665e",
        )

        ax0.legend()
        ax0.set_yscale("log")

        ax1.grid()
        ax1.step(hka_time, sub_a, label="SHLDLO A Sub", linewidth=0.5, color="#8c510a")
        ax1.step(hkb_time, sub_b, label="SHLDLO B Sub", linewidth=0.5, color="#01665e")
        lims = ax1.get_ylim()

        ax1.plot(
            ns.met_to_time([grb_met, grb_met]),
            lims,
            linestyle="dotted",
            color="#7fbf7b",
        )
        ax1.legend()

        ### Solar stuff
        # Check and see if you're within the last 7 days:
        nt = Time.now()
        if (nt - grbtime).jd < 7:
            in_goes = "xrays-7-day.json"
            download = False
            if os.path.exists(in_goes):
                if os.path.getmtime(in_goes) > 2 * 3600:
                    os.remove(in_goes)
                    download = True
            else:
                download = True
            if download:
                import subprocess

                subprocess.run(
                    [
                        "wget",
                        "https://services.swpc.noaa.gov/json/goes/primary/xrays-7-day.json",
                    ]
                )

            df = pd.read_json(in_goes)
            df["time_tag"] = pd.to_datetime(df["time_tag"], format="%Y-%m-%dT%H:%M:%SZ")

            df2 = df[
                (df["energy"] == "0.1-0.8nm")
                & (df["time_tag"] > tstart.datetime)
                & (df["time_tag"] < tend.datetime)
            ]

            ax2.step(df2["time_tag"], df2["flux"], label="GOES-17 XRS 1-min ave")
            ax22 = ax2.twinx()
            y1, y2 = ax2.get_ylim()

            ax22.yaxis.tick_right()
            ax22.set_ylim(1e-7, 1e-3)
            ax2.set_ylim(1e-7, 1e-3)

            ax22.set_yscale("log")
            ax22.minorticks_off()

            ax22.set_yticks([1e-7, 1e-6, 1e-5, 1e-4])
            ax22.set_yticklabels(["B", "C", "M", "X"])

        else:
            # Do the FIDO thing:
            if tstart < Time("2019-01-01T01:00:00"):
                gind = 15
            else:
                gind = 18
            result = Fido.search(a.Time(tstart.fits, tend.fits), a.Instrument("XRS"))
            files = Fido.fetch(result, progress=False)
            if len(files) > 0:
                goes_all = ts.TimeSeries(files, concatenate=True)
                goes = goes_all.truncate(tstart.iso, tend.iso)
                goes.plot(columns=["xrsb"], axes=ax2)
                #        ax2.xaxis.set_major_formatter(DateFormatter('%Y-%m-%d %H:%M'))
                ax2.set_ylim(1e-7, 1e-3)

        ax2.set_xlim(tstart.datetime, tend.datetime)
        ax2.set_yscale("log")

        y1, y2 = ax2.get_ylim()

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

        # Now get the last one:
        left_edge = mdates.date2num(
            ns.met_to_time(attorb["TIME"][left_edge_ind]).datetime
        )
        right_edge = mdates.date2num(
            ns.met_to_time(attorb["TIME"][in_sun].max()).datetime
        )
        width = right_edge - left_edge

        rect = Rectangle((left_edge, lims[0]), width, 1, color="yellow", alpha=0.5)
        ax2.add_patch(rect)

    ###
    ### Geographic plot
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

        #

        ax4.step(ct, hista, label="FPMA", linewidth=0.5, color="#fc8d59")
        ax4.step(ct, histb, label="FPMB", linewidth=0.5, color="#4575b4")
        ax4.set_title(f"E>100 keV, {widths:5.2f}-s bins")
        lims = ax4.get_ylim()

        ax4.plot(
            ns.met_to_time([grb_met, grb_met]),
            lims,
            linestyle="dotted",
            color="#7fbf7b",
        )

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

        #

        ax5.step(ct, hista, label="FPMA", linewidth=0.5, color="#fc8d59")
        ax5.step(ct, histb, label="FPMB", linewidth=0.5, color="#4575b4")
        ax5.set_title(f"E>100 keV, {widths:5.2f}-s bins")
        lims = ax5.get_ylim()

        ax5.plot(
            ns.met_to_time([grb_met, grb_met]),
            lims,
            linestyle="dotted",
            color="#7fbf7b",
        )

    outpdf = os.path.join(outpath, "grb_report.pdf")
    plt.savefig(outpdf)


def grb_visibility(grbtime, coord):
    # Initialize Skyfield ephemeris tools.
    from skyfield.api import EarthSatellite, Loader
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    import nustar_pysolar.io as io

    load_path = "./"
    load = Loader(load_path)

    ts = load.timescale()
    t = ts.from_astropy(grbtime)

    planets = load("de436.bsp")
    earth = planets["Earth"]

    tlefile = io.download_tle(outdir=load_path)
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


# Main here
if __name__ == "__main__":
    seqid = "None"
    if len(sys.argv) > 1:
        seqid = f"{sys.argv[1]}"
        print(f"Running for {seqid}")

    outpath = "/home/nustar1/Web/grbs/automated_search"
    #    outpath='./'
    run_grb_search(seqid=seqid, latency=7, outpath=outpath)

    # Update html file
    grbs = GRBReports(path=outpath)
    grbs.read_dbase()
    grbs.clean_init()
    grbs.write_dbase()
    grbs.update_reports()
    grbs.write_dbase()
    grbs.write_html()
