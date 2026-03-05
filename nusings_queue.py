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


MISSION_POLICY = {
    "Swift-BAT": {"priority": 100},
    "Einstein-Probe-WXT": {"priority": 90},
    "SVOM-Eclairs": {"priority": 80},
    "SVOM-GRM": {"priority": 70},
    "CALET-GBM": {"priority": 20},
    "Fermi-GBM": {"priority": 10},
    "IceCube": {"priority": 5},
    # everything else defaults to priority 0
}


def get_mission_policy(mission):
    for key in MISSION_POLICY:
        if key in mission:
            return MISSION_POLICY[key]
    return {"priority": 0}


def should_update_position(
    new_mission,
    new_notice_time,
    owner_mission,
    owner_notice_time,
):
    new_priority = get_mission_policy(new_mission)["priority"]
    owner_priority = (
        get_mission_policy(owner_mission)["priority"]
        if owner_mission is not None
        else -1
    )

    # No owner yet
    if owner_mission is None:
        return True, "No existing position owner."

    # Higher priority always wins
    if new_priority > owner_priority:
        print(
            f"New mission {new_mission} has higher priority ({new_priority}) than current owner {owner_mission} ({owner_priority}). Updating position."
        )
        return True

    # Lower priority never wins
    if new_priority < owner_priority:
        print(
            f"New mission {new_mission} has lower priority ({new_priority}) than current owner {owner_mission} ({owner_priority}). Not updating position."
        )
        return False

    # Same priority: only newer notice can update
    print(
        f"New mission {new_mission} has same priority ({new_priority}) as current owner {owner_mission} ({owner_priority}). Comparing notice times."
    )
    return Time(new_notice_time, format="isot") > Time(owner_notice_time, format="isot")


def merge_new_notices_to_queue(gcn_db_path, ts_queue_db_path, ts_back_search):

    gcn_conn = sqlite3.connect(gcn_db_path)
    gcn_cursor = gcn_conn.cursor()

    ts_conn = sqlite3.connect(ts_queue_db_path)
    ts_cursor = ts_conn.cursor()

    # --- Ensure schema ---
    ts_cursor.execute("""
        CREATE TABLE IF NOT EXISTS ts_queue (
            NuID TEXT PRIMARY KEY,
            trigger_time TEXT,
            ra REAL,
            dec REAL,
            missions_list TEXT,
            queue_status TEXT,
            position_owner TEXT,
            position_owner_notice_time TEXT
        )
    """)
    ts_conn.commit()

    current_time = Time.now()
    time_threshold = current_time - (ts_back_search * u.day)

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
        _, _, new_mission, _, trigger_time, new_ra, new_dec, _, new_notice_time = notice

        ts_cursor.execute(
            """
            SELECT * FROM ts_queue
            WHERE ABS(strftime('%s', trigger_time) - strftime('%s', ?)) <= 100
            """,
            (trigger_time,),
        )
        rows = ts_cursor.fetchall()

        if rows:
            (
                NuID,
                _,
                ra,
                dec,
                missions_list,
                queue_status,
                owner_mission,
                owner_notice_time,
            ) = rows[0]

            missions = missions_list.split(",") if missions_list else []
            if new_mission not in missions:
                missions.append(new_mission)

            update_position = should_update_position(
                new_mission,
                new_notice_time,
                owner_mission,
                owner_notice_time,
            )

            if update_position and new_ra is not None and new_dec is not None:
                ts_cursor.execute(
                    """
                    UPDATE ts_queue
                    SET ra = ?, dec = ?, queue_status = 'pending',
                        missions_list = ?, position_owner = ?,
                        position_owner_notice_time = ?
                    WHERE NuID = ?
                    """,
                    (
                        new_ra,
                        new_dec,
                        ",".join(sorted(missions)),
                        new_mission,
                        new_notice_time,
                        NuID,
                    ),
                )
            else:
                ts_cursor.execute(
                    """
                    UPDATE ts_queue
                    SET missions_list = ?
                    WHERE NuID = ?
                    """,
                    (",".join(sorted(missions)), NuID),
                )

        else:
            NuID = create_nuid_from_isot(trigger_time)

            ts_cursor.execute(
                """
                INSERT INTO ts_queue (
                    NuID, trigger_time, ra, dec,
                    missions_list, queue_status,
                    position_owner, position_owner_notice_time
                )
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    NuID,
                    trigger_time,
                    new_ra,
                    new_dec,
                    new_mission,
                    new_mission if new_ra is not None else None,
                    new_notice_time if new_ra is not None else None,
                ),
            )

    ts_conn.commit()
    gcn_conn.close()
    ts_conn.close()


def send_ts_products_on_slack(output_dir, NuID, channel_id):
    """
    Send the triggered search products on slack.
    Args:
        output_dir (str): Directory where the triggered search products are stored.
        NuID (str): NuID of the triggered search.
    """
    files_to_send = glob.glob(f"{output_dir}/*png")
    print(f"Files to send for {NuID}: {files_to_send}")
    message = f"Triggered search products for {NuID}:\n"
    send_slack_files(files_to_send, message, channel_id)
    time.sleep(5)
    log_file = glob.glob(f"{output_dir}/*log")
    send_slack_files(log_file, f"Triggered search details for {NuID}:", channel_id)
    time.sleep(5)
    # added the sleep to avoid hitting rate limits and to ensure the files are sent in order.
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
        (
            NuID,
            trigger_time,
            ra,
            dec,
            missions_list,
            queue_status,
            pos_owner,
            pos_owner_notice_time,
        ) = entry
        essential_data_path = config["sings-paths"]["essential-data-path"]
        dest_dir = config["sings-paths"]["ts-products-dir"]
        trigger_year = Time(trigger_time).strftime("%Y")
        ts_cursor.execute(
            "UPDATE ts_queue SET queue_status = 'processing' WHERE NuID = ?",
            (NuID,),
        )
        ts_conn.commit()

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
        with open(log_file, "a") as f:
            f.write(f"NuID: {NuID}\n")
            f.write(f"Trigger time: {trigger_time}\n")
            f.write(f"RA: {ra}\n")
            f.write(f"Dec: {dec}\n")
            f.write(f"Missions list: {missions_list}\n")
            f.write(f"Queue status: {queue_status}\n")
            f.write(f"Position owner: {pos_owner}\n")
            f.write(f"Position owner notice time: {pos_owner_notice_time}\n")
            f.write(f"Command run: {command}\n")
            f.write(f"Run time: {Time.now().iso}\n")
            f.write("\n\n")
        # check the number of files in the output directory for this NuID. If there are 4 or more files, we assume the search is complete and successful.
        # This is a placeholder check and could be replaced with a more robust check based on the actual output of the search pipeline.
        if os.path.exists(output_dir):
            num_files = len(os.listdir(output_dir))
            print(f"Number of files in output directory for entry {NuID}: {num_files}")
            if num_files >= 4:
                print(f"Changing the status of entry {NuID} to processed.\n\n")
                queue_status = "processed"
                if config["slack"]["slack-ts-reports-status"]:
                    send_slack_message(
                        f"Processing pending triggered search entry: {entry}",
                        channel_id=config["slack"]["slack-ts-reports-id"],
                    )
                    print(
                        f"Sending triggered search products for entry {NuID} on slack."
                    )
                    files_path = f"{output_dir}/"
                    send_ts_products_on_slack(
                        files_path,
                        NuID,
                        channel_id=config["slack"]["slack-ts-reports-id"],
                    )
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
                f"\n\nChecking for new notices and pending queue entries at {Time.now().iso}..."
            )
            merge_new_notices_to_queue(gcn_db_path, ts_queue_db_path, ts_back_search)
            print(f"\n\nProcessing pending queue entries at {Time.now().iso}...")
            process_pending_queue_entries(config, ts_queue_db_path, ts_back_search)
            print(f"Sleeping for {interval} seconds...\n\n")
            time_now = Time.now()
            hour_now = int(time_now.strftime("%H"))
            minute_now = int(time_now.strftime("%M"))
            # send a slack message every day whenever the time is between 12:00 and 12:30 UTC about pending and processed entries in the last ts_back_search days.
            if hour_now == 12 and minute_now < 30:
                ts_cursor = sqlite3.connect(ts_queue_db_path).cursor()
                time_threshold = time_now - (ts_back_search * u.day)
                # find the entries that are still pending and are within interval day old
                ts_cursor.execute(
                    """
                    SELECT * FROM ts_queue
                    WHERE queue_status = 'pending'
                    AND trigger_time >= ?
                    ORDER BY trigger_time ASC
                """,
                    (time_threshold.iso,),
                )
                still_pending_entries = ts_cursor.fetchall()
                # send a slack notification about the pending entries
                if still_pending_entries:
                    message = f"Pending triggered searches in the last {ts_back_search} days:\n"
                    for entry in still_pending_entries:
                        (
                            NuID,
                            trigger_time,
                            ra,
                            dec,
                            missions_list,
                            queue_status,
                            pos_owner,
                            pos_owner_notice_time,
                        ) = entry
                        message += f"- NuID: {NuID}, Trigger time: {trigger_time}, RA: {ra}, Dec: {dec}, Missions: {missions_list}, Position owner: {pos_owner}, Position owner notice time: {pos_owner_notice_time}\n"
                    if config["slack"]["slack-ts-notices-status"]:
                        send_slack_message(
                            message, channel_id=config["slack"]["slack-ts-notices-id"]
                        )
                # repeat the same for all the processed entries in the last ts_back_search days
                ts_cursor.execute(
                    """
                    SELECT * FROM ts_queue
                    WHERE queue_status = 'processed'
                    AND trigger_time >= ?
                    ORDER BY trigger_time ASC
                """,
                    (time_threshold.iso,),
                )
                processed_entries = ts_cursor.fetchall()
                if processed_entries:
                    message = f"Processed triggered searches in the last {ts_back_search} days:\n"
                    for entry in processed_entries:
                        (
                            NuID,
                            trigger_time,
                            ra,
                            dec,
                            missions_list,
                            queue_status,
                            pos_owner,
                            pos_owner_notice_time,
                        ) = entry
                        message += f"- NuID: {NuID}, Trigger time: {trigger_time}, RA: {ra}, Dec: {dec}, Missions: {missions_list}, Position owner: {pos_owner}, Position owner notice time: {pos_owner_notice_time}\n"
                    if config["slack"]["slack-ts-notices-status"]:
                        send_slack_message(
                            message, channel_id=config["slack"]["slack-ts-notices-id"]
                        )
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
