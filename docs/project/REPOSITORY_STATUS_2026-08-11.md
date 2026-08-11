# Repository Status — 2026-08-11

## Версии

- Stable baseline: `v3.6.0`.
- Accepted development baseline: `v3.7-alpha3`.
- Current candidate: `v3.7-beta1 / 0.3.7b1`.
- Beta1 functional acceptance: PASS.
- Beta1 extended 12–24 h burn-in: pending.
- Следующий этап после принятия beta1: Issue #34 / `v3.7.0 Stable`.

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

Подробный sanitized handoff:
`docs/releases/V3_7_BETA1_GITHUB_HANDOFF_RU.md`.

## GitHub Issues

- #17 — Portfolio Manager umbrella; оставить open до `v3.7.0 Stable`.
- #29/#30 — alpha2; closed/completed.
- #31 — beta1 stabilization; open до полного beta acceptance.
- #32 — beta1 checklist; open до burn-in/recovery review.
- #33 — Codex → GitHub implementation/evidence handoff; текущая публикация.
- #34 — Stable qualification; заблокирована до принятия beta1.

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

1. Ветка `v3-7-beta1` и draft PR доступны для review.
2. Issue #33 содержит ссылки на commit/PR/evidence.
3. Пользователь завершает расширенный Sandbox burn-in.
4. По результату обновляются и закрываются Issues #31/#32 либо создаётся
   минимальный beta1.x fix.
5. Issue #34 начинается только после принятия beta1.
