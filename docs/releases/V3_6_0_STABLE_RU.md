# MOEX Research Robot v3.6.0 Stable

Дата сборки: 2026-07-30  
Python package: `0.3.6`  
Канал: `stable`  
База: принятая `v3.6-rc1.4`

## Acceptance

- две длительные Sandbox-сессии;
- суммарно 41 ч 27 мин 20 с;
- 30 уникальных исполнений — 15 BUY и 15 SELL;
- 30/30 полных lifecycle-цепочек;
- 2 перехода `OPEN → MARKET_IDLE → OPEN`;
- disconnect и restart recovery — PASS;
- duplicate submit — 0;
- fill без reconciliation — 0;
- execution без Risk accounting — 0;
- `RISK_RUNTIME_ERROR` — 0;
- установка и запуск в Windows — PASS;
- standalone-сборка — PASS;
- запуск и работа без установленного Python — PASS.

## Артефакты сборки

- `moex_trading_robot_research_v3_6_0.zip`
- `moex_trading_robot_v3_6_0_stable_hotfix.zip`
- `moex_trading_robot_v3_6_0_SHA256.txt`

SHA-256:

```text
a44cd1080b194d4481e6b3bb7496b7905ba4f99469b644828c73adedec1f14a7  moex_trading_robot_research_v3_6_0.zip
d60422358884065749e518a80265737f5bc8a918bc765114cad5389d65a67106  moex_trading_robot_v3_6_0_stable_hotfix.zip
```

## Автоматическая проверка

- full pytest: `307 passed`;
- stable metadata: `4 passed`;
- GUI resilience + MARKET_IDLE + recovery targeted: `34 passed`;
- Risk Lab: `8/8 PASS`;
- clean full ZIP: `307 passed`;
- overlay поверх официальной RC1.4: `307 passed`;
- runtime sentinel preservation: PASS;
- release hygiene: PASS;
- deterministic full/hotfix rebuild: PASS;
- ZIP integrity: PASS.

## Профиль риска

Clean-install использует стандартный RiskPolicy: максимум 1 лот и 4 исполнения в день. Overlay намеренно сохраняет пользовательский runtime. Если в RC1.4 оставался burn-in-профиль с лимитом 32, после backup его нужно явно вернуть через `restore_stable_default_risk_profile.bat`.

## Ограничения

- T-Invest Sandbox only;
- реальный execution отсутствует;
- один исполняемый инструмент и одна PRIMARY;
- long-only.

## Следующий этап

`v3.7.0 Portfolio Manager`: каноническая портфельная модель, PortfolioRepository, PortfolioReconciler, P&L, targets, ownership и единый источник состояния для GUI/Risk/Execution.
