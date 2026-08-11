# Обновление v3.7-alpha3 → v3.7-beta1

1. Остановите робота и убедитесь, что нет pending/uncertain заявки.
2. Создайте проверенный backup runtime alpha3.
3. Распакуйте beta1 в **новую** папку; не накладывайте её поверх старой версии.
4. Запустите `install_and_verify_v3_7_beta1.bat`.
5. Перенесите runtime только после проверки account ID, checksum и schema 2.
6. Первый запуск выполните без Sandbox Execution и дождитесь свежего
   canonical snapshot со статусом `MATCHED`.

Beta1 не выполняет автоматическую миграцию schema 1. Если alpha3 уже работает
на schema 2, повторный cutover не требуется.

Rollback выполняется восстановлением полного проверенного backup alpha3, а не
копированием отдельных Python/JSON-файлов поверх beta1.
