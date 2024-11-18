#!/usr/bin/python3

# Standard library imports
import argparse
import datetime
import gzip
import json
import os
import re
import shutil
import subprocess
import sys

# Third-party imports
import requests
import yaml
from google.protobuf import text_format

# Local imports
from checkin import checkin_generator_pb2
from utils import functions

def send_telegram_message(bot_token, chat_id, message, button_text, button_url):
    """
    Send a message to a Telegram chat with an inline button.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'MarkdownV2',
        'reply_markup': {
            'inline_keyboard': [[
                {
                    'text': button_text,
                    'url': button_url
                }
            ]]
        }
    }
    response = requests.post(url, json=payload)

    if response.status_code != 200:
        print(f"Failed to send message. Status code: {response.status_code}")
        print(f"Response: {response.text}")
    else:
        print("Message sent successfully")

    return response.json()

def escape_markdown_v2(text):
    """
    Escape special characters for Telegram's MarkdownV2 format.
    """
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return ''.join(['\\' + char if char in escape_chars else char for char in text])

def remove_html_tags(text):
    """
    Remove HTML tags and URLs from the given text.
    """
    text = re.sub('<.*?>', '', text)
    text = re.sub(r'\s*\(http[s]?://\S+\)?', '', text)
    return text.strip()

def load_config(config_file):
    """
    Load configuration from a YAML file.
    """
    with open(config_file, 'r') as file:
        return yaml.safe_load(file)

def load_update_info():
    """
    Load update information from a JSON file.
    """
    if os.path.exists('update_info.json'):
        with open('update_info.json', 'r') as f:
            return json.load(f)
    return {}

def write_update_info(update_info):
    """
    Write update information to a JSON file.
    """
    with open('update_info.json', 'w') as f:
        json.dump(update_info, f, indent=2)

def main():
    # Check for required environment variables
    if 'bot_token' in os.environ and 'chat_id' in os.environ:
        bot_token, chat_id = os.environ['bot_token'], os.environ['chat_id']
    else:
        print("Error: Environment variables 'bot_token' and 'chat_id' are not set.")
        sys.exit(1)

    # Parse command-line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true', help='Print debug information to text file.')
    parser.add_argument('-c', '--config', default='config.yml', help='Path to the config file')
    parser.add_argument('--download', action='store_true', help='Download the OTA file.')
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)

    # Extract configuration values
    build_tag = config['build_tag']
    incremental = config['incremental']
    android_version = config['android_version']
    model = config['model']
    device = config['device']
    oem = config['oem']
    product = config['product']

    # Prepare headers for the HTTP request
    headers = {
        'accept-encoding': 'gzip, deflate',
        'content-encoding': 'gzip',
        'content-type': 'application/x-protobuffer',
        'user-agent': f'Dalvik/2.1.0 (Linux; U; Android {android_version}; {model} Build/{build_tag})'
    }

    # Prepare the check-in request
    checkinproto = checkin_generator_pb2.AndroidCheckinProto()
    payload = checkin_generator_pb2.AndroidCheckinRequest()
    build = checkin_generator_pb2.AndroidBuildProto()
    response = checkin_generator_pb2.AndroidCheckinResponse()

    # Set up the build information
    build.id = f'{oem}/{product}/{device}:{android_version}/{build_tag}/{incremental}:user/release-keys'
    build.timestamp = 0
    build.device = device
    checkinproto.build.CopyFrom(build)
    checkinproto.roaming = "WIFI::"
    checkinproto.userNumber = 0
    checkinproto.deviceType = 2
    checkinproto.voiceCapable = False
    checkinproto.unknown19 = "WIFI"

    # Set up the payload
    payload.imei = functions.generateImei()
    payload.id = 0
    payload.digest = functions.generateDigest()
    payload.checkin.CopyFrom(checkinproto)
    payload.locale = 'en-US'
    payload.timeZone = 'America/New_York'
    payload.version = 3
    payload.serialNumber = functions.generateSerial()
    payload.macAddr.append(functions.generateMac())
    payload.macAddrType.extend(['wifi'])
    payload.fragment = 0
    payload.userSerialNumber = 0
    payload.fetchSystemUpdates = 1
    payload.unknown30 = 0

    # Serialize and compress the payload
    with gzip.open('test_data.gz', 'wb') as f_out:
        f_out.write(payload.SerializeToString())

    # Send the check-in request
    with open('test_data.gz', 'rb') as post_data:
        r = requests.post('https://android.googleapis.com/checkin', data=post_data, headers=headers)

    print(f"Checking device... {model}")
    print(f"Current version... {incremental}")

    config_name = os.path.splitext(os.path.basename(args.config))[0]
    update_info = load_update_info()

    try:
        # Parse the response and extract update information
        download_url = ""
        found = False
        response.ParseFromString(r.content)
        
        if args.debug:
            with open('debug.txt', 'w') as f:
                f.write(text_format.MessageToString(response))
        
        # Look for the download URL in the response
        for entry in response.setting:
            if b'https://android.googleapis.com' in entry.value:
                download_url = entry.value.decode()
                found = True
                break
        
        # Prepare update information
        update_info[config_name] = {
            "title": "",
            "device": model,
            "description": "",
            "url": download_url,
            "size": "",
            "found": True,
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        if found:
            # Extract additional update details
            for entry in response.setting:
                if entry.name.decode() == "update_title":
                    update_info[config_name]["title"] = entry.value.decode()
                    break
            for entry in response.setting:
                if entry.name.decode() == "update_description":
                    update_info[config_name]["description"] = remove_html_tags(entry.value.decode())
                    break
            for entry in response.setting:
                if entry.name.decode() == "update_size":
                    update_info[config_name]["size"] = entry.value.decode()
                    break
            print("Found updates.")
        else:
            update_info[config_name] = {
                "found": False,
                "timestamp": datetime.datetime.now().isoformat()
            }
            print("There are no new updates for your device.")
        
        # Save the update information
        write_update_info(update_info)
    
    except Exception as e:
        print(f"Unable to obtain OTA URL. Error: {str(e)}")

    # Extract update title from the response
    update_title = update_info[config_name].get("title", "")

    # Check if the GitHub release already exists
    try:
        subprocess.run(
            ["gh", "release", "view", update_title],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        print(f"Release with title '{update_title}' already exists. Skipping post.")
        return  # Exit the main function to skip posting
    except subprocess.CalledProcessError:
        print("Release not found. Proceeding with posting...")

    # Prepare and send Telegram message with update information
    message = f"*Update available for {escape_markdown_v2(model)}*\n\n"
    if update_info[config_name]['found']:
        message += f"*Title:*\n{escape_markdown_v2(update_info[config_name]['title'])}\n\n"
        message += f"*Description:*\n{escape_markdown_v2(update_info[config_name]['description'])}\n\n"
        message += f"*Size:* {escape_markdown_v2(update_info[config_name]['size'])}\n\n"
        button_text = "Google OTA Link"
        button_url = update_info[config_name]['url']
        send_telegram_message(bot_token, chat_id, message, button_text, button_url)

if __name__ == "__main__":
    main()
