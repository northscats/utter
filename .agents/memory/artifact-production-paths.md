---
name: Пути запуска артефактов
description: Различие рабочих каталогов development workflow и production artifact runner.
---

В production-командах артефакта указывать пути от корня репозитория. Не переносить относительный путь из development workflow без проверки его рабочего каталога.

**Why:** Development workflow запускался из каталога артефакта, поэтому короткий путь к скрипту работал локально. Production runner запустил ту же команду от корня репозитория и завершился с code 127 после успешной сборки и миграции.

**How to apply:** Для `[services.production]` сверяться с корневыми путями, запускать точную production-команду из корня и проверять startup health endpoint перед публикацией.