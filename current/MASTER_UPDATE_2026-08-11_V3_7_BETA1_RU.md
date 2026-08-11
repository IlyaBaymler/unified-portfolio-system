# Master Update 2026-08-11 — v3.7-beta1

## Исходная цель

Начать beta1 заново от принятого архива `v3.7-alpha3`, не продолжая ошибочную
предыдущую попытку. Изменения ограничены четырьмя стабилизационными задачами:

1. observation-scoped portfolio warnings;
2. безопасная SecretProvider observability;
3. корректная классификация transient API outage;
4. явный compatibility shadow status.

Дополнительно по результату user-host smoke устранён split-runtime portable
сборки: Risk, robot и portfolio state используют единый sibling `runtime`.

## Граница изменения

Торговый путь, broker POST, canonical schema, cutover и Risk policy не
расширяются. Любой unsafe canonical state по-прежнему закрывает execution.

## Результат реализации

- полный pytest regression — PASS;
- Risk Lab — 8/8 PASS;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- ни один secret/runtime файл не входит в release tree;
- build manifest и standalone verifier требуют `0.3.7b1`.

Все перечисленные automated gate пройдены: `454 passed`, Risk Lab `8/8 PASS`.
Установка, standalone-запуск, restart из `run_gui.bat` и один полный Sandbox
BUY→HOLD→SELL также прошли успешно. Для release acceptance остаётся расширенный
12–24-часовой burn-in и recovery matrix.
