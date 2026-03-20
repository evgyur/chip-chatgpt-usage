---
name: chip-chatgpt-usage
description: "Публичный скилл: script-first мониторинг ChatGPT Pro usage (5h/Week) без модельных догадок; отчёт строится только скриптом из source snapshot."
metadata:
  clawdbot:
    emoji: 📊
    command: /chatgpt-usage
---

# chip-chatgpt-usage

Это **script-first** skill. Модель не должна придумывать usage-report из общих рассуждений, `session_status`, браузера или других эвристик.

## Root cause, который этот skill закрывает

Старая версия skill была **docs-only**:
- cron запускал agentTurn с текстовым prompt;
- модель сама решала, откуда брать данные;
- иногда подставляла `session_status` OpenClaw;
- иногда начинала искать браузер/UI;
- в итоге отчёты были непредсказуемыми и могли противоречить факту.

Правильный контракт:
- **один скрипт** строит отчёт;
- **один source snapshot** даёт входные данные;
- если source snapshot отсутствует или невалиден → **`NO_REPLY`**;
- `session_status` запрещён как source-of-truth.

## Что входит в skill

- `scripts/report.py` — единственный генератор отчёта
- `state/source.schema.example.json` — пример source snapshot schema
- `cron-job.example.json` — безопасный cron prompt, который требует запускать скрипт, а не сочинять отчёт

## Source contract

Скрипт ждёт JSON snapshot с полями:

```json
{
  "source": "manual_chatgpt_ui_capture",
  "fetched_at": "2026-03-20T10:00:00+03:00",
  "five_hour": {
    "left_ratio": 0.92,
    "reset_in_seconds": 7200
  },
  "week": {
    "left_ratio": 0.77,
    "reset_in_seconds": 380000
  }
}
```

### Важно
- `source` должен быть **реальным** и отличным от `session_status` / `runtime_usage`.
- Скрипт **жёстко отвергает** `session_status`, `openclaw_status`, `runtime_usage`.
- Snapshot может быть записан любым внешним extractor’ом, но этот skill **не позволяет модели подменять extractor своей фантазией**.

## Запуск

```bash
python3 /opt/clawd-workspace/skills/public/chip-chatgpt-usage/scripts/report.py --format telegram
```

Если source snapshot отсутствует:
- скрипт печатает `NO_REPLY`

Если source snapshot невалиден:
- скрипт падает с ошибкой

## Quick bootstrap

Создать пример snapshot:

```bash
python3 /opt/clawd-workspace/skills/public/chip-chatgpt-usage/scripts/report.py init-example --force
```

Потом отредактировать `state/source.json` под реальный source.

## Cron rule

Cron не должен просить модель “сделай usage report”.
Он должен требовать:
1. запустить `report.py`;
2. вернуть **строго stdout скрипта**;
3. если stdout=`NO_REPLY`, ответить `NO_REPLY`;
4. ничего не дописывать от себя.

## Absolute rules

- Нельзя использовать `session_status` как source-of-truth.
- Нельзя подставлять synthetic numbers.
- Нельзя идти в браузер, если skill запущен как script-first reporting pipeline.
- Нельзя “объяснять, почему не получилось” вместо выполнения контракта; нужен stdout скрипта.
