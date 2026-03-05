"""
Module for sending messages to Slack.
Authors: Gaurav Waratkar

Note:
- Uses the Slack Web API to send messages to a specified channel.
- The Slack API token and channel ID are stored in the bashrc file.
"""

import os
import slack_sdk as slack
from nusings_config import load_config
import time


def send_slack_message(message, channel_id=None):
    """
    Send a message to the specified Slack channel.
    Args:
        message (str): The message to send.
        channel_id (str, optional): The Slack channel ID.
    """
    slack_token = os.getenv("NUSTAR_SLACK_TOKEN")

    client = slack.WebClient(token=slack_token)
    try:
        response = client.chat_postMessage(channel=channel_id, text=message)
        print(f"Message sent to Slack channel {channel_id}")
        time.sleep(1)  # Sleep for 1 second to avoid hitting rate limits
    except Exception as e:
        print(f"Error sending message to Slack: {e}")
        raise e


def send_slack_files(files_path, message, channel_id=None):
    """
    Send a file to the specified Slack channel with an accompanying message.
    Args:
        files_path (list): A list of paths to the files to send. Can send one file too.
        message (str): The message to accompany the file.
        channel_id (str, optional): The Slack channel ID.
    """
    slack_token = os.getenv("NUSTAR_SLACK_TOKEN")

    client = slack.WebClient(token=slack_token)
    try:
        if len(files_path) == 1:
            response = client.files_upload_v2(
                channel=channel_id, file=files_path[0], initial_comment=message
            )
            print(f"File {files_path[0]} sent to Slack channel {channel_id}")
        else:
            files_to_upload = []
            for file_path in files_path:
                files_to_upload.append(
                    {
                        "file": file_path,
                        "title": os.path.basename(file_path),
                    }
                )
            response = client.files_upload_v2(
                channel=channel_id, file_uploads=files_to_upload, initial_comment=message
            )
            print(f"Files {files_path} sent to Slack channel {channel_id}")
    except Exception as e:
        print(f"Error sending file to Slack: {e}")
        raise e


if __name__ == "__main__":
    # Example usage
    config = load_config("nusings_config.yaml")
    sings_ts_notices_channel = config["slack"]["slack-ts-notices"]
    sings_ts_notices_channel_id = config["slack"]["slack-ts-notices-id"]
    sings_ts_notices_channel_status = config["slack"]["slack-ts-notices-status"]
    print(sings_ts_notices_channel_id, sings_ts_notices_channel_status)
    if sings_ts_notices_channel_status:
        send_slack_message(
            "This is a test message from the NuSTAR SINGS pipeline.",
            channel_id=sings_ts_notices_channel_id,
        )
