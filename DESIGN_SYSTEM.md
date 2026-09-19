# Дизайн-система: Календарь мероприятий

Hi-fi макет каталога B2B-мероприятий. Язык UI: `ru`. Данные: `window.EVENTS` из `events-data.js`.

## Токены

| Токен | Значение | Назначение |
|---|---|---|
| `bg` | `#F7F8FA` | Фон страницы |
| `surface` | `#FFFFFF` | Карточки, фильтр-бар, drawer |
| `text` | `#1A1D21` | Основной текст |
| `muted` | `#6B7280` | Подписи, мета |
| `border` | `#E5E7EB` | Обводки, разделители |
| `accent` | `#2563EB` | Ссылки, активные табы, primary CTA |
| `accent-soft` | `#EFF6FF` | Hover / soft fill |
| `it` | `#2563EB` | Вертикаль IT |
| `apk` | `#16A34A` | Вертикаль АПК |
| `industry` | `#B45309` | Вертикаль Промышленность |
| `status.confirmed` | `#15803D` | Дата подтверждена |
| `status.tentative` | `#CA8A04` | Дата ориентировочная |
| `status.tbd` | `#6B7280` | Дата уточняется |
| `status.link_broken` | `#DC2626` | Ссылка недоступна |
| Сетка | 8pt | Отступы 8 / 12 / 16 / 24 |
| Радиус | 8 / 12 / 999 | Controls / cards / chips |
| Шрифт | Inter, system-ui | 12 / 14 / 16 / 20 |
| Тень | `0 1px 2px rgba(0,0,0,.04)` | Карточки |
| Drawer shadow | `0 8px 32px rgba(0,0,0,.12)` | Боковая панель |

## Компоненты

### Tab
Два таба: **Мероприятия** (default) | **Календарь**. Активный — `accent` + нижняя граница 2px. ID: `#tab-list`, `#tab-cal`.

### ChipVertical
Чипы: IT / АПК / Промышленность / Все. Активный заливается цветом вертикали (Все → accent). Атрибут `data-vertical="it|apk|industry|all"`. Контейнер: `#chips-vertical`.

### SelectCity
`<select id="filter-city">`. Опции — уникальные `city` из данных (непустые), сортировка `ru`. Первая опция: «Все города».

### FilterBar
Блок `#filter-bar`: чипы вертикали + ряд селектов (город, страна, тип) + даты from/to + toggle + кнопки Сбросить / Обновить. На мобиле `flex-wrap`.

### RefreshButton
Кнопка `#btn-refresh` «Обновить». По клику: перечитывает `window.EVENTS`, сбрасывает page offset, toast «Каталог обновлён».

### EventRow
Плотная строка в `#event-list`: badges (vertical + status) → title → одна строка мета (тип · город · дата) → опционально одна строка `summary` (truncate, без лейбла «О мероприятии», без дубля тип/город). Клик открывает EventDrawer. Класс `.event-row`, `data-id`.

### DayCell
Ячейка месяца: номер дня + до **2** усечённых title (ellipsis) + **«+N»** если больше. Пустой день — только номер. Пн-старт. Дни с событиями — фон `accent-soft`. Сегодня — accent ring (`box-shadow` inset). Клик по дню или «+N» → `from=to=день` + таб Мероприятия. Класс `.day-cell`, `data-date="YYYY-MM-DD"`.

### CalToolbar
`←` / `#cal-month-label` («Сентябрь 2026») / «Сегодня» (`#btn-cal-today`) / `→`.

### BadgeStatus
Пилюля: confirmed / tentative / tbd / link_broken. Классы `.badge-status.is-confirmed` и т.д.

### EventDrawer
Правая панель `#event-drawer`: title, badges, поля **Когда / Где / Тип / Вертикаль** (без «Что»), блок **О мероприятии**, CTA «На сайт организатора». Overlay `#drawer-overlay`.

**О мероприятии:**
- есть `summary` → полный текст, clamp **6** строк + «Ещё» / «Свернуть» (`#btn-sutevka-more`);
- нет `summary` → «Краткого описания пока нет. Откройте сайт организатора.»

### EmptyState
- Список: «По таким фильтрам список пуст. Расширьте город, тип или даты.» / пустой каталог без фильтров: «Пока в каталоге пусто. Нажмите «Обновить» или загляните позже.»
- Месяц: «В этом месяце пусто. Откройте другой месяц или вкладку Мероприятия.»

## Карта экранов

| Экран | ID / зона | Поведение |
|---|---|---|
| List | `#panel-list` | Default. «Найдено N», пагинация 50 («Показать ещё»). Upcoming only (`date >= today`). |
| Calendar | `#panel-cal` | Сетка месяца, ← / Сегодня / →. Dense DayCell. |
| Drawer | `#event-drawer` | Детали + «О мероприятии» + CTA. |

## Handoff для Разработчика

1. **Контракт события:** `{id, title, vertical, type, city, country, date, status, url, place}`; `vertical ∈ it|apk|industry`; `status` чаще `confirmed`; `date` ISO `YYYY-MM-DD`.
2. **Город = SELECT**, не search-input. Уникальные значения из `EVENTS`, пустые города в опции не включать (фильтр «город не указан» — отдельно, если понадобится).
3. **Страна / тип** — SELECT из уникальных значений данных + «Все …».
4. **Refresh:** loading «Обновляю каталог…»; успех «Каталог обновлён · Найдено N · Обновлено: …»; ошибка «Не удалось обновить…»; title «Подтянуть новые мероприятия из источников».
5. **Default:** upcoming only; сброс чипа на «Все»; pageSize = 50.
6. **Календарь:** Mon-start; max 2 title + `+N`; клик дня/`+N` → `from=to=день` + таб Мероприятия; сегодня = accent ring; дни с событиями = accent-soft; кнопка «Сегодня».
7. **О мероприятии:** поле `summary` опционально; clamp 6 + «Ещё»/«Свернуть»; пусто → канон «Краткого описания пока нет…».
8. **ID consistency:** все контролы с префиксами `filter-` / `btn-` / `panel-` / `tab-`; JS только через эти id.

## Строки для Копирайтера / NMT

- Календарь мероприятий
- Каталог B2B-событий · IT · АПК · Промышленность
- Вертикаль / Город / Страна / Тип / С / По
- Все / IT / АПК / Промышленность
- Все города / Все страны / Все типы
- Только с подтверждённой датой
- Сбросить / Обновить
- Обновить / Обновляю каталог…
- Обновить / Обновляю каталог… / Каталог обновлён · Найдено N · Обновлено: ДД.ММ, ЧЧ:ММ
- Не удалось обновить. Проверьте сеть и попробуйте ещё раз.
- Подтянуть новые мероприятия из источников · Найдено N · Обновлено: ДД.ММ, ЧЧ:ММ
- Не удалось обновить. Проверьте сеть и попробуйте ещё раз.
- Подтянуть новые мероприятия из источников
- Мероприятия / Календарь
- Найдено N
- Показать ещё
- Дата подтверждена / Дата ориентировочная / Дата уточняется / Ссылка недоступна
- Когда / Где / Тип / Вертикаль / О мероприятии
- На сайт организатора
- По таким фильтрам список пуст. Расширьте город, тип или даты.
- Пока в каталоге пусто. Нажмите «Обновить» или загляните позже.
- В этом месяце пусто. Откройте другой месяц или вкладку Мероприятия.
- Без города / Площадка не указана
- Сегодня
- Ещё / Свернуть
- Краткого описания пока нет. Откройте сайт организатора.
- +N (остальные события дня)

## Календарь v2 (зафиксировано)

- Шапка: «Сегодня» + ← →; подпись месяца «Сентябрь 2026»
- DayCell: max 2 title + «+N»; сегодня — кольцо accent
- Drawer summary: clamp 6 строк · «Ещё» / «Свернуть»; empty: «Краткого описания пока нет. Откройте сайт организатора.»
- EventRow: одна строка сутевки (truncate), без заголовка «О мероприятии» в ряду

## Hi-fi pass (2026-09-19)

Visible product polish so Ctrl+F5 shows a shipped SaaS calendar, not a wireframe:

- **Tokens:** page bg `#F4F6F8`, text `#0F172A`, muted `#64748B`, richer card/drawer shadows; vertical colors unchanged (IT / АПК / industry).
- **Header:** sticky product bar with logo mark «К», title, subtitle with live `#header-count`.
- **Filter bar:** elevated card (`shadow-md`), pill chips with fill when active, controls height ~38px, primary «Обновить» with accent shadow.
- **Event cards:** 14–16px padding, hover lift + blue border accent, muted one-line summary at 12px.
- **Calendar:** toolbar card, today ring, colored event chips, clearer `+N` pill.
- **Drawer:** ~460px, soft summary block (`#F1F5F9`), strong full-width CTA.
- **Empty states:** centered card, soft border, more breathing room.
- Behavior / IDs / NMT strings unchanged.
