# Быстрый запуск MOEX Research Robot v3.7-alpha3

## Установка/проверка

```bat
install_and_verify_v3_7_alpha3.bat
```

## Обновление alpha2 runtime

```bat
run_portfolio_cutover.bat preview --account-id <ACCOUNT_ID>
run_portfolio_cutover.bat cutover --account-id <ACCOUNT_ID> --confirmation "CUTOVER PORTFOLIO STATE 2"
```

Затем:

```bat
run_gui.bat
```

## Standalone

```bat
BUILD_STANDALONE.bat
```

Перед торговлей: schema 2, source CANONICAL, migration COMPLETED, FRESH/MATCHED, pending/uncertain отсутствуют.
