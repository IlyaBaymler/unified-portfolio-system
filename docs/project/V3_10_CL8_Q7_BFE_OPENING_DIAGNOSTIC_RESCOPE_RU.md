# CL8 Q7 — bounded opening diagnostic correction/rescope

Статус: локальный successor candidate. Этот файл не означает implementation
acceptance, provider authority, Stage B restart или Sandbox burn-in authority.

## 1. Exact predecessor и evidence binding

```text
predecessor commit = bfe9e0c102aaaabc96f8c0caf657cfcd06479461
predecessor tree   = f06c9255c3a720c19c5d184a0bea015f1fb3b8c4

accepted B0 SHA-256 =
8da2464538bc71056848d69000420703022bdb172c48bf23df7f4e387de74b57

failed Stage B Preparation SHA-256 =
d3227969a2518b0b33799374cf49edab258cf93e1f7d86549ecee25f32660c4c

failed prepare evidence SHA-256 =
f5e7d675eb3834e8a595bbf40ed7772dfc70b26dbcc3a6cf281417836a15dfc7

terminal evidence SHA-256 =
e2e7072a626dbfba5dd211e1a0859cad31e779f730922fb9c51b3f798dbe0b33

local-only cancel evidence SHA-256 =
2c085dce29df084a535665f19788a7030914a97feb07756d286361a847f4e443

offline diagnostic SHA-256 =
fb7ac8e6185690520d76c30e192713c8f7d69afce8ba7ec0abe9d9a7f0869ba2

diagnostic disposition SHA-256 =
abd6c1c5af733322abd994e82e5a6ab741dcb00abd452b80d9c22bf7b41f404c
```

Observed terminal blocker:

```text
CL4_OPENING / OPENING_INVALID
```

После отдельно авторизованного local-only cancel authority находится в
`LEGACY_ACTIVE / revision 2`; partial opening отсутствует. Offline diagnosis
классифицировал provider-to-CL4 normalization gap и отсутствие finite CL4
dependency reason в CLI evidence. Raw provider response не сохранялся, поэтому
correction не объявляет конкретный live wire value доказанным.

## 2. Fixed correction set

```text
CL8-Q7-BFE-DIAG-01 = provider MoneyValue -> strict CL4 normalization boundary
CL8-Q7-BFE-DIAG-02 = finite privacy-safe CL4 dependency_reason in CLI evidence
```

Другие findings этим rescope не открываются.

## 3. Provider MoneyValue normalization

Перед передачей `GetSandboxPortfolio` response в CL4 разрешено одно pure,
ephemeral преобразование:

```text
field        = totalAmountCurrencies
exact keyset = currency, nano, units
input token  = exact built-in str "rub"
output token = exact built-in str "RUB"
```

`units`, `nano` и все остальные top-level members остаются неизменными.
Исходный response не мутирует. Для нормализованного snapshot создаются новые
top-level и MoneyValue mappings. CL4 сохраняет strict canonical `RUB` semantics;
CL1/CL3/CL4 codec, proof identity и persisted schema не изменяются.

Нормализация запрещена для non-exact dict containers, изменённого keyset,
`str` subclass, custom equality object, другого case/token/currency и missing
currency. Canonical `RUB` проходит unchanged. Invalid `units`, `nano`, range и
sign combinations не исправляются и fail-closed в принятом CL3/CL4 codec.

Один и тот же normalizing transport view обязателен для production GUI
composition и private cutover CLI. Только `get_portfolio` получает bounded view;
все остальные provider methods и privacy-safe provider metadata делегируются
без изменения.

## 4. Finite CLI dependency reason

CLI может вывести `dependency_reason` только когда одновременно выполнены все
условия:

```text
reason = OPENING_INVALID
stage  = CL4_OPENING
dependency_reason belongs to the exact accepted CL4Reason enum values
```

Unknown token, private text, exception detail и dependency reason другого stage
не выводятся. Stack trace, raw response, token и raw Account ID не сохраняются.

## 5. Frozen successor allowlist

Ровно один successor commit может изменить только четыре path:

```text
docs/project/V3_10_CL8_Q7_BFE_OPENING_DIAGNOSTIC_RESCOPE_RU.md
current/trading_robot/gui_runtime_controller.py
current/tools/v3_10_runtime_cash_cutover.py
current/tests/test_v3_10_q7_preparation_runtime.py
```

Все остальные paths immutable. Дополнительный path требует отдельного explicit
rescope.

## 6. Required oracles

Successor обязан доказать:

1. exact built-in string `rub` преобразуется в `RUB`;
2. исходный response не мутирует;
3. accepted CL4 proof строится из normalized snapshot;
4. `RUB` проходит unchanged;
5. другие case/currency tokens и type/container substitutions не принимаются;
6. invalid units/nano/keyset не становятся valid;
7. non-portfolio provider methods и metadata остаются pass-through;
8. production GUI и cutover CLI используют normalizing transport view;
9. только finite accepted CL4 reason может появиться как `dependency_reason`;
10. correction/review выполняют zero provider calls и zero runtime mutation;
11. repository delta ограничен frozen four-path allowlist.

## 7. Successor gate

После одного successor commit разрешён только отдельный exact-range read-only
review `CL8-Q7-BFE-DIAG-01..02`. Explicit acceptance допустим только при
`material findings = 0`.

После acceptance требуется повторить затронутые candidate-bound qualification
gates, Q4/Q5, Stage A и verified B0. Только затем может быть сформирован новый
Stage B Preparation SHA. Любой новый provider READ требует новой отдельной
команды `START EXPERIMENT` с exact Preparation SHA.

## 8. Authority boundary

```text
SOURCE IMPLEMENTATION = CANDIDATE / NOT ACCEPTED
PROVIDER ACCESS = NOT AUTHORIZED
PROVIDER READ = NOT AUTHORIZED
STAGE B = BLOCKED
START EXPERIMENT = NOT ISSUED
SANDBOX BURN-IN = NOT AUTHORIZED
STABLE ACCEPTANCE = BLOCKED
PUBLICATION = NOT AUTHORIZED
```
