# Площадки с API / доступом (кроме TimePad)

Дата: 2026-09-19. Цель: закрыть дыру регулярных форматов и перекос вертикалей IT/АПК/Пром.

## Без закрытого ключа (можно крутить сейчас)

| Источник | Доступ | IT / АПК / Пром | Заметка |
|---|---|---|---|
| **KudaGo** `https://kudago.com/public-api/v1.4/` | JSON без токена | Слабо для B2B-завтраков. `education` ~200, `exhibition` ~640, `business-events` ≈0–1 | Городская культура; не драйвер отраслевых завтраков |
| **Expomap** | HTML/SSR, не API | Пром / выставки / конференции | Уже в пайпе (theme + country) |
| **All-Events** | HTML-календарь по `type-is-*` | Кросс; theme agro/IT | Завтрак/стол/ужин/митап/вебинар |
| **ICT2Go** `ict2go.ru/events/` | HTML | **IT** (митапы, конференции) | Keywords: завтрак / митап / круглый |
| **Exponet** | HTML + XML (~10 item) | Пром / АПК | `…/topics/agriculture|food|zoology/…` |

### KudaGo — быстрый старт

```
GET /public-api/v1.4/events/?page_size=100&actual_since=<unix>&categories=education|exhibition
fields=id,title,dates,description,place,site_url
```

Категории: `business-events`, `education`, `exhibition`, `festival` (см. `/event-categories/`).

### Exponet XML

```
http://www.exponet.ru/content/xml/exhibitions.ru.xml
```

Открыт без ключа, потолок ~10 item — enrichment, не объём.

## Нужен ключ / партнёрка

| Источник | Endpoint / док | Что нужно | Вертикали |
|---|---|---|---|
| **QTickets** Partners | `GET …/api/partners/v1/events/lists` ([док](https://qtickets.help/article/partners-api/)) | Partner auth (сейчас `WRONG_AUTHORIZATION`) | Билеты, микс отраслей |
| **QTickets** REST | `GET /api/rest/v1/events` | Bearer TOKEN организатора | Управление своими событиями |
| **Radario** | `api.radario.ru` ([док](http://docs.radario.ru/api)) | Header `app-key`, API v1.1 | Билеты/афиша |
| **Яндекс Афиша / Tickets** | Agent/supplier API | Договор + `auth` | Массовая афиша; мало B2B-АПК |
| **Ticketland External** | `external-api.ticketland.ru` | B2B | Театр/концерт, не отраслевые завтраки |

## TimePad (уже есть токен)

```
GET https://api.timepad.ru/v1/events.json
Authorization: Bearer <token>
limit≤100, skip, sort=+starts_at
cities=… (пачками)
keywords=завтрак|круглый стол|ужин|митап|meetup|вебинар|семинар
fields=name,description_short,description_html,starts_at,ends_at,city,url,categories,organization,location
```

- Битый токен → 403 даже на «публичные» методы.
- Rate limit ~60 req/min → бэкофф при 429.
- **Не кладёт vertical** → дефолт в «Пром» даёт перекос Андрея.

## Ремап vertical (приоритетнее новых API)

TimePad/часть AE не знают IT/АПК/Пром. Эвристика по `title`+`description`+`categories`:

1. **IT** — it, devops, продукт, стартап, ai, ml, разработ, digital, fintech, saas, frontend, backend, data, кибер, cloud, мобильн…
2. **АПК** — агро, ферм, пище, молоч, сельск, вет, комбикорм, растениевод, животновод, день поля…
3. иначе **Пром** или «Без вертикали» (продуктовое решение).

Новые бесплатные API **не** закроют дыру IT/АПК-завтраков сами по себе.

## Рекомендуемый порядок

1. Ремап TimePad vertical (уже в работе) + дожать description-батч.
2. ICT2Go + All-Events IT/agro theme (HTML).
3. KudaGo education/exhibition — опционально, низкий ROI для завтраков.
4. QTickets / Radario — только если Андрей даст ключ партнёра.

## Не делать

- Ждать Яндекс Афишу / Ticketland ради B2B-отраслей.
- Класть все TimePad-события в «Пром» по умолчанию.
- Светить токены в чат / логи.
