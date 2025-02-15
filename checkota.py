#!/usr/bin/python3

import argparse
import datetime
import gzip
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
import yaml
from google.protobuf import text_format

from checkin import checkin_generator_pb2
from utils import functions

class Logger:
    @staticmethod
    def info(message: str):
        print(f"\033[94m=>\033[0m {message}")

    @staticmethod
    def success(message: str):
        print(f"\033[92m✓\033[0m {message}")

    @staticmethod
    def error(message: str):
        print(f"\033[91m✗\033[0m {message}")

@dataclass
class Config:
    build_tag: str
    incremental: str
    android_version: str
    model: str
    device: str
    oem: str
    product: str

    @classmethod
    def from_yaml(cls, config_file: str) -> 'Config':
        with open(config_file, 'r') as file:
            config_data = yaml.safe_load(file)
        return cls(**config_data)

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, message: str, button_text: str, button_url: str) -> dict:
        Logger.info("Sending Telegram notification...")
        url = f"{self.base_url}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': message,
            'parse_mode': 'html',
            'reply_markup': {
                'inline_keyboard': [[
                    {'text': button_text, 'url': button_url}
                ]]
            }
        }
        
        response = requests.post(url, json=payload)
        response.raise_for_status()
        Logger.success("Telegram notification sent successfully")
        return response.json()

class UpdateChecker:
    def __init__(self, config: Config):
        self.config = config
        self.headers = {
            'accept-encoding': 'gzip, deflate',
            'content-encoding': 'gzip',
            'content-type': 'application/x-protobuffer',
            'user-agent': (f'Dalvik/2.1.0 (Linux; U; Android {config.android_version}; '
                          f'{config.model} Build/{config.build_tag})')
        }

    def _prepare_checkin_request(self) -> checkin_generator_pb2.AndroidCheckinRequest:
        checkinproto = checkin_generator_pb2.AndroidCheckinProto()
        payload = checkin_generator_pb2.AndroidCheckinRequest()
        build = checkin_generator_pb2.AndroidBuildProto()

        build.id = (f'{self.config.oem}/{self.config.product}/{self.config.device}:'
                   f'{self.config.android_version}/{self.config.build_tag}/'
                   f'{self.config.incremental}:user/release-keys')
        build.timestamp = 0
        build.device = self.config.device
        
        checkinproto.build.CopyFrom(build)
        checkinproto.roaming = "WIFI::"
        checkinproto.userNumber = 0
        checkinproto.deviceType = 2
        checkinproto.voiceCapable = False
        checkinproto.unknown19 = "WIFI"

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

        return payload

    def check_for_updates(self, debug: bool = False) -> Tuple[bool, Optional[Dict]]:
        Logger.info("Checking for updates...")
        payload = self._prepare_checkin_request()
        compressed_payload = gzip.compress(payload.SerializeToString())
        
        response = requests.post(
            'https://android.googleapis.com/checkin',
            data=compressed_payload,
            headers=self.headers
        )
        response.raise_for_status()

        checkin_response = checkin_generator_pb2.AndroidCheckinResponse()
        checkin_response.ParseFromString(response.content)

        if debug:
            Path('debug.txt').write_text(text_format.MessageToString(checkin_response))

        update_info = self._parse_response(checkin_response)
        return bool(update_info.get('url')), update_info

    def _parse_response(self, response: checkin_generator_pb2.AndroidCheckinResponse) -> Dict:
        update_info = {
            'device': self.config.model,
            'found': False,
            'timestamp': datetime.datetime.now().isoformat()
        }

        for entry in response.setting:
            if b'https://android.googleapis.com' in entry.value:
                update_info.update({
                    'url': entry.value.decode(),
                    'found': True
                })
                break

        if update_info['found']:
            for entry in response.setting:
                name = entry.name.decode()
                if name == 'update_title':
                    update_info['title'] = entry.value.decode()
                elif name == 'update_description':
                    update_info['description'] = self._clean_description(entry.value.decode())
                elif name == 'update_size':
                    update_info['size'] = entry.value.decode()

        return update_info

    @staticmethod
    def _clean_description(text: str) -> str:
        text = re.sub(r'<br\s*/?>\n\n', '<br>', text)
        text = re.sub(r'<br\s*/?>\n', '<br>', text)
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub('<.*?>', '', text)
        text = re.sub(r'\s*\(http[s]?://\S+\)?', '', text)
        return text.strip()

class UpdateManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config_name = self.config_path.stem
        self.update_info_path = Path('update_info.json')

    def load_update_info(self) -> Dict:
        if self.update_info_path.exists():
            return json.loads(self.update_info_path.read_text())
        return {}

    def save_update_info(self, update_info: Dict):
        self.update_info_path.write_text(json.dumps(update_info, indent=2))

    def check_existing_release(self, title: str) -> bool:
        Logger.info(f"Checking for existing release...")
        try:
            import subprocess
            subprocess.run(
                ["gh", "release", "view", title],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            Logger.info(f"Release '{title}' already exists")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            Logger.info("No existing release found")
            return False

def main():
    parser = argparse.ArgumentParser(description='OTA Update Checker')
    parser.add_argument('--debug', action='store_true', help='Print debug information')
    parser.add_argument('-c', '--config', default='config.yml', help='Path to config file')
    parser.add_argument('--download', action='store_true', help='Download OTA file')
    args = parser.parse_args()

    try:
        bot_token = os.environ['bot_token']
        chat_id = os.environ['chat_id']
    except KeyError as e:
        Logger.error(f"Environment variable {e} is not set")
        return 1

    try:
        config = Config.from_yaml(args.config)
        update_checker = UpdateChecker(config)
        telegram = TelegramNotifier(bot_token, chat_id)
        update_manager = UpdateManager(args.config)

        Logger.info(f"Device: {config.model}")
        Logger.info(f"Current version: {config.incremental}")

        found, update_data = update_checker.check_for_updates(args.debug)
        
        update_info = update_manager.load_update_info()
        update_info[update_manager.config_name] = update_data
        update_manager.save_update_info(update_info)

        if not found:
            Logger.info("No new updates available")
            return 0

        if update_data.get('title'):
            if update_manager.check_existing_release(update_data['title']):
                Logger.info("Skipping notification for existing release")
                return 0
        else:
            Logger.error("Update title not found in response")
            return 1

        # Prepare notification message
        Logger.info("Preparing notification message...")
        message = (
            f"<blockquote><b>OTA update available for {config.model}</b></blockquote>\n\n"
            f"<b>{update_data['title']}</b>\n\n"
            f"{update_data['description']}\n\n"
            f"Size: {update_data['size']}\n\n"
        )

        # Send notification with detailed status
        try:
            telegram.send_message(message, "Google OTA Link", update_data['url'])
            Logger.success("Process completed successfully")
            return 0
        except requests.exceptions.RequestException as e:
            Logger.error(f"Failed to send Telegram notification: {str(e)}")
            return 1

    except Exception as e:
        Logger.error(str(e))
        if args.debug:
            raise
        return 1

if __name__ == "__main__":
    sys.exit(main())
