# Recovery Runbook v3.9.0 Stable Candidate

## Общий порядок

1. Остановить instrument runtimes; не создавать повторную заявку вручную.
2. Зафиксировать Central queue/pending/uncertain и broker snapshot.
3. Проверить canonical revision/checksum, Portfolio Risk policy/state/proof.
4. Создать и проверить backup; собрать sanitized support bundle.
5. Возобновлять только после fresh `READY/FRESH/MATCHED`, Risk
   `READY/ENFORCED`, `risk_resync_required=false` и отдельной authorization.

## Restart до/после POST

- queued proof восстанавливается без dispatch/resubmit;
- перед POST обязательна новая revalidation;
- ambiguous response остаётся `UNCERTAIN` до broker reconciliation;
- fill не terminal до canonical reconciliation и Risk accounting.

## External position или cash

Автоматическое adoption/resync запрещено. Используйте только read-only prepare,
проверьте account/currency/revision и применяйте exact-confirmation operator
command. Необъяснимое изменение остаётся blocker.

## Kill switches

Global/instrument switch нельзя сбрасывать при bootstrap или restore. Сначала
read-only status/explain, затем отдельная exact-confirmation transition. После
restart подтвердите сохранность state и отсутствие provider POST.

## Backup/restore и rollback

Verify checksum/manifest до restore; всегда выполняйте preview. Restore требует
`RESTORE RUNTIME` и isolated target. После restore проверьте primary/checksum/
last-good companions, SQLite `integrity_check`, schemas, queue/reservations и
нулевой resubmit. Rollback к принятому v3.8 runtime — отдельный manual gate.
