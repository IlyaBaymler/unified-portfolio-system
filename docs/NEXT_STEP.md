# Следующий шаг

Дата обновления: 2026-08-11.

Функциональный acceptance `v3.7-beta1 / 0.3.7b1` пройден. Source, tests,
manifest и sanitized evidence подготовлены для Issue #33 и ветки
`v3-7-beta1`.

## Параллельные контрольные действия beta1

### 1. GitHub handoff

- опубликовать fast-forward/merge history в `v3-7-beta1` без force-push;
- открыть draft PR к `main`;
- использовать `6e4f7da..v3-7-beta1` как содержательный beta1 diff;
- выполнить GitHub/ChatGPT review evidence package;
- закрыть Issue #33 после подтверждения доступности source/tests/evidence.

### 2. Расширенный Sandbox burn-in

1. Использовать standalone-пакет с единым sibling `runtime`.
2. Выполнить 12–24 часа наблюдения и 2–4 контролируемых BUY→HOLD→SELL.
3. Проверить intentional disconnect и восстановление до fresh `MATCHED`.
4. Проверить restart с открытой позицией без повторного broker POST.
5. Проверить `OPEN → MARKET_IDLE → OPEN` и отклонение stale decision.
6. Просмотреть trading events, Risk Burn-in report и support bundle.

## Beta release gate

```text
0 duplicate submit
0 fill without canonical reconciliation
0 execution without Risk accounting
0 unresolved pending/uncertain execution
0 stale blocking warnings in READY/MATCHED
0 secret/runtime files in release archive
real account disabled
multi-instrument execution absent
```

После Issue #33 и пользовательского burn-in review принимается решение по
Issues #31/#32. Затем начинается Issue #34 / `v3.7.0 Stable` — отдельный
release-qualification этап без новых торговых функций.
