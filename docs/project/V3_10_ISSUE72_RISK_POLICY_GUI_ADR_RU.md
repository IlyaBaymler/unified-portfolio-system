# ADR — Issue #72 Risk Policy GUI boundary

## Decision

`OPTION A = READ_ONLY_GUI_PLUS_EXISTING_ACCEPTED_OPERATOR_TOOLS`.

GUI показывает exact policy hash, RiskState revision, readiness, kill switch, resync и
Portfolio Risk status из accepted read models. GUI не редактирует policy, не пишет Risk
JSON, не применяет лимиты и не создаёт второй Risk owner.

Авторитетные изменения Risk Policy выполняются существующими operator tools под их
собственными gates и журналированием. Issue #72 удаляет command binding и вызов
`RiskProfileEditor.apply_max_orders_per_day` из активного GUI.

## Consequences

- отсутствие policy writer в Tk callback graph;
- один и тот же non-null `PortfolioRiskRuntime` обязателен для Central и adapter;
- kill switch, resync, missing policy/state или scope mismatch блокируют полный Start;
- отображение PASS не даёт execution, provider, publication или experiment authority.

## Status

`IMPLEMENTATION CANDIDATE / NOT ACCEPTED`. Команда `START EXPERIMENT` не дана.
