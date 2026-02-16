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
    except Exception as e:
        print(f"Error sending message to Slack: {e}")
        raise e


if __name__ == "__main__":
    # Example usage
    config = load_config("nusings_config.yaml")
    sings_ts_notices_channel = config["slack"]["slack-ts-notices"]
    sings_ts_notices_channel_id = config["slack"]["slack-ts-notices-id"]
    sings_ts_notices_channel_status = config["slack"]["slack-ts-notices-status"]
    print(sings_ts_notices_channel_id, sings_ts_notices_channel_status)
    if sings_ts_notices_channel_status:
        send_slack_message("This is a test message from the NuSTAR SINGS pipeline.", channel_id=sings_ts_notices_channel_id)
