# Recovery Runbook v3.7-alpha3

## 1. Перед любым восстановлением

1. Остановить execution.
2. Не повторять заявку вручную.
3. Получить свежий broker snapshot.
4. Проверить `trading_events.db`, pending/uncertain order и canonical revision.
5. Создать backup текущего runtime.

## 2. Schema 1 после обновления

Программа не мигрирует автоматически.

```bat
run_portfolio_cutover.bat preview --account-id <ID>
```

При `allowed=true`:

```bat
run_portfolio_cutover.bat cutover --account-id <ID> --confirmation "CUTOVER PORTFOLIO STATE 2"
```

При `BLOCKED` устранить причину; JSON вручную не редактировать.

## 3. Crash по фазам

- `TARGET_PROPOSED`: переоценить target, POST не повторять;
- `ORDER_SUBMITTED`: broker lookup по прежнему request ID;
- `FILLED`: выполнить broker + canonical reconciliation;
- `CANONICAL_RECONCILED`: выполнить только execution record и Risk accounting;
- `RISK_ACCOUNTED`: очистить завершённые маркеры.

## 4. Revision conflict

`REVISION_CONFLICT` означает, что состояние изменилось между чтением и commit. Нужно повторно получить snapshot и preflight. Принудительно снижать revision запрещено.

## 5. Transaction failure

При `CANONICAL_TRANSACTION_FAILED`:

- проверить checksum/lastgood;
- проверить file lock/OneDrive;
- не разрешать новые orders;
- восстановить из проверенного backup только после preview.

## 6. External activity

Manual/diagnostic изменение создаёт mismatch/unattributed и блокирует preflight. Для доказанного flat close использовать только:

```text
ACK EXTERNAL CLOSE <TICKER> 0
```

после fresh broker snapshot и полного lifecycle.

## 7. Rollback alpha3 → alpha2

Допустим только из pre-cutover backup и при отсутствии сделок после его точки. Простое копирование alpha2-кода поверх schema 2 не является rollback.
