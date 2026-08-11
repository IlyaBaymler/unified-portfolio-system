# v3.7-beta1 — GitHub Handoff and Evidence Package

Дата: 2026-08-11.  
Версия: `v3.7-beta1 / 0.3.7b1`.  
GitHub: Issues #31, #32 и #33.  
Publication target: `v3-7-beta1`.

## Назначение

Документ связывает опубликованный source diff, automated gate и очищенное
Windows/Sandbox evidence. Raw runtime, token, Account ID, SQLite, logs,
backups, support bundles и несаницированные reports в Git не включаются.

Расширенный 12–24-часовой beta burn-in ведётся отдельно и не подменяется этим
handoff.

## История и граница diff

```text
0a5bfcd  GitHub main и исходная точка beta worktree
6e4f7da  import accepted v3.7-alpha3 source baseline
aa046a6  rebuild v3.7-beta1 stabilization release
3d15edd  portable split-runtime fix
e99f6c7  functional acceptance documentation
```

Удалённая `v3-7-alpha3` хранит принятую версию преимущественно release-архивом,
а `6e4f7da` добавляет её распакованный source baseline. Поэтому:

- repository integration diff: `origin/main...v3-7-beta1`;
- содержательный beta1 diff: `6e4f7da..v3-7-beta1`;
- implementation commits: `aa046a6`, `3d15edd`;
- acceptance/evidence commits идут после implementation commits.

Команды review:

```bash
git log --oneline 6e4f7da..v3-7-beta1
git diff --stat 6e4f7da..v3-7-beta1
git diff 6e4f7da..v3-7-beta1 -- current/trading_robot current/tests
```

## Implementation matrix

| Scope Issue #31/#32 | Реализация | Основное evidence |
|---|---|---|
| Observation-scoped warnings | `portfolio_adapters.py`, `portfolio_manager.py` | `test_portfolio_manager_v3_7.py` |
| SecretProvider metadata-only probe | `secret_provider.py`, `runtime_bootstrap.py` | `test_runtime_bootstrap.py`, `test_support_readiness_rc1.py` |
| Recovered transient classification | `risk_reporting.py` | `test_risk_reporting.py` |
| Shadow `OK/DEGRADED/DISABLED` | `portfolio_model.py`, `readiness.py`, `support_bundle.py` | `test_portfolio_model_v3_7.py`, `test_support_readiness_rc1.py` |
| Portable sibling runtime | `desktop_gui.py` | `test_standalone_rc1.py` |
| Version/release contract | `build_manifest.json`, release scripts/docs | `test_beta_release_v3_7.py`, `test_release_hygiene.py` |

## Automated evidence

| Gate | Результат |
|---|---:|
| Full pytest regression | `454 passed` |
| Targeted beta1 observability tests | PASS, входят в full regression |
| Risk Lab | `8/8 PASS` |
| Migration schema 1 → schema 2 regression | PASS |
| Crash/recovery matrix | PASS |
| Release hygiene and secret scan | PASS |
| Compileall | PASS |
| Standalone layout verifier | PASS |
| Deterministic ZIP audit | PASS |

Воспроизведение на Windows из `current/`:

```bat
VERIFY_V3_7_BETA1.bat
run_risk_lab.bat
```

Точечный regression split-runtime:

```bat
.venv\Scripts\python.exe -m pytest -q tests\test_standalone_rc1.py
```

## Windows/Sandbox functional evidence

Sanitized source summary:
`trading_events_v3_7_beta1_2026-08-11_171624.csv`.

- установка и standalone launch — PASS;
- restart из `run_gui.bat` и загрузка Risk profile — PASS;
- один полный BUY→HOLD→SELL;
- submitted/accepted/filled — 2/2/2;
- post-fill canonical reconciliation — 2/2;
- Risk accounting — 2/2;
- duplicate submit — 0;
- Risk runtime, API и canonical transaction errors — 0;
- финальный state — `READY/FRESH/MATCHED`, `blocking=false`, shadow `OK`,
  warnings `0`, revision `5`.

Полная очищенная запись:
`docs/releases/V3_7_BETA1_FUNCTIONAL_ACCEPTANCE_RU.md`.

## Release artifacts

```text
alpha3 source baseline SHA-256:
2a643a9a48e53db1731fd91d6487c4040b774d947c92d63943665d655e429455

beta1 source ZIP SHA-256:
730a1d1dbef8bafd87bede739dd6d33574896ee6f0a466c4a861592771cd4ee2
```

Файлы:

- `releases/v3.7-beta1/moex_trading_robot_research_v3_7_beta1.zip`;
- `releases/v3.7-beta1/moex_trading_robot_v3_7_beta1_SHA256.txt`;
- `current/RELEASE_MANIFEST_V3_7_BETA1.txt`;
- `current/CHANGELOG_V3_7_BETA1_RU.md`;
- `current/V3_7_BETA1_ARCHITECTURE_RU.md`;
- `current/V3_7_BETA1_TEST_PLAN_RU.md`;
- `current/V3_7_BETA1_RECOVERY_RUNBOOK_RU.md`.

## Незакрытый beta release gate

- расширенный 12–24 h Sandbox burn-in;
- intentional disconnect и recovery до fresh `MATCHED`;
- restart с открытой позицией;
- `OPEN → MARKET_IDLE → OPEN`;
- review итогового Risk Burn-in report и support bundle.

После завершения этого gate пользователь принимает решение по Issues #31/#32.
Issue #34 `v3.7.0 Stable` до этого не начинается.
