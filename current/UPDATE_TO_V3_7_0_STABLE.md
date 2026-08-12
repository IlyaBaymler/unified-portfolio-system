# Обновление до v3.7.0 Stable

Поддерживаемая база: принятая `v3.7-beta1`; upgrade с `v3.7-alpha3` допускается
после тех же backup/schema checks.

1. Остановите robot execution и убедитесь, что pending/uncertain отсутствуют.
2. Создайте и проверьте полный runtime backup исходной версии.
3. Распакуйте Stable в **новую** папку; не накладывайте code поверх старой.
4. Запустите `install_and_verify_v3_7_0.bat`.
5. Сверьте account ID, checksums и canonical PortfolioState schema 2.
6. Переносите runtime только после успешного backup verification.
7. Первый запуск выполните с Sandbox Execution off до fresh `MATCHED` и Risk
   `PASS`.

Stable не выполняет автоматический schema cutover. Schema 1 требует отдельной
подтверждённой процедуры, а не молчаливой миграции при запуске.

Rollback выполняется восстановлением **полного** проверенного backup beta1 и
только если после точки backup не было новых сделок. Нельзя смешивать Stable
code с отдельными JSON/SQLite-файлами beta1 вручную.
