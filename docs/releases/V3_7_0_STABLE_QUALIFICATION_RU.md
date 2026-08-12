# v3.7.0 Stable — локальная квалификация кандидата

Дата: 2026-08-12.

Версия: `v3.7.0 / 0.3.7`.

База: принятая `v3.7-beta1`, объединённая в `main` через PR #35,
merge commit `4e4a12bda87bc82cd9d195e5960001646c7de6d3`.

Статус: **локальная автоматическая квалификация PASS; пользовательское
принятие Stable ещё не дано**.

## Scope

Функциональное расширение отсутствует. Сохранены:

- `PortfolioState` schema 2;
- canonical-only reads;
- `PortfolioTransactionCoordinator` как единственный writer;
- immutable preflight и revision recheck перед broker POST;
- обязательная post-fill canonical reconciliation;
- идемпотентный Risk accounting;
- T-Invest Sandbox, один инструмент, long-only;
- отключённые real-account и multi-instrument execution.

Изменены release metadata, имена артефактов, release-документация и тесты
контракта Stable. Историческое поле `sandbox_acceptance` сохранено для
совместимости и явно помечено как унаследованное evidence v3.6. Принятые
результаты beta1 — 6 BUY→HOLD→SELL и 12/12 заявок — записаны отдельно в
`beta1_sandbox_acceptance`.

## Локальные результаты

- Stable release contract: `9 passed`;
- полный regression suite: `455 passed`;
- crash/recovery, migration и backup/restore subset: `61 passed`;
- Risk Lab: `8/8 PASS`;
- release hygiene и compileall: PASS;
- deterministic source build: PASS, два совпадающих SHA-256;
- clean source ZIP: `455 passed`, hygiene PASS;
- Windows standalone build: PASS;
- standalone layout verification: PASS;
- принятый beta1 rollback-артефакт: SHA-256 подтверждён, `454 passed`,
  hygiene PASS;
- generated `risk_stable_output` исключён из source ZIP и закреплён
  regression-тестом;
- forbidden/runtime/secret files в source ZIP: 0.

Source ZIP:
`releases/v3.7.0/moex_trading_robot_research_v3_7_0.zip`.

SHA-256:
`184bbd2be93e5d2b9f38c79d9fdb6bd10bf829ddb3e166712ca3fca068e63f3b`.

## Оставшиеся ручные gates

На пользовательском Windows/Sandbox-контуре необходимо подтвердить:

1. clean install и upgrade с принятой beta1;
2. фактический запуск собранного standalone без установленного Python;
3. rollback на принятую beta1 в тестовой копии;
4. backup/verify/restore и sanitized support bundle на рабочем runtime;
5. финальный Sandbox burn-in 24–48 часов без invariant violations;
6. итоговый review support bundle и явное пользовательское acceptance.

До этого `stable_qualification.status=candidate`,
`user_acceptance=false`, `final_burn_in_complete=false`. Git tag, GitHub
Release и закрытие Issue #34 преждевременны.
