# Issue #72 — Sandbox account disposition and cleanup runbook

До final acceptance требуется ровно один terminal disposition:

- `REDUNDANT_ACCOUNT_RETAINED_WITH_REASON`; или
- `REDUNDANT_ACCOUNT_CLEANUP_COMPLETED`.

Synthetic fixture, свободный текст и hash нетипизированного утверждения не доказывают
ни один disposition.

## Retain path

Trusted operator evidence связывает fresh sanitized account list, active account scope hash,
redundant scope hash, bounded reason, observation time и evidence hash. Raw Account ID,
token и credentials не включаются.

## Cleanup Preparation Stage

1. Получить fresh account list через отдельно авторизованный collector.
2. Доказать active/redundant classification.
3. Показать masked/hash preview и exact intended action.
4. Зафиксировать, что active account не входит в mutation set.
5. Подготовить exact operator confirmation phrase и post-action read-back.

Preparation Stage не выполняет provider action.

## Separate experiment

Cleanup разрешается только после отдельного accepted preparation record и буквальной
команды пользователя `START EXPERIMENT`.

После неё runner обязан повторно получить fresh list, потребовать exact operator
confirmation, выполнить ровно одну заявленную provider action и получить fresh
post-action read-back. Read-back должен доказать, что active account не изменён, redundant
account имеет ожидаемый terminal status, а raw identifiers отсутствуют в shareable evidence.

Ошибка, ambiguity или disconnect дают `DISPOSITION_NOT_ESTABLISHED`; automatic retry и
automatic cleanup запрещены.
