# CL8 Q7 — runtime preparation runbook

Этот runbook относится только к accepted contract
`1b87316a310e094c8c1c0d2bd3790221b49f60b4 / 610e544f802cea599fbfce56ede0a72a7e14c945`.
Он не разрешает provider access, CL7 activation, Sandbox order POST или Q7 burn-in.

## 1. Границы

Stage A выполняется offline. Подготовка Stage B только фиксирует входы будущего
эксперимента `CL8-Q7-CL7-ACTIVATION-V1`. Сам Stage B требует отдельного exact
`START EXPERIMENT` и всех четырёх CL7 confirmation phrases. Даже успешный Stage B
не разрешает `CL8-SANDBOX-BURNIN-V1`: для burn-in потребуется ещё одна отдельная
Preparation Stage и новая команда `START EXPERIMENT`.

Все output records и backup ZIP должны находиться вне source tree либо в
исключённом private qualification directory. Runtime directory является private
operator state; его абсолютный путь не переносится в shareable evidence.

## 2. Protected custody

Production source — Windows Credential Manager, namespace `MOEXResearchRobot`:

```text
TBANK_SANDBOX_TOKEN
TBANK_SANDBOX_ACCOUNT_ID
V310_CL_IDENTITY_KEY_HEX
V310_CL_IDENTITY_KEY_ID
```

Обычный GUI и Stage A не создают и не меняют identity. Если pair отсутствует,
единственный разрешённый provisioning command:

```powershell
python tools/v3_10_q7_prepare_runtime.py provision-identity `
  --identity-key-id <NON_SECRET_ID> `
  --confirmation "PROVISION V3.10 CL7 IDENTITY"
```

Команда возвращает только metadata. Raw token, Account ID и identity key нельзя
копировать в журнал, issue, PR, evidence или support bundle.

## 3. Stage A — offline materialization

До запуска оператор фиксирует exact candidate commit/tree и проверяет clean
source tree. В private runtime уже должны находиться ровно 2–3 проверенных
`SANDBOX_EXECUTION` profiles. Tool не придумывает instrument profile.

```powershell
python tools/v3_10_q7_prepare_runtime.py materialize `
  --runtime-dir <PRIVATE_RUNTIME> `
  --candidate-commit <EXACT_COMMIT> `
  --candidate-tree <EXACT_TREE> `
  --output-record <EXCLUDED_DIR>\Q7_STAGE_A.json `
  --backup-output <EXCLUDED_DIR>\Q7_B0.zip
```

Допустимый terminal result для revision-0 authority:

```text
status = ACTIVATION_REQUIRED
provider_calls_performed = false
provider_mutations_performed = false
```

Это успешная offline materialization, но ещё не Q7 Preparation PASS. Проверить:

- record canonical SHA-256 и внешний file SHA-256;
- B0 SHA/size и `manifest.json` SHA;
- B0 verification status `VERIFIED`;
- exact configured-set identity и count 2/3;
- отсутствие secrets и private path в record/backup;
- authority осталась `LEGACY_ACTIVE`, если runtime был clean.

После implementation должны быть заново выполнены Q1, Q4 и Q5 на exact
successor candidate. Q2/Q3 требуют отдельной adoption проверки.

## 4. Stage B Preparation Stage

До authenticated provider read создаётся immutable record:

```powershell
python tools/v3_10_q7_prepare_runtime.py prepare-activation `
  --runtime-dir <PRIVATE_RUNTIME> `
  --candidate-commit <EXACT_COMMIT> `
  --candidate-tree <EXACT_TREE> `
  --stage-a-record <EXCLUDED_DIR>\Q7_STAGE_A.json `
  --b0-backup <EXCLUDED_DIR>\Q7_B0.zip `
  --output-record <EXCLUDED_DIR>\Q7_ACTIVATION_PREPARATION.json
```

Expected result:

```text
experiment_id = CL8-Q7-CL7-ACTIVATION-V1
provider_calls_before_authorization = 0
provider_mutations_before_authorization = 0
burnin_authorized = false
```

После independent review этого record Stage B может быть открыт только отдельной
командой `START EXPERIMENT`, явно привязанной к его exact SHA. Выполнение
`prepare`, `confirm`, `activate`, `arm` остаётся в существующем
`v3_10_runtime_cash_cutover.py`; Q7 preparation tool этих side effects не имеет.

## 5. B1 и final preparation

Команда допускается только после separately authorized Stage B и read-back:

```text
authority = EXACT_CASH_ARMED
post_attempt_count = 0
pending_dispatch_proof_sha256 = null
Central blocker = none
Portfolio = FRESH / coherent
CashLedger/Risk/configured set = valid and exact
provider order mutations during activation = 0
```

```powershell
python tools/v3_10_q7_prepare_runtime.py finalize `
  --runtime-dir <PRIVATE_RUNTIME> `
  --candidate-commit <EXACT_COMMIT> `
  --candidate-tree <EXACT_TREE> `
  --activation-record <EXCLUDED_DIR>\Q7_ACTIVATION_PREPARATION.json `
  --q4-artifact-identity-sha256 <Q4_SHA256> `
  --q5-privacy-summary-sha256 <Q5_SHA256> `
  --backup-output <EXCLUDED_DIR>\Q7_B1.zip `
  --output-record <EXCLUDED_DIR>\Q7_FINAL_PREPARATION.json
```

`PASS` означает только готовность к отдельному рассмотрению burn-in Preparation
Stage. Он не запускает стратегию, не разрешает POST и не является Stable
acceptance или publication authority.

## 6. Fail-closed recovery

При partial/malformed protected identity, mismatch account/config/candidate,
corrupt custody, pending/uncertain Central state, `EXACT_CASH_DISPATCH_PENDING`,
backup verification failure или evidence substitution остановиться. Не удалять
и не пересоздавать operator custody. Не выполнять автоматический retry Stage B.
Зафиксировать finite blocker, сохранить private evidence и запросить отдельное
governance decision.
