# Repository Status — 2026-08-12

## Версии

- Stable baseline: `v3.6.0`.
- Accepted alpha baseline: `v3.7-alpha3`.
- Accepted development version: `v3.7-beta1 / 0.3.7b1`.
- Beta1 functional acceptance: PASS.
- Beta1 extended burn-in/recovery acceptance: PASS 2026-08-12.
- Активный следующий этап: Issue #34 / `v3.7.0 Stable`.

## Ветка и GitHub handoff

Локальная реализация выполнена заново в чистом worktree и объединена с
актуальной документационной историей удалённой beta-ветки:

```text
local branch: v3-7-beta1-rebuild
publication target: origin/v3-7-beta1
base before local implementation: origin/main @ 0a5bfcd
incoming remote documentation head: 89db2dd
publication tracking: GitHub Issue #33
```

Ключевые локальные коммиты:

```text
6e4f7da chore: import accepted v3.7-alpha3 source baseline
aa046a6 feat: rebuild v3.7-beta1 stabilization release
3d15edd fix: keep portable risk and state in runtime directory
e99f6c7 docs: record v3.7-beta1 functional acceptance
5273ed5 docs: add beta1 GitHub handoff evidence
```

Для содержательного beta1 review использовать baseline commit `6e4f7da`.
Прямой diff удалённой alpha3-ветки к beta1 дополнительно показывает импорт
распакованного source tree, поскольку alpha3 ранее публиковалась архивом.

GitHub используется как auditable boundary:

```text
Codex local implementation/tests/build
→ v3-7-beta1 source diff and evidence
→ GitHub/ChatGPT review
→ user burn-in acceptance
→ Issues #31/#32 release decision
```

## Проверки

- full pytest regression — `454 passed`;
- Risk Lab — `8/8 PASS`;
- migration schema 1 → 2 и crash/recovery matrix — PASS;
- release hygiene, secret scan, compileall, standalone layout и deterministic
  ZIP — PASS;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- Sandbox BUY→HOLD→SELL — PASS, 2/2 orders;
- duplicate submit, missing reconciliation, missing Risk accounting — 0;
- runtime/API/canonical transaction failures — 0;
- финальный canonical state — `READY/FRESH/MATCHED`, `blocking=false`, shadow
  `OK`, warnings `0`, revision `5`.
- 16 ч 09 мин extended burn-in, 6 BUY→HOLD→SELL, 12/12 orders — PASS;
- intentional disconnect, restart с открытой позицией и MARKET_IDLE recovery —
  PASS;
- Risk Burn-in report/support bundle — reviewed;
- duplicate submit, missing reconciliation/accounting и unresolved execution —
  0.

Подробный sanitized handoff:
`docs/releases/V3_7_BETA1_GITHUB_HANDOFF_RU.md`.

## GitHub Issues

- #17 — Portfolio Manager umbrella; оставить open до `v3.7.0 Stable`.
- #29/#30 — alpha2; closed/completed.
- #31 — beta1 stabilization; completed.
- #32 — beta1 checklist; completed.
- #33 — Codex → GitHub implementation/evidence handoff; completed.
- #34 — Stable qualification; разблокирована принятием beta1.

## Ветки

```text
main                 — принятая проектная/документационная база
v3-7-alpha3          — frozen accepted alpha baseline
v3-7-beta1           — beta implementation and evidence branch
release-v3.6.0       — историческая stable release branch
```

`develop` является исторической интеграционной веткой и не обязателен в
текущем local-Codex workflow.

## Repository hygiene

Не публиковать:

```text
.env
tokens / Account ID
risk_state.json
robot_state.json
portfolio_state.json
portfolio_legacy_shadow.json
sandbox_diagnostic_state.json
canonical_migration_report*.json
runtime_bootstrap_report.json
trading_events.db*
*.log
backups/
support/
runtime/
несаницированные reports/
```

Публиковать исходники, тесты, `.env.example`, versioned release docs, sanitized
test/acceptance summary, source ZIP и SHA-256.

## Следующий контрольный пункт

1. Опубликовать финальный acceptance commit в `v3-7-beta1`.
2. Закрыть Issues #31/#32 и перевести PR #35 в ready-for-review.
3. Не выполнять merge в `main` без отдельного решения.
4. Начать qualification checklist Issue #34 от принятой beta1.
