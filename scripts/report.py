#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')
ROOT = Path('/opt/clawd-workspace/skills/public/chip-chatgpt-usage')
STATE_FILE = ROOT / 'state' / 'source.json'


@dataclass
class UsageSnapshot:
    source: str
    fetched_at: datetime
    five_left: float
    five_reset_seconds: int
    week_left: float
    week_reset_seconds: int


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def load_source() -> UsageSnapshot:
    override = os.getenv('CHATGPT_USAGE_SOURCE_JSON')
    if override:
        data = json.loads(override)
    else:
        if not STATE_FILE.exists():
            raise FileNotFoundError(f'missing source file: {STATE_FILE}')
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))

    source = str(data.get('source') or '').strip() or 'unknown'
    if source in {'session_status', 'openclaw_status', 'runtime_usage'}:
        raise ValueError('forbidden source: session_status/runtime_usage is not a valid ChatGPT Pro source-of-truth')

    try:
        fetched_at = datetime.fromisoformat(str(data['fetched_at']).replace('Z', '+00:00'))
    except Exception as e:
        raise ValueError(f'invalid fetched_at: {e}')

    try:
        five = data['five_hour']
        week = data['week']
        five_left = clamp01(float(five['left_ratio']))
        five_reset_seconds = int(five['reset_in_seconds'])
        week_left = clamp01(float(week['left_ratio']))
        week_reset_seconds = int(week['reset_in_seconds'])
    except Exception as e:
        raise ValueError(f'invalid source schema: {e}')

    if not (0 <= five_reset_seconds <= 7 * 24 * 3600 and 0 <= week_reset_seconds <= 14 * 24 * 3600):
        raise ValueError('reset seconds out of range')

    return UsageSnapshot(
        source=source,
        fetched_at=fetched_at,
        five_left=five_left,
        five_reset_seconds=five_reset_seconds,
        week_left=week_left,
        week_reset_seconds=week_reset_seconds,
    )


def fmt_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    mins = seconds // 60
    days, rem_m = divmod(mins, 1440)
    hours, mins = divmod(rem_m, 60)
    parts = []
    if days:
        parts.append(f'{days}д')
    if hours:
        parts.append(f'{hours}ч')
    if mins and not days:
        parts.append(f'{mins}м')
    return ' '.join(parts) if parts else '0м'


def fmt_percent(left_ratio: float) -> int:
    return int(round(left_ratio * 100))


def forecast(snapshot: UsageSnapshot) -> dict[str, Any]:
    week_window_seconds = 7 * 24 * 3600
    elapsed = max(0, week_window_seconds - snapshot.week_reset_seconds)
    used = clamp01(1.0 - snapshot.week_left)
    if elapsed <= 0 or used <= 0:
        return {'kind': 'no-risk', 'eta': None}
    rate = used / elapsed
    if rate <= 0:
        return {'kind': 'no-risk', 'eta': None}
    eta_seconds = int(snapshot.week_left / rate)
    reset_dt = snapshot.fetched_at.astimezone(MSK) + timedelta(seconds=snapshot.week_reset_seconds)
    exhaust_dt = snapshot.fetched_at.astimezone(MSK) + timedelta(seconds=eta_seconds)
    return {
        'kind': 'risk' if eta_seconds <= snapshot.week_reset_seconds else 'safe',
        'eta': eta_seconds,
        'reset_dt': reset_dt,
        'exhaust_dt': exhaust_dt,
        'rate': rate,
        'elapsed': elapsed,
        'used': used,
    }


def render_telegram(snapshot: UsageSnapshot) -> str:
    five_reset = fmt_duration(snapshot.five_reset_seconds)
    week_reset = fmt_duration(snapshot.week_reset_seconds)
    week_reset_dt = (snapshot.fetched_at.astimezone(MSK) + timedelta(seconds=snapshot.week_reset_seconds)).strftime('%d.%m.%Y %H:%M МСК')
    fc = forecast(snapshot)

    lines = [
        '📊 ChatGPT Pro подписка: Отчёт по использованию',
        f'• ⏱️ 5h window: осталось {fmt_percent(snapshot.five_left)}% (≈{five_reset})',
        f'• 📆 Week window: осталось {fmt_percent(snapshot.week_left)}%, до сброса ≈{week_reset} ({week_reset_dt})',
    ]
    if fc['kind'] == 'no-risk':
        lines += [
            '',
            '🔮 Прогноз по неделе:',
            '• Темп пока слишком мал для надёжной оценки исчерпания.',
            '',
            '⚠️ Вывод: По текущему темпу лимит не закончится до сброса',
        ]
    else:
        exhaust_dt = fc['exhaust_dt'].strftime('%d.%m.%Y %H:%M МСК')
        lines += [
            '',
            '🔮 Прогноз по неделе:',
            f'• ориентировочное исчерпание: {exhaust_dt}',
            '',
        ]
        if fc['kind'] == 'risk':
            gap = snapshot.week_reset_seconds - fc['eta']
            lines.append(f'⚠️ Вывод: с высокой вероятностью недельный лимит закончится до сброса; разрыв ≈{fmt_duration(gap)}')
        else:
            lines.append('⚠️ Вывод: По текущему темпу лимит не закончится до сброса')
    return '\n'.join(lines)


def render_json(snapshot: UsageSnapshot) -> str:
    fc = forecast(snapshot)
    return json.dumps({
        'source': snapshot.source,
        'fetched_at': snapshot.fetched_at.isoformat(),
        'five_hour_left_ratio': snapshot.five_left,
        'five_hour_reset_in_seconds': snapshot.five_reset_seconds,
        'week_left_ratio': snapshot.week_left,
        'week_reset_in_seconds': snapshot.week_reset_seconds,
        'forecast': {
            'kind': fc.get('kind'),
            'eta_seconds': fc.get('eta'),
            'exhaust_at_msk': fc.get('exhaust_dt').isoformat() if fc.get('exhaust_dt') else None,
            'reset_at_msk': fc.get('reset_dt').isoformat() if fc.get('reset_dt') else None,
        },
    }, ensure_ascii=False, indent=2)


def cmd_init_example(args) -> int:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    example = {
        'source': 'manual_chatgpt_ui_capture',
        'fetched_at': datetime.now(tz=MSK).astimezone().isoformat(),
        'five_hour': {'left_ratio': 0.92, 'reset_in_seconds': 7200},
        'week': {'left_ratio': 0.77, 'reset_in_seconds': 380000},
    }
    if STATE_FILE.exists() and not args.force:
        print(f'EXISTS {STATE_FILE}')
        return 0
    STATE_FILE.write_text(json.dumps(example, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'WROTE {STATE_FILE}')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    p_init = sub.add_parser('init-example')
    p_init.add_argument('--force', action='store_true')
    p_init.set_defaults(func=cmd_init_example)
    ap.add_argument('--format', choices=['telegram', 'json'], default='telegram')
    args = ap.parse_args()

    if getattr(args, 'func', None):
        return args.func(args)

    try:
        snapshot = load_source()
    except FileNotFoundError:
        print('NO_REPLY')
        return 0
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2

    if args.format == 'json':
        print(render_json(snapshot))
    else:
        print(render_telegram(snapshot))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
