# Recovery Runbook v3.7-beta1

## Общий порядок

1. Остановить execution и не повторять заявку вручную.
2. Получить свежий broker snapshot.
3. Проверить pending/uncertain order, account ID, canonical revision и checksum.
4. Сохранить runtime backup и support bundle без token.
5. Возобновлять работу только после fresh `MATCHED` snapshot и Risk `PASS`.

## Warning не исчез после refresh

Убедитесь, что опубликована новая revision. Warning из старой revision не должен
переноситься; повторяющийся warning означает актуальную причину reconciliation.

## Credential status

- `credential_absent`: credential не найден; настройте Credential Manager или
  локальный `.env`;
- `provider_unavailable`: защищённый provider недоступен; не трактуйте как
  отсутствие token;
- `.env_fallback`: используется локальный fallback; файл нельзя включать в
  support/release archive;
- `credential_present`: credential найден, значение намеренно не показывается.

## API outage

Recovered transient требует более позднего свежего безопасного canonical
snapshot. При unresolved transient или non-transient failure новые заявки
запрещены до восстановления.

## Shadow degraded

`DEGRADED` не разрешает и не запрещает canonical execution. Сохраните support
bundle, устраните проблему write-only shadow, затем проверьте новый status.

## Risk profile не найден после restart

Нормальная portable layout хранит mutable state в sibling-каталоге `runtime`,
а не в `app`.

Если после restart появляется Risk runtime error или сообщение, что профиль
Sandbox Execution не сохранён:

1. Остановите execution; не создавайте профиль заново поверх неизвестного state.
2. Сохраните копию всего sibling `runtime` и проверьте наличие `risk_profiles`
   и `robot_state` без вывода credential в лог.
3. Убедитесь, что запущен обновлённый beta1 executable, а `run_gui.bat` указывает
   на тот же portable tree.
4. Не переносите mutable JSON в `app` и не объединяйте runtime двух установок.
5. После запуска дождитесь fresh `MATCHED`, Risk `PASS` и отсутствия
   pending/uncertain execution до возобновления заявок.

Симптом, при котором файлы присутствуют в `runtime`, но execution ищет их в
`app`, означает старую сборку со split-runtime defect. Установите обновлённый
пакет в новую папку, сохранив исходный runtime как recovery evidence.

## Rollback beta1 → alpha3

Используйте полный проверенный backup принятого alpha3 и убедитесь, что после его
точки не было новых сделок. Не смешивайте beta1-код и runtime alpha3 вручную.
