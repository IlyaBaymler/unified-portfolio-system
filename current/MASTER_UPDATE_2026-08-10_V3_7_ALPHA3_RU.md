Дата: 2026-08-10

Решение:
Собрать v3.7-alpha3 Canonical State Cutover с PortfolioState schema 2,
единственным canonical writer и отключённым legacy portfolio read-path.

Основание:
v3.7-alpha2 успешно прошла acceptance canonical preflight, revision race,
post-fill reconciliation, recovery и standalone.

Новый статус:
Alpha3 реализует явную миграцию schema 1→2, single-writer transactions,
canonical-only Risk/Execution/Recovery и write-only compatibility shadow.

Следующий шаг:
Пройти 12 обязательных acceptance-сценариев, затем 12–24-часовую
Sandbox-сессию.

Главный риск:
Silent fallback или второй writer могут вернуть расхождение actual/target/
ownership. Любая неоднозначность должна блокировать broker POST.
