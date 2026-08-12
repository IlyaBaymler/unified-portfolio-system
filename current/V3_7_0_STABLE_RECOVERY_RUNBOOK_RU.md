# Recovery Runbook v3.7.0 Stable

## Общий порядок

1. Остановить execution и не повторять заявку вручную.
2. Получить свежий broker snapshot.
3. Проверить pending/uncertain order, account ID, canonical revision/checksum.
4. Создать и проверить runtime backup; собрать sanitized support bundle.
5. Возобновлять только после fresh `READY/FRESH/MATCHED` и Risk `PASS`.

## Warning не исчез после refresh

Убедитесь, что опубликована новая revision. Старый warning не переносится;
повторившийся warning означает актуальную reconciliation-причину.

## Credential status

- `credential_absent` — credential не найден;
- `provider_unavailable` — защищённый provider недоступен, что не равно
  отсутствию token;
- `.env_fallback` — используется локальный fallback, запрещённый в support/ZIP;
- `credential_present` — credential найден без раскрытия значения.

## API outage

Recovered transient требует более позднего fresh safe snapshot. При unresolved
transient или non-transient failure заявки запрещены до восстановления.

## Shadow degraded

`DEGRADED` не разрешает и не запрещает canonical execution. Сохраните support
bundle, восстановите write-only shadow и проверьте новый status.

## Restart / split runtime

Mutable state должен находиться в sibling `runtime`, а не в `app`. При restart
проверьте Risk profile, robot state, PortfolioState и EventJournal в одном
runtime. Не объединяйте state разных установок.

## Backup/restore

1. Verify backup checksum и manifest до остановки исходной версии.
2. Preview restore и сверить список заменяемых файлов.
3. Выполнять restore только с confirmation `RESTORE RUNTIME`.
4. После restore проверить SQLite integrity, canonical checksum и fresh broker
   reconciliation.

## Rollback Stable → beta1

Используйте полный проверенный backup принятой beta1. Rollback допустим только
на тестовой копии или если после backup не было новых сделок. Не копируйте
отдельные Python/JSON/SQLite-файлы поверх Stable. После rollback execution
остаётся off до fresh `MATCHED`, Risk `PASS` и отсутствия pending/uncertain.
