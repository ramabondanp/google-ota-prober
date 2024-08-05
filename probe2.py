#!/usr/bin/python3

from checkin import checkin_generator_pb2
from google.protobuf import text_format
from utils import functions
import argparse, requests, gzip, shutil, os, yaml, re, sys
import json, datetime

def send_telegram_message(bot_token, chat_id, message):
    response = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data={'chat_id': chat_id, 'text': message})
    return response.json()

def remove_html_tags(text):
    return re.sub('<.*?>', '', text)

def load_config(config_file):
    with open(config_file, 'r') as file:
        return yaml.safe_load(file)

def load_update_info():
    if os.path.exists('update_info.json'):
        with open('update_info.json', 'r') as f:
            return json.load(f)
    return {}

def write_update_info(update_info):
    with open('update_info.json', 'w') as f:
        json.dump(update_info, f, indent=2)

if 'bot_token' in os.environ and 'chat_id' in os.environ:
    bot_token, chat_id = os.environ['bot_token'], os.environ['chat_id']
else:
    print("Error: Environment variables 'bot_token' and 'chat_id' are not set.")
    sys.exit(1)

parser = argparse.ArgumentParser()
parser.add_argument('--debug', action='store_true', help='Print debug information to text file.')
parser.add_argument('-c', '--config', default='config.yml', help='Path to the config file')
parser.add_argument('--download', action='store_true', help='Download the OTA file.')
args = parser.parse_args()
config = load_config(args.config)

build_tag, incremental, android_version, model, device, oem, product = config['build_tag'], config['incremental'], config['android_version'], config['model'], config['device'], config['oem'], config['product']

headers = {'accept-encoding': 'gzip, deflate', 'content-encoding': 'gzip', 'content-type': 'application/x-protobuffer', 'user-agent': f'Dalvik/2.1.0 (Linux; U; Android {android_version}; {model} Build/{build_tag})'}

checkinproto, payload = checkin_generator_pb2.AndroidCheckinProto(), checkin_generator_pb2.AndroidCheckinRequest()
build = checkin_generator_pb2.AndroidBuildProto()
response = checkin_generator_pb2.AndroidCheckinResponse()

build.id = f'{oem}/{product}/{device}:{android_version}/{build_tag}/{incremental}:user/release-keys'
build.timestamp, build.device = 0, device
checkinproto.build.CopyFrom(build)
checkinproto.roaming, checkinproto.userNumber, checkinproto.deviceType, checkinproto.voiceCapable, checkinproto.unknown19 = "WIFI::", 0, 2, False, "WIFI"

payload.imei, payload.id, payload.digest = functions.generateImei(), 0, functions.generateDigest()
payload.checkin.CopyFrom(checkinproto)
payload.locale, payload.timeZone, payload.version, payload.serialNumber = 'en-US', 'America/New_York', 3, functions.generateSerial()
payload.macAddr.append(functions.generateMac())
payload.macAddrType.extend(['wifi'])
payload.fragment, payload.userSerialNumber, payload.fetchSystemUpdates, payload.unknown30 = 0, 0, 1, 0

with gzip.open('test_data.gz', 'wb') as f_out:
    f_out.write(payload.SerializeToString())

post_data = open('test_data.gz', 'rb')
r = requests.post('https://android.googleapis.com/checkin', data=post_data, headers=headers)
post_data.close()

print("Checking device... " + model)
print("Current version... " + incremental)

config_name = os.path.splitext(os.path.basename(args.config))[0]
update_info = load_update_info()

try:
    download_url, found = "", False
    response.ParseFromString(r.content)
    if args.debug:
        with open('debug.txt', 'w') as f:
            f.write(text_format.MessageToString(response))
    for entry in response.setting:
        if b'https://android.googleapis.com' in entry.value:
            download_url, found = entry.value.decode(), True
            break
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
    write_update_info(update_info)
    if not found:
        print("There are no new updates for your device.")
except:
    print("Unable to obtain OTA URL.")

# Send update information to Telegram
message = f"Update available for {model}:\n\n"
if update_info[config_name]['found']:
    message += f"Title:\n{update_info[config_name]['title']}\n\n"
    message += f"Description:\n{update_info[config_name]['description']}\n\n"
    message += f"Size: {update_info[config_name]['size']}\n\n"
    message += f"URL: {update_info[config_name]['url']}\n"
    send_telegram_message(bot_token, chat_id, message)
