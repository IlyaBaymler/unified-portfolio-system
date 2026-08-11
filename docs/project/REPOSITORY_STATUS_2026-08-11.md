# Repository Status — 2026-08-11

## Версии

- Stable baseline: `v3.6.0`.
- Accepted development baseline: `v3.7-alpha3`.
- Current candidate: `v3.7-beta1 / 0.3.7b1`.
- Beta1 functional acceptance: PASS.
- Beta1 extended 12–24 h burn-in: pending.

## Локальная ветка

Работа выполнена заново в чистом worktree:

```text
branch: v3-7-beta1-rebuild
upstream: origin/v3-7-beta1
base: origin/main @ 0a5bfcd
local commits ahead after this documentation update: 4
remote publication: not performed
```

Коммиты реализации:

```text
6e4f7da chore: import accepted v3.7-alpha3 source baseline
aa046a6 feat: rebuild v3.7-beta1 stabilization release
3d15edd fix: keep portable risk and state in runtime directory
docs      record v3.7-beta1 functional acceptance
```

## Проверки

- full pytest regression — `454 passed`;
- Risk Lab — `8/8 PASS`;
- release hygiene, compileall, standalone layout и deterministic ZIP — PASS;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- Sandbox BUY→HOLD→SELL — PASS, 2/2 orders;
- duplicate submit, missing reconciliation, missing Risk accounting — 0;
- runtime/API/canonical transaction failures — 0;
- финальный canonical state — `READY/FRESH/MATCHED`, `blocking=false`, shadow
  `OK`, warnings `0`, revision `5`.

## GitHub Issues

- #17 — Portfolio Manager umbrella; оставить open до `v3.7.0 Stable`.
- #29/#30 — alpha2; completed.
- #31/#32 — beta1 scope и acceptance matrix; оставить open до расширенного
  burn-in, review артефактов и публикации beta1.

## Repository hygiene

Не публиковать `.env`, tokens/account ID, runtime JSON, SQLite DB/WAL/SHM,
логи, backup, support bundle и локальные lock-файлы.

Перед публикацией beta1 повторно проверить SHA-256 source ZIP, release hygiene,
secret scan и соответствие release README фактическому acceptance.
