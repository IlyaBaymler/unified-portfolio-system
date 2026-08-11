# Development process

Дата обновления: 2026-08-11.

## Рабочая связка ChatGPT + Codex

Проект использует разделение ролей:

- **ChatGPT** — обсуждение гипотез, анализ результатов и логов, постановка задач, архитектурный контроль, acceptance decision, Issues, roadmap и release records.
- **Codex (локально)** — имплементация в коде, рефакторинг, unit/integration tests, локальная отладка, сборка и работа с Git.
- **GitHub** — проверяемый источник истории кода, документации, Issues и release metadata.

## Обязательный handoff из Codex

Перед анализом новой версии в ChatGPT локальные изменения должны быть отправлены в соответствующую version branch GitHub. Минимально нужны:

1. commit SHA;
2. diff относительно принятой базы;
3. исходный код и тесты;
4. version/build manifest;
5. changelog/architecture/test-plan изменения;
6. full pytest summary;
7. targeted regression summary;
8. release hygiene/secret scan summary.

Большие runtime logs, рабочие state-файлы, `.env`, SQLite и support bundles в Git не коммитятся; они передаются отдельно для анализа.

## Ветвление

Текущая практическая схема:

- `main` — принятая проектная/документационная база и release metadata;
- `v3-7-alpha3` — frozen accepted alpha baseline;
- `v3-7-beta1` — активная beta-разработка;
- version/release branches прошлых версий сохраняются как история.

`develop` является исторической интеграционной веткой и не считается обязательной для текущего локального Codex-workflow, пока это отдельно не будет возвращено в процесс.

## Правила изменения версии

1. Codex работает от актуальной version branch.
2. Вычислительное ядро тестируется отдельно от GUI.
3. Код, тесты и документация меняются согласованно.
4. После реализации Codex отправляет изменения в GitHub.
5. ChatGPT анализирует diff и evidence, а не локальные неподтверждённые результаты.
6. Блокирующие дефекты оформляются отдельными Issues.
7. Версия принимается только после automated + Windows/Sandbox acceptance.
8. После acceptance создаётся release/acceptance record и обновляется roadmap.
9. Real-account execution не разрешается автоматически переходом версии; для него нужен отдельный gate.

## Критерий готовности изменения

Изменение не считается завершённым, пока не выполнены:

- основной сценарий;
- ошибочные/неопределённые состояния;
- regression затронутых контуров;
- экспорт/журналирование, если применимо;
- README/changelog/version update;
- standalone-проверка, если релиз её требует.
