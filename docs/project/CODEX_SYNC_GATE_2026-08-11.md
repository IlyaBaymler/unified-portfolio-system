# Codex -> GitHub sync gate — 2026-08-11

## Причина

Локальная разработка перенесена в Codex, а ChatGPT используется как аналитический и acceptance-контур. Поэтому GitHub становится проверяемой границей между реализацией и анализом.

На момент этой записи удалённые `main` и `v3-7-beta1` не содержат отдельного beta1 implementation diff: ветка `v3-7-beta1` была синхронизирована с `main` перед началом локальной разработки.

## Обязательное правило

Перед анализом любой новой версии в ChatGPT локальный Codex должен отправить в соответствующую version branch:

- исходный код изменений;
- тесты;
- version/build manifest;
- changelog и изменённую техническую документацию;
- безопасные test summaries без токенов, Account ID и runtime state.

Большие runtime logs, рабочие state-файлы, `.env`, SQLite и support bundles в Git не коммитятся; они передаются отдельно для анализа.

## Минимальный handoff beta1

Для `v3.7-beta1` перед acceptance review нужны:

1. commit SHA локальной реализации в `v3-7-beta1`;
2. diff относительно принятой `v3.7-alpha3`;
3. полный pytest summary;
4. targeted beta1 test summary;
5. Risk Lab 8/8;
6. migration/crash/recovery regression summary;
7. build manifest `0.3.7b1`;
8. release hygiene/secret scan summary;
9. Windows/Sandbox acceptance artifacts перед финальным решением.

## Разделение ролей

- **Codex:** имплементация, рефакторинг, unit/integration tests, локальная сборка и отладка.
- **ChatGPT:** анализ требований и результатов, архитектурный контроль, acceptance decision, постановка Issues, обновление roadmap/release records.
- **GitHub:** источник проверяемой истории кода, решений, Issues и release metadata.
