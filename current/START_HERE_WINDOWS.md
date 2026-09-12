# Быстрый запуск MOEX Research Robot v3.10.0 Stable Release Candidate

## Source candidate

```bat
install_and_verify_v3_10_0.bat
run_gui.bat
```

Установщик создаёт локальную `.venv` и выполняет offline release-candidate
verification. PASS этого шага не является Stable acceptance и не разрешает
provider access или эксперимент.

## Portable candidate

Создайте пакет через `BUILD_STANDALONE.bat`, распакуйте его в новую папку и
запустите `MOEX Research Robot.bat`. Standalone qualification должна отдельно
доказать чистую установку, запуск без system Python и отсутствие private runtime
данных внутри артефакта.

## Перед Sandbox execution

- exact Sandbox account scope подтверждён;
- configured instrument set и canonical positions согласованы;
- CL4 broker proof свежий и reconciliation имеет `MATCHED`;
- CL5 CashAvailability и CL6 Risk cash context относятся к тем же revisions;
- Central queue/reservations и pending/uncertain проверены;
- CL7 находится в разрешённом состоянии и прошёл final freshness gate;
- отдельная operator authorization действительно получена.

Real-account execution отсутствует. Automatic retry/resubmit ambiguous POST
запрещён.
