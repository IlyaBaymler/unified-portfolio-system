# Быстрый запуск MOEX Research Robot v3.7-beta1

## Исходный пакет

```bat
install_and_verify_v3_7_beta1.bat
run_gui.bat
```

Установщик создаёт локальную `.venv`, устанавливает зависимости и запускает
beta1 verification gate. Используйте новую папку, а runtime alpha3 переносите
только после создания backup и проверки canonical schema 2.

## Portable package

Запустите `MOEX Research Robot.bat`. Python на целевом компьютере не требуется.

## Перед Sandbox Execution

- выбран правильный Sandbox account ID;
- snapshot свежий, canonical source и `MATCHED`;
- pending/uncertain отсутствуют;
- Risk gate показывает `PASS`;
- размер заявки равен 1 lot.

Real-account execution в beta1 отключён.
