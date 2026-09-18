# v3.10 CL8 Q7 — exact `RUB` / `rub` withdraw-limits alias amendment

Статус этого файла: **CONTRACT SUCCESSOR CANDIDATE / REVIEW AND EXPLICIT
ACCEPTANCE REQUIRED / IMPLEMENTATION BLOCKED**.

Этот amendment не разрешает provider access, изменение пользовательского
runtime, B0 restore, Preparation Stage, `START EXPERIMENT`, Stage B, burn-in,
Stable acceptance, remote writes, публикацию или merge.

## 1. Exact custody и authority

Contract-successor branch:

```text
agent/v3-10-clean-cl8-q7-withdraw-alias-contract-amendment
```

Exact predecessor:

```text
commit = 7486b108f808061122a1da54d70c7feef0b68457
tree   = 568d19363bee5fb78e9fd1f249a399d1f1513938
```

Initial gate:

```text
HEAD = merge-base = predecessor
ahead / behind = 0 / 0
worktree clean
changed paths = 0
```

Accepted contract being amended:

```text
accepted commit = acd7a49a830ebfcfe7545869d471c599dfa8d2bb
accepted tree   = b71f7d483cf6632e8e181f3aebba33ac7a0921d7
path = docs/project/V3_10_CL8_Q7_BROKER_CASH_VIEW_RESCOPE_CONTRACT_RU.md
git blob = 8a0983a79348815282a713feae6f1e8dca6536d9
SHA-256 = 8561a3a273cbef94f2f00d9a9287ecb5a79e16b7a85a72dbc73c4bb8d78a9b95
```

Contract-only allowlist:

```text
docs/project/V3_10_CL8_Q7_WITHDRAW_LIMITS_RUB_ALIAS_AMENDMENT_RU.md
```

Все остальные repository paths immutable до explicit acceptance exact
successor commit/tree.

## 2. Узкая область amendment

Этот document закрывает только finding:

```text
CL8-Q7-OFFLINE-CLOSURE-01
```

Он заменяет только прежнее правило, по которому lowercase `rub` считался
foreign currency/запрещённым alias на decoder boundary для полей:

```text
WithdrawLimitsResponse.money
WithdrawLimitsResponse.blocked
WithdrawLimitsResponse.blockedGuarantee
```

Все остальные определения, формулы, типы, custody bindings, fail-closed
результаты, ownership boundaries и запреты принятого broker-cash-view contract
остаются нормативными без изменений.

## 3. Нормативное правило exact wire aliases

На указанной decoder boundary разрешены ровно два exact wire token:

```text
"RUB"
"rub"
```

Они дают одну и ту же производную canonical Money-проекцию:

```text
Money(currency="RUB", minor_units=<exact decoded amount>)
```

Разрешение относится только к exact builtin `str`. Subclass `str`, mixed case,
leading/trailing whitespace, locale-dependent spelling, Unicode lookalikes и
любая иная строка не являются RUB alias.

Запрещены любые общие преобразования, включая:

```text
.upper()
.lower()
.casefold()
.strip()
Unicode normalization
locale conversion
```

Parser должен использовать закрытое множество `{ "RUB", "rub" }`; расширение
этого множества требует нового contract successor.

## 4. Raw identity и semantic projection

Wire response остаётся неизменным. Decoder не переписывает исходный mapping,
item или значение `currency`. Canonical response bytes, response SHA-256 и
transport/request binding вычисляются из original raw response.

Следовательно:

```text
wire "RUB" response identity != wire "rub" response identity
```

при одинаковой производной Money-проекции. Нельзя требовать равенство raw
identity от двух разных wire responses.

`BrokerWithdrawLimitsCashProof` version 1 и существующие public fields
сохраняются. Этот accepted successor является явным, hash-bound уточнением
decoder semantics; он не разрешает runtime cross-version equality, silent
schema upgrade или generic alias registry.

## 5. Presence, zero и foreign cash

Правило применяется одинаково к каждому из трёх массивов.

- Exact `RUB` или `rub` с нулевой суммой остаётся присутствующей RUB записью.
- Пустой массив означает отсутствие RUB записи и не превращается в explicit
  zero presence.
- Non-zero item любой иной exact currency остаётся genuine foreign cash и
  устанавливает действующий fail-closed foreign-cash blocker.
- Действующая политика для explicit zero foreign currency не изменяется.
- Malformed Money, overflow, bool-as-int, неверный exact type и unknown keys
  продолжают отклоняться до READY.

## 6. Semantic duplicate gate

Duplicate проверяется отдельно внутри каждого массива после определения
semantic currency.

Каждая комбинация ниже в одном массиве отклоняется typed причиной
`WITHDRAW_LIMITS_RESPONSE_INVALID`:

```text
RUB + RUB
rub + rub
RUB + rub
rub + RUB
```

Одинаковая semantic currency в разных массивах сама по себе не duplicate.
Decoder не суммирует duplicate entries и не выбирает одну из них.

## 7. Сохранённые финансовые и authority invariants

Amendment не изменяет:

- exact CL3 Money validation и integer arithmetic;
- conservative cash lower bound;
- однократное вычитание `QUEUED` reservations;
- запрет повторного вычитания provider blocked/guarantee;
- CL4 reconciliation, CL5 availability, CL6 context и CL7 proof gates;
- account/environment/freshness/completeness/transport provenance;
- raw response custody и request binding;
- запрет detached/forged/stale/wrong-account evidence;
- отсутствие cash, reservation, Risk, Central или execution ownership у
  decoder;
- запрет provider POST, automatic retry/resubmit и order dispatch;
- исторический Stage B root-cause status `INDETERMINATE`.

Alias acceptance не создаёт READY сама по себе и не обходится вокруг любого
другого fail-closed blocker.

## 8. Frozen implementation allowlist

После отдельного read-only adversarial review и explicit acceptance exact contract
commit/tree разрешается создать новую implementation branch только от этого
accepted head. Implementation delta ограничен ровно тремя существующими paths:

```text
current/trading_robot/cash_availability.py
current/tests/test_v3_10_cash_availability.py
current/tests/test_v3_10_runtime_cash_cutover_recovery.py
```

Любой четвёртый path, изменение принятого contract blob, transport API,
fixtures, build/release tooling или runtime state означает
`SCOPE_EXPANSION_REQUIRED`.

Implementation должен быть воспроизведён заново от accepted amendment head.
Commit `c723619a3c560a0ce38d43de0454faf30a1b707b`, его tests и standalone ZIP
`fb0025959758ecdcdf4c85350ccfad02e5fa8c9f0cbee82a2609d053d94034a3`
являются только reference evidence и не получают acceptance переносом.

## 9. Contract-owned acceptance oracle

Обязательные adversarial cases для каждого из трёх массивов:

| Case | Required result |
|---|---|
| exact `RUB`, non-zero | RUB Money; presence true; no false foreign flag |
| exact `rub`, non-zero | same Money projection; presence true; no false foreign flag |
| exact `RUB` / `rub`, zero | presence true; distinct from absent item |
| empty array | existing absence semantics |
| genuine foreign non-zero | existing fail-closed blocker |
| genuine foreign zero | existing policy unchanged |
| `RUB/RUB`, `rub/rub`, `RUB/rub`, `rub/RUB` | typed rejection before READY |
| `Rub`, `rUb`, whitespace, Unicode lookalikes | not recognized as RUB |
| `str` subclass/custom equality object | typed rejection |
| malformed/overflow/bool-as-int/unknown key | existing typed rejection |

Integration oracle должен использовать production decoder и real adapter path
с fake I/O. Он обязан подтвердить:

- raw input и response identity не изменены;
- detached/forged, stale и wrong-account observations отклоняются;
- exact lowercase alias на полностью корректных synthetic dependencies не
  создаёт false foreign blocker;
- negative cases не доходят до dispatch;
- provider calls и provider mutations равны нулю.

## 10. Review и acceptance gates

До implementation требуются:

```text
one contract-only commit
exact commit/tree/blob/SHA custody
separate read-only adversarial review
material findings = 0
explicit acceptance exact commit/tree
```

Только после этого разрешены новая isolated implementation branch, three-path
replay, source tests и standalone build. Source PASS, binary binding и artifact
hash должны быть получены заново для exact successor; ни один PASS от
`c723619a...` или ZIP `fb002595...` не переносится.

После implementation требуются отдельные exact-head review и acceptance.
Standalone acceptance требует exact source-to-binary binding. Native Windows
smoke при отсутствии безопасной изолированной среды остаётся честным
`NOT_RUN / BLOCKED_ENVIRONMENT`, а не переносится со старого бинарника.

## 11. Authority boundary

```text
CONTRACT SUCCESSOR = CANDIDATE / NOT ACCEPTED
IMPLEMENTATION = BLOCKED
STANDALONE = BLOCKED
PROVIDER ACCESS = NOT AUTHORIZED
USER RUNTIME MUTATION = NOT AUTHORIZED
B0 RESTORE = NOT AUTHORIZED
PREPARATION / STAGE B = NOT AUTHORIZED
Q7 BURN-IN = NOT AUTHORIZED
STABLE ACCEPTANCE = NOT GRANTED
REMOTE WRITES / PUBLICATION = NOT AUTHORIZED
```
