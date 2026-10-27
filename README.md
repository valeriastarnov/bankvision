# BankVision

MVP интеллектуальной оценки залогового оборудования (токарный станок) для хакатона
«Финтех без границ: банк будущего для бизнеса и клиентов».

Клиент загружает фото оборудования и документ → OCR и извлечение полей → ручная проверка
полей сотрудником → сверка фото и документа → флаги расхождений и пропусков → подбор
аналогов → предварительный диапазон стоимости → индекс полноты и согласованности →
отчёт → маршрут заявки («Готово к проверке сотрудником», «Запросить материалы у клиента»,
«Требуется экспертная проверка»).

## Чем BankVision НЕ является

- **Не принимает кредитное решение** и не решает, принимать ли залог. Маршрут — рекомендация,
  что делать с заявкой дальше; маршрута «одобрено» нет.
- **Не рыночная оценка.** Диапазон стоимости — демонстрационный, на синтетической базе
  аналогов: «Демонстрационная оценка на синтетических данных. Не является рыночной стоимостью».
- **Индекс полноты и согласованности данных — не вероятность и не уверенность.** Это
  правило-ориентированный балл с экспертными, не откалиброванными весами.
- **Не проверен на реальных данных.** Реальных фото и документов нет; все тестовые данные,
  производители и модели — синтетические и вымышленные.
- Не оценивает техническое состояние, наработку и комплектацию оборудования.

## Установка на Windows

Нужны Python 3.11+ (разработка и проверка — на Python 3.14.5) и Tesseract OCR с русским языком.

### 1. Python

Установите Python 3.11+ с [python.org](https://www.python.org/downloads/) (галочка
«Add python.exe to PATH» не обязательна — ниже используется лаунчер `py`). Проверка:

```powershell
py -0p
```

> На некоторых машинах команда `python` открывает Microsoft Store — поэтому везде
> используются `py` и `.venv\Scripts\python.exe`.

### 2. Tesseract OCR с русским языком

```powershell
winget install UB-Mannheim.TesseractOCR
```

или установщик с <https://github.com/UB-Mannheim/tesseract/wiki>. В установщике в разделе
«Additional language data» отметьте **Russian**. Проверка (PowerShell и cmd):

```
"C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

В PowerShell перед путём в кавычках нужен `&`:
`& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs`. В списке должны быть
`rus` и `eng`. Если `rus` нет — скачайте
[rus.traineddata](https://github.com/tesseract-ocr/tessdata/raw/main/rus.traineddata) в
`C:\Program Files\Tesseract-OCR\tessdata\` (нужны права администратора).

Если Tesseract установлен в другую папку, задайте путь переменной окружения:

| PowerShell | cmd |
|---|---|
| `$env:TESSERACT_CMD = "D:\Tesseract-OCR\tesseract.exe"` | `set TESSERACT_CMD=D:\Tesseract-OCR\tesseract.exe` |

### 3. Проект, виртуальное окружение, зависимости

PowerShell:

```powershell
git clone https://github.com/valeriastarnov/bankvision.git
cd bankvision
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

cmd:

```bat
git clone https://github.com/valeriastarnov/bankvision.git
cd bankvision
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Активировать окружение не обязательно: все команды ниже вызывают `python.exe` из `.venv`
напрямую (так не мешает политика выполнения скриптов PowerShell).

## Запуск

| PowerShell | cmd |
|---|---|
| `.\.venv\Scripts\python.exe -m uvicorn app.main:app` | `.venv\Scripts\python.exe -m uvicorn app.main:app` |

Откройте <http://127.0.0.1:8000>. База SQLite и загруженные файлы создаются в `data/`
(`data/bankvision.db`, `data/uploads/`), в git не попадают.

Отчёт по заявке: `http://127.0.0.1:8000/applications/<номер>/report` → кнопка
«Печать / сохранить в PDF» (печать браузера → «Сохранить как PDF»).

## Синтетические фикстуры и демо-данные

Фикстуры уже лежат в `tests/fixtures/`; генератор детерминирован (фиксированный seed),
повторный запуск даёт те же файлы.

| Действие | PowerShell | cmd |
|---|---|---|
| Фикстуры (шильдики, паспорта, эталоны JSON) | `.\.venv\Scripts\python.exe tests\generate_fixtures.py` | `.venv\Scripts\python.exe tests\generate_fixtures.py` |
| Отложенная выборка для проверки метрики качества | `.\.venv\Scripts\python.exe tests\generate_fixtures.py --holdout` | `.venv\Scripts\python.exe tests\generate_fixtures.py --holdout` |
| База аналогов `data/analogs.csv` | `.\.venv\Scripts\python.exe data\generate_analogs.py` | `.venv\Scripts\python.exe data\generate_analogs.py` |
| 4 демо-заявки (чистый кейс, размытое фото, расхождение номера, нет аналогов) | `.\.venv\Scripts\python.exe -m scripts.seed_demo` | `.venv\Scripts\python.exe -m scripts.seed_demo` |

`seed_demo` печатает номера и ссылки созданных заявок. Запускайте его до или во время
работы сервера — база общая.

## Тесты

| PowerShell | cmd |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest -q` | `.venv\Scripts\python.exe -m pytest -q` |

Полный прогон занимает 1–2 минуты (OCR синтетических фото). Тесты с OCR пропускаются
с явной причиной, если Tesseract не найден. Фактические результаты прогонов с датами —
[tests/RESULTS.md](tests/RESULTS.md).

Скрипты оценки (выводят Markdown-таблицы, использованы для RESULTS.md):
`tests.run_ocr_eval`, `tests.run_extract_eval`, `tests.run_crosscheck_eval`,
`tests.run_valuation_eval`, `tests.run_routing_eval`, `tests.run_holdout_eval` —
запуск как `python -m tests.<имя>`.

## Docker

**Dockerfile не проверен**: на машине разработки Docker не установлен. Образ основан на
`python:3.12-slim` с пакетами `tesseract-ocr`, `tesseract-ocr-rus`.

```
docker build -t bankvision .
docker run -p 8000:8000 bankvision
```

## Структура проекта

```
app/
  main.py            FastAPI: страницы и API
  config.py          все пороги, веса и параметры (переменные окружения)
  db.py, models.py   SQLite (sqlite3), модели
  services/
    ingest.py        приём файлов: формат, сигнатура, размер, число фото
    image_quality.py качество фото: резкость, яркость, разрешение
    ocr.py, pdf.py   Tesseract, текстовый слой PDF / растеризация + OCR
    extract.py       извлечение полей правилами
    crosscheck.py    сверка документа и фото, нормализация
    review.py        итоговые значения, ручные правки, журнал
    valuation.py     аналоги, поправка на возраст, медиана и P25–P75
    scoring.py       индекс полноты и согласованности данных
    routing.py       маршрут заявки с причинами
    report.py        сборка отчёта
    analysis.py      обработка заявки после загрузки
  templates/, static/
data/analogs.csv, data/analogs_README.md, data/generate_analogs.py
docs/methodology_valuation.md, docs/methodology_score.md
scripts/seed_demo.py
tests/generate_fixtures.py, tests/fixtures/, tests/test_*.py, tests/run_*.py, tests/RESULTS.md
Dockerfile, requirements.txt
```

## Методики

- Оценка по аналогам: [docs/methodology_valuation.md](docs/methodology_valuation.md)
- Индекс и маршрутизация: [docs/methodology_score.md](docs/methodology_score.md)
- База аналогов: [data/analogs_README.md](data/analogs_README.md)

## Ограничения

### Данные

- **Всё синтетическое.** Фото шильдиков нарисованы Pillow, паспорта — простые PDF,
  производители и модели вымышленные. На реальных фото и документах система не проверялась;
  качество OCR и извлечения на них неизвестно.
- **Оценка замкнута на синтетической базе.** Цены в `data/analogs.csv` порождены
  формулой с тем же принципом снижения цены с возрастом, что и поправка в оценке.
  Поэтому согласованность результатов на этой базе ничего не говорит о точности на рынке.
- Нет поправок на техническое состояние, наработку, комплектацию, регион, торг и дату
  объявления; цены предложения ≠ цены сделок. Коэффициент поправки на возраст (0.07 в год) —
  демонстрационное допущение.

### Проверка качества фото (`app/services/image_quality.py`)

Проверено на отложенной синтетической выборке (RESULTS.md, «Отложенная выборка»).
Пороги не калиброваны на реальных фото.

- **Пересвет таблички на тёмном фоне не обнаруживается.** Метрика яркости — средняя по
  всему кадру (на выборке пересвет ×1.35 и ×1.6 дал 166.7 и 173.5 при пороге 200).
- **Флаги размытости и темноты срабатывают раньше, чем падает читаемость.** Размытие
  r = 1.5–3.0, смаз 9 px и затемнение до ×0.18 помечены, хотя OCR читал номер верно —
  пользователь может получить лишнюю просьбу переснять.
- Запас резкости чётких фото над порогом небольшой (~160–190 при пороге 100); параметр
  сглаживания метрики (σ = 1) выбран после просмотра результатов на основной выборке.

### Распознавание и сверка

- OCR читает кириллические модели вроде «ТС-1640М» латиницей. Сверка нормализует двойники,
  но если документ (скан) и фото распознаны одинаково неверно, сверка покажет «совпадает»:
  она сравнивает источники между собой, а не с истиной.
- Нормализация номера — только O/0 и I/l/1; путаницы S/5 и B/8 не нормализуются.
- Совпадение с фото, у которого есть флаг качества, считается «совпадает» с пометкой.

### Индекс и маршрутизация

- **Индекс — не вероятность и не уверенность**; веса 20/30/30/20 выбраны экспертно и не
  откалиброваны.
- **Порог индекса (50) выбран с учётом фикстур:** при подборе весов баллы сценариев уже
  были известны; размытое фото проходит порог с запасом 3.8 балла.
- Тёмное фото, на котором все ключевые поля совпали, получает «Готово к проверке
  сотрудником» — флаг качества только снижает индекс.

## Что проверено

Только то, что реально запускалось; подробности и даты — в [tests/RESULTS.md](tests/RESULTS.md).

- Автотесты pytest на Windows 11, Python 3.14.5, Tesseract 5.5.3: все проходят
  (число тестов и время — в последнем разделе RESULTS.md).
- Сценарии 1–7 из постановки дают ожидаемые маршруты на синтетических фикстурах
  (`tests.run_routing_eval`).
- OCR, извлечение полей, сверка и оценка — прогоны по всем фикстурам с таблицами в RESULTS.md.
- Метрика качества фото — однократный прогон на отложенной синтетической выборке.
- Запуск приложения (`uvicorn`) и ответы страниц — вручную через HTTP-запросы.
- **Установка с нуля по этому README** (2026-10-27): клон с GitHub в пустую папку →
  venv → зависимости → генерация фикстур, отложенной выборки и базы аналогов
  (результат совпал с файлами в репозитории байт-в-байт) → pytest → `seed_demo` →
  `uvicorn` → страницы заявки, проверки, оценки и отчёта отвечают 200. Команды для
  PowerShell и выборочно для cmd. Исправлять README не понадобилось.

**Не проверено:** реальные фото и документы; Dockerfile; печать отчёта в PDF в браузере
(CSS для печати есть, визуально не проверялся); установка Tesseract по инструкции выше
(на машине разработки он был установлен заранее); другие версии Python и Windows.
