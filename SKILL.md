---
name: chip-chatgpt-usage
description: "Публичный скилл: мониторинг остатков ChatGPT Pro (5h/Week) с прогнозом исчерпания недельного лимита и авто-отправкой в Telegram DM через cron."
metadata:
  clawdbot:
    emoji: 📊
    command: /chatgpt-usage
---

# chip-chatgpt-usage

Публичный скилл для OpenClaw: регулярный отчёт по Usage (5h/Week) + прогноз, когда закончится недельный лимит при текущем темпе.

## Что делает

- Берёт данные из `session_status` (те же, что показывает `/status`).
- Формирует отчёт в профессиональном формате с эмодзи.
- Считает линейный прогноз исчерпания Week-window:
  - `elapsed = 7d - time_to_week_reset`
  - `used = 1 - week_left`
  - `rate = used / elapsed`
  - `eta_exhaust = week_left / rate`
- Отправляет в личку по расписанию (`cron`, isolated agentTurn).

## Шаблон отчёта

```text
📊 ChatGPT Pro подписка: Отчёт по использованию
- ⏱️ 5h window: осталось X% (≈Y)
- 📆 Week window: осталось X%, до сброса ≈Y (дата/время МСК)

🔮 Прогноз по неделе (линейная модель):
- ориентировочное исчерпание: DD.MM.YYYY HH:MM МСК

⚠️ Вывод:
- закончится до сброса / не закончится до сброса
```

## Быстрый запуск (каждые 3 часа)

Используй `cron add` с payload kind `agentTurn` и delivery в Telegram DM.

Готовый пример — в файле `cron-job.example.json`.

## Важно

- Скилл **не хранит секреты**.
- Источник данных — runtime usage метрики OpenClaw/провайдера модели.
- Прогноз — приближённый (линейная модель), для операционного контроля, не финансовый SLA.
