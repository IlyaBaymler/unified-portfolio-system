# Master Update 2026-08-11 — v3.7-beta1

## Цель

Начать beta1 заново от принятого архива `v3.7-alpha3`, не продолжая ошибочную
предыдущую попытку. Изменения ограничены четырьмя стабилизационными задачами:

1. observation-scoped portfolio warnings;
2. безопасная SecretProvider observability;
3. корректная классификация transient API outage;
4. явный compatibility shadow status.

## Граница изменения

Торговый путь, broker POST, canonical schema, cutover и Risk policy не
расширяются. Любой unsafe canonical state по-прежнему закрывает execution.

## Критерий готовности

- полный pytest regression — PASS;
- Risk Lab — 8/8 PASS;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- ни один secret/runtime файл не входит в release tree;
- build manifest и standalone verifier требуют `0.3.7b1`.
