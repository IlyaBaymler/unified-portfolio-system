# Recovery Runbook v3.10.0 Stable Release Candidate

## Немедленная остановка

1. Остановите account-level configured runtime и не отправляйте заявку повторно.
2. Зафиксируйте sanitized identifiers: account scope hash, runtime session,
   Central intent/lease, CL7 revision/state и provider request identity.
3. Сохраните broker observations, pending/uncertain и reconciliation evidence.
4. Создайте verified backup и sanitized support bundle.

## Restart и ambiguous outcome

- queued intent восстанавливается без POST;
- attempt-before-POST marker сохраняется после restart;
- состояния after-attempt/before-response и ambiguous response не resubmitятся;
- lookup выполняется только по сохранённой request identity и account scope;
- resume возможен после exact read-back, ledger/reconciliation closure и нового
  Central/Risk/CL7 proof.

## Cash custody

Проверьте CL2 SQLite primary/WAL/SHM consistency и immutable committed read-back.
Затем подтвердите CL4 proof account/response/as-of binding, CL5 overlap disposition
и CL6 Risk cash context revision. `MATCHED` не разрешает execution автоматически.

## Backup, restore и corruption

Verify manifest/checksums до restore. Restore выполняется только в isolated target
после preview и `RESTORE RUNTIME`. Проверьте primary/checksum/last-good companions,
SQLite integrity, Central, Portfolio, Risk и CL7 CAS chain. Corrupt, incomplete,
mixed-revision или wrong-account restore остаётся fail-closed.

## Rollback

Если v3.10 exact-mode economic attempt уже записан, v3.9 code rollback запрещён.
Без attempt допускается только полный verified pre-upgrade backup; частичное
перенесение state files не допускается. После rollback execution остаётся
выключенным до fresh reconciliation и operator review.

Provider access, cleanup и experiment не запускаются этим runbook автоматически.
