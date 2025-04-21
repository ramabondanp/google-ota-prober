#!/usr/bin/python3

import argparse
import datetime
import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List

# Third-party imports
try:
    import requests
    import yaml
    from google.protobuf import text_format
    from checkin import checkin_generator_pb2
    from utils import functions
except ImportError as e:
    print(f"Error: Missing required library. {e}", file=sys.stderr)
    sys.exit(1)

# Constants
CHECKIN_URL = 'https://android.googleapis.com/checkin'
USER_AGENT_TEMPLATE = 'Dalvik/2.1.0 (Linux; U; Android {android_version}; {model} Build/{build_tag})'
PROTO_CONTENT_TYPE = 'application/x-protobuffer'
UPDATE_INFO_FILENAME = "update_info.json"
DEBUG_FILENAME = "debug_checkin_response.txt"

# Simple colored console logger
class Logger:
    @staticmethod
    def info(message: str): print(f"\033[94m=>\033[0m {message}")
    @staticmethod
    def success(message: str): print(f"\033[92m✓\033[0m {message}")
    @staticmethod
    def error(message: str): print(f"\033[91m✗\033[0m {message}", file=sys.stderr)
    @staticmethod
    def warning(message: str): print(f"\033[93m!\033[0m {message}", file=sys.stderr)

@dataclass
class Config:
    """Device configuration data."""
    build_tag: str
    incremental: str
    android_version: str
    model: str
    device: str
    oem: str
    product: str

    @classmethod
    def from_yaml(cls, config_file: Path) -> 'Config':
        """Load configuration from YAML file."""
        if not config_file.is_file():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        
        try:
            with open(config_file, 'r') as file:
                config_data = yaml.safe_load(file)
                
            if not isinstance(config_data, dict):
                raise ValueError("Config file content is not a valid dictionary.")
                
            return cls(**config_data)
            
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML config file: {e}")
        except TypeError as e:
            raise ValueError(f"Missing or invalid keys in config file: {e}")

    def get_fingerprint(self) -> str:
        """Build fingerprint string."""
        return (f'{self.oem}/{self.product}/{self.device}:'
                f'{self.android_version}/{self.build_tag}/'
                f'{self.incremental}:user/release-keys')

class TelegramNotifier:
    """Handle Telegram notifications."""
    MAX_MESSAGE_LENGTH = 4000
    
    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("Bot token and chat ID are required for TelegramNotifier.")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _split_message(self, message: str) -> List[str]:
        """Split long message into chunks respecting Telegram's length limit."""
        if len(message) <= self.MAX_MESSAGE_LENGTH:
            return [message]
        
        parts = []
        current_part = ""
        blocks = message.split('\n\n')
        
        for block in blocks:
            if len(current_part + block + '\n\n') > self.MAX_MESSAGE_LENGTH:
                if current_part:
                    parts.append(current_part.rstrip())
                current_part = block + '\n\n'
            else:
                current_part += block + '\n\n'
        
        if current_part:
            parts.append(current_part.rstrip())
        
        # Handle HTML tag continuity between parts
        processed_parts = []
        open_tags = []
        
        for i, part in enumerate(parts):
            tags = re.findall(r'<(\w+)[^>]*>', part)
            close_tags = re.findall(r'</(\w+)>', part)
            
            for tag in tags:
                if tag not in close_tags:
                    open_tags.append(tag)
            for tag in close_tags:
                if tag in open_tags:
                    open_tags.remove(tag)
            
            if i < len(parts) - 1 and open_tags:
                part += ''.join(f'</{tag}>' for tag in reversed(open_tags))
                parts[i + 1] = ''.join(f'<{tag}>' for tag in open_tags) + parts[i + 1]
            
            processed_parts.append(part)
        
        return processed_parts

    def send_message(self, message: str, button_text: Optional[str] = None, 
                    button_url: Optional[str] = None) -> bool:
        """Send message via Telegram Bot API."""
        Logger.info("Sending Telegram notification...")
        message_parts = self._split_message(message)
        
        try:
            for i, part in enumerate(message_parts):
                payload = {
                    'chat_id': self.chat_id,
                    'text': part,
                    'parse_mode': 'html',
                    'disable_web_page_preview': True,
                }
                
                if i == len(message_parts) - 1 and button_text and button_url:
                    payload['reply_markup'] = {
                        'inline_keyboard': [[
                            {'text': button_text, 'url': button_url}
                        ]]
                    }

                response = requests.post(
                    f"{self.base_url}/sendMessage",
                    json=payload,
                    timeout=15
                )
                response.raise_for_status()
                
                if i < len(message_parts) - 1:
                    time.sleep(0.5)
            
            Logger.success("Telegram notification sent successfully.")
            return True
            
        except Exception as e:
            Logger.error(f"Failed to send Telegram notification: {e}")
            return False

class UpdateChecker:
    """Check for OTA updates using Android Checkin service."""
    def __init__(self, config: Config):
        self.config = config
        self.user_agent = USER_AGENT_TEMPLATE.format(
            android_version=config.android_version,
            model=config.model,
            build_tag=config.build_tag
        )
        self.headers = {
            'accept-encoding': 'gzip, deflate',
            'content-encoding': 'gzip',
            'content-type': PROTO_CONTENT_TYPE,
            'user-agent': self.user_agent
        }

    def _prepare_checkin_request(self) -> bytes:
        """Build and serialize AndroidCheckinRequest protobuf message."""
        payload = checkin_generator_pb2.AndroidCheckinRequest()
        build = checkin_generator_pb2.AndroidBuildProto()
        checkinproto = checkin_generator_pb2.AndroidCheckinProto()

        build.id = self.config.get_fingerprint()
        build.timestamp = 0
        build.device = self.config.device

        checkinproto.build.CopyFrom(build)
        checkinproto.roaming = "WIFI::"
        checkinproto.userNumber = 0
        checkinproto.deviceType = 2
        checkinproto.voiceCapable = False

        try:
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
        except AttributeError as e:
            raise ImportError(f"Required function missing from 'utils.functions': {e}")

        return gzip.compress(payload.SerializeToString())

    def check_for_updates(self, debug: bool = False) -> Tuple[bool, Optional[Dict]]:
        """Perform checkin request and parse response for update info."""
        Logger.info("Checking for updates via Google Checkin service...")
        
        try:
            compressed_payload = self._prepare_checkin_request()
            
            response = requests.post(
                CHECKIN_URL,
                data=compressed_payload,
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()

            checkin_response = checkin_generator_pb2.AndroidCheckinResponse()
            checkin_response.ParseFromString(response.content)
            
            if debug:
                debug_file = Path(DEBUG_FILENAME)
                debug_file.write_text(text_format.MessageToString(checkin_response))
                Logger.info(f"Debug checkin response saved to {debug_file}")

            update_info = self._parse_response(checkin_response)
            has_update = update_info.get('found', False) and 'url' in update_info

            return has_update, update_info
            
        except Exception as e:
            Logger.error(f"Update check failed: {e}")
            if debug and 'response' in locals():
                debug_file = Path(DEBUG_FILENAME.replace(".txt", "_error.bin"))
                debug_file.write_bytes(response.content)
                Logger.info(f"Raw error response saved to {debug_file}")
            return False, None

    def _parse_response(self, response: checkin_generator_pb2.AndroidCheckinResponse) -> Dict:
        """Extract update information from CheckinResponse."""
        update_info = {
            'device': self.config.model,
            'found': False,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'title': None,
            'description': None,
            'size': None,
            'url': None
        }

        # Find update URL first
        for entry in response.setting:
            try:
                if entry.name == b'update_url' or b'https://android.googleapis.com/packages/ota' in entry.value:
                    update_info['url'] = entry.value.decode('utf-8')
                    update_info['found'] = True
                    break
            except (UnicodeDecodeError, AttributeError):
                continue

        # If URL found, look for other details
        if update_info['found']:
            for entry in response.setting:
                try:
                    name = entry.name.decode('utf-8')
                    value = entry.value.decode('utf-8')

                    if name == 'update_title':
                        update_info['title'] = self._tidy_title(value)
                    elif name == 'update_description':
                        update_info['description'] = self._clean_description(value)
                    elif name == 'update_size':
                        update_info['size'] = value
                except (UnicodeDecodeError, AttributeError):
                    continue

        return update_info

    @staticmethod
    def _clean_description(text: str) -> str:
        """Clean HTML from description text."""
        text = re.sub(r'\n', '', text)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\s*\(http[s]?://\S+\)?', '', text)
        return text.strip()

    @staticmethod
    def _tidy_title(text: str) -> str:
        """Clean title text."""
        return text.strip()

class UpdateInfoStore:
    """Manage update information JSON file."""
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def load(self) -> Dict:
        """Load update information from JSON file."""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r') as f:
                    content = f.read()
                    if not content.strip():
                        return {}
                    return json.loads(content)
            except (json.JSONDecodeError, OSError) as e:
                Logger.error(f"Error reading {self.file_path}: {e}")
                return {}
        return {}

    def save(self, update_info: Dict):
        """Save update information to JSON file."""
        try:
            with open(self.file_path, 'w') as f:
                json.dump(update_info, f, indent=2, ensure_ascii=False)
        except (OSError, TypeError) as e:
            Logger.error(f"Error writing to {self.file_path}: {e}")

# Helper Functions

def check_command_exists(command: str) -> bool:
    """Check if command exists in system PATH."""
    return shutil.which(command) is not None

def check_external_commands(commands: List[str]) -> bool:
    """Check if all required external commands are available."""
    missing = [cmd for cmd in commands if not check_command_exists(cmd)]
    if missing:
        Logger.error(f"Missing required command(s): {', '.join(missing)}")
        return False
    return True

def get_target_fingerprint_from_ota(ota_url: str) -> Optional[str]:
    """Extract post-build fingerprint from OTA URL."""
    Logger.info("Attempting to fetch target fingerprint from OTA metadata...")
    required_commands = ['curl', 'bsdtar', 'grep', 'sed']
    if not check_external_commands(required_commands):
        return None

    cmd = (
        f"curl --fail -Ls --max-time 60 --limit-rate 100K {shlex.quote(ota_url)} "
        f"| ( bsdtar -Oxf - 'META-INF/com/android/metadata' 2>/dev/null || true ) "
        f"| ( grep -m1 '^post-build=' | sed 's/^post-build=//' && killall curl ) "
        f"2>/dev/null"
    )

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90, check=False)

        if result.returncode != 0 or not result.stdout.strip():
            Logger.warning("Could not extract fingerprint from OTA metadata.")
            return None

        fingerprint = result.stdout.strip()
        Logger.info(f"Extracted target fingerprint: {fingerprint}")
        return fingerprint

    except Exception as e:
        Logger.error(f"Error fetching target fingerprint: {e}")
        return None

def check_github_release_exists(release_tag: str) -> bool:
    """Check if GitHub release with given tag exists."""
    if not check_command_exists("gh"):
        Logger.error("GitHub CLI 'gh' not found. Please install 'gh' to check for releases.")
        sys.exit(1)

    Logger.info(f"Checking for GitHub release with tag: {release_tag}...")
    try:
        subprocess.run(
            ["gh", "release", "view", release_tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30
        )
        Logger.info(f"Release '{release_tag}' already exists on GitHub.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        Logger.info(f"No existing GitHub release found for tag '{release_tag}'.")
        return False
    except Exception as e:
        Logger.error(f"GitHub release check error: {e}")
        return False

def setup_arg_parser() -> argparse.ArgumentParser:
    """Configure command line argument parser."""
    parser = argparse.ArgumentParser(description='Android OTA Update Checker')
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging and save checkin response.'
    )
    parser.add_argument(
        '-c', '--config',
        type=Path,
        required=True,
        help='Path to device configuration YAML file (required).'
    )
    parser.add_argument(
        '--skip-telegram',
        action='store_true',
        help='Skip sending Telegram notifications.'
    )
    parser.add_argument(
        '--skip-git',
        action='store_true',
        help='Skip checking for existing GitHub releases.'
    )
    parser.add_argument(
        '-i', '--incremental',
        help='Override incremental version from config file.'
    )
    return parser

def main() -> int:
    """Main script execution."""
    if sys.version_info < (3, 7):
        Logger.error("This script requires Python 3.7 or higher.")
        return 1

    parser = setup_arg_parser()
    args = parser.parse_args()

    # Load configuration
    try:
        config = Config.from_yaml(args.config)
        if args.incremental:
            Logger.info(f"Overriding incremental version: {args.incremental}")
            config.incremental = args.incremental
    except Exception as e:
        Logger.error(f"Configuration error: {e}")
        return 1

    # Get config name for update info storage
    config_name = args.config.stem

    # Setup Telegram notifier
    telegram_notifier = None
    if not args.skip_telegram:
        bot_token = os.environ.get('bot_token')
        chat_id = os.environ.get('chat_id')
        if not bot_token or not chat_id:
            Logger.warning("BOT_TOKEN or CHAT_ID not set. Skipping Telegram notifications.")
            args.skip_telegram = True
        else:
            try:
                telegram_notifier = TelegramNotifier(bot_token, chat_id)
            except ValueError as e:
                Logger.error(f"Telegram Notifier setup failed: {e}")
                args.skip_telegram = True

    # Initialize components
    update_checker = UpdateChecker(config)
    update_store = UpdateInfoStore(Path(UPDATE_INFO_FILENAME))

    # Log initial state
    current_fingerprint = config.get_fingerprint()
    Logger.info(f"Device: {config.model} ({config.device})")
    Logger.info(f"Current Build Fingerprint: {current_fingerprint}")

    # Check for updates
    found, update_data = update_checker.check_for_updates(args.debug)

    # Store results
    all_update_info = update_store.load()
    if update_data:
        all_update_info[config_name] = update_data
    else:
        all_update_info[config_name] = {
            'device': config.model,
            'found': False,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'error': 'Update check failed or returned no data'
        }
    update_store.save(all_update_info)

    # Handle update outcome
    if not found or not update_data:
        Logger.info("No new OTA update found.")
        return 0

    # Process found update
    update_title = update_data.get('title')
    update_url = update_data.get('url')
    update_size = update_data.get('size')
    update_description = update_data.get('description', 'No description provided.')

    if not all([update_title, update_url, update_size]):
        Logger.error("Update detected but missing essential information.")
        return 1

    Logger.success(f"New OTA update found: {update_title}")
    Logger.info(f"Size: {update_size}")
    Logger.info(f"URL: {update_url}")

    # Check existing GitHub release - exit if gh command not available
    if not args.skip_git:
        release_tag = update_title
        if check_github_release_exists(release_tag):
            Logger.info(f"Skipping notification as GitHub release already exists.")
            return 0

    # Fetch target fingerprint
    target_fingerprint = get_target_fingerprint_from_ota(update_url) or "N/A"
    if target_fingerprint != "N/A":
        Logger.info(f"Target Build Fingerprint: {target_fingerprint}")

    # Send notification
    if not args.skip_telegram and telegram_notifier:
        message = (
            f"<blockquote><b>OTA Update Available</b></blockquote>\n\n"
            f"<b>Device:</b> {config.model}\n\n"
            f"<b>Title:</b> {update_title}\n\n"
            f"{update_description}\n\n"
            f"<b>Size:</b> {update_size}\n"
            f"<b>Fingerprint:</b>\n<code>{target_fingerprint}</code>"
        )

        if not telegram_notifier.send_message(message, "Google OTA Link", update_url):
            return 1

    Logger.success("Update check completed successfully.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
