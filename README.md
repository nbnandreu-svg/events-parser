# Events Parser — календарь мероприятий (IT / АПК / промышленность)

Локальный каталог и UI для агрегации B2B-мероприятий: фильтры по теме, городу, региону, типу; карточка с описанием; выгрузка Excel (.xls); обновление данных.

**Демо (GitHub Pages):** https://nbnandreu-svg.github.io/events-parser/

Код и скрипты: https://github.com/nbnandreu-svg/events-parser

На хостинге открывается готовый снимок каталога. Кнопка «Обновить» и догрузка описаний работают только при локальном `python3 serve.py`.

## Быстрый старт

```bash
cd events-parser   # или папка репозитория
python3 serve.py
```

Открой [http://localhost:8765](http://localhost:8765), при необходимости **Ctrl+F5** + «Обновить».

Требования: Python 3.10+.

## Что внутри

| Файл | Назначение |
|------|------------|
| `index.html` | UI календаря / списка |
| `events-data.js` | Каталог для браузера (`window.EVENTS`) |
| `events_upcoming.json` | Тот же каталог в JSON |
| `serve.py` | Локальный сервер + refresh |
| `scripts/` | Скрипты ingest / enrich / reclass |
| `DESIGN_SYSTEM.md` | Дизайн-токены |

Вертикали: `it` · `apk` · `industry`. Источники: Expomap, Exponet, All-Events, ICT2Go, TimePad API, KudaGo, Workevent и др.

## TimePad API (опционально)

Токен **не** хранится в репозитории. Положите его локально:

```bash
mkdir -p ~/.secrets
# значение токена — только у вас, не коммитьте
echo 'YOUR_TOKEN' > ~/.secrets/TIMEPAD_TOKEN
chmod 600 ~/.secrets/TIMEPAD_TOKEN
```

Скрипты читают `/home/box/.secrets/TIMEPAD_TOKEN` или путь из env `TIMEPAD_TOKEN_FILE`.

## Примечания

- Каталог — снимок на момент публикации; для свежих данных запускайте ingest из `scripts/`.
- Lifestyle/не-B2B события отфильтрованы классификатором.
- Именованных «АПК × бизнес-завтрак» на рынке мало; в АПК сильнее форумы / столы / ужины.

## Лицензия

Private demo — для показа продукта. Источники данных принадлежат площадкам-оригиналам; ссылки ведут на первоисточник.
