# Обновление до v3.9.0 Stable Candidate

## Clean install

1. Распакуйте source ZIP в новую папку.
2. Не переносите `.env`, logs, JSON/SQLite state или locks вручную.
3. Выполните `install_and_verify_v3_9_0.bat`.
4. Запустите `run_gui.bat` только после PASS автоматического preflight.
5. До конфигурации Sandbox execution убедитесь, что real account отсутствует.

## Upgrade из принятого runtime

1. Остановите все instrument runtimes и проверьте отсутствие active dispatch.
2. Создайте backup и выполните independent verify.
3. Сохраните исходный package/runtime неизменённым для rollback.
4. Выполните preview restore в isolated v3.9 runtime.
5. Restore требует точного подтверждения `RESTORE RUNTIME`.
6. После restore проверьте checksums, SQLite integrity, schema 2/4, Central queue,
   pending/uncertain и fresh broker reconciliation.
7. Не включайте Sandbox execution до отдельной M6 runtime qualification.

## Rollback

Rollback выполняется только на isolated/disposable copy или при доказанном
отсутствии новых сделок после backup. Нельзя накладывать отдельные JSON/SQLite
файлы поверх другой версии. После rollback execution остаётся выключенным до
fresh canonical reconciliation и Risk PASS.
