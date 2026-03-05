"""
Stream GCN Notices. Save, Parse and Store them in a SQL database.
Author: Gaurav Waratkar
Note:
- Make sure to set the GCN_CLIENT_ID and GCN_CLIENT_SECRET environment variables before running.
- This script listens to various GCN notice topics, saves the notices to files, parses them based on their mission, and stores relevant information in a SQLite database.
- Make sure you add new mission to the mission_parsers dictionary AND implement the corresponding parsing function.
- The script currently supports VOEvent and JSON formatted notices.
- The script stores ALL incoming notices (that are signed up) without filtering for no loss of information.
- The triggered queue should implement any filtering based on user requirements in a different script.
"""

from gcn_kafka import Consumer
import os
import json
from bs4 import BeautifulSoup
import sqlite3
from astropy.time import Time
import yaml
from nusings_config import load_config
from message_slack import send_slack_message


def add_notice_to_db_save(
    config,
    notice_content,
    topic,
    mission,
    vo,
    trigger_ID,
    trigger_time,
    ra=None,
    dec=None,
    error_radius=None,
    notice_time=None,
):
    db_name = config["sings-paths"]["gcn-db-path"]
    folder_base = config["sings-paths"]["notice-archive-dir"]
    year_now = Time(trigger_time).strftime("%Y")
    notices_db = sqlite3.connect(db_name)
    cursor = notices_db.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gcn_topic TEXT,
            mission TEXT,
            trigger_id TEXT,
            trigger_time TEXT,
            ra REAL,
            dec REAL,
            error_radius REAL,
            notice_time TEXT
        )
    """)
    # check if the notice exists already in the DB (by checking against the topic name and notice time)
    # add only if the notice doesn't exist to avoid duplicates (reruns of the script)
    if notice_time is not None:
        cursor.execute(
            """
            SELECT id FROM notices WHERE gcn_topic = ? AND notice_time = ?
        """,
            (topic, notice_time),
        )
        existing_notice = cursor.fetchone()
        if existing_notice:
            print(
                f"Notice for {mission} with Trigger ID {trigger_ID} already exists in database {db_name}. "
                "Skipping insertion."
            )
            notices_db.close()
            return "Exists"
    cursor.execute(
        """
        INSERT INTO notices (gcn_topic, mission, trigger_id, trigger_time, ra, dec, error_radius, notice_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (topic, mission, trigger_ID, trigger_time, ra, dec, error_radius, notice_time),
    )
    notices_db.commit()
    notices_db.close()
    print(
        f"Notice for {mission} with Trigger ID {trigger_ID} added to database {db_name}."
    )
    mission_parsers = config["mission_parsers"]
    mission_folder = find_mission_from_topic(topic, mission_parsers)
    if vo == "xml":
        if not os.path.exists(f"{folder_base}/{mission_folder}/{year_now}"):
            os.makedirs(f"{folder_base}/{mission_folder}/{year_now}")
        with open(
            f"{folder_base}/{mission_folder}/{year_now}/{topic.replace('.', '_')}_{trigger_ID}.xml",
            "w",
        ) as f:
            f.write(notice_content)
            print(f"VOEvent Notice stored at: {f.name}")
    elif vo == "json":
        if not os.path.exists(f"{folder_base}/{mission_folder}/{year_now}"):
            os.makedirs(f"{folder_base}/{mission_folder}/{year_now}")
        with open(
            f"{folder_base}/{mission_folder}/{year_now}/{topic.replace('.', '_')}_{trigger_ID}.json",
            "w",
        ) as f:
            json.dump(notice_content, f, indent=2)
            print(f"JSON Notice stored at: {f.name}")
    # send a slack message about the new notice
    if config["slack"]["slack-ts-notices-status"]:
        send_slack_message(
            f"New GCN Notice: {topic} with ID {trigger_ID} at {trigger_time}",
            channel_id=config["slack"]["slack-ts-notices-id"],
        )
    return "New"


def parse_calet(notice_content, topic, config):
    print("Parsing CALET notice...")
    mission = "CALET-GBM"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )

    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_fermi(notice_content, topic, config):
    print("Parsing Fermi notice...")
    mission = "Fermi-GBM"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_icecube(notice_content, topic, config):
    print("Parsing IceCube notice...")
    mission = "IceCube"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "event_id"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID, trigger_time, notice_time


# def parse_lvc(notice, topic, db_name):
#     print("Parsing LVC notice...")


def parse_swift(notice_content, topic, config):
    print("Parsing Swift notice...")
    mission = "Swift-BAT"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_svom_grm(notice_content, topic, config):
    print("Parsing SVOM-GRM notice...")
    mission = "SVOM-GRM"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    trigger_ID = what.find("Param", {"name": "Burst_Id"}).get("value")
    notice_time = p.find("Who").find("Date").text.split("+")[0]
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_svom_eclairs(notice_content, topic, config):
    print("Parsing SVOM-ECLAIRs notice...")
    mission = "SVOM-ECLAIRs"
    p = BeautifulSoup(notice_content, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    trigger_ID = what.find("Param", {"name": "Burst_Id"}).get("value")
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    notice_time = p.find("Who").find("Date").text.split("+")[0]
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_ipn(notice_content, topic, config):
    print("Parsing IPN notice...")
    mission = "IPN"
    p = BeautifulSoup(notice_content, features="xml")
    what = p.find("What")
    misc_group = what.find("Group", {"name": "Misc"})
    for param in misc_group.find_all("Param"):
        if param.get("value").lower() == "true":
            true_contributor = param.get("name").replace("_contributed", "")
            break
    mission = "IPN-" + true_contributor
    burst_tjd = what.find("Param", {"name": "Burst_TJD"}).get("value")
    burst_sod = what.find("Param", {"name": "Burst_SOD"}).get("value").split(".")[0]
    trigger_id = f"{burst_tjd}-{burst_sod}"
    wherewhen = p.find("WhereWhen")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    notice_time = p.find("Who").find("Date").text
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_id}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "xml",
        trigger_id,
        trigger_time,
        notice_time=notice_time,
    )
    return trigger_id, trigger_time, notice_time


def parse_einstein_probe(notice_content, topic, config):
    print("Parsing Einstein Probe notice...")
    mission = "Einstein-Probe-WXT"
    trigger_time = str(Time(notice_content.get("trigger_time")).isot)
    trigger_ID = notice_content.get("id")[0]
    ra = notice_content.get("ra")
    dec = notice_content.get("dec")
    error_radius = notice_content.get("ra_dec_error")
    notice_time = str(Time.now().isot)
    # No notice time in the JSON, so using trigger time(to avoid ignoring these notices in the
    # current setup where we check for duplicates based on topic and notice time)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    # check in the db if the notice already exists based on the trigger ID and save only if it doesn't exist.
    db_name = config["sings-paths"]["gcn-db-path"]
    notices_db = sqlite3.connect(db_name)
    cursor = notices_db.cursor()
    cursor.execute(
        """
        SELECT id FROM notices WHERE mission = ? AND trigger_id = ?
    """,
        (mission, trigger_ID),
    )
    existing_notice = cursor.fetchone()
    if existing_notice:
        print(
            f"Notice for {mission} with Trigger ID {trigger_ID} already exists in database {db_name}. "
            "Skipping insertion."
        )
        notices_db.close()
        return trigger_ID, trigger_time, notice_time
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "json",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_igwn(notice_content, topic, config):
    print("Parsing IGWN notice...")
    mission = "IGWN"
    alert_type = notice_content.get("alert_type")
    topic = f"igwn.gwalert.{alert_type}"
    superevent_id = notice_content.get("superevent_id")
    if "MS" in superevent_id:
        print("Ignoring Mock Superevent notice.")
        return "Ignore"
    if alert_type == "RETRACTION":
        print("Ignoring RETRACTION notice.")  # Let's IGNORE ALL RETRACTIONS for now
        return "Ignore"
    trigger_time = str(Time(notice_content.get("event").get("time")).isot)
    notice_time = notice_content.get("time_created")
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Superevent ID: {superevent_id}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    # not saving to DB or saving the file for now
    # we can change this during IR run
    # save skymap in the future if needed (not needed for SINGS)
    return f"{superevent_id}_{alert_type}"


def parse_guano(notice_content, topic, config):
    print("Parsing Guano notice...")
    mission = "Swift-BAT"
    trigger_ID = notice_content.get("id")[0]
    trigger_time = str(Time(notice_content.get("trigger_time")).isot)
    notice_time = str(Time(notice_content.get("alert_datetime")).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "json",
        trigger_ID,
        trigger_time,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_chime(notice_content, topic, config):
    # Based on https://gcn.nasa.gov/missions/chime
    print("Parsing CHIME/FRB notice...")
    mission = "CHIME"
    trigger_ID = notice_content.get("id")
    trigger_time = str(Time(notice_content.get("trigger_time")).isot)
    ra = notice_content.get("ra")
    dec = notice_content.get("dec")
    error_radius = max(notice_content.get("ra_dec_error"))
    notice_time = str(
        Time.now().isot
    )  # No notice time in the JSON, so using current tim
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    db_name = config["sings-paths"]["gcn-db-path"]
    notices_db = sqlite3.connect(db_name)
    cursor = notices_db.cursor()
    cursor.execute(
        """
        SELECT id FROM notices WHERE mission = ? AND trigger_id = ?
    """,
        (mission, trigger_ID),
    )
    existing_notice = cursor.fetchone()
    if existing_notice:
        print(
            f"Notice for {mission} with Trigger ID {trigger_ID} already exists in database {db_name}. "
            "Skipping insertion."
        )
        notices_db.close()
        return trigger_ID, trigger_time, notice_time
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "json",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_dsa110(notice_content, topic, config):
    # Based on https://gcn.nasa.gov/missions/dsa110
    print("Parsing DSA110 notice...")
    mission = "DSA110"
    trigger_ID = notice_content.get("id")
    trigger_time = str(Time(notice_content.get("trigger_time")).isot)
    ra = notice_content.get("ra")
    dec = notice_content.get("dec")
    error_radius = max(notice_content.get("ra_dec_error"))
    notice_time = str(
        Time.now().isot
    )  # No notice time in the JSON, so using current time
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, "
        f"Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    db_name = config["sings-paths"]["gcn-db-path"]
    notices_db = sqlite3.connect(db_name)
    cursor = notices_db.cursor()
    cursor.execute(
        """
        SELECT id FROM notices WHERE mission = ? AND trigger_id = ?
    """,
        (mission, trigger_ID),
    )
    existing_notice = cursor.fetchone()
    if existing_notice:
        print(
            f"Notice for {mission} with Trigger ID {trigger_ID} already exists in database {db_name}. "
            "Skipping insertion."
        )
        notices_db.close()
        return trigger_ID, trigger_time, notice_time
    add_notice_to_db_save(
        config,
        notice_content,
        topic,
        mission,
        "json",
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID, trigger_time, notice_time


def parse_circulars(notice_content, topic, config):
    print("Parsing Circulars notice...")
    mission = "GCN-Circulars"
    circ_id = notice_content.get("circularId")
    subject = notice_content.get("subject")
    # body = notice_content.get("body")
    print(f"Mission: {mission}, Circular ID: {circ_id}, Subject: {subject}")
    # Not adding the circulars to the DB for now.
    # Send slack message only instead later.
    circ_id = f"{circ_id}: {subject}"
    return circ_id, Time.now().isot, "NA"


def find_mission_from_topic(topic, mission_parsers):
    """
    Identify mission name from the topic string.
    Returns the mission name or None if not found.
    """
    for mission in mission_parsers.keys():
        if mission.lower() in topic.lower():
            return mission
    return None


def parse_notice(topic, notice_content, mission_parsers, config):
    """
    Parse the incoming notice based on topic.
    Automatically dispatches to the correct mission parser.
    """
    mission = find_mission_from_topic(topic, mission_parsers)
    if not mission:
        raise ValueError(f"Could not identify mission from topic: {topic}")

    parser = mission_parsers[mission]
    print(f"Dispatching to parser for mission: {mission}")
    result, trigger_time, notice_time = parser(notice_content, topic, config)
    if result == "Ignore":
        print("Notice ignored based on parser decision.")
        return "Ignore"
    return result, mission, trigger_time, notice_time


def process_notice(notice_message, mission_parsers, config):
    """
    Process the GCN notice.
    Args:
        notice (str): The GCN notice message.
        mission_parsers (dict): A dictionary mapping mission names to their parsing functions.
        config (dict): Configuration dictionary containing database paths and other settings.
    """
    channel_name = notice_message.topic()
    print(f"Processing notice from channel: {channel_name}")
    try:
        if channel_name in vo_topics:
            print("VOEvent notice detected.")
            value_str = notice_message.value().decode("utf-8")
            ret, mission, trigger_time, notice_time = parse_notice(
                channel_name, value_str, mission_parsers, config
            )
            if ret == "Ignore" or notice_time == "NA":
                print(
                    "We got a circular or VOEvent Notice ignored based on parser decision."
                )
                return

        elif channel_name in json_topics:
            print("JSON notice detected.")
            value_str = notice_message.value().decode("utf-8")
            alert_json = json.loads(value_str)
            ret, mission, trigger_time, notice_time = parse_notice(
                channel_name, alert_json, mission_parsers, config
            )
            if ret == "Ignore" or notice_time == "NA":
                print(
                    "We got a circular or JSON Notice ignored based on parser decision."
                )
                return

        else:
            print("Unknown notice format.\n")
        print(f"Notice from channel {channel_name} processed.\n")
    except Exception as e:
        print(f"Error processing notice from channel {channel_name}: {e}\n")


if __name__ == "__main__":
    conf = load_config("nusings_config.yaml")
    notices_db_path = conf["sings-paths"]["gcn-db-path"]
    notices_dir_path = conf["sings-paths"]["notice-archive-dir"]
    notices_slack_channel_name = conf["slack"]["slack-ts-notices"]
    notices_slack_channel_id = conf["slack"]["slack-ts-notices-id"]
    notices_slack_status = conf["slack"]["slack-ts-notices-status"]
    slack = [notices_slack_channel_name, notices_slack_channel_id, notices_slack_status]

    print("Starting GCN Notice Streamer...")
    if notices_slack_status:
        send_slack_message(
            "GCN Notice Streamer has started.", channel_id=notices_slack_channel_id
        )

    # Warning: don't share the client secret with others.
    client_id = os.getenv("GCN_CLIENT_ID", "fill me in")
    client_secret = os.getenv("GCN_CLIENT_SECRET", "fill me in")
    if client_id == "fill me in" or client_secret == "fill me in":
        raise ValueError(
            "Please set GCN_CLIENT_ID and GCN_CLIENT_SECRET environment variables."
        )
        raise SystemExit(1)

    config = {
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "group.id": "NuSTAR SINGS",
    }
    consumer = Consumer(
        client_id=client_id,
        client_secret=client_secret,
        domain="gcn.nasa.gov",
        config=config,
    )

    mission_parsers = {
        "calet": parse_calet,
        "fermi": parse_fermi,
        "icecube": parse_icecube,
        # "lvc": parse_lvc, # we have IGWN-json now
        "swift": parse_swift,
        "grm": parse_svom_grm,
        "eclairs": parse_svom_eclairs,
        "einstein_probe": parse_einstein_probe,
        "circulars": parse_circulars,
        "ipn": parse_ipn,
        "igwn": parse_igwn,
        "guano": parse_guano,
        "chime": parse_chime,
        "dsa110": parse_dsa110,
    }
    # KEEP THESE TOPICS SEPARATE BASED ON FORMAT
    vo_topics = [
        "gcn.classic.voevent.CALET_GBM_FLT_LC",
        "gcn.classic.voevent.FERMI_GBM_ALERT",
        "gcn.classic.voevent.FERMI_GBM_FIN_POS",
        "gcn.classic.voevent.FERMI_GBM_FLT_POS",
        "gcn.classic.voevent.FERMI_GBM_GND_POS",
        "gcn.classic.voevent.FERMI_GBM_SUBTHRESH",
        "gcn.classic.voevent.ICECUBE_ASTROTRACK_GOLD",
        "gcn.classic.voevent.IPN_RAW",
        # "gcn.classic.voevent.LVC_INITIAL",
        # "gcn.classic.voevent.LVC_PRELIMINARY",
        "gcn.classic.voevent.SWIFT_BAT_GRB_POS_ACK",
        "gcn.notices.svom.voevent.grm",
        "gcn.notices.svom.voevent.eclairs",
    ]
    json_topics = [
        # "gcn.heartbeat",
        # "gcn.circulars",
        # "igwn.gwalert",
        # "gcn.notices.swift.bat.guano",
        "gcn.notices.einstein_probe.wxt.alert",
        # "gcn.notices.chime.frb.alert",
        # "gcn.notices.dsa110.frb",
    ]

    topics = vo_topics + json_topics
    consumer.subscribe(topics)
    print("Listening to the following topics:")
    for topic in topics:
        print(f" - {topic}")
    print("Press Ctrl+C to exit.\n")
    conf["mission_parsers"] = mission_parsers
    try:
        while True:
            for message in consumer.consume(timeout=1):
                if message.error():
                    print(message.error())
                    continue
                print(f"topic={message.topic()}, offset={message.offset()}")
                process_notice(message, mission_parsers, conf)
    except KeyboardInterrupt:
        print("Exiting...")
        # send the shutdown message to slack
        if notices_slack_status:
            send_slack_message(
                "GCN Notice Streamer has stopped.", channel_id=notices_slack_channel_id
            )
    finally:
        consumer.close()
