# Обновление v3.7-alpha2 → v3.7-alpha3

1. Остановить Dry-run/Sandbox Execution.
2. Проверить отсутствие pending/uncertain order и незавершённого Risk accounting.
3. Создать и проверить runtime backup.
4. Закрыть GUI.
5. Распаковать `moex_trading_robot_v3_7_alpha3_hotfix.zip` поверх alpha2.
6. Запустить `install_and_verify_v3_7_alpha3.bat`.
7. Выполнить preview:

```bat
run_portfolio_cutover.bat preview --account-id <ACCOUNT_ID>
```

8. При `allowed=true` выполнить:

```bat
run_portfolio_cutover.bat cutover ^
  --account-id <ACCOUNT_ID> ^
  --confirmation "CUTOVER PORTFOLIO STATE 2"
```

9. Проверить `canonical_migration_report.json`:

```text
success = true
status = COMPLETED
source_schema = 1
target_schema = 2
```

10. Запустить GUI, обновить портфель и проверить Production Readiness.

## Важно

- cutover не выполняется автоматически;
- при `BLOCKED` исходный schema 1 не перезаписывается;
- hotfix сохраняет runtime-файлы;
- standalone EXE нужно пересобрать через `BUILD_STANDALONE.bat`.
