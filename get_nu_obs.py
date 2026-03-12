"""
Get the seq ids of the NuSTAR observations for a given GRB time.

"""

import pandas as pd
import os
import numpy as np
from astropy.time import Time


def find_matching_orbit_row(obs, trigger_time_utc):
    """
    obs: pandas DataFrame with 'Start' and 'End' columns (yday format strings)
    trigger_time_utc: string in yday format, e.g. '2026:047:22:30:00'
    """

    start = obs["Start"].astype(str).values
    end = obs["End"].astype(str).values

    mask = (start <= trigger_time_utc) & (end >= trigger_time_utc)

    idx = np.where(mask)[0]
    print(f"Found {len(idx)} matching orbits for trigger time {trigger_time_utc}")

    if len(idx) == 0:
        raise RuntimeError("No matching orbit found")
    if len(idx) > 1:
        print("Warning: multiple matching orbits found")

    return obs.iloc[idx]


def get_seq(grbtime, config_path="data/"):
    # ;Start|End|Sequenceid|Name|RA|Dec|Yaw Offset|Q0|Q1|Q2|Q3|Event|Last Modified|Comment
    cols = [
        "Start",
        "End",
        "sequenceID",
        "Name",
        "RA",
        "Dec",
        "Yaw Offset",
        "Q0",
        "Q1",
        "Q2",
        "Q3",
        "Event",
        "Last Modified",
        "Comment",
    ]

    rows = []
    aft_path = os.path.join(config_path, "NuSTAR.aft")

    with open(aft_path, "r") as f:
        for line in f.readlines()[1:]:
            line = line.strip()
            if not line or line.startswith(";"):
                continue

            parts = line.split('|')

            if len(parts) < len(cols):
                continue

            rows.append(parts[: len(cols)])

    obs = pd.DataFrame(rows, columns=cols)
    obs["Start"] = obs["Start"].astype(str)
    obs["End"] = obs["End"].astype(str)
    grbtime = Time(grbtime, scale="utc")
    sep = find_matching_orbit_row(obs, grbtime.yday)
    start, end, seqid, name, ra_point, dec_point = sep[
        ["Start", "End", "sequenceID", "Name", "RA", "Dec"]
    ].values[0]
    base = "/disk/bifrost/nustar/fltops/"
    seq1 = seqid[:-3]
    path = f"{base}/{seq1}_{name}/{seqid}/"
    return start, end, seqid, name, ra_point, dec_point, path


if __name__ == "__main__":
    grbtime = "2026-03-12T09:46:46.62"
    start, end, seqid, name, ra_point, dec_point, path = get_seq(
        grbtime, config_path="../config_data/"
    )
    print(f"Start: {start}")
    print(f"End: {end}")
    print(f"Sequence ID: {seqid}")
    print(f"Name: {name}")
    print(f"RA Pointing: {ra_point}")
    print(f"Dec Pointing: {dec_point}")
    print(f"Data Path: {path}")
