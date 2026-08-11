# Следующий шаг

Функциональный acceptance `v3.7-beta1 / 0.3.7b1` пройден. Следующий шаг —
расширенный Sandbox burn-in и подготовка beta1 к публикации без расширения
торгового scope.

## Обязательная burn-in сессия

1. Использовать пересобранный standalone-пакет с единым sibling `runtime`.
2. Выполнить 12–24 часа наблюдения и 2–4 контролируемых BUY→HOLD→SELL.
3. Проверить intentional disconnect и восстановление до fresh `MATCHED`.
4. Проверить restart с открытой позицией без повторного broker POST.
5. Проверить `OPEN → MARKET_IDLE → OPEN` и отклонение stale decision.
6. Просмотреть trading events, Risk Burn-in report и support bundle.

## Release gate

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

После прохождения gate: опубликовать beta1 branch/PR и source archive, затем
перейти к clean install, upgrade/rollback и длительному acceptance
`v3.7.0 Stable`.
