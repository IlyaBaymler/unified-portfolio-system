# Обновление до v3.10.0 Stable Release Candidate

## Clean install

1. Проверьте SHA-256 source или standalone artifact по принятому manifest.
2. Распакуйте пакет в новую папку.
3. Не переносите `.env`, logs, JSON/SQLite state, locks или backup contents вручную.
4. Для source package выполните `install_and_verify_v3_10_0.bat`.
5. До provider configuration проверьте `sandbox_only=true` и отсутствие
   real-account endpoint.

## Upgrade из принятого v3.9 runtime

1. Остановите GUI и все instrument runtimes; убедитесь, что active dispatch нет.
2. Зафиксируйте Central queue, pending/uncertain и broker observation.
3. Создайте полный CL7-aware backup и выполните independent verify.
4. Сохраните исходный package и runtime неизменёнными.
5. Выполните restore только в isolated target с preview и exact confirmation
   `RESTORE RUNTIME`.
6. Проверьте primary/checksum/last-good files, SQLite `integrity_check`, CL2
   ledger/inbox, CL7 authority record и account-scope binding.
7. После запуска выполните fresh CL3 sync, CL4 reconciliation, CL5 snapshot и
   CL6 Risk cash context rebuild.
8. Execution остаётся disarmed до отдельной authorization.

## Rollback

Code/package rollback не является state rewind. Если после перехода на v3.10
существует exact-mode economic attempt, откат к v3.9 запрещён и должен завершаться
fail-closed. Допустимы остановка, disarm, recovery/reconciliation и manual review.
Rollback без economic attempt выполняется только из verified pre-upgrade backup в
isolated target; смешивание отдельных JSON/SQLite файлов разных revisions запрещено.

## Authority boundary

Этот документ не является `ACCEPT V3.10.0 STABLE`, `PUBLISH V3.10.0 STABLE` или
`START EXPERIMENT` и не разрешает provider mutation.
