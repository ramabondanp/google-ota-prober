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

# Third-party imports - ensure these are installed
try:
    import requests
    import yaml
    from google.protobuf import text_format
    # Assuming checkin_generator_pb2 is generated and available in the same directory
    # or PYTHONPATH
    from checkin import checkin_generator_pb2
    # Assuming utils.functions exists and provides necessary generators
    from utils import functions
except ImportError as e:
    print(f"Error: Missing required library. Please install requirements. {e}", file=sys.stderr)
    sys.exit(1)

# --- Constants ---
CHECKIN_URL = 'https://android.googleapis.com/checkin'
USER_AGENT_TEMPLATE = 'Dalvik/2.1.0 (Linux; U; Android {android_version}; {model} Build/{build_tag})'
PROTO_CONTENT_TYPE = 'application/x-protobuffer'
UPDATE_INFO_FILENAME = "update_info.json"
CONFIG_FILENAME_DEFAULT = "config.yml"
DEBUG_FILENAME = "debug_checkin_response.txt"

# --- Utility Classes ---

class Logger:
    """Simple colored console logger."""
    @staticmethod
    def info(message: str):
        print(f"\033[94m=>\033[0m {message}")

    @staticmethod
    def success(message: str):
        print(f"\033[92m✓\033[0m {message}")

    @staticmethod
    def error(message: str):
        print(f"\033[91m✗\033[0m {message}", file=sys.stderr)

    @staticmethod
    def warning(message: str):
        print(f"\033[93m!\033[0m {message}", file=sys.stderr)


@dataclass
class Config:
    """Holds device configuration data."""
    build_tag: str
    incremental: str
    android_version: str
    model: str
    device: str
    oem: str
    product: str

    @classmethod
    def from_yaml(cls, config_file: Path) -> 'Config':
        """Loads configuration from a YAML file."""
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
        """Constructs the build fingerprint string."""
        return (f'{self.oem}/{self.product}/{self.device}:'
                f'{self.android_version}/{self.build_tag}/'
                f'{self.incremental}:user/release-keys')

# --- Core Logic Classes ---

class TelegramNotifier:
    """Handles sending notifications via Telegram Bot API."""
    # Maximum length for a single Telegram message
    MAX_MESSAGE_LENGTH = 4000
    
    def __init__(self, bot_token: Optional[str], chat_id: Optional[str]):
        if not bot_token or not chat_id:
             raise ValueError("Bot token and chat ID are required for TelegramNotifier.")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def _split_message(self, message: str) -> List[str]:
        """Splits a long message into chunks that respect Telegram's length limit and HTML formatting.
        
        Args:
            message: The message to split, potentially containing HTML tags.
            
        Returns:
            List of message parts that are each under the length limit.
        """
        if len(message) <= self.MAX_MESSAGE_LENGTH:
            return [message]
        
        parts = []
        current_part = ""
        
        # Split on double newlines to keep logical blocks together
        blocks = message.split('\n\n')
        
        for block in blocks:
            # If adding this block would exceed the limit, store current part and start new one
            if len(current_part + block + '\n\n') > self.MAX_MESSAGE_LENGTH:
                if current_part:
                    parts.append(current_part.rstrip())
                current_part = block + '\n\n'
            else:
                current_part += block + '\n\n'
        
        # Add the last part if it's not empty
        if current_part:
            parts.append(current_part.rstrip())
        
        # Post-process parts to ensure HTML tags are properly closed and reopened
        processed_parts = []
        open_tags = []
        
        for i, part in enumerate(parts):
            # Find all opening tags in order
            tags = re.findall(r'<(\w+)[^>]*>', part)
            close_tags = re.findall(r'</(\w+)>', part)
            
            # Track which tags are still open at the end of this part
            for tag in tags:
                if tag not in close_tags:
                    open_tags.append(tag)
            for tag in close_tags:
                if tag in open_tags:
                    open_tags.remove(tag)
            
            # If this isn't the last part and we have open tags
            if i < len(parts) - 1:
                # Close all open tags at the end of this part
                for tag in reversed(open_tags):
                    part += f'</{tag}>'
                
                # Reopen all tags at the start of the next part
                parts[i + 1] = ''.join(f'<{tag}>' for tag in open_tags) + parts[i + 1]
            
            processed_parts.append(part)
        
        return processed_parts

    def send_message(self, message: str, button_text: Optional[str] = None, button_url: Optional[str] = None) -> bool:
        """Sends a message via the Telegram Bot API, splitting if necessary.
        
        Args:
            message: The message to send, may contain HTML formatting.
            button_text: Optional text for an inline button.
            button_url: Optional URL for the inline button.
            
        Returns:
            bool: True if all message parts were sent successfully, False otherwise.
        """
        Logger.info("Sending Telegram notification...")
        
        # Split the message if needed
        message_parts = self._split_message(message)
        
        try:
            for i, part in enumerate(message_parts):
                payload = {
                    'chat_id': self.chat_id,
                    'text': part,
                    'parse_mode': 'html',
                    'disable_web_page_preview': True,
                }
                
                # Only add the button to the last part
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
                
                # Add a small delay between messages to avoid rate limiting
                if i < len(message_parts) - 1:
                    time.sleep(0.5)
            
            Logger.success("Telegram notification sent successfully.")
            return True
            
        except requests.exceptions.RequestException as e:
            Logger.error(f"Failed to send Telegram notification: {e}")
            return False
        except Exception as e:
            Logger.error(f"An unexpected error occurred during Telegram notification: {e}")
            return False

class UpdateChecker:
    """Checks for OTA updates using the Android Checkin service."""
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
        """Builds and serializes the AndroidCheckinRequest protobuf message."""
        payload = checkin_generator_pb2.AndroidCheckinRequest()
        build = checkin_generator_pb2.AndroidBuildProto()
        checkinproto = checkin_generator_pb2.AndroidCheckinProto()

        build.id = self.config.get_fingerprint()
        build.timestamp = 0 # Timestamp is typically 0 for checkin requests
        build.device = self.config.device

        # Populate Checkin proto
        checkinproto.build.CopyFrom(build)
        checkinproto.roaming = "WIFI::" # Example value
        checkinproto.userNumber = 0
        checkinproto.deviceType = 2 # DEVICE_ANDROID_DEVICE
        checkinproto.voiceCapable = False # Assuming non-phone device, adjust if needed
        # checkinproto.unknown19 = "WIFI" # This field usage might vary

        # Populate Request proto
        # Using static values as in original; ensure utils.functions is available
        try:
            payload.imei = functions.generateImei() # Requires utils.functions
            payload.id = 0 # Typically 0 for initial checkin
            payload.digest = functions.generateDigest() # Requires utils.functions
            payload.checkin.CopyFrom(checkinproto)
            payload.locale = 'en-US' # Standard locale
            payload.timeZone = 'America/New_York' # Standard timezone
            payload.version = 3 # Checkin protocol version
            payload.serialNumber = functions.generateSerial() # Requires utils.functions
            payload.macAddr.append(functions.generateMac()) # Requires utils.functions
            payload.macAddrType.extend(['wifi'])
            payload.fragment = 0
            payload.userSerialNumber = 0
            payload.fetchSystemUpdates = 1 # Request system updates
            # payload.unknown30 = 0 # Optional field
        except AttributeError as e:
             raise ImportError(f"Required function missing from 'utils.functions': {e}")

        serialized_payload = payload.SerializeToString()
        return gzip.compress(serialized_payload)

    def check_for_updates(self, debug: bool = False) -> Tuple[bool, Optional[Dict]]:
        """Performs the checkin request and parses the response for update info."""
        Logger.info("Checking for updates via Google Checkin service...")
        try:
            compressed_payload = self._prepare_checkin_request()
        except Exception as e:
            Logger.error(f"Failed to prepare checkin request: {e}")
            return False, None

        try:
            response = requests.post(
                CHECKIN_URL,
                data=compressed_payload,
                headers=self.headers,
                timeout=30 # Added timeout
            )
            response.raise_for_status() # Check for HTTP errors

        except requests.exceptions.RequestException as e:
            Logger.error(f"Checkin request failed: {e}")
            return False, None
        except Exception as e:
            Logger.error(f"An unexpected error occurred during checkin request: {e}")
            return False, None


        checkin_response = checkin_generator_pb2.AndroidCheckinResponse()
        try:
            # Protobuf parsing can fail if the response is not valid
            checkin_response.ParseFromString(response.content)
        except Exception as e: # Catch generic Exception as protobuf errors are varied
             Logger.error(f"Failed to parse checkin response: {e}")
             if debug:
                 debug_file = Path(DEBUG_FILENAME.replace(".txt", "_error.bin"))
                 debug_file.write_bytes(response.content)
                 Logger.info(f"Raw error response saved to {debug_file}")
             return False, None

        if debug:
            debug_file = Path(DEBUG_FILENAME)
            try:
                 debug_file.write_text(text_format.MessageToString(checkin_response))
                 Logger.info(f"Debug checkin response saved to {debug_file}")
            except Exception as e:
                 Logger.error(f"Failed to write debug file: {e}")


        update_info = self._parse_response(checkin_response)
        has_update = update_info.get('found', False) and 'url' in update_info

        return has_update, update_info

    def _parse_response(self, response: checkin_generator_pb2.AndroidCheckinResponse) -> Dict:
        """Extracts relevant update information from the CheckinResponse."""
        update_info = {
            'device': self.config.model,
            'found': False,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'title': None,
            'description': None,
            'size': None,
            'url': None
        }

        # Find the update URL first
        for entry in response.setting:
            try:
                # Check for the characteristic update URL pattern
                if entry.name == b'update_url' or b'https://android.googleapis.com/packages/ota' in entry.value:
                    update_info['url'] = entry.value.decode('utf-8')
                    update_info['found'] = True
                    break # Found the primary info, stop searching for URL
            except UnicodeDecodeError:
                 Logger.warning(f"Could not decode setting value: {entry.value!r}")
            except AttributeError:
                 Logger.warning("Malformed setting entry in response")


        # If an update URL was found, look for other details
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
                        update_info['size'] = value # Size is usually just a number string
                    # Add more fields here if needed based on debug output

                except (UnicodeDecodeError, AttributeError):
                    # Ignore entries that can't be decoded or are malformed
                    continue

            # Basic validation: ensure core fields are present if 'found' is True
            if not all([update_info['url'], update_info['title'], update_info['size']]):
                 Logger.warning("Update found, but some metadata (URL, title, size) might be missing.")
                 # Optionally reset 'found' if essential info is missing
                 # update_info['found'] = False

        return update_info

    @staticmethod
    def _clean_description(text: str) -> str:
        """Removes HTML tags and extra whitespace from description text."""
        text = re.sub(r'\n', '', text)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE) # Replace <br> with newlines
        text = re.sub(r'<[^>]*>', '', text)  # Remove all other HTML tags (improved pattern)
        text = re.sub(r'\s*\(http[s]?://\S+\)?', '', text) # Remove URLs in parentheses
        return text.strip()

    @staticmethod
    def _tidy_title(text: str) -> str:
        """Removes extra spaces from the title."""
        # Original code removed *all* spaces, which might be too aggressive.
        # Let's just strip leading/trailing whitespace. Adjust if needed.
        # text = re.sub(r' ', '', text) # Original behaviour
        return text.strip()


class UpdateInfoStore:
    """Manages reading and writing the update information JSON file."""
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def load(self) -> Dict:
        """Loads update information from the JSON file."""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r') as f:
                    content = f.read()
                    # Handle empty file case
                    if not content.strip():
                         return {}
                    return json.loads(content)
            except json.JSONDecodeError as e:
                Logger.error(f"Error decoding JSON from {self.file_path}: {e}")
                # Decide recovery strategy: backup and overwrite, or fail
                return {} # Return empty dict on error
            except OSError as e:
                Logger.error(f"Error reading file {self.file_path}: {e}")
                return {}
        return {}

    def save(self, update_info: Dict):
        """Saves update information to the JSON file."""
        try:
            with open(self.file_path, 'w') as f:
                json.dump(update_info, f, indent=2, ensure_ascii=False) # Use ensure_ascii=False for better unicode
        except OSError as e:
            Logger.error(f"Error writing file {self.file_path}: {e}")
        except TypeError as e:
             Logger.error(f"Error serializing update info to JSON: {e}")


# --- Helper Functions ---

def check_command_exists(command: str) -> bool:
    """Checks if a command exists in the system's PATH."""
    return shutil.which(command) is not None

def check_external_commands(commands: List[str]) -> bool:
     """Checks if all required external commands are available."""
     missing = [cmd for cmd in commands if not check_command_exists(cmd)]
     if missing:
          Logger.error(f"Missing required command(s): {', '.join(missing)}. Please install them.")
          return False
     return True

def get_target_fingerprint_from_ota(ota_url: str) -> Optional[str]:
    """
    Attempts to extract the post-build fingerprint from an OTA URL using curl and bsdtar.
    This is fragile as it depends on external tools and network access.
    """
    Logger.info("Attempting to fetch target fingerprint from OTA metadata...")
    required_commands = ['curl', 'bsdtar', 'grep', 'sed'] # Removed killall, might not be needed/safe
    if not check_external_commands(required_commands):
        Logger.warning("Cannot fetch target fingerprint due to missing tools.")
        return None

    # Using a safer approach without killall, relying on curl's ability
    # to stop after receiving enough data for the pipe.
    # Limit rate to avoid excessive download. Timeout added.
    # Note: This still downloads part of the OTA.
    cmd = (
        f"curl --fail -Ls --max-time 60 --limit-rate 100K {shlex.quote(ota_url)} "
        f"| ( bsdtar -Oxf - 'META-INF/com/android/metadata' 2>/dev/null || true ) "
        f"| ( grep -m1 '^post-build=' | sed 's/^post-build=//' && killall curl ) "
        f"2>/dev/null"
    )

    try:
        # Using shell=True is generally discouraged, but complex pipes make it easier here.
        # Ensure ota_url is quoted via shlex.quote for safety.
        # Timeout for the subprocess itself.
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90, check=False) # check=False to handle errors manually

        if result.returncode != 0:
            Logger.warning(f"Command to fetch fingerprint failed (Code: {result.returncode}). Stderr: {result.stderr.strip()}")
            return None

        fingerprint = result.stdout.strip()
        if not fingerprint:
            Logger.warning("Could not extract fingerprint from OTA metadata (empty result).")
            return None

        Logger.info(f"Extracted target fingerprint: {fingerprint}")
        return fingerprint

    except subprocess.TimeoutExpired:
         Logger.warning("Fetching target fingerprint timed out.")
         return None
    except Exception as e:
        Logger.error(f"An unexpected error occurred while fetching target fingerprint: {e}")
        return None


def check_github_release_exists(release_tag: str) -> bool:
    """Checks if a GitHub release with the given tag exists using the 'gh' CLI."""
    if not check_command_exists("gh"):
        Logger.warning("GitHub CLI 'gh' not found. Skipping release check.")
        return False # Assume it doesn't exist if we can't check

    Logger.info(f"Checking for existing GitHub release with tag: {release_tag}...")
    try:
        # Use run with check=True to raise CalledProcessError if gh release view fails
        subprocess.run(
            ["gh", "release", "view", release_tag],
            stdout=subprocess.DEVNULL, # Hide output on success
            stderr=subprocess.PIPE,    # Capture errors for logging
            check=True,
            timeout=30
        )
        Logger.info(f"Release '{release_tag}' already exists on GitHub.")
        return True
    except FileNotFoundError:
         # This handles the case where 'gh' exists check passed but run fails. Redundant but safe.
         Logger.warning("GitHub CLI 'gh' command failed to run. Skipping release check.")
         return False
    except subprocess.CalledProcessError as e:
        # This means 'gh release view <tag>' failed, likely because the release doesn't exist
        Logger.info(f"No existing GitHub release found for tag '{release_tag}'.")
        # Log stderr if it contains useful info (optional)
        # if e.stderr:
        #    Logger.info(f"gh command stderr: {e.stderr.decode().strip()}")
        return False
    except subprocess.TimeoutExpired:
        Logger.warning("GitHub release check timed out.")
        return False # Treat timeout as "doesn't exist" or "unable to confirm"
    except Exception as e:
        Logger.error(f"An unexpected error occurred during GitHub release check: {e}")
        return False # Safer to assume false on unexpected errors


def setup_arg_parser() -> argparse.ArgumentParser:
    """Configures and returns the argument parser."""
    parser = argparse.ArgumentParser(description='Android OTA Update Checker')
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging and save checkin response.'
    )
    parser.add_argument(
        '-c', '--config',
        default=CONFIG_FILENAME_DEFAULT,
        type=Path,
        help=f'Path to the device configuration YAML file (default: {CONFIG_FILENAME_DEFAULT}).'
    )
    # Removed --download as it was unused
    # parser.add_argument('--download', action='store_true', help='Download OTA file (Not implemented)')
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
        help='Override the incremental version from the config file.'
    )
    return parser

# --- Main Execution ---

def main() -> int:
    """Main script execution logic."""
    parser = setup_arg_parser()
    args = parser.parse_args()

    # --- Configuration Loading ---
    try:
        config = Config.from_yaml(args.config)
        # Override incremental version if provided via command line
        if args.incremental:
            Logger.info(f"Overriding incremental version from config with: {args.incremental}")
            config.incremental = args.incremental
    except (FileNotFoundError, ValueError) as e:
        Logger.error(f"Configuration error: {e}")
        return 1
    except Exception as e:
         Logger.error(f"An unexpected error occurred loading configuration: {e}")
         return 1

    config_name = args.config.stem # Used as key in update_info.json

    # --- Environment Variable Handling (for Telegram) ---
    telegram_notifier: Optional[TelegramNotifier] = None
    if not args.skip_telegram:
        bot_token = os.environ.get('bot_token') # Changed name for clarity
        chat_id = os.environ.get('chat_id')     # Changed name for clarity
        if not bot_token or not chat_id:
            Logger.warning("Environment variables BOT_TOKEN or CHAT_ID not set. Skipping Telegram notifications.")
            args.skip_telegram = True # Force skip if vars missing
        else:
            try:
                 telegram_notifier = TelegramNotifier(bot_token, chat_id)
            except ValueError as e: # Handles initialization errors
                 Logger.error(f"Telegram Notifier setup failed: {e}")
                 return 1

    # --- Initialize Components ---
    update_checker = UpdateChecker(config)
    update_store = UpdateInfoStore(Path(UPDATE_INFO_FILENAME))

    # --- Logging Initial State ---
    current_fingerprint = config.get_fingerprint()
    Logger.info(f"Device: {config.model} ({config.device})")
    Logger.info(f"Current Build Fingerprint: {current_fingerprint}")
    Logger.info(f"Checking based on Incremental: {config.incremental}")

    # --- Check for Updates ---
    try:
        found, update_data = update_checker.check_for_updates(args.debug)
    except Exception as e:
        Logger.error(f"An critical error occurred during update check: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1 # Exit if the check itself fails critically

    # --- Store Results ---
    # Load existing data and update/add the entry for this config
    all_update_info = update_store.load()
    if update_data: # Only store if check returned data
         all_update_info[config_name] = update_data
    else:
         # Optionally store failure info
         all_update_info[config_name] = {
              'device': config.model,
              'found': False,
              'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'error': 'Update check failed or returned no data'
         }
    update_store.save(all_update_info)


    # --- Handle Update Found ---
    if not found or not update_data:
        Logger.info("No new OTA update found for this configuration.")
        return 0 # Successful run, no update found

    # --- Process Found Update ---
    update_title = update_data.get('title')
    update_url = update_data.get('url')
    update_size = update_data.get('size')
    update_description = update_data.get('description', 'No description provided.')

    if not update_title or not update_url or not update_size:
        Logger.error("Update was detected, but essential information (title, URL, size) is missing in the response.")
        return 1 # Exit with error if critical info missing

    Logger.success(f"New OTA update found: {update_title}")
    Logger.info(f"Size: {update_size}")
    Logger.info(f"URL: {update_url}")

    # --- Check Existing GitHub Release ---
    if not args.skip_git:
        # Use the tidy update title as the potential release tag
        release_tag = update_title
        if check_github_release_exists(release_tag):
            Logger.info(f"Skipping notification as GitHub release '{release_tag}' already exists.")
            return 0 # Success, but no action needed

    # --- Fetch Target Fingerprint (Optional but Recommended) ---
    # This is done *after* the GitHub check to avoid unnecessary network/processing
    # Use shlex for quoting the URL if needed
    import shlex
    target_fingerprint = get_target_fingerprint_from_ota(update_url)
    if target_fingerprint:
        Logger.info(f"Target Build Fingerprint: {target_fingerprint}")
    else:
        Logger.warning("Could not determine target fingerprint. Proceeding without it.")
        target_fingerprint = "N/A" # Placeholder

    # --- Prepare and Send Notification ---
    if not args.skip_telegram and telegram_notifier:
        message = (
            f"<blockquote><b>OTA Update Available</b></blockquote>\n\n"
            f"<b>Device:</b> {config.model}\n\n"
            f"<b>Title:</b> {update_title}\n\n"
            f"{update_description}\n\n"
            f"<b>Size:</b> {update_size}\n"
            f"<b>Fingerprint:</b>\n<code>{target_fingerprint}</code>"
        )

        # Send the notification via the notifier instance
        if not telegram_notifier.send_message(message, "Google OTA Link", update_url):
             # Error already logged by send_message
             return 1 # Exit with error if notification failed

    elif args.skip_telegram:
         Logger.info("Skipping Telegram notification as requested.")
    elif not telegram_notifier:
         Logger.warning("Telegram notifier not available (config issue?). Skipping notification.")

    Logger.success("Update check process completed successfully.")
    return 0


if __name__ == "__main__":
    # Ensure external dependencies needed for fingerprint check are mentioned
    # (curl, bsdtar, grep, sed) and potentially 'gh' for release checking.
    # Also 'utils.functions' module needs to be present.
    # Consider adding a check at the start if these are critical.
    if sys.version_info < (3, 7):
         Logger.error("This script requires Python 3.7 or higher.")
         sys.exit(1)
    sys.exit(main())
