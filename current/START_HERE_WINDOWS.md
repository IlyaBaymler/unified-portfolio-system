# Быстрый запуск MOEX Research Robot v3.9.0 Stable Candidate

## Source candidate

```bat
install_and_verify_v3_9_0.bat
run_gui.bat
```

Установщик создаёт локальную `.venv`, устанавливает зависимости и запускает
автоматический M6 preflight. Runtime предыдущей версии переносите только через
проверенный backup, preview restore и отдельную operator confirmation.

## Portable candidate

Запустите `MOEX Research Robot.bat`. Python на целевом компьютере не требуется.
Сам факт запуска не закрывает standalone/restart/burn-in acceptance.

## Перед Sandbox Execution

- выбран правильный Sandbox account ID;
- canonical snapshot свежий и `READY/FRESH/MATCHED`;
- Central queue/reservations и pending/uncertain проверены;
- Portfolio Risk `READY/ENFORCED`, kill switches в ожидаемом состоянии;
- `risk_resync_required=false`;
- увеличение риска имеет свежий dispatch proof.

Real-account execution в v3.9.0 отсутствует.
