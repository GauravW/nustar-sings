"""
Queue script for the NuSTAR SINGS - triggered searches.
Author: Gaurav Waratkar

Note:
- Script assumes that all GCN notices are stored in a local db.
- Script maintains the queue at nuts_queue.db file - columns: NuID, trigger_time, ra, dec, missions_list, queue_status
- Script will combine multiple notices for the trigger times within 100s.
    - We use the earliest trigger time as the main trigger time.
    - We store the list of all missions that reported these notices in the new queue entry.
    - Queue status is set to "pending" for new entries.
- Script will check the queue for pending entries.
    - The trigger is either a new entry added to the queue. Or the availability of new NuSTAR data. (need to watch this folder)
- For each pending entry, it will check if NuSTAR data is available.
- If data is available, it will set the queue status to "processing" and trigger the triggered search pipeline.
- After the search is complete, change queue status to "completed", "data gap", or "failed" (if there was an error).
    - This check is done by checking if 3 pdf files and a log file are present in the output directory.
- Everything is controlled via a config file: nusings_config.yaml
    - config file contains db paths, data directories (Nustar data & our output path), whether to send slack notifications or not,
    - how long back to check for new notices, etc
- Logs everything with package logging
"""

import os
import time
import sqlite3
from astropy.time import Time
import astropy.units as u
import subprocess as subp
from nusings_config import load_config
import glob
from message_slack import send_slack_message, send_slack_files


def create_nuid_from_isot(trigger_time_isot):
    """
    Create a NuID from the trigger time in ISOT format.
    Args:
        trigger_time_isot (str): Trigger time in ISOT format.
    Returns:
        str: NuID in the format 'NUTSYYYYMMDDTHHMMSS'
    """
    t = Time(trigger_time_isot, format="isot", scale="utc")
    nuid = f"NUTS{t.strftime('%Y%m%dT%H%M%S')}"
    return nuid


def merge_new_notices_to_queue(gcn_db_path, ts_queue_db_path, ts_back_search):
    """
    Merge new GCN notices from gcn_db_path to the triggered search queue db at ts_queue_db_path.
    Args:
        gcn_db_path (str): Path to the GCN notices database.
        ts_queue_db_path (str): Path to the triggered search queue database.
        ts_back_search (int): Number of days back to search for new notices.
    """
    gcn_conn = sqlite3.connect(gcn_db_path)
    gcn_cursor = gcn_conn.cursor()

    ts_conn = sqlite3.connect(ts_queue_db_path)
    ts_cursor = ts_conn.cursor()

    ts_cursor.execute("""
        CREATE TABLE IF NOT EXISTS ts_queue (
            NuID TEXT PRIMARY KEY,
            trigger_time TEXT,
            ra REAL,
            dec REAL,
            missions_list TEXT,
            queue_status TEXT
        )
    """)
    ts_conn.commit()

    current_time = Time.now()
    time_threshold = current_time - (ts_back_search * u.day)
    print(
        f"Current time: {current_time.iso}, Time threshold for new notices: {time_threshold.iso}"
    )

    gcn_cursor.execute(
        """
        SELECT * FROM notices
        WHERE trigger_time >= ?
        ORDER BY trigger_time ASC
    """,
        (time_threshold.iso,),
    )
    new_notices = gcn_cursor.fetchall()

    for notice in new_notices:
        _, _, mission, _, trigger_time, ra, dec, _, _ = notice
        print(
            f"\nProcessing notice: Mission: {mission}, Trigger time: {trigger_time}, RA: {ra}, Dec: {dec}"
        )
        # check if a notice with similar trigger time exists in the queue (within 100s)
        ts_cursor.execute(
            """
            SELECT * FROM ts_queue
            WHERE ABS(strftime('%s', trigger_time) - strftime('%s', ?)) <= 100
        """,
            (trigger_time,),
        )
        existing_entry = ts_cursor.fetchall()

        # if exists, update missions_list. If this new mission is swift then use the new ra, dec
        # if the ra, dec is updated, then the queue status is reset to pending
        if existing_entry:
            print(f"Found existing entry for notice: {existing_entry[0]}")
            NuID, _, _, _, existing_missions_list, _ = existing_entry[0]
            existing_missions = existing_missions_list.split(",")
            print(f"Existing missions list for this entry: {existing_missions}")
            if mission not in existing_missions:
                print(
                    f"Mission {mission} not in existing missions list {existing_missions}. Updating the entry."
                )
                existing_missions.append(mission)
                updated_missions_list = ",".join(existing_missions)
                if mission == "Swift-BAT" or mission == "Einstein-Probe-WXT":
                    print(
                        f"Since this is a {mission} notice, updating the RA/Dec, and queue status is pending."
                    )
                    ts_cursor.execute(
                        """
                        UPDATE ts_queue
                        SET missions_list = ?, ra = ?, dec = ?, queue_status = ?
                        WHERE NuID = ?
                    """,
                        (updated_missions_list, ra, dec, "pending", NuID),
                    )
                else:
                    ts_cursor.execute(
                        """
                        UPDATE ts_queue
                        SET missions_list = ?
                        WHERE NuID = ?
                    """,
                        (updated_missions_list, NuID),
                    )
                    print(
                        f"Updated the missions list for entry {NuID} to {updated_missions_list}."
                    )
            elif mission in existing_missions and mission == "Fermi-GBM":
                # update the ra, dec only if the mission list doesn't already contain swift or einstein probe.
                # this is to cater for the updated notices that fermi sends out
                print(
                    f"Mission {mission} is Fermi and already in the missions list. Checking if RA/Dec needs to be updated."
                )
                if (
                    "Swift-BAT" not in existing_missions
                    and "Einstein-Probe-WXT" not in existing_missions
                ):
                    print(
                        "Did not find Swift-BAT or Einstein-Probe-WXT in the existing missions list."
                    )
                    print(
                        "Updating the RA/Dec with the new Fermi values, and setting queue status to pending."
                    )
                    ts_cursor.execute(
                        """
                        UPDATE ts_queue
                        SET ra = ?, dec = ?, queue_status = ?
                        WHERE NuID = ?
                    """,
                        (ra, dec, "pending", NuID),
                    )

        # if not exists, create a new entry in the queue with status "pending"
        else:
            print(
                "No existing entry found for notice. Creating a new entry in the queue with status pending."
            )
            NuID = create_nuid_from_isot(trigger_time)
            ts_cursor.execute(
                """
                INSERT INTO ts_queue (NuID, trigger_time, ra, dec, missions_list, queue_status)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (NuID, trigger_time, ra, dec, mission, "pending"),
            )

    ts_conn.commit()
    gcn_conn.close()
    ts_conn.close()


def send_ts_products_on_slack(output_dir, NuID):
    """
    Send the triggered search products on slack.
    Args:
        output_dir (str): Directory where the triggered search products are stored.
        NuID (str): NuID of the triggered search.
    """
    # This function will send the triggered search products on slack. The implementation of this function will depend on how we want to format the message and which slack channel we want to send it to. For now, we will just print the files that we would send.

    files_to_send = glob.glob(f"{output_dir}/*pdf")
    print(f"Files to send for {NuID}: {files_to_send}")
    message = f"Triggered search products for {NuID}:\n" + "\n".join(files_to_send)
    config = load_config("nusings_config.yaml")
    send_slack_files(
        files_to_send, message, channel_id=config["slack"]["slack-ts-reports-id"]
    )
    # return all ok
    return True


def process_pending_queue_entries(config, ts_queue_db_path, ts_back_search):
    """
    Process pending entries in the triggered search queue.
    Args:
        config (dict): Configuration dictionary.
        ts_queue_db_path (str): Path to the triggered search queue database.
        ts_back_search (int): Number of days back to search for new notices.
    """
    # This function will check for pending entries in the queue, check if NuSTAR data is available, and trigger the search pipeline if data is available.
    # The implementation of this function will depend on how we check for NuSTAR data availability and how we trigger the search pipeline. For now, we will just print the pending entries.

    ts_conn = sqlite3.connect(ts_queue_db_path)
    ts_cursor = ts_conn.cursor()

    current_time = Time.now()
    time_threshold = current_time - (ts_back_search * u.day)

    ts_cursor.execute(
        """
        SELECT * FROM ts_queue
        WHERE queue_status = 'pending' AND trigger_time >= ?
        ORDER BY trigger_time ASC
    """,
        (time_threshold.iso,),
    )
    pending_entries = ts_cursor.fetchall()

    for entry in pending_entries:
        print(f"Pending entry: {entry}")
        if config["slack"]["slack-ts-reports-status"]:
            send_slack_message(
                f"Processing pending triggered search entry: {entry}",
                channel_id=config["slack"]["slack-ts-reports-id"],
            )
        NuID, trigger_time, ra, dec, missions_list, queue_status = entry
        essential_data_path = config["sings-paths"]["essential-data-path"]
        dest_dir = config["sings-paths"]["ts-products-dir"]
        trigger_year = Time(trigger_time).strftime("%Y")
        # check if the folder exists.
        year_dir = os.path.join(dest_dir, trigger_year)
        if not os.path.exists(year_dir):
            os.makedirs(year_dir)
        if ra is None or dec is None:
            print(f"RA or Dec is None for entry {NuID}.")
            command = f"python make_grb_report_callable.py {NuID} {trigger_time}\
                  --config_path {essential_data_path} --dest_path {year_dir}"
        else:
            command = f"python make_grb_report_callable.py {NuID} {trigger_time} --ra {ra} --dec {dec}\
                  --config_path {essential_data_path} --dest_path {year_dir}"
        print(f"Running command for entry {NuID}: {command}")
        subp.call(command, shell=True)

        output_dir = f"{year_dir}/{NuID}"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        log_file = f"{year_dir}/{NuID}/{NuID}_queue_log.txt"
        # append the log file every time the same NuID is processed
        with open(log_file, "a") as f:
            f.write(f"NuID: {NuID}\n")
            f.write(f"Trigger time: {trigger_time}\n")
            f.write(f"RA: {ra}\n")
            f.write(f"Dec: {dec}\n")
            f.write(f"Missions list: {missions_list}\n")
            f.write(f"Queue status: {queue_status}\n")
            f.write(f"Command run: {command}\n")
            f.write(f"Run time: {Time.now().iso}\n")
            f.write("\n\n")
        # check the number of files in the output directory for this NuID. If there are 4 or more files, we assume the search is complete and successful.
        # This is a placeholder check and could be replaced with a more robust check based on the actual output of the search pipeline.
        if os.path.exists(output_dir):
            num_files = len(os.listdir(output_dir))
            print(f"Number of files in output directory for entry {NuID}: {num_files}")
            if num_files >= 4:
                queue_status = "processed"
                print(
                    f"Search complete for entry {NuID}. Setting queue status to processed."
                )
                if config["slack"]["slack-ts-reports-status"]:
                    print(f"Sending triggered search products for entry {NuID} on slack.")
                    files_path = f"{output_dir}/*"
                    message = f"Triggered search products for {NuID}:\n"
                    send_ts_products_on_slack(files_path, message, channel_id=config["slack"]["slack-ts-reports-id"])
            else:
                queue_status = "pending"
                print(f"Search not complete for entry {NuID}. Still pending.\n\n")
        ts_cursor.execute(
            """
            UPDATE ts_queue
            SET queue_status = ?
            WHERE NuID = ?
        """,
            (queue_status, NuID),
        )
        ts_conn.commit()

    # find the entries that are still pending
    ts_cursor.execute(
        """
        SELECT * FROM ts_queue
        WHERE queue_status = 'pending'
    """,
    )
    still_pending_entries = ts_cursor.fetchall()
    # send a slack notification about the pending entries
    if still_pending_entries:
        message = "Pending triggered searches:\n"
        for entry in still_pending_entries:
            NuID, trigger_time, ra, dec, missions_list, queue_status = entry
            message += f"- NuID: {NuID}, Trigger time: {trigger_time}, RA: {ra}, Dec: {dec}, Missions: {missions_list}\n"
        send_slack_message(message, channel_id=config["slack"]["slack-ts-notices-id"])

    ts_conn.close()


if __name__ == "__main__":
    config = load_config("nusings_config.yaml")
    essential_data_path = config["sings-paths"]["essential-data-path"]
    nu_data_path = config["nustar-data-dir"]
    gcn_db_path = config["sings-paths"]["gcn-db-path"]
    ts_queue_db_path = config["sings-paths"]["ts-queue-db"]
    ts_products_dir = config["sings-paths"]["ts-products-dir"]
    ts_back_search = config["ts-config"]["ts-back-search"]  # days

    # TODO: change this in the future to something like watchdog
    interval = config["ts-config"]["interval"]  # seconds
    try:
        if config["slack"]["slack-ts-notices-status"]:
            send_slack_message(
                "Starting the NuSTAR SINGS triggered search queue script.",
                channel_id=config["slack"]["slack-ts-notices-id"],
            )
        while True:
            print(
                f"Checking for new notices and pending queue entries at {Time.now().iso}..."
            )
            merge_new_notices_to_queue(gcn_db_path, ts_queue_db_path, ts_back_search)
            print(f"Processing pending queue entries at {Time.now().iso}...")
            process_pending_queue_entries(config, ts_queue_db_path, ts_back_search)
            print(f"Sleeping for {interval} seconds...\n\n")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nExiting the script.")
        if config["slack"]["slack-ts-notices-status"]:
            send_slack_message(
                "Stopping the NuSTAR SINGS triggered search queue script.",
                channel_id=config["slack"]["slack-ts-notices-id"],
            )
    except Exception as e:
        print(f"\nError in the script: {e}")
        if config["slack"]["slack-ts-notices-status"]:
            send_slack_message(
                f"Error in the NuSTAR SINGS triggered search queue script: {e}",
                channel_id=config["slack"]["slack-ts-notices-id"],
            )
