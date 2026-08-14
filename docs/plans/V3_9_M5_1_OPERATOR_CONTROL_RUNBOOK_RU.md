# v3.9 M5.1 — Operator Risk Control Runbook

Статус: опубликовано в draft PR #44 из ветки
`agent/v3-9-beta1-recovery-ux`; замечания финального review исправлены,
merge не выполнялся.

## Граница этапа

`tools/v3_9_risk_control.py` переиспользует единственные persisted
`RiskProfileStore` и `RiskStateStore`. Новый policy/state ledger не создаётся.

CLI:

- не рассчитывает Portfolio Risk независимо от domain services;
- не создаёт Strategy proposal или Central intent;
- не выполняет arming, provider POST, dispatch или resubmit;
- не изменяет runtime для `inspect`, `explain` и `review-policy`;
- маскирует Account ID в success/error JSON и выводит только его SHA-256 и
  последние четыре символа;
- изменяет только `risk_state.json` под существующим inter-process lock для
  явно подтверждённых kill-switch transitions.

Опция `--output` создаёт только явно запрошенный оператором JSON-отчёт и не
считается runtime mutation.

## Read-only inspection

Из каталога `current/`:

```powershell
python tools/v3_9_risk_control.py inspect `
  --runtime-dir <runtime> `
  --account-id <account-id>

python tools/v3_9_risk_control.py explain `
  --runtime-dir <runtime> `
  --account-id <account-id>

python tools/v3_9_risk_control.py review-policy `
  --runtime-dir <runtime> `
  --account-id <account-id>
```

`increase_admission` имеет три состояния:

- `READY` — известных persistent blockers нет;
- `CONDITIONAL` — активен instrument halt, поэтому заблокированы только
  перечисленные `blocked_instrument_ids`;
- `BLOCKED` — отсутствует/не готов policy, нарушен account scope, активен
  global halt либо требуется Risk resync.

Для `SANDBOX_EXECUTION` статус `READY` требует именно `ENFORCED` policy и
непустой совпадающий `account_scope`; `READY/OBSERVE_ONLY` остаётся блокирующим
operator status. Введённый `instrument_id` нормализуется в uppercase до
проверки confirmation и сохранения halt.

Повреждённый `risk_profiles.json` или `risk_state.json` не заменяется default
state: команда завершается fail-closed с exit code `1`. Ошибка выдаётся как
sanitized JSON в `stderr`, без traceback и raw Account ID.

## Global kill switch

Включение требует точную фразу `ENGAGE GLOBAL RISK HALT`:

```powershell
python tools/v3_9_risk_control.py engage-global `
  --runtime-dir <runtime> `
  --account-id <account-id> `
  --reason "operator reason" `
  --operator-ref "ticket-or-session" `
  --confirm "ENGAGE GLOBAL RISK HALT"
```

Снятие требует отдельную фразу `CLEAR RISK HALT`:

```powershell
python tools/v3_9_risk_control.py clear-global `
  --runtime-dir <runtime> `
  --account-id <account-id> `
  --confirm "CLEAR RISK HALT"
```

## Instrument kill switch

Confirmation привязан к конкретному `instrument_id`:

```powershell
python tools/v3_9_risk_control.py engage-instrument `
  --runtime-dir <runtime> `
  --account-id <account-id> `
  --instrument-id SBER `
  --reason "operator reason" `
  --confirm "ENGAGE INSTRUMENT RISK HALT SBER"

python tools/v3_9_risk_control.py clear-instrument `
  --runtime-dir <runtime> `
  --account-id <account-id> `
  --instrument-id SBER `
  --confirm "CLEAR INSTRUMENT RISK HALT SBER"
```

Фраза с другим инструментом отклоняется до записи. Clear kill switch не снимает
независимый `risk_resync_required`; external cash baseline resync относится к
отдельному M5.2 gate.

## Acceptance M5.1

Автоматический gate должен доказать:

1. три read-only команды byte-identical для runtime stores;
2. missing/corrupt policy/state остаётся fail-closed;
3. неправильная confirmation-фраза не создаёт `risk_state.json` и не меняет
   существующий state;
4. global/instrument engage и clear атомарны и переживают повторное чтение;
5. raw Account ID отсутствует в success/error output, включая `stderr`;
6. proposal/intent/dispatch/provider POST остаются `false`;
7. corrupt policy обнаруживается до mutation, поэтому оператор не получает
   ошибку после частично выполненной команды;
8. полный regression и Ruff проходят.

EventJournal integration, sanitized support bundle и standalone coverage входят
в M5.3 и не объявляются завершёнными этим runbook.

Финальный automated gate: targeted `7 passed`, full regression `728 passed`,
pip check, critical/strict Ruff, compileall и `git diff --check` — PASS.
