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


def add_notice_to_db(
    db_name,
    topic,
    mission,
    trigger_ID,
    trigger_time,
    ra=None,
    dec=None,
    error_radius=None,
    notice_time=None,
):
    """
    Add notice details to the SQLite database.
    """
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


def parse_calet(notice, topic, db_name):
    print("Parsing CALET notice...")
    mission = "CALET-GBM"
    p = BeautifulSoup(notice, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID


def parse_fermi(notice, topic, db_name):
    print("Parsing Fermi notice...")
    mission = "Fermi-GBM"
    p = BeautifulSoup(notice, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID


def parse_icecube(notice, topic, db_name):
    print("Parsing IceCube notice...")
    mission = "IceCube"
    p = BeautifulSoup(notice, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "event_id"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID


# def parse_lvc(notice, topic, db_name):
#     print("Parsing LVC notice...")


def parse_swift(notice, topic, db_name):
    print("Parsing Swift notice...")
    mission = "Swift-BAT"
    p = BeautifulSoup(notice, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    ra = wherewhen.find("C1").text
    dec = wherewhen.find("C2").text
    error_radius = wherewhen.find("Error2Radius").text
    trigger_ID = what.find("Param", {"name": "TrigID"}).get("value")
    notice_time = str(Time(p.find("Who").find("Date").text).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time,
    )
    return trigger_ID


def parse_svom_grm(notice, topic, db_name):
    print("Parsing SVOM-GRM notice...")
    mission = "SVOM-GRM"
    p = BeautifulSoup(notice, features="xml")
    wherewhen = p.find("WhereWhen")
    what = p.find("What")
    trigger_time = str(Time(wherewhen.find("ISOTime").text).isot)
    trigger_ID = what.find("Param", {"name": "Burst_Id"}).get("value")
    notice_time = p.find("Who").find("Date").text.split("+")[0]
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name, topic, mission, trigger_ID, trigger_time, notice_time=notice_time
    )
    return trigger_ID


def parse_svom_eclairs(notice, topic, db_name):
    print("Parsing SVOM-ECLAIRs notice...")
    mission = "SVOM-ECLAIRs"
    p = BeautifulSoup(notice, features="xml")
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
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID


def parse_ipn(notice, topic, db_name):
    print("Parsing IPN notice...")
    mission = "IPN"
    p = BeautifulSoup(notice, features="xml")
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
    add_notice_to_db(
        db_name, topic, mission, trigger_id, trigger_time, notice_time=notice_time
    )
    return trigger_id


def parse_einstein_probe(notice, topic, db_name):
    print("Parsing Einstein Probe notice...")
    mission = "Einstein-Probe-WXT"
    trigger_time = str(Time(notice.get("trigger_time")).isot)
    trigger_ID = notice.get("id")[0]
    ra = notice.get("ra")
    dec = notice.get("dec")
    error_radius = notice.get("ra_dec_error")
    notice_time = str(
        Time.now().isot
    )  # No notice time in the JSON, so using current time
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, RA: {ra}, Dec: {dec}, Error Radius: {error_radius}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name,
        topic,
        mission,
        trigger_ID,
        trigger_time,
        ra,
        dec,
        error_radius,
        notice_time=notice_time,
    )
    return trigger_ID


def parse_igwn(notice, topic, db_name):
    print("Parsing IGWN notice...")
    mission = "IGWN"
    alert_type = notice.get("alert_type")
    topic = f"igwn.gwalert.{alert_type}"
    superevent_id = notice.get("superevent_id")
    if "MS" in superevent_id:
        print("Ignoring Mock Superevent notice.")
        return "Ignore"
    if alert_type == "RETRACTION":
        print("Ignoring RETRACTION notice.")  # Let's IGNORE ALL RETRACTIONS for now
        return "Ignore"
    trigger_time = str(Time(notice.get("event").get("time")).isot)
    notice_time = notice.get("time_created")
    notice_time = str(Time(notice_time).isot)
    print(
        f"Mission: {mission}, Superevent ID: {superevent_id}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name, topic, mission, superevent_id, trigger_time, notice_time=notice_time
    )
    # save skymap in the future if needed (not needed for SINGS)
    return f"{superevent_id}_{alert_type}"


def parse_guano(notice, topic, db_name):
    print("Parsing Guano notice...")
    mission = "Swift-BAT"
    trigger_ID = notice.get("id")[0]
    trigger_time = str(Time(notice.get("trigger_time")).isot)
    notice_time = str(Time(notice.get("alert_datetime")).isot)
    print(
        f"Mission: {mission}, Trigger ID: {trigger_ID}, Time: {trigger_time}, Notice Time: {notice_time}"
    )
    add_notice_to_db(
        db_name, topic, mission, trigger_ID, trigger_time, notice_time=notice_time
    )
    return trigger_ID


def parse_circulars(notice, topic, db_name):
    print("Parsing Circulars notice...")
    mission = "GCN-Circulars"
    circ_id = notice.get("circularId")
    subject = notice.get("subject")
    # body = notice.get("body")
    print(f"Mission: {mission}, Circular ID: {circ_id}, Subject: {subject}")
    # Not adding the circulars to the DB for now.
    # Send slack message only instead later.
    return circ_id


def find_mission_from_topic(topic, mission_parsers):
    """
    Identify mission name from the topic string.
    Returns the mission name or None if not found.
    """
    for mission in mission_parsers.keys():
        if mission.lower() in topic.lower():
            return mission
    return None


def parse_notice(topic, notice_value, mission_parsers, db_name="gcn_notices_sings.db"):
    """
    Parse the incoming notice based on topic.
    Automatically dispatches to the correct mission parser.
    """
    mission = find_mission_from_topic(topic, mission_parsers)
    if not mission:
        raise ValueError(f"Could not identify mission from topic: {topic}")

    parser = mission_parsers[mission]
    print(f"Dispatching to parser for mission: {mission}")
    print("Using parser:", parser.__name__)
    result = parser(notice_value, topic, db_name)
    if result == "Ignore":
        print("Notice ignored based on parser decision.")
        return "Ignore"
    return result


def process_notice(notice_message, mission_parsers, db_name="gcn_notices_sings.db"):
    """
    Process the GCN notice and store it in a SQL database.
    Args:
        notice (str): The GCN notice message.
    """
    print("Processing notice...")
    channel_name = notice_message.topic()
    print(f"Storing notice from channel: {channel_name}")
    folder_base = "data/notices/"
    try:
        if channel_name in vo_topics:
            print("VOEvent notice detected.")
            value_str = notice_message.value().decode("utf-8")
            ret = parse_notice(
                channel_name, value_str, mission_parsers, db_name=db_name
            )
            # ret is used for trigger_ID in naming the file
            if ret == "Ignore":
                print("VOEvent Notice ignored based on parser decision.")
                return
            with open(
                os.path.join(
                    folder_base,
                    f"{channel_name.replace('.', '_')}_{ret}.xml",
                ),
                "w",
            ) as f:
                f.write(value_str)
            print(f"VOEvent Notice stored at: {f.name}")
            if ret == "Done":
                print("VOEvent Notice parsed successfully.")

        elif channel_name in json_topics:
            print("JSON notice detected.")
            value_str = notice_message.value().decode("utf-8")
            alert_json = json.loads(value_str)
            ret = parse_notice(
                channel_name, alert_json, mission_parsers, db_name=db_name
            )
            # ret is used for trigger_ID in naming the file
            if ret == "Ignore":
                print("JSON Notice ignored based on parser decision.")
                return
            with open(
                os.path.join(
                    folder_base,
                    f"{channel_name.replace('.', '_')}_{ret}.json",
                ),
                "w",
            ) as f:
                json.dump(alert_json, f, indent=2)
            print(f"JSON Notice stored at: {f.name}")
            if ret == "Done":
                print("JSON Notice parsed successfully.")

        else:
            print("Unknown notice format.")
        print(f"Notice from channel {channel_name} processed and stored.\n")
    except Exception as e:
        print(f"Error processing notice from channel {channel_name}: {e}\n")


if __name__ == "__main__":
    print("Starting GCN Notice Streamer...")
    # Connect as a consumer
    # Warning: don't share the client secret with others.
    notices_db_path = "gcn_notices_sings.db"
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

    # List all topics
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
        "gcn.circulars",
        "igwn.gwalert",
        "gcn.notices.swift.bat.guano",
        "gcn.notices.einstein_probe.wxt.alert",
    ]
    topics = vo_topics + json_topics
    consumer.subscribe(topics)
    print("Listening to the following topics:")
    for topic in topics:
        print(f" - {topic}")
    print("Press Ctrl+C to exit.\n")

    while True:
        for message in consumer.consume(timeout=1):
            if message.error():
                print(message.error())
                continue
            # Print the topic and message ID
            print(f"topic={message.topic()}, offset={message.offset()}")
            # Here you can add code to parse the message and store it in a SQL database
            process_notice(message, mission_parsers, notices_db_path)
