# MOEX Research Robot v3.7.0 Stable Candidate

Дата подготовки: 2026-08-12.

Этот каталог содержит локально квалифицированный source-архив кандидата
`v3.7.0 / 0.3.7`.

## Артефакт

- `moex_trading_robot_research_v3_7_0.zip`;
- SHA-256: `aece8e64ad7306bdb2870a0d10481577cd57a16e9dee1a49034734c30feb952a`.

Архив собран дважды с одинаковым SHA-256, проверен в чистой распаковке и не
содержит runtime state, секретов, журналов или несаницированных отчётов.
В сборку включено qualification-исправление support bundle: канонический
Account ID автоматически определяется и удаляется также из составных строк.
Clean extraction: `456 passed`, release hygiene PASS.

## Статус

Автоматическая локальная квалификация: PASS.

Релиз остаётся кандидатом до завершения ручных Windows/Sandbox-проверок,
финального burn-in и отдельного пользовательского подтверждения. Артефакт
merged в `main` через PR #36; support-bundle security hardening опубликован
через PR #38. Stable tag и GitHub Release не создавались.

Подробный протокол:
`docs/releases/V3_7_0_STABLE_QUALIFICATION_RU.md`.
