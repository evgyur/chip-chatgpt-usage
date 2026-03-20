#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')
ROOT = Path('/opt/clawd-workspace/skills/public/chip-chatgpt-usage')
STATE_DIR = ROOT / 'state'
STATE_FILE = STATE_DIR / 'source.json'
INBOX_FILE = STATE_DIR / 'source.inbox.json'
AUTH_FILE = Path('/home/chip/.openclaw/agents/chipdm/agent/auth.json')
AUTH_PROFILES_FILE = Path('/home/chip/.openclaw/agents/chipdm/agent/auth-profiles.json')
FORBIDDEN_SOURCES = {'session_status', 'openclaw_status', 'runtime_usage'}
WHAM_URL = 'https://chatgpt.com/backend-api/wham/usage'


def decode_jwt_payload(token: str) -> dict:
    parts = token.split('.')
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += '=' * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
    except Exception:
        return {}


def normalize_payload(data: dict) -> dict:
    source = str(data.get('source') or '').strip()
    if not source:
        raise ValueError('missing source')
    if source in FORBIDDEN_SOURCES:
        raise ValueError(f'forbidden source: {source}')

    fetched_at = str(data.get('fetched_at') or '').strip()
    if not fetched_at:
        fetched_at = datetime.now(tz=MSK).astimezone().isoformat()

    five = data.get('five_hour') or {}
    week = data.get('week') or {}

    out = {
        'source': source,
        'fetched_at': fetched_at,
        'five_hour': {
            'left_ratio': float(five['left_ratio']),
            'reset_in_seconds': int(five['reset_in_seconds']),
        },
        'week': {
            'left_ratio': float(week['left_ratio']),
            'reset_in_seconds': int(week['reset_in_seconds']),
        },
    }
    return out


def load_candidate(args) -> dict:
    if args.json:
        return json.loads(args.json)
    if os.getenv('CHATGPT_USAGE_SOURCE_JSON'):
        return json.loads(os.environ['CHATGPT_USAGE_SOURCE_JSON'])
    path = Path(args.from_file) if args.from_file else INBOX_FILE
    if not path.exists():
        raise FileNotFoundError(f'missing source candidate: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_openai_oauth() -> tuple[str, str | None]:
    if AUTH_PROFILES_FILE.exists():
        store = json.loads(AUTH_PROFILES_FILE.read_text(encoding='utf-8'))
        last_good = ((store.get('lastGood') or {}).get('openai-codex'))
        profiles = store.get('profiles') or {}
        profile = profiles.get(last_good) if last_good else None
        if not profile:
            for p in profiles.values():
                if (p or {}).get('provider') == 'openai-codex' and (p or {}).get('type') == 'oauth':
                    profile = p
                    break
        if profile and profile.get('access'):
            return profile['access'], profile.get('accountId')
    if AUTH_FILE.exists():
        auth = json.loads(AUTH_FILE.read_text(encoding='utf-8'))
        oauth = auth.get('openai-codex') or {}
        access = oauth.get('access')
        if access:
            claims = decode_jwt_payload(access)
            account_id = ((claims.get('https://api.openai.com/auth') or {}).get('chatgpt_account_id'))
            return access, account_id
    raise FileNotFoundError('missing openai-codex oauth token')


def fetch_wham() -> dict:
    access, account_id = resolve_openai_oauth()
    headers = {
        'Authorization': f'Bearer {access}',
        'User-Agent': 'CodexBar',
        'Accept': 'application/json',
    }
    if account_id:
        headers['ChatGPT-Account-Id'] = account_id
    req = Request(WHAM_URL, headers=headers)
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    now = int(datetime.now(timezone.utc).timestamp())
    rl = data.get('rate_limit') or {}
    primary = rl.get('primary_window') or {}
    secondary = rl.get('secondary_window') or {}
    return {
        'source': 'openclaw_wham_usage_endpoint',
        'fetched_at': datetime.now(tz=MSK).astimezone().isoformat(),
        'five_hour': {
            'left_ratio': max(0.0, min(1.0, 1.0 - float(primary.get('used_percent', 0)) / 100.0)),
            'reset_in_seconds': max(0, int(primary.get('reset_at', now)) - now),
        },
        'week': {
            'left_ratio': max(0.0, min(1.0, 1.0 - float(secondary.get('used_percent', 0)) / 100.0)),
            'reset_in_seconds': max(0, int(secondary.get('reset_at', now)) - now),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--from-file')
    ap.add_argument('--json')
    ap.add_argument('--from-wham', action='store_true', default=True)
    ap.add_argument('--print-path', action='store_true')
    args = ap.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if args.json or os.getenv('CHATGPT_USAGE_SOURCE_JSON') or args.from_file:
            candidate = load_candidate(args)
        else:
            candidate = fetch_wham()
        normalized = normalize_payload(candidate)
    except FileNotFoundError:
        print('NO_SOURCE')
        return 0
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2

    tmp = STATE_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(STATE_FILE)
    if args.print_path:
        print(str(STATE_FILE))
    else:
        print('UPDATED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
