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

try:
    import requests
    import yaml
    from google.protobuf import text_format
    from checkin import checkin_generator_pb2
    from utils import functions
except ImportError as e:
    print(f"Error: Missing required library. {e}", file=sys.stderr)
    sys.exit(1)

CHECKIN_URL = 'https://android.googleapis.com/checkin'
USER_AGENT_TPL = 'Dalvik/2.1.0 (Linux; U; Android {0}; {1} Build/{2})'
PROTO_TYPE = 'application/x-protobuffer'
UPDATE_FILE = "update_info.json"
DEBUG_FILE = "debug_checkin_response.txt"

class Log:
    @staticmethod
    def i(m): print(f"\033[94m=>\033[0m {m}")
    @staticmethod
    def s(m): print(f"\033[92m✓\033[0m {m}")
    @staticmethod
    def e(m): print(f"\033[91m✗\033[0m {m}", file=sys.stderr)
    @staticmethod
    def w(m): print(f"\033[93m!\033[0m {m}", file=sys.stderr)

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
    def from_yaml(cls, file: Path) -> 'Config':
        if not file.is_file():
            raise FileNotFoundError(f"Config file not found: {file}")
        
        try:
            with open(file, 'r') as f:
                data = yaml.safe_load(f)
                
            if not isinstance(data, dict):
                raise ValueError("Config file content is not a valid dictionary.")
                
            return cls(**data)
            
        except yaml.YAMLError as e:
            raise ValueError(f"Error parsing YAML config: {e}")
        except TypeError as e:
            raise ValueError(f"Missing or invalid keys in config: {e}")

    def fingerprint(self) -> str:
        return (f'{self.oem}/{self.product}/{self.device}:'
                f'{self.android_version}/{self.build_tag}/'
                f'{self.incremental}:user/release-keys')

class TgNotify:
    MAX_LEN = 4000
    
    def __init__(self, token: str, chat_id: str, proxies: Optional[Dict] = None):
        if not token or not chat_id:
            raise ValueError("Bot token and chat ID required")
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}"
        # Removed proxies from Telegram notifications
        self.proxies = None

    def _split(self, msg: str) -> List[str]:
        if len(msg) <= self.MAX_LEN:
            return [msg]
        
        parts = []
        curr = ""
        blocks = msg.split('\n\n')
        
        for block in blocks:
            if len(curr + block + '\n\n') > self.MAX_LEN:
                if curr:
                    parts.append(curr.rstrip())
                curr = block + '\n\n'
            else:
                curr += block + '\n\n'
        
        if curr:
            parts.append(curr.rstrip())
        
        proc_parts = []
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
            
            proc_parts.append(part)
        
        return proc_parts

    def send(self, msg: str, btn_text: Optional[str] = None, 
             btn_url: Optional[str] = None) -> bool:
        Log.i("Sending Telegram notification...")
        parts = self._split(msg)
        
        try:
            for i, part in enumerate(parts):
                payload = {
                    'chat_id': self.chat_id,
                    'text': part,
                    'parse_mode': 'html',
                    'disable_web_page_preview': True,
                }
                
                if i == len(parts) - 1 and btn_text and btn_url:
                    payload['reply_markup'] = {
                        'inline_keyboard': [[
                            {'text': btn_text, 'url': btn_url}
                        ]]
                    }

                r = requests.post(
                    f"{self.url}/sendMessage",
                    json=payload,
                    # Removed proxies here
                    proxies=None,
                    timeout=15
                )
                r.raise_for_status()
                
                if i < len(parts) - 1:
                    time.sleep(0.5)
            
            Log.s("Notification sent successfully")
            return True
            
        except Exception as e:
            Log.e(f"Failed to send notification: {e}")
            return False

class UpdateChecker:
    def __init__(self, cfg: Config, proxies: Optional[Dict] = None):
        self.cfg = cfg
        self.ua = USER_AGENT_TPL.format(
            cfg.android_version, cfg.model, cfg.build_tag
        )
        self.headers = {
            'accept-encoding': 'gzip, deflate',
            'content-encoding': 'gzip',
            'content-type': PROTO_TYPE,
            'user-agent': self.ua
        }
        # Keep proxies for update checking
        self.proxies = proxies

    def _build_request(self) -> bytes:
        payload = checkin_generator_pb2.AndroidCheckinRequest()
        build = checkin_generator_pb2.AndroidBuildProto()
        checkin = checkin_generator_pb2.AndroidCheckinProto()

        build.id = self.cfg.fingerprint()
        build.timestamp = 0
        build.device = self.cfg.device

        checkin.build.CopyFrom(build)
        checkin.roaming = "WIFI::"
        checkin.userNumber = 0
        checkin.deviceType = 2
        checkin.voiceCapable = False

        try:
            payload.imei = functions.generateImei()
            payload.id = 0
            payload.digest = functions.generateDigest()
            payload.checkin.CopyFrom(checkin)
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
            raise ImportError(f"Required function missing: {e}")

        return gzip.compress(payload.SerializeToString())

    def check(self, debug: bool = False) -> Tuple[bool, Optional[Dict]]:
        Log.i("Checking for updates...")
        
        try:
            data = self._build_request()
            
            r = requests.post(
                CHECKIN_URL,
                data=data,
                headers=self.headers,
                # Keep proxies for update check
                proxies=self.proxies,
                timeout=10
            )
            r.raise_for_status()

            resp = checkin_generator_pb2.AndroidCheckinResponse()
            resp.ParseFromString(r.content)
            
            if debug:
                Path(DEBUG_FILE).write_text(text_format.MessageToString(resp))
                Log.i(f"Debug response saved to {DEBUG_FILE}")

            info = self._parse(resp)
            has_update = info.get('found', False) and 'url' in info

            return has_update, info
            
        except Exception as e:
            Log.e(f"Update check failed: {e}")
            if debug and 'r' in locals():
                Path(DEBUG_FILE.replace(".txt", "_error.bin")).write_bytes(r.content)
                Log.i(f"Raw error response saved")
            return False, None

    def _parse(self, resp: checkin_generator_pb2.AndroidCheckinResponse) -> Dict:
        info = {
            'device': self.cfg.model,
            'found': False,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'title': None,
            'description': None,
            'size': None,
            'url': None
        }

        for entry in resp.setting:
            try:
                if entry.name == b'update_url' or b'https://android.googleapis.com/packages/ota' in entry.value:
                    info['url'] = entry.value.decode('utf-8')
                    info['found'] = True
                    break
            except:
                continue

        if info['found']:
            for entry in resp.setting:
                try:
                    name = entry.name.decode('utf-8')
                    value = entry.value.decode('utf-8')

                    if name == 'update_title':
                        info['title'] = value.strip()
                    elif name == 'update_description':
                        info['description'] = self._clean_desc(value)
                    elif name == 'update_size':
                        info['size'] = value
                except:
                    continue

        return info

    @staticmethod
    def _clean_desc(text: str) -> str:
        text = re.sub(r'\n', '', text)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]*>', '', text)
        text = re.sub(r'\s*\(http[s]?://\S+\)?', '', text)
        return text.strip()

class InfoStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Dict:
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    content = f.read()
                    if not content.strip():
                        return {}
                    return json.loads(content)
            except Exception as e:
                Log.e(f"Error reading {self.path}: {e}")
                return {}
        return {}

    def save(self, data: Dict):
        try:
            with open(self.path, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            Log.e(f"Error writing to {self.path}: {e}")

def check_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def check_cmds(cmds: List[str]) -> bool:
    missing = [cmd for cmd in cmds if not check_cmd(cmd)]
    if missing:
        Log.e(f"Missing required command(s): {', '.join(missing)}")
        return False
    return True

def get_fingerprint(url: str, proxy: Optional[str] = None) -> Optional[str]:
    Log.i("Fetching target fingerprint...")
    cmds = ['curl', 'bsdtar', 'grep', 'sed']
    if not check_cmds(cmds):
        return None

    # Remove proxy for fingerprint fetching
    proxy_opt = ""
    
    cmd = (
        f"curl --fail -Ls{proxy_opt} --max-time 60 --limit-rate 100K {shlex.quote(url)} "
        f"| ( bsdtar -Oxf - 'META-INF/com/android/metadata' 2>/dev/null || true ) "
        f"| ( grep -m1 '^post-build=' | sed 's/^post-build=//' && killall curl ) "
        f"2>/dev/null"
    )

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=90, check=False)

        if result.returncode != 0 or not result.stdout.strip():
            Log.w("Could not extract fingerprint")
            return None

        fp = result.stdout.strip()
        Log.i(f"Extracted fingerprint: {fp}")
        return fp

    except Exception as e:
        Log.e(f"Error fetching fingerprint: {e}")
        return None

def check_release(tag: str) -> bool:
    if not check_cmd("gh"):
        Log.e("GitHub CLI 'gh' not found")
        sys.exit(1)

    Log.i(f"Checking release tag: {tag}...")
    try:
        subprocess.run(
            ["gh", "release", "view", tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=30
        )
        Log.i(f"Release '{tag}' exists")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        Log.i(f"Release '{tag}' not found")
        return False
    except Exception as e:
        Log.e(f"GitHub check error: {e}")
        return False

def setup_proxy(proxy_url: Optional[str] = None) -> Dict:
    """Set up proxy configuration for requests."""
    if not proxy_url:
        return {}
    
    Log.i(f"Using proxy for update checks: {proxy_url}")
    
    # Format: protocol://[user:pass@]host:port
    if proxy_url.startswith(('socks5://', 'socks4://', 'socks://')):
        return {'http': proxy_url, 'https': proxy_url}
    else:
        # Handle http/https proxies
        if not proxy_url.startswith(('http://', 'https://')):
            proxy_url = f"http://{proxy_url}"
        return {'http': proxy_url, 'https': proxy_url}

def main() -> int:
    if sys.version_info < (3, 7):
        Log.e("Requires Python 3.7+")
        return 1

    parser = argparse.ArgumentParser(description='Android OTA Update Checker')
    parser.add_argument('--debug', action='store_true', help='Enable debugging')
    parser.add_argument('-c', '--config', type=Path, required=True, help='Config file path')
    parser.add_argument('--skip-telegram', action='store_true', help='Skip Telegram')
    parser.add_argument('--skip-git', action='store_true', help='Skip GitHub check')
    parser.add_argument('-i', '--incremental', help='Override incremental version')
    parser.add_argument('--proxy', help='Proxy URL for update checks (e.g., socks5://127.0.0.1:1080 or http://user:pass@host:port)')
    args = parser.parse_args()

    # Setup proxy only for update checking
    proxies = setup_proxy(args.proxy)
    
    try:
        cfg = Config.from_yaml(args.config)
        if args.incremental:
            Log.i(f"Override incremental: {args.incremental}")
            cfg.incremental = args.incremental
    except Exception as e:
        Log.e(f"Config error: {e}")
        return 1

    config_name = args.config.stem

    tg = None
    if not args.skip_telegram:
        token = os.environ.get('bot_token')
        chat = os.environ.get('chat_id')
        if not token or not chat:
            Log.w("Telegram env vars not set")
            args.skip_telegram = True
        else:
            try:
                # Initialize TgNotify without proxies
                tg = TgNotify(token, chat, None)
            except ValueError as e:
                Log.e(f"Telegram setup failed: {e}")
                args.skip_telegram = True

    # Use proxies only for the update checker
    checker = UpdateChecker(cfg, proxies)
    store = InfoStore(Path(UPDATE_FILE))

    fp = cfg.fingerprint()
    Log.i(f"Device: {cfg.model} ({cfg.device})")
    Log.i(f"Build: {fp}")

    found, data = checker.check(args.debug)

    all_info = store.load()
    if data:
        all_info[config_name] = data
    else:
        all_info[config_name] = {
            'device': cfg.model,
            'found': False,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'error': 'Check failed'
        }
    store.save(all_info)

    if not found or not data:
        Log.i("No updates found")
        return 0

    title = data.get('title')
    url = data.get('url')
    size = data.get('size')
    desc = data.get('description', 'No description')

    if not all([title, url, size]):
        Log.e("Missing essential info")
        return 1

    Log.s(f"New OTA update: {title}")
    Log.i(f"Size: {size}")
    Log.i(f"URL: {url}")

    if not args.skip_git:
        if check_release(title):
            Log.i("GitHub release exists, skipping notification")
            return 0

    # Get fingerprint without using proxy
    target_fp = get_fingerprint(url, None)
    if target_fp != "N/A":
        Log.i(f"Target build: {target_fp}")

    if not args.skip_telegram and tg:
        msg = (
            f"<blockquote><b>OTA Update Available</b></blockquote>\n\n"
            f"<b>Device:</b> {cfg.model}\n\n"
            f"<b>Title:</b> {title}\n\n"
            f"{desc}\n\n"
            f"<b>Size:</b> {size}\n"
            f"<b>Fingerprint:</b>\n<code>{target_fp}</code>"
        )

        if not tg.send(msg, "Google OTA Link", url):
            return 1

    Log.s("Update check completed")
    return 0

if __name__ == "__main__":
    sys.exit(main())
