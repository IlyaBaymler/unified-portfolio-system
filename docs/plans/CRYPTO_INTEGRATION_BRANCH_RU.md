# Crypto / Digital Assets Integration — боковая ветка roadmap

Дата: 2026-08-13.

Статус: концептуальная необязательная ветка. Не блокирует основную линию развития `v3.8 → v3.9 → v3.10 → v4.x`.

## Цель

В перспективе добавить в Unified Portfolio System операции с цифровыми активами так, чтобы Portfolio Manager и Portfolio Supervisor могли учитывать криптовалюту в едином портфеле, не смешивая MOEX/T-Invest execution с crypto-specific инфраструктурой.

Первая цель ветки — не построение отдельного криптотрейдера, а проверка asset-agnostic и multi-venue архитектуры портфельной системы.

## Архитектурный принцип

Криптовалюта не должна добавляться как «ещё один MOEX ticker». Верхний портфельный слой может быть общим, но execution-domain должен оставаться отдельным.

Целевая схема:

```text
Portfolio Supervisor
        ↓
Target Portfolio
        ↓
Portfolio Risk / Policy
        ↓
Rebalance Planner
        ↓
Execution Plan
   ┌────┴─────────────┐
   ▼                  ▼
Securities venue   Crypto venue
   ↓                  ↓
T-Invest Adapter   CryptoVenueAdapter
```

В дальней архитектуре `BrokerAdapter` желательно обобщить до `ExecutionVenueAdapter` или эквивалентного интерфейса:

```text
ExecutionVenueAdapter
├── TInvestAdapter
└── CryptoVenueAdapter
```

Это не означает обязательный refactor текущего v3.7 core: абстракция вводится только тогда, когда реально начинается multi-venue этап.

## Общая модель актива

Будущие portfolio-domain interfaces не должны предполагать, что любой актив имеет целое число биржевых лотов и торговую сессию MOEX.

Предпочтительные общие понятия:

- `asset_id`;
- `asset_class` (`EQUITY`, `BOND`, `CASH`, `ETF`, `CRYPTO`, ...);
- `venue`;
- `account`;
- `quantity`;
- `quantity_step`;
- `lot_size`, если применимо;
- `min_quantity`;
- `min_notional`;
- `price_precision` / `quantity_precision`;
- base/quote currency;
- market value в общей валюте портфеля.

MOEX lot-based execution и fractional crypto quantity должны быть реализациями одного более общего контракта, а не взаимными исключениями в Portfolio Supervisor.

## Отличия crypto execution-domain

Нужно учитывать отдельно:

- торговлю 24/7 вместо обычной биржевой сессии;
- дробные количества;
- min notional и venue-specific precision;
- отдельные market-data и account APIs;
- venue maintenance / rate limits / degraded API;
- stablecoin как отдельный актив, а не как эквивалент банковского cash;
- venue/custody risk;
- возможное различие trading/deposit/withdrawal availability.

Вместо общего предположения `OPEN → MARKET_IDLE → OPEN` будущий multi-venue слой должен уметь представлять `VenueHealth`, например:

```text
market_data
trading
account_access
deposits
withdrawals
latency / degraded status
```

## Первая версия ветки

Начинать только со Spot и минимального набора активов.

Допустимый первоначальный scope:

- BTC/ETH и при необходимости один-два stablecoin;
- read-only portfolio/account integration;
- market data;
- paper/shadow execution;
- затем sandbox/test environment, если выбранный venue предоставляет безопасный тестовый контур;
- portfolio valuation в общей валюте;
- базовый Crypto Risk projection;
- audit/reconciliation аналогично основному core.

Не включать в первую версию:

- leverage;
- margin;
- perpetual futures;
- options;
- shorts;
- lending;
- staking;
- DeFi;
- bridges;
- autonomous withdrawals;
- on-chain key/custody management.

## Portfolio Risk

Crypto integration требует дополнительных ограничений поверх обычного instrument risk:

- максимальная доля `CRYPTO` в портфеле;
- максимальная доля отдельного crypto asset;
- максимальная концентрация на одном venue;
- stablecoin concentration;
- liquidity/min-notional/precision constraints;
- повышенная volatility/tail-risk policy;
- отдельный `VenueHealth` gate.

Portfolio Supervisor может предлагать crypto target, но не может обходить эти ограничения.

## Stablecoin policy

Stablecoin не считается обычным `FiatCash`.

Предпочтительно разделять:

```text
FiatCash
CryptoAsset
└── Stablecoin
```

Причина — наличие отдельного issuer/custody/venue risk даже при стабильной номинальной цене.

## Предлагаемый порядок боковой ветки

Ветка запускается только после того, как основная система имеет устойчивую multi-instrument/portfolio architecture.

```text
Основная ветка
v3.8  Multi-Instrument Sandbox
v3.9  Portfolio Risk
v3.10 Cash-flow
v4.x  Portfolio Supervisor / Target Portfolio / Rebalancing
        │
        └── optional crypto branch
            ↓
            Multi-Venue abstraction
            ↓
            Crypto Market Data + Read-only Portfolio
            ↓
            Crypto Spot Paper/Shadow
            ↓
            Crypto Spot Sandbox/Test Environment
            ↓
            Cross-Asset Portfolio: securities + crypto
            ↓
            отдельное решение о Limited Live Crypto
```

Версии этой ветки не обязаны совпадать с основной нумерацией до начала реализации. На этапе проектирования она остаётся отдельной roadmap branch.

## Strategy layer

Криптостратегии должны подключаться через тот же будущий `StrategyModuleProtocol`/`StrategyProposal`, что и стратегии для ценных бумаг.

Например:

```text
SMA/SBER
Donchian/LKOH
Trend/BTC
Momentum/ETH
        ↓
Portfolio Supervisor
```

Supervisor работает с proposals/targets, а venue-specific details остаются ниже Rebalance/Execution слоя.

## Safety boundary

Обязательная цепочка сохраняется:

```text
Strategy / Supervisor proposal
→ Portfolio Policy
→ Portfolio Risk
→ Rebalance / Execution Plan
→ venue-specific preflight
→ ExecutionVenueAdapter
→ reconciliation
→ EventJournal / accounting
```

Ни Strategy, ни Supervisor, ни AI/ML слой не получают прямого доступа к crypto venue POST в обход Policy/Risk/Execution.

Autonomous withdrawal/custody actions не входят в данную ветку до отдельного архитектурного и security review.

## Условия начала реализации

До начала активной разработки желательно иметь:

1. стабильный multi-instrument core;
2. Portfolio Risk на account-wide уровне;
3. Cash-flow/accounting separation;
4. Target Portfolio / Rebalance Plan;
5. asset-agnostic quantity model;
6. понятную multi-account/multi-venue boundary;
7. отдельный выбор легального и технически пригодного crypto venue с учётом юрисдикции пользователя на момент реализации.

Последний пункт требует актуальной проверки законодательства, доступности API, KYC/AML и условий выбранной площадки непосредственно перед реализацией.

## Критерий успеха ветки

Первый полноценный результат — единый read-only/paper portfolio, где securities и crypto отображаются в одной canonical экономической модели, но исполняются и восстанавливаются через независимые venue adapters без нарушения существующих safety invariants.