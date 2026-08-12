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
branch: fix/v3-7-support-bundle-redaction
base: origin/main @ 5cf717e
handoff: PR #36 merged, main @ d696f74
docs sync: PR #37 merged, main @ 5cf717e
security fix: PR #38
release publication: не выполнялась
```

Ветка содержит release-only переход к `v3.7.0 / 0.3.7`: metadata,
документацию, имена артефактов, Stable verifier и тесты release contract.
Торговая архитектура, schema 2, canonical-only reads, single-writer и
Risk/Execution protocol не изменялись.

## Квалификация

- Stable release contract: `9 passed`;
- full pytest regression: `456 passed`;
- recovery/migration/backup subset: `62 passed`;
- Risk Lab: `8/8 PASS`;
- release hygiene, compileall и safety boundary: PASS;
- Windows standalone build/layout: PASS;
- deterministic source ZIP: PASS;
- clean source ZIP: `456 passed`, hygiene PASS;
- accepted beta1 rollback artifact: SHA-256 PASS, `454 passed`;
- source ZIP forbidden/runtime/secret files: 0.
- clean install/upgrade, standalone launch и backup/restore: PASS;
- sanitized support bundle independent scan: Account ID `0`, token `0`,
  forbidden members `0`, checksum mismatches `0`.

Qualification обнаружила, что исходный support bundle сохранял канонический
Account ID внутри составного journal `transaction_id`. Небезопасный bundle
удалён до публикации. Локальный fix добавляет auto-discovery Account ID,
redaction внутри строк и regression-тест.

Исторические 30 execution v3.6 и принятые beta1 6 циклов/12 заявок теперь
имеют разные manifest scopes. Stable manifest остаётся `candidate`,
`user_acceptance=false`, `final_burn_in_complete=false`.

## Артефакт

```text
releases/v3.7.0/moex_trading_robot_research_v3_7_0.zip
SHA-256 aece8e64ad7306bdb2870a0d10481577cd57a16e9dee1a49034734c30feb952a
```

## Следующий контрольный пункт

После PR #38 остаются rollback exercise и 24–48-часовой burn-in. После review
финального evidence требуется явное acceptance; только затем
завершается Issue #34 и создаются tag/GitHub Release.
