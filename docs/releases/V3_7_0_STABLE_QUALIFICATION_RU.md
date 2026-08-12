# v3.7.0 Stable — локальная квалификация кандидата

Дата: 2026-08-12.

Версия: `v3.7.0 / 0.3.7`.

База: принятая `v3.7-beta1`, Stable candidate из PR #36 и синхронизация
статуса из PR #37; актуальный `origin/main` —
`5cf717efdaf9c8d8aee59a7e0740a958f2a0d531`.

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

Изменены release metadata, имена артефактов, release-документация, тесты
контракта Stable и security hardening support bundle. Историческое поле
`sandbox_acceptance` сохранено для
совместимости и явно помечено как унаследованное evidence v3.6. Принятые
результаты beta1 — 6 BUY→HOLD→SELL и 12/12 заявок — записаны отдельно в
`beta1_sandbox_acceptance`.

## Локальные результаты

- Stable release contract: `9 passed`;
- полный regression suite: `456 passed`;
- crash/recovery, migration и backup/restore subset: `62 passed`;
- Risk Lab: `8/8 PASS`;
- release hygiene и compileall: PASS;
- deterministic source build: PASS, два совпадающих SHA-256;
- clean source ZIP: `456 passed`, hygiene PASS;
- Windows standalone build: PASS;
- standalone layout verification: PASS;
- принятый beta1 rollback-артефакт: SHA-256 подтверждён, `454 passed`,
  hygiene PASS;
- generated `risk_stable_output` исключён из source ZIP и закреплён
  regression-тестом;
- forbidden/runtime/secret files в source ZIP: 0.
- support bundle real-runtime exact-value scan: Account ID `0`, token `0`,
  forbidden members `0`, checksum mismatches `0`;
- support bundle автоматически определяет канонический Account ID и удаляет
  его также из составных строк, включая journal `transaction_id`.

Source ZIP:
`releases/v3.7.0/moex_trading_robot_research_v3_7_0.zip`.

SHA-256:
`aece8e64ad7306bdb2870a0d10481577cd57a16e9dee1a49034734c30feb952a`.

## Подтверждённые user-host gates

- clean install нового source ZIP — PASS;
- upgrade из verified beta1 backup — PASS;
- standalone launch без системного Python — PASS;
- backup/verify/restore — PASS;
- external close acknowledgement после restore — `READY/FRESH`, blocking
  false, pending/uncertain `0/0`;
- sanitized support bundle review и независимый scan — PASS.

Qualification поймала исходную утечку Account ID до публикации. Небезопасный
локальный bundle удалён; исправление и regression подготовлены в ветке
`fix/v3-7-support-bundle-redaction`.

## Оставшиеся gates

Необходимо:

1. review/merge security fix;
2. rollback на принятую beta1 в тестовой копии;
3. финальный Sandbox burn-in 24–48 часов без invariant violations;
4. итоговый review evidence и явное пользовательское acceptance.

До этого `stable_qualification.status=candidate`,
`user_acceptance=false`, `final_burn_in_complete=false`. Git tag, GitHub
Release и закрытие Issue #34 преждевременны.
