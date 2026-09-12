# Changelog v3.10.0 Stable Release Candidate

Версия пакета: `0.3.10`
Display identity: `v3.10.0`
Release channel: `stable`

## Денежный и runtime контур

- добавлены exact Money/CashLedger contract и append-only persistence;
- добавлены read-only broker adapters и deterministic classification;
- добавлены opening proof, shadow reconciliation и CashAvailability projection;
- Reporting и Portfolio Risk получают immutable cash context;
- добавлена durable runtime cash authority state machine, attempt-before-POST
  marker и fail-closed crash recovery без automatic resubmit;
- GUI/runtime принят по Issue #72 с account-level configured-set ownership и
  без параллельного provider/Risk/Central mutation owner.

## Qualification и release

- добавлена CL8 offline qualification infrastructure, privacy/custody evidence,
  exact regression comparator и deterministic artifact verification;
- Q0 принят; release metadata и package identity переведены на v3.10.0;
- v3.9 root release documents удалены из active source root;
- source и standalone artifact names заморожены contractом CL8.

## Открытые gates

Release cut ещё не прошёл independent review. Q1–Q8 evidence, explicit
`ACCEPT V3.10.0 STABLE` и отдельное `PUBLISH V3.10.0 STABLE` не зафиксированы.
Provider access и эксперименты этим changelog не разрешаются.
