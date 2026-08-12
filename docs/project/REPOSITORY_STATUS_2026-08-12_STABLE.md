# Repository Status — v3.7.0 Stable candidate — 2026-08-12

## Версии и GitHub

- Последний опубликованный Stable: `v3.6.0`.
- Принятая beta: `v3.7-beta1 / 0.3.7b1`.
- PR #35: reviewed и merged.
- Принятый `main`: `4e4a12bda87bc82cd9d195e5960001646c7de6d3`.
- Issue #34: активная Stable qualification.
- Issue #17: Portfolio Manager umbrella остаётся open до принятия Stable.

## Локальная ветка

```text
worktree: unified-portfolio-system-stable
branch: v3-7-0-stable
base: origin/main @ 4e4a12b
handoff: PR #36, v3-7-0-stable -> main
release publication: не выполнялась
```

Ветка содержит release-only переход к `v3.7.0 / 0.3.7`: metadata,
документацию, имена артефактов, Stable verifier и тесты release contract.
Торговая архитектура, schema 2, canonical-only reads, single-writer и
Risk/Execution protocol не изменялись.

## Квалификация

- Stable release contract: `9 passed`;
- full pytest regression: `455 passed`;
- recovery/migration/backup subset: `61 passed`;
- Risk Lab: `8/8 PASS`;
- release hygiene, compileall и safety boundary: PASS;
- Windows standalone build/layout: PASS;
- deterministic source ZIP: PASS;
- clean source ZIP: `455 passed`, hygiene PASS;
- accepted beta1 rollback artifact: SHA-256 PASS, `454 passed`;
- source ZIP forbidden/runtime/secret files: 0.

Исторические 30 execution v3.6 и принятые beta1 6 циклов/12 заявок теперь
имеют разные manifest scopes. Stable manifest остаётся `candidate`,
`user_acceptance=false`, `final_burn_in_complete=false`.

## Артефакт

```text
releases/v3.7.0/moex_trading_robot_research_v3_7_0.zip
SHA-256 184bbd2be93e5d2b9f38c79d9fdb6bd10bf829ddb3e166712ca3fca068e63f3b
```

## Следующий контрольный пункт

На пользовательском Windows/Sandbox-контуре: clean install/upgrade,
standalone без Python, rollback exercise, backup/restore/support bundle и
24–48-часовой burn-in. Candidate branch и PR #36 опубликованы для review/merge.
Затем — review evidence и явное acceptance; только после него обновляются
Issue #34, tag и GitHub Release.
