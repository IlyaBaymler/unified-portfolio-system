# V3.10 CL6 — Reporting + Portfolio Risk cash context: bounded contract freeze

Статус:

`CL6 CONTRACT FREEZE CANDIDATE / IMPLEMENTATION BLOCKED / READ-ONLY`

Parent program: stable-line v3.10 → v4.

Historical evidence: Issues #54 and #55.

Accepted predecessor:

`CL5 = ACCEPTED / INTEGRATED / COMPLETED`.

---

## 1. Exact predecessor и lineage

CL6 contract work начинается только от exact accepted/integrated CL5 stable-line head:

```text
repository = baimleriv/unified-portfolio-system

program branch =
program/v3-10-v4-stable-line

exact predecessor commit =
864963dd69cb4c907cbc762b66ed12f012f32741

exact predecessor tree =
496503685e57b024c2b511352b2637ed74c5663d

contract branch =
agent/v3-10-clean-cl6-contract-freeze

HEAD at branch creation =
864963dd69cb4c907cbc762b66ed12f012f32741

merge-base =
864963dd69cb4c907cbc762b66ed12f012f32741

ahead = 0
behind = 0
worktree = clean
```

Допустимая ancestry:

```text
v3.9 oracle
-> accepted CL0
-> accepted CL1
-> accepted CL2
-> accepted CL3
-> accepted CL4
-> accepted CL5
-> CL6 contract candidate
```

`main`, historical v3.10/MoneyV2 branches и historical Issues являются только evidence/reference и не являются ancestry или integration authority.

---

## 2. Contract-freeze allowlist

До отдельного exact-head CL6 contract acceptance разрешено менять ровно один repository path:

```text
docs/project/V3_10_CL6_REPORTING_RISK_CASH_CONTEXT_CONTRACT_RU.md
```

Любой другой changed, added, deleted, renamed или untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

На contract-freeze этапе запрещены изменения:

- CL1–CL5 implementation;
- Portfolio;
- Central;
- Risk;
- Execution;
- runtime;
- GUI;
- tests;
- fixtures;
- workflows;
- dependency manifests;
- release files.

---

## 3. Frozen future implementation allowlist

Только после:

1. independent/adversarial CL6 contract review;
2. closure всех material contract findings;
3. explicit acceptance exact contract commit/tree;

может быть создана отдельная implementation branch непосредственно от accepted CL6 contract head.

Future implementation delta ограничен ровно тремя paths:

```text
current/trading_robot/reporting_risk_cash_context.py

current/tests/test_v3_10_reporting_risk_cash_context.py

current/tests/fixtures/v3_10_reporting_risk_cash_context_vectors.json
```

Accepted CL6 contract immutable во время implementation.

CL1–CL5, Portfolio, Central, Risk и Execution files immutable.

Максимальный cumulative CL6 surface относительно exact CL5 predecessor:

```text
1 contract path
+
3 implementation paths
=
4 paths
```

Любой четвёртый implementation path или изменение predecessor path:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

---

## 4. Bounded mission

CL6 создаёт две независимые read-only поверхности:

```text
A. deterministic cash-flow-adjusted reporting/performance

B. exact PortfolioRiskCashContext
```

Они могут использовать одни и те же accepted predecessor identities, но:

```text
ReportingResult
!=
PortfolioRiskCashContext
!=
PortfolioRiskDecision
!=
ExecutionAuthorization
```

CL6 не создаёт нового runtime owner и не изменяет никакое predecessor state.

---

## 5. Surface A — Reporting mission

Reporting обязан:

1. читать accepted CL2/CL4 ledger evidence;
2. классифицировать accepted cash effects для reporting;
3. отделять external contributions от strategy return;
4. принимать отдельные exact valuation points;
5. детерминированно рассчитывать TWR;
6. детерминированно рассчитывать или отказывать XIRR;
7. выпускать privacy-safe canonical `PerformanceReport`;
8. связывать report с exact ledger revision/head/export/projection;
9. сохранять явную completeness provenance;
10. никогда не влиять на execution authority.

---

## 6. Surface B — Risk cash context mission

CL6 обязан строить immutable:

```text
PortfolioRiskCashContext
```

из:

```text
exact revalidated CL5 CashAvailabilitySnapshot
+
exact Portfolio identity/custody evidence
+
exact Risk policy identity
+
exact RiskState authorization guard identity
+
ledger / reconciliation / Central identities inherited from CL5
```

CL6 не выполняет Risk evaluation.

Результат:

```text
READY_FOR_LOCKED_REVALIDATION
```

означает только:

> все read-only evidence согласованы настолько, чтобы поздний runtime gate мог выполнить отдельную locked revalidation.

Он не означает:

```text
PASS
BUY_ALLOWED
ORDER_ALLOWED
EXECUTION_AUTHORIZED
```

---

## 7. Fundamental authority boundary

CL6 не получает authority:

- менять CashLedger;
- менять Central reservations/intents;
- менять Portfolio;
- менять RiskState;
- менять RiskPolicy;
- запускать Risk evaluator;
- создавать PortfolioRiskDecision;
- создавать ExecutionAuthorization;
- вызывать `RiskRuntimeAdapter.evaluate`;
- вызывать `dispatch_authorization_guard`;
- вызывать provider transport;
- выполнять provider POST;
- выполнять BUY/SELL;
- менять GUI/runtime;
- выполнять recovery/resubmit;
- обращаться к real account;
- выполнять experiment;
- выполнять release.

Pre-POST locked revalidation и runtime adoption остаются CL7.

---

## 8. No float cash bridge

Existing predecessor Portfolio/Risk models содержат legacy float-based monetary fields.

CL6 MUST NOT использовать как authoritative exact cash:

```text
CashBalance.available
CashBalance.blocked

PortfolioRiskInput.cash_available_rub
PortfolioRiskInput.nav_rub

RiskSnapshot.cash_rub
RiskSnapshot.portfolio_equity_rub

RiskState.last_cash_rub
RiskState.last_equity_rub
```

Authoritative available cash в CL6 Risk context происходит только из:

```text
CL5 CashAvailabilitySnapshot.free_investable_cash
```

как exact CL1 `Money`.

Запрещены:

```text
float -> Decimal -> Money
Money -> float -> Money
kopeck rounding of CL5 Money
```

для authoritative Risk cash identity.

---

## 9. Reporting valuation boundary

CashLedger содержит cash effects, но не содержит полной рыночной стоимости портфеля.

Поэтому CL6 не выводит portfolio valuation из ledger.

TWR/XIRR получают valuation evidence только через immutable:

```text
PortfolioValuationPoint
```

Valuation point:

- caller-supplied;
- HMAC-bound;
- exact CL1 Money;
- reporting-only;
- никогда не используется как Risk cash;
- никогда не передаёт current-cash ownership.

---

## 10. Permitted implementation imports

Future production module может импортировать только standard library и accepted public predecessor semantics из:

```text
cash_ledger_domain.py
cash_ledger_persistence.py
cash_ledger_opening_reconciliation.py
cash_availability.py
broker_read_adapters.py

portfolio_model.py
portfolio_preflight.py

risk.py
risk_runtime.py
```

Разрешённый Risk import:

```text
RiskPolicy
RiskState
InstrumentRiskHalt
risk_state_guard_hash
```

только для read-only validation/hash.

Запрещены production imports:

```text
PortfolioRepository
CentralOrderManager
RiskRuntimeAdapter
RiskProfileStore
RiskStateStore
PortfolioRiskEvaluator
portfolio_risk_runtime
sandbox_execution_adapter
ExecutionAdapter
provider SDK
requests/httpx
sqlite3
Tkinter
EventJournal
pandas
numpy
scipy
```

Import CL6 module выполняет zero I/O и zero global-state mutation.

---

## 11. Frozen version axes

```text
CL6_CONTRACT_VERSION = 1

PORTFOLIO_VALUATION_POINT_VERSION = 1

CASH_FLOW_SUMMARY_VERSION = 1

TWR_RESULT_VERSION = 1

XIRR_RESULT_VERSION = 1

PERFORMANCE_REPORT_VERSION = 1

PORTFOLIO_IDENTITY_EVIDENCE_VERSION = 1

RISK_GUARD_EVIDENCE_VERSION = 1

PORTFOLIO_RISK_CASH_CONTEXT_VERSION = 1

CL6_CROSS_LANGUAGE_FIXTURE_VERSION = 1
```

Unknown/newer versions fail closed.

---

## 12. Frozen bounds

```text
MAX_LEDGER_EXPORT_BYTES = 16_777_216

MAX_LEDGER_TRANSACTIONS = 100_000

MAX_VALUATION_POINTS = 10_000

MAX_EXTERNAL_FLOW_TIMESTAMPS = 10_000

MAX_RATIONAL_DECIMAL_DIGITS = 4_096

MAX_PORTFOLIO_POSITIONS = 100_000

MAX_PORTFOLIO_PENDING_ORDERS_TOTAL = 100_000

MAX_PORTFOLIO_CANONICAL_BYTES = 16_777_216

MAX_RISK_HALTS = 10_000

MAX_RECORDED_EXECUTION_IDS = 512

MAX_RISK_CANONICAL_BYTES = 1_048_576

MAX_STRING_SCALARS = 4_096

MAX_REVISION = 9_223_372_036_854_775_807

MAX_CONTEXT_AGE_NS = 120_000_000_000

MAX_CONTEXT_SKEW_NS = 10_000_000_000

XIRR_DECIMAL_PRECISION = 80

XIRR_MAX_ITERATIONS = 256

XIRR_ROOT_INTERVAL_EPSILON = 1E-24

XIRR_MIN_RATE = -0.999999999

XIRR_MAX_RATE = 1000

XIRR_OUTPUT_DECIMALS = 12
```

Все bounds inclusive, если явно не указано обратное.

---

## 13. Canonical primitives

CL6 canonical JSON:

```python
json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("ascii")
```

Запрещены:

- BOM;
- duplicate keys;
- external whitespace;
- NaN;
- Infinity;
- binary float inside CL6 authoritative canonical DTO;
- Unicode surrogate;
- non-canonical reserialization.

SHA-256:

```text
[0-9a-f]{64}
```

HMAC-SHA-256:

```python
hmac.new(
    identity_key,
    canonical_preimage,
    hashlib.sha256,
).hexdigest()
```

`identity_key`:

```text
exact bytes length 32..64
```

`identity_key_id`:

```text
[A-Z][A-Z0-9_]{0,63}
```

CL6 timestamp:

```text
YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ
```

с real Gregorian UTC date.

### 13.1. Canonical scalar encoding table

Все revision fields имеют exact in-memory type `int`, `bool` запрещён, range:

```text
0 <= revision <= MAX_REVISION
```

В canonical JSON следующие fields кодируются decimal strings без leading zero:

```text
PortfolioValuationPoint.portfolio_revision

PerformanceReport.ledger_revision

PortfolioIdentityEvidence.portfolio_revision

PortfolioRiskCashContext.ledger_revision
PortfolioRiskCashContext.central_order_revision
PortfolioRiskCashContext.portfolio_revision
```

Следующие non-negative count/version fields кодируются JSON integer numbers:

```text
CashFlowSummary.*_count

TWRResult.subperiod_count

XIRRResult.sign_change_count
XIRRResult.cash_flow_count

PortfolioIdentityEvidence.portfolio_schema_version
RiskGuardEvidence.risk_state_version

all CL6 DTO version fields
```

KAT sections 85–95 используют именно эти representations. Opaque digest не является заменой этой table.

---

# PART A — REPORTING

## 14. Reporting public enums

### ValuationPhase

```text
PERIOD_START
PRE_EXTERNAL_FLOW
PERIOD_END
```

### ReportingCategory

```text
OPENING
EXTERNAL_FLOW
INVESTMENT_INCOME
EXPENSE
INTERNAL_SETTLEMENT
MANUAL_ADJUSTMENT
```

### MetricStatus

```text
AVAILABLE
UNAVAILABLE
AMBIGUOUS
```

### MetricReason

```text
AVAILABLE

LEDGER_INCOMPLETE

VALUATION_MISSING

NON_POSITIVE_SUBPERIOD_BASE

END_FLOW_VALUATION_AMBIGUOUS

MANUAL_ADJUSTMENT_PRESENT

OPENING_INSIDE_PERIOD

RATIONAL_LIMIT_EXCEEDED

XIRR_NO_SIGN_CHANGE

XIRR_MULTIPLE_SIGN_CHANGES

XIRR_ROOT_OUT_OF_RANGE

XIRR_NUMERIC_FAILURE
```

### ReportStatus

```text
COMPLETE
DEGRADED
```

---

## 15. Reporting public DTOs

CL6 reporting exports:

```text
PortfolioValuationPoint
CashFlowSummary
TWRResult
XIRRResult
PerformanceReport
```

Все immutable.

Каждый имеет:

```text
to_canonical_dict()
canonical_bytes
sha256
```

где `sha256 = SHA256(canonical_bytes)`.

---

## 16. Reporting public functions

```python
build_portfolio_valuation_point(
    total_value: Money,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    phase: ValuationPhase,
    portfolio_revision: int,
    portfolio_decision_checksum: str,
    portfolio_document_checksum: str,
    source_sha256: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioValuationPoint
```

```python
build_performance_report(
    ledger_export_bytes: bytes,
    valuations: tuple[PortfolioValuationPoint, ...],
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    period_start: str,
    period_end: str,
    generated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PerformanceReport
```

No filesystem/output path входит в CL6 API.

---

## 17. Reporting environment

Version 1 поддерживает только:

```text
SANDBOX
RUB
```

Иной environment/currency:

```text
ENVIRONMENT_UNSUPPORTED
CURRENCY_UNSUPPORTED
```

---

## 18. PortfolioValuationPoint exact semantics

`PortfolioValuationPoint` exact fields:

```text
account_scope_sha256

as_of

environment

identity_key_id

phase

portfolio_decision_checksum

portfolio_document_checksum

portfolio_revision

source_sha256

total_value

valuation_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl6-portfolio-valuation-point
```

`total_value` — exact nested CL1 Money.

Требуется:

```text
currency = RUB
total_value.minor_units >= 0
```

`portfolio_revision` — non-negative plain integer in memory, canonical decimal string в JSON.

`portfolio_decision_checksum`,
`portfolio_document_checksum`,
`source_sha256` — lowercase SHA-256.

---

## 19. PortfolioValuationPoint identity

`valuation_identity_sha256` — HMAC exact canonical preimage:

```json
{
  "account_scope_sha256": "<sha>",
  "as_of": "<timestamp>",
  "domain": "v3.10-cl6-portfolio-valuation-point-identity",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "phase": "<ValuationPhase>",
  "portfolio_decision_checksum": "<sha>",
  "portfolio_document_checksum": "<sha>",
  "portfolio_revision": "<decimal>",
  "source_sha256": "<sha>",
  "total_value": "<exact nested CL1 Money object>",
  "version": 1
}
```

Изменение любого authoritative field меняет HMAC.

Valuation point не утверждает, что valuation является broker cash.

---

## 20. Ledger export validation

`build_performance_report` принимает только canonical CL2 export bytes.

До reporting classification implementation обязана:

1. require exact `bytes`;
2. require size `<= MAX_LEDGER_EXPORT_BYTES`;
3. canonical-parse export через accepted CL2 parser;
4. reject duplicate keys/non-canonical bytes;
5. revalidate export через accepted CL4 `project_shadow_cash`;
6. require projection account/environment correlation;
7. bind:
   - `ledger_export_sha256`;
   - `ledger_revision`;
   - `ledger_head_sha256`;
   - `ledger_projection_sha256`;
   - `projection.complete`;
   - incompleteness kinds;
8. parse transaction canonical bytes through accepted CL1 parser;
9. recompute every transaction full/source/economic identity;
10. require transaction count `<= MAX_LEDGER_TRANSACTIONS`.

Exact CL4 revalidation call:

```python
project_shadow_cash(
    ledger_export_bytes,
    account_scope_sha256=account_scope_sha256,
    environment=environment,
    as_of=generated_at,
    identity_key=identity_key,
)
```

Required correlation:

```text
projection.account_scope_sha256 == account_scope_sha256
projection.environment == environment
projection.as_of == generated_at
```

`generated_at`, а не `period_end`, является ledger-evidence observation time. Transactions и observations later than `period_end` but not later than `generated_at` остаются частью full export validation/projection identity, но не входят в reporting interval и сами по себе не создают `PROOF_BEFORE_LEDGER_EFFECT`/`PROOF_BEFORE_LEDGER_EVIDENCE`.

CL2 export может содержать несколько account scopes. После полной validation всего export exact reporting transaction set определяется только как:

```text
transaction.source.account_scope_sha256 == account_scope_sha256
```

Foreign-account transactions/observations:

- полностью identity/graph-validated как часть export;
- не входят в `transaction_count` или category counts/effects;
- не создают external-flow timestamps;
- не участвуют в TWR/XIRR;
- не делают target-account report invalid только своим присутствием.

Для target-account transaction с `reversal_of_sha256` или `corrects_sha256` exact target обязан существовать и иметь тот же `account_scope_sha256`. Cross-account lineage:

```text
ACCOUNT_SCOPE_INVALID
```

Missing/ambiguous same-account target:

```text
LEDGER_GRAPH_INVALID
```

CL6 не чинит ledger export и не пропускает повреждённые entries.

---

## 21. Reporting interval

Требуется:

```text
period_start < period_end <= generated_at
```

Transaction report interval:

```text
(period_start, period_end]
```

то есть:

```text
effective_at > period_start
effective_at <= period_end
```

Transaction в точности на `period_start` относится к opening state периода и не считается period cash flow.

---

## 22. Reporting classification matrix

Для non-REVERSAL transaction:

```text
OPENING_BALANCE
-> OPENING

DEPOSIT
WITHDRAWAL
-> EXTERNAL_FLOW

DIVIDEND
COUPON
INTEREST
-> INVESTMENT_INCOME

COMMISSION
TAX
REFUND
-> EXPENSE

TRADE_SETTLEMENT
-> INTERNAL_SETTLEMENT

MANUAL_ADJUSTMENT
-> MANUAL_ADJUSTMENT
```

### Mandatory invariants

```text
TRADE_SETTLEMENT != external contribution

DEPOSIT/WITHDRAWAL != strategy P&L

DIVIDEND/COUPON/INTEREST != external contribution

COMMISSION/TAX/REFUND != external contribution
```

---

## 23. Broker-cash effect

Для каждой accepted transaction CL6 использует только posting:

```text
LedgerAccount.ASSET_BROKER_CASH
```

как signed reporting cash effect.

Требуется ровно один такой posting на transaction.

Его `Money.minor_units` используется без rounding.

Положительное значение:

```text
cash increase
```

Отрицательное:

```text
cash decrease
```

---

## 24. Reversal and correction reporting

REVERSAL не получает самостоятельную reporting category.

Он обязан:

1. иметь valid `reversal_of_sha256`;
2. найти exact target transaction;
3. унаследовать reporting category target;
4. использовать уже отрицательный/обратный broker-cash posting reversal transaction;
5. не инвертировать сумму второй раз.

Correction transaction:

```text
corrects_sha256 != null
classification != REVERSAL
```

классифицируется по своей собственной classification.

Таким образом:

```text
original + reversal
```

cancel в исходной reporting category, а correction остаётся в своей фактической category.

Missing/ambiguous reversal target:

```text
LEDGER_GRAPH_INVALID
```

---

## 25. CashFlowSummary

Exact fields:

```text
transaction_count

opening_count
opening_cash_effect

external_flow_count
external_flow_cash_effect

investment_income_count
investment_income_cash_effect

expense_count
expense_cash_effect

internal_settlement_count
internal_settlement_cash_effect

manual_adjustment_count
manual_adjustment_cash_effect

version
```

Canonical domain:

```text
v3.10-cl6-cash-flow-summary
```

Все cash effects — signed exact CL1 Money.

Counts — plain non-negative integers.

---

## 26. Valuation set

Для потенциально AVAILABLE TWR/XIRR требуется:

```text
exactly one PERIOD_START at period_start

exactly one PERIOD_END at period_end

exactly one PRE_EXTERNAL_FLOW
for every distinct EXTERNAL_FLOW timestamp inside period
```

`PRE_EXTERNAL_FLOW` at timestamp без external flow запрещён.

Duplicate valuation point for same `(phase, as_of)`:

```text
VALUATION_SET_INVALID
```

Любой point с phase/timestamp, не требуемым exact period/external-flow set, включая `PRE_EXTERNAL_FLOW` без target-account external flow:

```text
VALUATION_SET_INVALID
```

Отсутствие одного или нескольких required points не является structural exception после того, как все supplied points прошли exact DTO/HMAC/correlation validation. Оно создаёт valid degraded report:

```text
TWR.status = UNAVAILABLE
TWR.reason = VALUATION_MISSING

XIRR.status = UNAVAILABLE
XIRR.reason = VALUATION_MISSING

report_status = DEGRADED
```

с canonical `valuation_set_sha256` над фактически supplied valid points.

Количество points:

```text
<= MAX_VALUATION_POINTS
```

Все points обязаны иметь одинаковые:

```text
account_scope_sha256
environment
identity_key_id
```

и каждый HMAC revalidates exact.

---

## 27. Valuation-set identity

Points canonical order:

```text
PERIOD_START

all PRE_EXTERNAL_FLOW ordered by as_of ascending

PERIOD_END
```

Exact valuation-set object:

```json
{
  "account_scope_sha256": "<sha>",
  "domain": "v3.10-cl6-valuation-set",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "period_end": "<timestamp>",
  "period_start": "<timestamp>",
  "point_sha256": ["<sha>", "..."],
  "version": 1
}
```

`valuation_set_sha256` = SHA-256 exact canonical bytes.

---

## 28. Reporting completeness gate

If accepted CL4 ledger projection:

```text
complete == False
```

CashFlowSummary всё ещё может быть сформирован из committed ledger state.

Но:

```text
TWR.status = UNAVAILABLE
XIRR.status = UNAVAILABLE
reason = LEDGER_INCOMPLETE
report_status = DEGRADED
```

Unresolved evidence никогда не интерпретируется как zero cash effect.

---

## 29. Manual adjustment rule

Если внутри period имеется хотя бы одна effective:

```text
MANUAL_ADJUSTMENT
```

или reversal/correction chain, чей final report contribution содержит manual adjustment semantics:

```text
TWR = UNAVAILABLE
XIRR = UNAVAILABLE
reason = MANUAL_ADJUSTMENT_PRESENT
```

CashFlowSummary остаётся valid.

---

## 30. Opening-inside-period rule

Если:

```text
OPENING_BALANCE
```

попадает внутрь:

```text
(period_start, period_end]
```

то:

```text
TWR = UNAVAILABLE
XIRR = UNAVAILABLE
reason = OPENING_INSIDE_PERIOD
```

Период, начинающийся exact на opening cutoff, допустим, поскольку interval left-open.

### 30.1. Exact metric-reason precedence

После structural validation exact primary outcome определяется независимо для каждого metric.

TWR precedence, first applicable wins:

```text
1. LEDGER_INCOMPLETE
2. MANUAL_ADJUSTMENT_PRESENT
3. OPENING_INSIDE_PERIOD
4. VALUATION_MISSING
5. END_FLOW_VALUATION_AMBIGUOUS
6. NON_POSITIVE_SUBPERIOD_BASE
7. RATIONAL_LIMIT_EXCEEDED
8. AVAILABLE
```

XIRR precedence, first applicable wins:

```text
1. LEDGER_INCOMPLETE
2. MANUAL_ADJUSTMENT_PRESENT
3. OPENING_INSIDE_PERIOD
4. VALUATION_MISSING
5. XIRR_NO_SIGN_CHANGE
6. XIRR_MULTIPLE_SIGN_CHANGES
7. XIRR_ROOT_OUT_OF_RANGE
8. XIRR_NUMERIC_FAILURE
9. AVAILABLE
```

`AMBIGUOUS` применяется только для `XIRR_MULTIPLE_SIGN_CHANGES`; остальные non-AVAILABLE outcomes выше имеют status `UNAVAILABLE`.

---

## 31. TWR external-flow grouping

Все EXTERNAL_FLOW effects с одинаковым `effective_at` суммируются exact integer arithmetic:

```text
F(t)
```

где positive:

```text
capital contribution into broker account
```

negative:

```text
capital withdrawal
```

Не используется binary float.

---

## 32. TWR algorithm

Пусть:

```text
V0 = PERIOD_START valuation
```

Для каждого external-flow timestamp `t_i` в chronological order:

```text
P_i = PRE_EXTERNAL_FLOW valuation at t_i

F_i = net external broker-cash flow at t_i
```

Начальный post-flow base:

```text
B_0 = V0
```

Subperiod growth factor:

```text
G_i = P_i / B_(i-1)
```

После external flow:

```text
B_i = P_i + F_i
```

Final factor:

```text
G_final = V_end / B_last
```

TWR total growth:

```text
G = product(all G_i) * G_final
```

TWR return:

```text
R = G - 1
```

Все операции выполняются как exact integer rational arithmetic над Money minor units.

---

## 33. TWR denominator safety

Каждый denominator:

```text
B_i
```

обязан быть strictly positive.

Если нет:

```text
TWR.status = UNAVAILABLE
reason = NON_POSITIVE_SUBPERIOD_BASE
```

Если numerator/denominator reduced rational превышает:

```text
MAX_RATIONAL_DECIMAL_DIGITS
```

для любого numerator/denominator:

```text
UNAVAILABLE / RATIONAL_LIMIT_EXCEEDED
```

---

## 34. Flow at exact period end

Если external flow происходит exact в `period_end`, одновременно существуют:

```text
PRE_EXTERNAL_FLOW(period_end)
PERIOD_END(period_end)
```

Для unambiguous zero-duration final segment требуется:

```text
PERIOD_END value
==
PRE_EXTERNAL_FLOW value + net external flow at period_end
```

Иначе:

```text
TWR = UNAVAILABLE
reason = END_FLOW_VALUATION_AMBIGUOUS
```

---

## 35. TWRResult

Exact fields:

```text
status
reason

growth_numerator
growth_denominator

return_numerator
return_denominator

rate_decimal

subperiod_count

version
```

Canonical domain:

```text
v3.10-cl6-twr-result
```

AVAILABLE result:

- rationals reduced by GCD;
- denominator strictly positive;
- integer numerators/denominators canonical decimal strings;
- `rate_decimal` exactly 12 fractional decimal digits;
- output rounding `ROUND_HALF_EVEN`.

UNAVAILABLE result:

```text
growth_numerator = null
growth_denominator = null
return_numerator = null
return_denominator = null
rate_decimal = null
```

---

## 36. XIRR cash-flow construction

Investor-perspective cash flows:

### Period start

```text
-period_start_value
```

at `period_start`.

### External broker flow

For exact broker cash effect:

```text
F(t)
```

investor XIRR flow:

```text
-F(t)
```

Therefore:

```text
deposit +100 RUB -> investor -100 RUB

withdrawal -100 RUB -> investor +100 RUB
```

### Period end

```text
+period_end_value
```

at `period_end`.

Flows with identical timestamps are grouped exactly before root analysis.

Zero grouped cash flows are removed.

---

## 37. XIRR day-count basis

Frozen:

```text
ACT_365_FIXED
```

Exact elapsed year fraction:

```text
elapsed_nanoseconds
/
31_536_000_000_000_000
```

No date-only truncation.

No local timezone conversion.

---

## 38. XIRR sign-change policy

После chronological grouping и удаления zero flows считается число sign changes.

### 0 sign changes

```text
UNAVAILABLE
XIRR_NO_SIGN_CHANGE
```

### >1 sign changes

CL6 V1 не пытается доказать uniqueness сложного multi-root profile.

Возвращается консервативно:

```text
AMBIGUOUS
XIRR_MULTIPLE_SIGN_CHANGES
```

`rate_decimal = null`.

### exactly 1 sign change

Разрешён deterministic root search.

---

## 39. XIRR equation

Для rate `r`:

```text
r > -1
```

NPV:

```text
Σ CF_i /
exp(
    year_fraction_i * ln(1 + r)
)
```

Root satisfies:

```text
NPV(r) = 0
```

Money minor units преобразуются в exact Decimal RUB через division by exact `1_000_000_000`, не через float.

---

## 40. XIRR numerical contract

Standard-library `decimal` only.

Frozen context:

```text
precision = 80
rounding = ROUND_HALF_EVEN
```

Search interval:

```text
[-0.999999999, 1000]
```

Если endpoint values не bracket zero:

```text
UNAVAILABLE
XIRR_ROOT_OUT_OF_RANGE
```

Bisection:

```text
max iterations = 256
```

Stop when:

```text
interval width <= 1E-24
```

или exact midpoint NPV equals zero.

Output:

```text
rate_decimal
```

quantized to exactly 12 fractional digits using `ROUND_HALF_EVEN`.

Decimal `ln/exp` error/non-finite internal state:

```text
UNAVAILABLE
XIRR_NUMERIC_FAILURE
```

---

## 41. XIRRResult

Exact fields:

```text
status
reason
rate_decimal
sign_change_count
cash_flow_count
day_count_basis
algorithm
version
```

Canonical domain:

```text
v3.10-cl6-xirr-result
```

Frozen:

```text
day_count_basis = ACT_365_FIXED
algorithm = DECIMAL_BISECTION_V1
```

---

## 42. PerformanceReport status

```text
COMPLETE
```

iff:

```text
ledger_complete == true
TWR.status == AVAILABLE
XIRR.status == AVAILABLE
```

Otherwise:

```text
DEGRADED
```

A DEGRADED report remains a valid immutable report.

---

## 43. PerformanceReport exact fields

```text
account_scope_sha256

environment

period_start
period_end
generated_at

ledger_export_sha256
ledger_revision
ledger_head_sha256
ledger_projection_sha256

ledger_complete
ledger_incompleteness_kinds

valuation_set_sha256

cash_flow_summary

twr

xirr

report_status

identity_key_id

report_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl6-performance-report
```

`ledger_incompleteness_kinds` is sorted unique token array.

No raw observation, transaction, account or provider identifiers are exported.

---

## 44. PerformanceReport identity

`report_identity_sha256` = HMAC over same exact fields except itself, with domain:

```text
v3.10-cl6-performance-report-identity
```

Full nested canonical objects participate:

```text
cash_flow_summary
twr
xirr
```

не только их hashes.

---

## 45. Reporting independence invariant

```text
REPORTING METRIC UNAVAILABLE
```

или:

```text
PerformanceReport.report_status = DEGRADED
```

MUST NOT:

- block execution;
- allow execution;
- mutate Risk;
- mutate Central;
- change CashAvailability;
- change Portfolio.

Reporting status is never consumed as authorization by Surface B.

---

# PART B — PORTFOLIO RISK CASH CONTEXT

## 46. Risk-context public enums

### RiskCashContextStatus

```text
READY_FOR_LOCKED_REVALIDATION
BLOCKED
```

### RiskCashContextReason

```text
READY

CASH_AVAILABILITY_NOT_READY

CASH_AVAILABILITY_STALE

PORTFOLIO_NOT_READY

PORTFOLIO_STALE

RISK_GUARD_STALE

MIXED_EVIDENCE_SNAPSHOT
```

---

## 47. Risk-context public DTOs

```text
PortfolioIdentityEvidence

RiskGuardEvidence

PortfolioRiskCashContext
```

Все immutable.

Каждый имеет:

```text
to_canonical_dict()
canonical_bytes
sha256
```

---

## 48. Risk-context public functions

```python
build_portfolio_identity_evidence(
    lease: PortfolioSnapshotLease,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioIdentityEvidence
```

```python
build_risk_guard_evidence(
    policy: RiskPolicy,
    state: RiskState,
    *,
    raw_account_id: str,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    captured_at: str,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> RiskGuardEvidence
```

```python
build_portfolio_risk_cash_context(
    ledger_export_bytes: bytes,
    reconciliation: CashReconciliation,
    positions: BrokerPositionsCashProof,
    reservations: CentralReservationProjection,
    availability: CashAvailabilitySnapshot,
    portfolio: PortfolioIdentityEvidence,
    risk_guard: RiskGuardEvidence,
    *,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioRiskCashContext
```

---

## 49. Portfolio evidence source

CL6 принимает exact predecessor:

```text
PortfolioSnapshotLease
```

но использует из Portfolio только:

- account custody;
- revision;
- decision checksum;
- document checksum;
- snapshot timestamp;
- freshness;
- migration/source state;
- blocking state.

CL6 не использует Portfolio float cash/valuation fields для Risk cash.

---

## 50. Portfolio graph preflight

До вызова любого instance virtual method implementation обязана bounded-проверить exact graph.

Root types:

```text
type(lease) is PortfolioSnapshotLease

type(lease.state) is PortfolioState
```

Nested accepted types:

```text
AccountState
CashBalance
PositionState
PortfolioTarget | None
PositionOwnership | None
PendingOrderState
ReconciliationResult
PortfolioMigrationMetadata
```

Subclasses/proxies запрещены.

Bounds:

```text
len(positions) <= MAX_PORTFOLIO_POSITIONS

total pending orders <= MAX_PORTFOLIO_PENDING_ORDERS_TOTAL

canonical state bytes <= MAX_PORTFOLIO_CANONICAL_BYTES

all strings <= MAX_STRING_SCALARS
```

All ints must satisfy predecessor semantic ranges.

All float fields must be finite where predecessor permits float.

CL6 does not reinterpret those floats as exact Money.

---

## 51. Portfolio round-trip verification

После bounded preflight:

1. invoke accepted unbound serialization;
2. reconstruct through accepted `PortfolioState.from_dict`;
3. require semantic round-trip;
4. recompute `state.decision_sha256`;
5. recompute document checksum using predecessor formula:

```python
SHA256(
    json.dumps(
        state.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
)
```

6. require:

```text
lease.revision == state.revision

lease.decision_checksum == state.decision_sha256

lease.document_checksum == recomputed document checksum
```

Mismatch:

```text
PORTFOLIO_EVIDENCE_INVALID
```

---

## 52. Portfolio account-scope binding

CL6 reproduces accepted CL3 account-scope HMAC using:

```text
lease.state.account_id
```

Exact preimage:

```json
{
  "account_id": "<raw>",
  "domain": "v3.10-cl3-account-scope",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "provider": "TBANK",
  "version": 1
}
```

Expected HMAC must equal:

```text
account_scope_sha256
```

Raw account ID не выходит в CL6 output/error/repr.

---

## 53. Portfolio timestamp normalization

Predecessor Portfolio timestamps may use timezone-aware ISO-8601 rather than CL1 nanosecond grammar.

CL6 accepts only timezone-aware ISO-8601 with:

```text
YYYY-MM-DDTHH:MM:SS
optional .ffffff
Z or explicit ±HH:MM
```

fraction length `0..6`.

It normalizes to UTC exact CL6 form:

```text
YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ
```

where microseconds map exactly to nanoseconds by:

```text
nanoseconds = microseconds * 1000
```

No lossier timestamp form is accepted.

Normalized:

```text
portfolio_snapshot_at
portfolio_captured_at
```

participate in CL6 identity.

Exact source mapping:

```text
portfolio_snapshot_at = normalize(lease.state.snapshot_at)

portfolio_captured_at = normalize(lease.leased_at)
```

`lease.state.generated_at` остаётся bound через exact document checksum, но не подменяет `portfolio_captured_at`.

---

## 54. Portfolio readiness semantics

`PortfolioIdentityEvidence` может быть structurally valid даже если Portfolio not ready.

Context READY требует:

```text
portfolio_schema_version == 2

portfolio_source == CANONICAL

migration_status == COMPLETED

legacy_read_path_enabled == false

freshness == FRESH

blocking == false
```

Кроме того, exact accepted predecessor blocking statuses:

```text
state_status in {BLOCKED, MANUAL_REVIEW_REQUIRED}
```

всегда дают:

```text
BLOCKED / PORTFOLIO_NOT_READY
```

даже при `blocking == false`. Иные `state_status` сохраняются как evidence; CL6 не создаёт для них новый interpretation table.

---

## 55. PortfolioIdentityEvidence exact fields

```text
account_scope_sha256

environment

captured_at

portfolio_snapshot_at

portfolio_revision

portfolio_decision_checksum

portfolio_document_checksum

portfolio_schema_version

portfolio_source

migration_status

legacy_read_path_enabled

freshness

state_status

blocking

identity_key_id

evidence_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl6-portfolio-identity-evidence
```

---

## 56. PortfolioIdentityEvidence HMAC

Exact identity preimage использует те же fields без `evidence_identity_sha256`, domain:

```text
v3.10-cl6-portfolio-identity-evidence-identity
```

HMAC binds all evidence.

---

## 57. Risk policy/state boundary

CL6 принимает exact:

```text
RiskPolicy
RiskState
```

только для создания immutable guard evidence.

Он не:

- оценивает Risk;
- обновляет RiskState;
- пишет Risk persistence;
- reset-ит baseline;
- меняет kill switch;
- выполняет resync.

---

## 58. Risk graph preflight

До hash helper:

```text
type(policy) is RiskPolicy

type(state) is RiskState
```

Требуется:

```text
state.version == current predecessor RISK_STATE_VERSION
```

Nested:

```text
type(state.instrument_kill_switches) is tuple
every halt exact InstrumentRiskHalt

type(state.recorded_execution_ids) is tuple
every id exact str

type(policy.asset_class_concentration_limits) is tuple
every entry exact 2-tuple
```

Bounds:

```text
len(instrument_kill_switches) <= MAX_RISK_HALTS

len(recorded_execution_ids) <= MAX_RECORDED_EXECUTION_IDS

all strings <= MAX_STRING_SCALARS

canonical bounded source representation <= MAX_RISK_CANONICAL_BYTES
```

Float fields must be finite where predecessor permits them.

CL6 does not convert any of them into exact Money.

---

## 59. Risk hashes

After exact preflight:

```text
risk_policy_hash =
accepted RiskPolicy.policy_hash
```

and:

```text
risk_state_guard_hash =
accepted risk_state_guard_hash(state)
```

CL6 does not redefine either predecessor hash algorithm.

---

## 60. Risk account binding

`build_risk_guard_evidence` получает ephemeral:

```text
raw_account_id
```

CL6 reproduces accepted CL3 account-scope HMAC и требует equality с supplied:

```text
account_scope_sha256
```

Raw account ID не сохраняется.

Это read-only binding.

Actual RiskStateStore account lock/revalidation остаётся CL7.

---

## 61. RiskGuardEvidence exact fields

```text
account_scope_sha256

environment

captured_at

risk_policy_hash

risk_state_guard_hash

risk_state_version

identity_key_id

evidence_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl6-risk-guard-evidence
```

---

## 62. RiskGuardEvidence identity

HMAC exact same fields без identity SHA, domain:

```text
v3.10-cl6-risk-guard-evidence-identity
```

---

## 63. CL5 exact revalidation

`build_portfolio_risk_cash_context` MUST NOT trust detached `CashAvailabilitySnapshot`.

Он обязан recompute accepted CL5 result through:

```python
build_cash_availability(
    ledger_export_bytes,
    reconciliation,
    positions,
    reservations,
    evaluated_at=availability.evaluated_at,
    identity_key=identity_key,
)
```

и require:

```text
rebuilt.canonical_bytes
==
availability.canonical_bytes
```

Any mismatch:

```text
CASH_AVAILABILITY_INVALID
```

---

## 64. CL5 READY requirement

Context не может быть ready unless:

```text
availability.status == READY

availability.availability_reason == READY

availability.free_investable_cash != null
```

Если valid CL5 snapshot не READY:

```text
context.status = BLOCKED

reason = CASH_AVAILABILITY_NOT_READY

free_investable_cash = null
```

CL6 не пытается сделать CL5 result менее консервативным.

---

## 65. Exact cash source

В READY context:

```text
free_investable_cash
```

копируется byte-identically из:

```text
availability.free_investable_cash
```

как CL1 `Money`.

Никакой Portfolio/Risk float cash не участвует.

---

## 66. Risk-context correlation set

Все evidence должны иметь same:

```text
account_scope_sha256
environment = SANDBOX
currency = RUB
identity_key_id
```

Context прямо связывает:

```text
availability.sha256

ledger_export_sha256
ledger_revision
ledger_head_sha256

reconciliation_sha256

central_order_revision
central_reservation_projection_hash

portfolio evidence SHA
portfolio revision
portfolio decision checksum
portfolio document checksum

risk guard evidence SHA
risk policy hash
risk state guard hash
```

Mismatch identities:

```text
ACCOUNT_SCOPE_INVALID
EVIDENCE_CORRELATION_INVALID
```

а не silent rebinding.

---

## 67. Context timestamp set

Context directly binds:

```text
availability.evaluated_at

availability.broker_cash_as_of

availability.broker_positions_as_of

availability.central_projection_evaluated_at

portfolio.portfolio_snapshot_at

portfolio.captured_at

risk_guard.captured_at

context.evaluated_at
```

Все normalized to exact CL6 timestamp.

---

## 68. Dependency-from-future rule

Ни один dependency timestamp не может быть later than:

```text
context.evaluated_at
```

Violation:

```text
DEPENDENCY_FROM_FUTURE
```

as structural closed failure, not a BLOCKED context.

---

## 69. Context freshness

For `READY_FOR_LOCKED_REVALIDATION`, age relative to context `evaluated_at` of each:

```text
availability.evaluated_at

broker_cash_as_of

broker_positions_as_of

central_projection_evaluated_at

portfolio_snapshot_at

portfolio.captured_at

risk_guard.captured_at
```

must satisfy:

```text
age <= MAX_CONTEXT_AGE_NS
```

Disposition precedence:

```text
availability stale
-> CASH_AVAILABILITY_STALE

portfolio snapshot/capture stale
-> PORTFOLIO_STALE

risk guard stale
-> RISK_GUARD_STALE
```

---

## 70. Cross-evidence skew

For READY context:

```text
max(
    availability.evaluated_at,
    broker_cash_as_of,
    broker_positions_as_of,
    central_projection_evaluated_at,
    portfolio.portfolio_snapshot_at,
    portfolio.captured_at,
    risk_guard.captured_at
)
-
min(...)
<= MAX_CONTEXT_SKEW_NS
```

Otherwise:

```text
BLOCKED
MIXED_EVIDENCE_SNAPSHOT
```

Exact amount equality does not override temporal incoherence.

---

## 71. RiskCashContext disposition precedence

After all structural validation/correlation:

1. CL5 snapshot not READY:
   ```text
   BLOCKED / CASH_AVAILABILITY_NOT_READY
   ```

2. CL5 dependency stale:
   ```text
   BLOCKED / CASH_AVAILABILITY_STALE
   ```

3. Portfolio schema/source/migration/freshness/blocking or predecessor blocking `state_status` not ready:
   ```text
   BLOCKED / PORTFOLIO_NOT_READY
   ```

4. Portfolio timestamp stale:
   ```text
   BLOCKED / PORTFOLIO_STALE
   ```

5. Risk guard stale:
   ```text
   BLOCKED / RISK_GUARD_STALE
   ```

6. cross-evidence skew:
   ```text
   BLOCKED / MIXED_EVIDENCE_SNAPSHOT
   ```

7. otherwise:
   ```text
   READY_FOR_LOCKED_REVALIDATION / READY
   ```

Exactly one primary reason.

---

## 72. PortfolioRiskCashContext exact fields

```text
account_scope_sha256

environment

evaluated_at

status
reason

availability_sha256
availability_status
availability_reason
availability_evaluated_at

broker_cash_as_of
broker_positions_as_of

free_investable_cash

ledger_export_sha256
ledger_revision
ledger_head_sha256

reconciliation_sha256

central_order_revision
reservation_projection_hash
central_projection_evaluated_at

portfolio_evidence_sha256
portfolio_revision
portfolio_decision_checksum
portfolio_document_checksum
portfolio_snapshot_at
portfolio_captured_at

risk_guard_evidence_sha256
risk_guard_captured_at
risk_policy_hash
risk_state_guard_hash

identity_key_id

context_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl6-portfolio-risk-cash-context
```

For BLOCKED:

```text
free_investable_cash = null
```

---

## 73. PortfolioRiskCashContext identity

`context_identity_sha256` = HMAC exact same authoritative fields except itself with domain:

```text
v3.10-cl6-portfolio-risk-cash-context-identity
```

Changing any ledger, availability, Portfolio, Central, Risk or timestamp identity changes context HMAC.

---

## 74. READY_FOR_LOCKED_REVALIDATION semantics

This status means only:

```text
read-only evidence coherent at context build time
```

It explicitly does NOT mean:

```text
PortfolioRisk PASS
Central admission
reservation created
order allowed
provider POST allowed
```

Any future use must perform separate locked revalidation.

---

## 75. Mandatory CL7 revalidation boundary

CL6 freezes the evidence fields that CL7 must later recheck immediately before economic mutation:

```text
portfolio revision
portfolio decision checksum
portfolio document checksum

central revision
reservation projection hash

risk policy hash
risk state guard hash

ledger revision
ledger head

reconciliation SHA

CashAvailability SHA
CashAvailability freshness

PortfolioRiskCashContext identity
```

CL6 itself performs no such mutation or lock orchestration.

---

## 76. Reporting/Risk isolation

Mandatory:

```text
PerformanceReport
```

is never input to:

```text
build_portfolio_risk_cash_context
```

and:

```text
PortfolioRiskCashContext
```

is never required to construct:

```text
PerformanceReport
```

Thus:

```text
report unavailable/degraded
!=
Risk context blocked
```

and:

```text
Risk context blocked
!=
historical report invalid
```

---

# PUBLIC ERROR CONTRACT

## 77. Closed CL6Reason set

```text
TYPE_INVALID

VERSION_UNSUPPORTED

ENVIRONMENT_UNSUPPORTED

CURRENCY_UNSUPPORTED

ACCOUNT_SCOPE_INVALID

IDENTITY_KEY_INVALID

TIMESTAMP_INVALID

PERIOD_INVALID

LEDGER_EXPORT_INVALID

LEDGER_GRAPH_INVALID

LEDGER_IDENTITY_MISMATCH

VALUATION_INVALID

VALUATION_IDENTITY_INVALID

VALUATION_SET_INVALID

PORTFOLIO_EVIDENCE_INVALID

RISK_EVIDENCE_INVALID

CASH_AVAILABILITY_INVALID

EVIDENCE_CORRELATION_INVALID

DEPENDENCY_FROM_FUTURE

ARITHMETIC_OVERFLOW

NUMERIC_BOUND_EXCEEDED

CANONICAL_FORMAT_INVALID

INTERNAL_BOUNDARY_FAILED
```

Adding reason requires version transition or contract rescope.

---

## 78. CL6Error privacy boundary

`CL6Error` carries only:

```text
reason
optional finite predecessor cause reason
optional privacy-safe SHA/revision/stage evidence
```

Never exposes:

- raw Account ID;
- provider payload;
- raw transaction canonical body;
- credentials;
- token;
- filesystem path;
- raw exception text;
- Risk profile contents;
- instrument IDs;
- order IDs.

Public exception chaining:

```text
forbidden
```

Unexpected predecessor exception:

```text
INTERNAL_BOUNDARY_FAILED
```

without raw cause text.

### 78.1. Exact predecessor error translation

Expected predecessor exceptions никогда не проходят наружу напрямую. `cause_reason`, если exact predecessor reason существует и входит в finite accepted enum, сохраняется отдельно; primary CL6 reason определяется только этой table.

#### Reporting ledger boundary

```text
CL2 canonical parse failure
-> LEDGER_EXPORT_INVALID

CL4 project_shadow_cash / LEDGER_EXPORT_INVALID
-> LEDGER_EXPORT_INVALID

CL4 project_shadow_cash /
    LEDGER_GRAPH_INVALID |
    LEDGER_REVISION_INVALID |
    OPENING_MISSING |
    OPENING_CONFLICT |
    BASELINE_STALE
-> LEDGER_GRAPH_INVALID

CL4 project_shadow_cash / ACCOUNT_SCOPE_INVALID
-> ACCOUNT_SCOPE_INVALID

CL4 project_shadow_cash / ENVIRONMENT_UNSUPPORTED
-> ENVIRONMENT_UNSUPPORTED

CL4 project_shadow_cash / IDENTITY_KEY_INVALID
-> IDENTITY_KEY_INVALID

CL4 project_shadow_cash / VERSION_UNSUPPORTED
-> VERSION_UNSUPPORTED

CL4 project_shadow_cash / ARITHMETIC_OVERFLOW
-> ARITHMETIC_OVERFLOW
```

После successful CL4 projection CL1 transaction reconstruction maps:

```text
canonical/full/source/economic hash mismatch
-> LEDGER_IDENTITY_MISMATCH

invalid posting/classification/lineage/reference graph
-> LEDGER_GRAPH_INVALID

Money arithmetic overflow during reporting aggregation
-> ARITHMETIC_OVERFLOW
```

#### Portfolio/Risk/CL5 boundaries

```text
expected Portfolio predecessor serialization/model failure
-> PORTFOLIO_EVIDENCE_INVALID

expected RiskPolicy/RiskState/hash-helper failure
-> RISK_EVIDENCE_INVALID

CL5Error with reason other than INTERNAL_BOUNDARY_FAILED
during build_cash_availability rebuild
-> CASH_AVAILABILITY_INVALID

CL5Error / INTERNAL_BOUNDARY_FAILED
-> INTERNAL_BOUNDARY_FAILED
```

Public argument failures уже обязаны быть пойманы более ранним CL6 preflight. Поэтому `TYPE_INVALID`, `TIMESTAMP_INVALID` или другой non-listed CL4 reason из validated `project_shadow_cash` invocation означает:

```text
INTERNAL_BOUNDARY_FAILED
```

Любой unexpected predecessor exception также остаётся `INTERNAL_BOUNDARY_FAILED`. Public chaining запрещён во всех случаях.

---

# FIRST-FAILURE ORDER

## 79. build_portfolio_valuation_point

Exact order:

1. public argument exact types;
2. environment/account/key/key-id;
3. timestamp;
4. phase;
5. revision/hash fields;
6. exact CL1 Money reconstruction;
7. RUB/non-negative valuation rule;
8. canonical/HMAC construction.

---

## 80. build_performance_report

Exact order:

1. public argument exact types;
2. environment/account/key/key-id;
3. period/generated timestamps;
4. strict `period_start < period_end <= generated_at`;
5. ledger byte-size bound;
6. canonical CL2 parse;
7. CL4 ledger projection revalidation;
8. ledger revision/head/export correlation;
9. transaction-count bound;
10. exact transaction canonical/identity validation;
11. account-scope correlation;
12. valuation tuple/count/exact DTO types;
13. valuation HMAC reconstruction;
14. valuation account/environment/key correlation;
15. valuation set structural validation;
16. reporting classification/reversal graph;
17. CashFlowSummary arithmetic;
18. exact metric gates and primary-reason precedence from section 30.1;
19. TWR deterministic calculation;
20. XIRR deterministic calculation;
21. report status;
22. report HMAC/plain SHA.

Representative multi-invalid vectors mandatory.

---

## 81. build_portfolio_identity_evidence

Exact order:

1. public argument exact types;
2. environment/account/key/key-id/evaluated timestamp;
3. exact root Portfolio DTO types;
4. bounded nested Portfolio graph;
5. unbound serialization/canonical-byte bound;
6. predecessor Portfolio round-trip;
7. revision/decision/document checksum reproduction;
8. account-scope HMAC;
9. dependency timestamp parse/normalization;
10. dependency-from-future relation;
11. evidence HMAC/plain SHA.

---

## 82. build_risk_guard_evidence

Exact order:

1. public argument exact types;
2. environment/account/key/key-id/timestamps;
3. exact RiskPolicy/RiskState root types;
4. nested Risk collection bounds/types;
5. current RiskState version;
6. account-scope HMAC;
7. accepted RiskPolicy hash reproduction;
8. accepted RiskState guard hash reproduction;
9. dependency-from-future relation;
10. evidence HMAC/plain SHA.

---

## 83. build_portfolio_risk_cash_context

Exact order:

1. public argument exact types;
2. evaluated timestamp/key/key-id;
3. ledger/export coarse bounds;
4. CL5 rebuild through accepted public boundary;
5. byte-identical CashAvailability comparison;
6. PortfolioIdentityEvidence HMAC/plain-SHA reconstruction;
7. RiskGuardEvidence HMAC/plain-SHA reconstruction;
8. account/environment/currency/key correlation;
9. dependency-from-future relations;
10. exact Money structural arithmetic/correlation;
11. deterministic context disposition precedence;
12. context HMAC/plain SHA.

---

# DETERMINISM AND SIDE EFFECTS

## 84. Pure behavior

All CL6 builders:

- perform zero filesystem writes;
- perform zero provider calls;
- perform zero database writes;
- acquire no runtime mutation locks;
- spawn no thread/process;
- read no environment variable;
- read no system clock;
- use no randomness/UUID.

Every timestamp is supplied by caller or already present in immutable predecessor evidence.

Identical validated input -> byte-identical output.

---

# CONTRACT-OWNED KAT

## 85. Common KAT key material

Normative:

```text
identity_key_hex =
000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f

identity_key_id =
CL5_TEST_KEY_V1

account_scope_sha256 =
15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3
```

The account-scope value is inherited from the accepted predecessor KAT and is not redefined by CL6.

---

## 86. Performance numeric KAT

Period:

```text
period_start =
2026-01-01T00:00:00.000000000Z

period_end =
2027-01-01T00:00:00.000000000Z
```

Valuations:

```text
PERIOD_START:
100.000000000 RUB

PRE_EXTERNAL_FLOW at 2026-07-02:
110.000000000 RUB

PERIOD_END:
180.000000000 RUB
```

One external broker cash flow:

```text
2026-07-02T00:00:00.000000000Z
+50.000000000 RUB
```

Expected TWR:

```text
growth = 99 / 80

return = 19 / 80

rate_decimal =
0.237500000000

subperiod_count = 2
```

Expected XIRR:

```text
status = AVAILABLE

sign_change_count = 1

cash_flow_count = 3

day_count_basis = ACT_365_FIXED

algorithm = DECIMAL_BISECTION_V1

rate_decimal =
0.242497375454
```

---

## 87. PortfolioValuationPoint KAT hashes

### PERIOD_START

```text
valuation_identity_sha256 =
e682bf338569f70f14ef3deb3f49df71a6c2feb988fc5913c5bec42c051bcb76

valuation_point_sha256 =
be422475178220a0b319a01930c3b2cd3d7addda7a0ee408cbda2c74e068d6fb
```

Input identity fields:

```text
as_of =
2026-01-01T00:00:00.000000000Z

phase = PERIOD_START

portfolio_revision = 1

portfolio_decision_checksum =
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

portfolio_document_checksum =
abababababababababababababababababababababababababababababababab

source_sha256 =
acacacacacacacacacacacacacacacacacacacacacacacacacacacacacacacac
```

### PRE_EXTERNAL_FLOW

```text
valuation_identity_sha256 =
fca5ae307cfca3457c3a1e84754d3d8cc8ca08440975f7c1a3111d71dd92ed64

valuation_point_sha256 =
9bf672552a16d03ada419a5f32373a0ab59d094f9d288dc6ba97cebd1be14d05
```

Input:

```text
as_of =
2026-07-02T00:00:00.000000000Z

phase = PRE_EXTERNAL_FLOW

portfolio_revision = 2

portfolio_decision_checksum =
babababababababababababababababababababababababababababababababa

portfolio_document_checksum =
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb

source_sha256 =
bcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbc
```

### PERIOD_END

```text
valuation_identity_sha256 =
3c72886db0938de569b343cd9a2e87519fb3577ad918f8d779f9ed77e6addb6d

valuation_point_sha256 =
7c1faae4f02e2c9f05350b1c5b2de1db01ea915a2581f78f09a90b2aa9ff1515
```

Input:

```text
as_of =
2027-01-01T00:00:00.000000000Z

phase = PERIOD_END

portfolio_revision = 3

portfolio_decision_checksum =
cacacacacacacacacacacacacacacacacacacacacacacacacacacacacacacaca

portfolio_document_checksum =
cbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcbcb

source_sha256 =
cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
```

---

## 88. Valuation-set KAT

Exact expected:

```text
valuation_set_sha256 =
086a40cbdeff0dc73b3374a3e03e704666ea3248fbfb82c12f59db478cb6940f
```

Point SHA order:

```text
be422475178220a0b319a01930c3b2cd3d7addda7a0ee408cbda2c74e068d6fb

9bf672552a16d03ada419a5f32373a0ab59d094f9d288dc6ba97cebd1be14d05

7c1faae4f02e2c9f05350b1c5b2de1db01ea915a2581f78f09a90b2aa9ff1515
```

---

## 89. CashFlowSummary KAT

Expected:

```text
transaction_count = 1

external_flow_count = 1
external_flow_cash_effect = +50.000000000 RUB

all other category counts = 0
all other category effects = 0 RUB
```

Canonical SHA:

```text
617f979548fe8a1d74cf9e52194e82c6e0b5e6e0ecb3567ec1ef0d576454236a
```

---

## 90. TWRResult KAT

Exact canonical semantic values:

```text
status = AVAILABLE
reason = AVAILABLE

growth_numerator = 99
growth_denominator = 80

return_numerator = 19
return_denominator = 80

rate_decimal = 0.237500000000

subperiod_count = 2
```

Canonical SHA:

```text
0cd7f7f361836486688cbed80a1cdfb925eaec0ca713a8cf8a66e5de694a03de
```

---

## 91. XIRRResult KAT

```text
status = AVAILABLE
reason = AVAILABLE

rate_decimal = 0.242497375454

sign_change_count = 1
cash_flow_count = 3

day_count_basis = ACT_365_FIXED
algorithm = DECIMAL_BISECTION_V1
```

Canonical SHA:

```text
65973bd57b96c7002363c5f4ba10ea582482c13ebeb647eab07d8bf883c4596c
```

---

## 92. PerformanceReport identity KAT

Synthetic upstream identities:

```text
ledger_export_sha256 =
3333333333333333333333333333333333333333333333333333333333333333

ledger_head_sha256 =
4444444444444444444444444444444444444444444444444444444444444444

ledger_projection_sha256 =
6666666666666666666666666666666666666666666666666666666666666666

ledger_revision = 5

ledger_complete = true

ledger_incompleteness_kinds = []
```

Report:

```text
generated_at =
2027-01-01T00:00:00.000000000Z

report_status =
COMPLETE
```

Expected HMAC:

```text
report_identity_sha256 =
835a6f1d97b3f3ba186361daf5024db794535e55714458ddc501e70fb0d3502f
```

Expected canonical report SHA:

```text
performance_report_sha256 =
0492bad5de6f9fd3c2c1b18148d27a439835cf1bf76779364ecbad5ba6d487e2
```

Future fixture MUST reproduce both directly from exact canonical DTOs.

---

## 93. PortfolioIdentityEvidence KAT

Input:

```text
captured_at =
2026-09-11T10:00:00.000000000Z

portfolio_snapshot_at =
2026-09-11T10:00:00.000000000Z

portfolio_revision = 9

portfolio_schema_version = 2

portfolio_decision_checksum =
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

portfolio_document_checksum =
abababababababababababababababababababababababababababababababab

portfolio_source = CANONICAL

migration_status = COMPLETED

legacy_read_path_enabled = false

freshness = FRESH

state_status = READY

blocking = false
```

Expected:

```text
evidence_identity_sha256 =
0e208a34fb0281e67a9480dfe846134493f8601d08312e585cf3c9e0ae479307

portfolio_identity_evidence_sha256 =
7d4f2d9f642ec9e60bb4ea75b9fda7fbddf0fc3c030e9226227b28319401932f
```

---

## 94. RiskGuardEvidence KAT

Input:

```text
captured_at =
2026-09-11T10:00:00.000000000Z

risk_state_version = 4

risk_policy_hash =
dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd

risk_state_guard_hash =
eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
```

Expected:

```text
evidence_identity_sha256 =
a09701617df1ece90b02b3eb1061b84d91376938d765728189596d39a7a9057d

risk_guard_evidence_sha256 =
0de94c18c0304e954de3440436b5b190522df766aebdf8a00fbedf02f034a992
```

---

## 95. PortfolioRiskCashContext KAT

Inherited synthetic upstream values:

```text
availability_sha256 =
cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238

availability_status = READY
availability_reason = READY

ledger_export_sha256 =
3333333333333333333333333333333333333333333333333333333333333333

ledger_revision = 5

ledger_head_sha256 =
4444444444444444444444444444444444444444444444444444444444444444

reconciliation_sha256 =
1111111111111111111111111111111111111111111111111111111111111111

central_order_revision = 7

reservation_projection_hash =
5555555555555555555555555555555555555555555555555555555555555555

free_investable_cash =
50.000000000 RUB
```

All KAT timestamps:

```text
2026-09-11T10:00:00.000000000Z
```

Portfolio evidence SHA:

```text
7d4f2d9f642ec9e60bb4ea75b9fda7fbddf0fc3c030e9226227b28319401932f
```

Risk guard evidence SHA:

```text
0de94c18c0304e954de3440436b5b190522df766aebdf8a00fbedf02f034a992
```

Expected:

```text
status =
READY_FOR_LOCKED_REVALIDATION

reason =
READY
```

Context HMAC:

```text
context_identity_sha256 =
05409978eaff9beafb68c9c4e2a0531c15995e8f337604dbbdc964e12a975bd6
```

Canonical context SHA:

```text
portfolio_risk_cash_context_sha256 =
fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9
```

---

## 96. KAT scope

Sections 85–95 freeze:

- domains;
- field participation;
- canonical ordering rule;
- HMAC/SHA algorithms;
- performance numeric outputs.

Future fixture MUST additionally contain executable end-to-end vectors using a valid accepted CL2 ledger export.

The contract does not duplicate the full CL2 export bytes.

Fixture cannot redefine contract-owned KAT values.

---

# ACCEPTANCE MATRIX

## 97. Mandatory implementation acceptance matrix

Future implementation must include at least:

```text
V310-CL6-01
exact predecessor + three-path implementation delta

V310-CL6-02
module import / zero side effects

V310-CL6-03
canonical JSON / duplicate keys / surrogate / float rejection

V310-CL6-04
PortfolioValuationPoint HMAC and mutation matrix

V310-CL6-05
ledger export size/canonical/revision/head validation

V310-CL6-06
full transaction identity revalidation

V310-CL6-07
report classification matrix

V310-CL6-08
reversal category inheritance

V310-CL6-09
correction bundle reporting cancellation semantics

V310-CL6-10
trade principal excluded from external flow

V310-CL6-11
deposit/withdrawal excluded from strategy return contribution

V310-CL6-12
income/expense signed cash effects

V310-CL6-13
manual adjustment degrades metrics

V310-CL6-14
opening inside period degrades metrics

V310-CL6-15
valuation-set missing/duplicate/extra points

V310-CL6-16
no-flow TWR known result

V310-CL6-17
mid-period deposit TWR KAT

V310-CL6-18
mid-period withdrawal TWR

V310-CL6-19
same-timestamp external-flow aggregation

V310-CL6-20
period-end external-flow ambiguity rule

V310-CL6-21
non-positive TWR denominator

V310-CL6-22
rational digit bound

V310-CL6-23
XIRR no-root/no-sign-change

V310-CL6-24
XIRR one-root KAT

V310-CL6-25
XIRR multi-sign-change AMBIGUOUS

V310-CL6-26
XIRR root-out-of-range

V310-CL6-27
XIRR Decimal-only / no float

V310-CL6-28
ledger incomplete -> DEGRADED metrics unavailable

V310-CL6-29
PerformanceReport HMAC/SHA KAT

V310-CL6-30
PerformanceReport privacy scan

V310-CL6-31
Portfolio exact DTO preflight before virtual dispatch

V310-CL6-32
Portfolio revision/decision/document checksum revalidation

V310-CL6-33
Portfolio account-scope HMAC

V310-CL6-34
Portfolio timestamp normalization/future boundaries

V310-CL6-35
PortfolioIdentityEvidence HMAC/SHA KAT

V310-CL6-36
Risk exact DTO/collection bounds

V310-CL6-37
RiskPolicy predecessor hash binding

V310-CL6-38
RiskState predecessor guard-hash binding

V310-CL6-39
Risk account-scope privacy binding

V310-CL6-40
RiskGuardEvidence HMAC/SHA KAT

V310-CL6-41
CL5 byte-identical rebuild required

V310-CL6-42
CL5 non-READY -> context BLOCKED

V310-CL6-43
CL5 exact Money preserved without float conversion

V310-CL6-44
portfolio not-ready disposition

V310-CL6-45
availability/portfolio/risk stale precedence

V310-CL6-46
dependency-from-future closed failure

V310-CL6-47
cross-evidence skew boundary

V310-CL6-48
PortfolioRiskCashContext HMAC/SHA KAT

V310-CL6-49
mutation of every context identity field changes HMAC

V310-CL6-50
reporting degradation does not affect Risk context

V310-CL6-51
Risk context blocked does not affect reporting

V310-CL6-52
AST/import boundary excludes runtime/mutation/provider dependencies

V310-CL6-53
zero Central/Risk/Portfolio/Ledger mutations

V310-CL6-54
full unchanged CL1–CL5 regression

V310-CL6-55
exact three-path implementation allowlist

V310-CL6-56
Portfolio BLOCKED/MANUAL_REVIEW_REQUIRED status blocks even when blocking=false

V310-CL6-57
cross-evidence skew includes portfolio_snapshot_at boundary

V310-CL6-58
portfolio_captured_at comes only from lease.leased_at

V310-CL6-59
report CL4 projection uses generated_at and ignores post-period flows in metrics

V310-CL6-60
multi-account export validation with target-only reporting and cross-account lineage rejection

V310-CL6-61
canonical revision/count/version scalar encoding table

V310-CL6-62
exact predecessor-error translation table

V310-CL6-63
missing/extra valuation and metric-reason precedence matrix
```

---

## 98. Mandatory adversarial mutation matrix

At minimum mutate independently:

### Valuation

```text
amount +/- 1 nano
as_of
phase
portfolio revision
decision checksum
document checksum
source SHA
account scope
environment
key ID
HMAC
version
```

### Report

```text
period bounds
ledger export SHA
ledger revision
ledger head
projection SHA
ledger completeness bit
incompleteness kinds
valuation set SHA
summary nested field
TWR field
XIRR field
report status
generated_at
HMAC
version
```

### Portfolio evidence

```text
revision
decision checksum
document checksum
snapshot time
capture time
freshness
state status
migration status
legacy-read flag
blocking
portfolio source
account scope
HMAC
version
```

### Risk guard

```text
policy hash
state guard hash
state version
capture time
account scope
HMAC
version
```

### Risk context

```text
availability SHA
availability status/reason
each timestamp
free cash +/- 1 nano
ledger revision/head
reconciliation SHA
Central revision/hash
Portfolio hashes/revision
Risk hashes
status/reason
key ID
context HMAC
version
```

No retained HMAC may remain valid after mutation of an authoritative preimage field.

---

## 99. Multi-invalid precedence matrix

Tests must include combinations such as:

```text
bad type + bad timestamp

bad version + bad HMAC

invalid ledger + invalid valuation

invalid valuation HMAC + missing required point

ledger incomplete + missing valuation + manual adjustment + opening inside period

CL5 forged + Portfolio stale

Portfolio BLOCKED with blocking=false + otherwise fresh/coherent evidence

CL5 non-READY + Risk stale

dependency from future + cross-evidence skew

portfolio snapshot outside skew + fresh portfolio capture

Portfolio checksum mismatch + Risk hash mismatch
```

Observed primary reason must match sections 79–83 exactly.

---

## 100. Privacy scans

Tests scan:

- canonical DTO bytes;
- repr;
- str(error);
- error evidence;
- fixture;
- unexpected exception paths.

Forbidden leakage:

```text
raw Account ID

token/credential

provider payload

raw transaction payload

filesystem path

instrument ID from Portfolio/Risk internals

order IDs

raw exception text
```

---

## 101. Local implementation verification

Before independent implementation review:

1. dedicated CL6 suite;
2. all contract-owned KAT reproduced;
3. full cross-language fixture verification;
4. AST/import boundary;
5. Ruff;
6. format check;
7. compile;
8. `git diff --check`;
9. exact three implementation paths only;
10. full unchanged CL1–CL5 regression.

Green tests do not grant implementation acceptance.

---

# REVIEW / GOVERNANCE

## 102. Contract review protocol

Exactly:

```text
one independent/adversarial contract review
```

Result:

```text
PASS
```

or one finite fixed set:

```text
CL6-R1-xx
```

At most:

```text
one bounded contract correction batch
```

followed by:

```text
one finding-scoped closure review
```

If material blocker remains:

```text
RESCOPE / ABORT / DEFER
```

No recursive correction treadmill.

---

## 103. Implementation review protocol

Same rule:

```text
one independent/adversarial implementation review

at most one bounded implementation correction batch

one finding-scoped closure review
```

New material blocker after closure:

```text
RESCOPE / ABORT / DEFER
```

not another correction cycle.

---

## 104. Integration-readiness boundary

Later integration-readiness review проверяет только:

- exact accepted contract identity;
- exact accepted implementation identity;
- exact CL5 predecessor;
- merge-base;
- cumulative four-path surface;
- frozen contract blob;
- exact-head CI/publication identity;
- import/call-graph authority drift.

Он не является вторым semantic implementation review.

---

## 105. Contract acceptance binding

Explicit contract acceptance must bind:

```text
exact contract commit
exact contract tree
exact predecessor
exact merge-base
exact one-file delta
```

Issue/PR prose is metadata only.

Contract acceptance не означает:

```text
implementation acceptance
runtime activation
Risk enforcement
provider action
merge
release
experiment
```

---

## 106. Exit disposition

До отдельного contract acceptance текущий authority:

```text
CL6 CONTRACT FREEZE CANDIDATE

IMPLEMENTATION BLOCKED

REPORTING READ-ONLY

RISK CASH CONTEXT READ-ONLY

NO RISK DECISION AUTHORITY

NO EXECUTION AUTHORITY

NO RUNTIME AUTHORITY
```

Единственное разрешённое repository изменение:

```text
docs/project/V3_10_CL6_REPORTING_RISK_CASH_CONTEXT_CONTRACT_RU.md
```
