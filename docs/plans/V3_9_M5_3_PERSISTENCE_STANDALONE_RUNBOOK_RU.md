# v3.9 M5.3 — Persistence и Standalone Qualification Runbook

Дата: 2026-08-14
Статус: `M5.3 LOCAL ACCEPTANCE PASS / COMMIT-PUSH PENDING`

## 1. Граница этапа

M5.3 закрывает автоматические контракты EventJournal, backup/restore,
sanitized support bundle и Sandbox-only standalone layout. Новый
`tools/v3_9_persistence_qualification.py` только читает runtime и внешние
артефакты: он не восстанавливает state, не создаёт Strategy proposal или
Central intent и не разрешает dispatch, resubmit либо provider POST.

M5.3 не является Stable qualification. Фактический запуск frozen standalone,
длительный burn-in, manual
kill-switch acceptance, release manifest/ZIP и tag остаются отдельными gates.

## 2. Реализованный persistence contract

- checksum-managed JSON проверяется вместе с существующим или обязательным
  `.sha256`; отсутствующий, нечитаемый и несовпадающий checksum различаются;
- backup не создаётся из повреждённого checksum-managed source;
- успешный restore обновляет primary checksum и согласованный `.lastgood` с
  checksum для canonical/Central/runtime stores;
- аварийный rollback restore сохраняет исходные primary/checksum/last-good
  companions как одну транзакционную группу;
- EventJournal проверяется read-only; пустой остаточный WAL открывается
  immutable и не создаёт SHM, ненулевой WAL блокирует M5.3 final review;
- support bundle включает v3.8/v3.9 manifests, Portfolio Risk metadata,
  checksummed runtime integrity и заново рассчитанный Risk dashboard snapshot;
- support bundle не содержит token, raw Account ID или абсолютные runtime
  paths в integrity payload;
- standalone verifier требует Sandbox-only manifest, запрещает real-account
  execution, проверяет RiskState schema и пустые mutable directories, а также
  рекурсивно выявляет private runtime state.

Release identity в M5.3 не меняется. Фактический `software_version`, release
channel, deterministic package и source ZIP фиксируются только в M6.

## 3. Preconditions isolated runtime

Перед созданием evidence должны одновременно выполняться условия:

1. runtime подготовлен из `main` commit `cc02294de2995d618c4425010471204e3948da9c`
   или из проверяемого M5.3 commit;
2. оба instrument runtime имеют статус `STOPPED`;
3. canonical portfolio `FRESH/READY`, `blocking=false`, migration complete;
4. Central queue, active blockers, reserved cash и pending/uncertain равны нулю;
5. Sandbox Portfolio Risk policy имеет статус `READY`, режим `ENFORCED` и
   точный account scope;
6. global/instrument kill switches выключены, `risk_resync_required=false`;
7. backup, support bundle, standalone root и qualification report находятся
   вне qualified runtime; token для gate не требуется.

Не создавать искусственную заявку ради M5.3. Если runtime не quiescent,
final-review обязан завершиться `FAIL`.

## 4. Подготовка внешних артефактов

В source checkout можно направить maintenance tool в isolated runtime, а все
его рабочие каталоги — во внешний evidence root:

```powershell
$env:MOEX_ROBOT_RUNTIME_DIR = "<isolated-v3.9-runtime>"
$env:MOEX_ROBOT_BACKUPS_DIR = "<outside-runtime>\backups"
$env:MOEX_ROBOT_LOGS_DIR = "<outside-runtime>\logs"
$env:MOEX_ROBOT_REPORTS_DIR = "<outside-runtime>\reports"
$env:MOEX_ROBOT_SUPPORT_DIR = "<outside-runtime>\support"

python runtime_tool.py backup `
  --output "<outside-runtime>\backups\v3_9_m5_3_runtime.zip"

python runtime_tool.py support-bundle `
  --output "<outside-runtime>\support\v3_9_m5_3_support.zip" `
  --account-id "<sandbox-account-id>"
```

Пока source сохраняет принятую identity `v3.7.0`, M5.3 не переименовывает этот
binary в v3.9. Для автоматического layout gate создаётся честно помеченный
`STRUCTURAL_LAYOUT_FIXTURE`: без `.exe`, с `EXECUTABLE_NOT_BUILT.txt`, launcher
отказывается запускаться, а opt-in задаётся отдельно. Его `runtime/`,
`backups/`, `reports/`, `logs/` и `support/` пусты. Реальный build/launch
остаётся M6/manual gate.

## 5. Read-only inspect и final review

Промежуточная проверка runtime:

```powershell
python tools/v3_9_persistence_qualification.py inspect `
  --runtime-dir "<isolated-v3.9-runtime>" `
  --account-id "<sandbox-account-id>" `
  --output "<outside-runtime>\m5_3_inspect.json"
```

Полный автоматический gate:

```powershell
python tools/v3_9_persistence_qualification.py final-review `
  --runtime-dir "<isolated-v3.9-runtime>" `
  --account-id "<sandbox-account-id>" `
  --backup "<outside-runtime>\backups\v3_9_m5_3_runtime.zip" `
  --support-bundle "<outside-runtime>\support\v3_9_m5_3_support.zip" `
  --standalone-root "<outside-runtime>\MOEX_Research_Robot_v3_9_beta1" `
  --expected-version "0.3.9b1" `
  --expected-channel "beta" `
  --minimum-risk-state-schema 4 `
  --allow-structural-fixture `
  --output "<outside-runtime>\m5_3_final_review.json"
```

Ожидаются `status=PASS`, пустой `failures`, все safety flags `false`, а для
structural fixture — `structural_only=true` и
`executable_launch_verified=false`. Account ID в отчёте представлен только
mask/hash. `--output` внутри runtime или standalone root отклоняется до
inspection.

## 6. Отдельный rollback gate

Rollback не выполняется командой qualification. Для него нужна отдельная
изолированная копия и принятый v3.8 backup/runtime:

1. выполнить `preview-restore` и сохранить diff;
2. убедиться, что активных runtime/order transitions нет;
3. выполнить restore только после отдельного точного подтверждения
   `RESTORE RUNTIME`;
4. запустить восстановленный v3.8 runtime read-only/standalone и проверить его
   checksums, canonical state и отсутствие dispatch/resubmit;
5. не использовать production/live runtime как restore target.

Успех автоматических restore tests не является ручным подтверждением этого
gate.

## 7. Автоматические доказательства

- targeted M5.3/EventJournal matrix: `57 passed in 6.97s`;
- full local regression: `771 passed in 50.09s`;
- `pip check`: PASS;
- critical Ruff: PASS;
- strict v3.9 Ruff: PASS;
- compileall: PASS;
- broker API, provider POST, proposal, intent, dispatch и resubmit не
  использовались.

## 8. Isolated runtime preparation evidence

Gate `PREPARE ISOLATED V3.9 M5.3 RUNTIME` выполнен 2026-08-14. Источником
служит принятый post-resync M5.2 runtime. Перед копированием M5.3 read-only
inspect подтвердил: canonical `blocking=false`, два runtime `STOPPED`, Central
queue/blocker/reservation и pending равны нулю, policy `READY/ENFORCED`,
`risk_resync_required=false`, kill switches выключены.

В новую копию перенесён allowlist из 31 material-файла. Source/target SHA-256
совпали 31/31; lock, WAL/SHM, `.env`, token/secret/credential и log-файлы не
копировались. Повторный inspect новой копии завершился `PASS`, не изменил ни
одного material hash и сохранил все proposal/intent/dispatch/resubmit/provider
POST flags выключенными. Редактированные отчёты сохранены во внешнем
`MOEX v3.9 M5.3 Acceptance/evidence`.

Реальный runtime выявил и закрыл дополнительный provenance gap: checksummed
`v3_9_m5_2_runtime_seed_manifest.json` теперь сохраняется backup и проверяется
support/standalone coverage. После correction targeted/full regression повторены.

Gate `CREATE V3.9 M5.3 QUALIFICATION ARTIFACTS` выполнен 2026-08-14:

- verified backup: 14 entries, errors/warnings 0, SHA-256
  `e95df511fa812826abd7fe3adf3bc5be71f2a2cad56aa5f3f4f24b0c6afa6eaa`;
- sanitized support bundle: 5 members, checksum/account/secret/integrity
  findings 0, SHA-256
  `7170971b5aba769f58acce5bb9d6254669c44c3249161e16510c87c9271dacf9`;
- standalone structural fixture: layout PASS только с explicit opt-in,
  executable отсутствует и не запускался;
- агрегированный `inspect` всех трёх artifacts: `PASS`, failures 0,
  `writes_performed=false`, dispatch/provider POST false;
- 31 material runtime hash после создания artifacts остались byte-identical;
  backup operation оставила только служебный `runtime_backup.lock`, WAL/SHM нет.

Первый human final review выявил и correction закрыл пять boundary gaps:

- error-path больше не сериализует произвольный exception text с неизвестным
  Account ID;
- backup verification проверяет фактический JSON/schema/SQLite content, а
  qualification требует точного `app_version`;
- failed restore сохраняет orphan checksum/last-good companions даже при
  отсутствующем primary;
- support `sha256_manifest.json` обязан точно покрывать все остальные members;
- structural fixture требует точные fail-closed launcher/marker и согласованные
  `executable_built=false`, `executable_launch_verified=false`.

После correction qualification artifacts пересозданы. Повторный
`final-review`: `PASS`, failures 0, backup version/content PASS, support checksum
coverage findings 0, raw Account ID evidence leaks 0, material hash changes 0.
SHA-256 итогового redacted report:
`44deec5307cd738fa743b78dbb6ce241e5ed4f951536122d96f8c05221a5b2fa`.

## 9. Disposable restore evidence

Gate `RESTORE RUNTIME` выполнен 2026-08-14 только на disposable-копиях.
Источник восстановления — принятый v3.8 backup
`two_instrument_acceptance_final_20260813T112634Z.zip`, SHA-256
`90609febed89fc12a32010b1c24878e3360c1f09beff1cde347e48fdef91cede`,
`app_version=v3.8-dev`, 10 entries, verification errors/warnings 0.

Первая disposable-попытка выявила P1-gap: для совпавшего с backup
`multi_instrument_profiles.json` preview корректно показывал `UNCHANGED`, но
restore не создавал отсутствующий `.lastgood`. Первичный restore и FAIL-report
сохранены как audit evidence. Исправление включает recovery-only items в ту же
snapshot/rollback/post-validation транзакцию, не переписывает неизменившийся
primary и не создаёт для него ложную `pre_restore`-копию. Успешная и аварийная
ветки покрыты отдельными regression tests.

Повторный gate выполнен на новой `rollback-v3_8-disposable-corrected`, созданной
заново из исходного M5.3 runtime: 31/31 source/target hashes совпали, pre-restore
inspect PASS, preview `CREATE=1 / REPLACE=8 / UNCHANGED=1`. Restore применён с
точным confirmation `RESTORE RUNTIME`; создано восемь audit-копий только для
фактически заменённых существующих entries.

Post-restore review: `PASS`, failures 0, restored entries 10/10, checksum 6/6,
last-good 5/5, canonical `FRESH/READY`, `blocking=false`, Central queue/blocker/
reservation 0, pending 0, SQLite schema 2 и integrity PASS. Offline v3.8 inspect
с запрещённым network client вернул `IDLE`; material/residual hash changes 0,
WAL/SHM 0, raw Account ID evidence leaks 0. Исходный M5.3 runtime остался
byte-identical. SHA-256 redacted post-restore review:
`f0feb842db024cefec03421e094d05ef2907e698070c7b914506294c6cd2ad7d`.

## 10. Последовательность operator gates

1. `PREPARE ISOLATED V3.9 M5.3 RUNTIME` — выполнено;
2. `CREATE V3.9 M5.3 QUALIFICATION ARTIFACTS` — выполнено;
3. `RUN V3.9 M5.3 FINAL REVIEW` — выполнено после correction;
4. отдельный `RESTORE RUNTIME` на disposable copy принятого v3.8 — выполнено
   после correction;
5. после принятия M5.3 — commit/push, draft PR и CI отдельными командами;
6. M6 Stable qualification только после merge M5.3.

## 11. Следующий отдельный gate

Следующий gate — `COMMIT/PUSH M5.3`. Фактический standalone build/launch остаётся
отдельным gate M6 и пока не заявлен выполненным.
