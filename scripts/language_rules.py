"""Explicit country/language signals; names and foreign company brands are not signals."""
import re

# Country-only signals from Russia/CIS do not qualify. Explicit non-Russian
# presentation languages remain independently eligible.
COUNTRIES=(
    'Китай|Китая|Китае|КНР|China|Инди[яи]|Индией|India|Иран|Ирана|Iran|'
    'Бразили[яи]|Brazil|Турци[яи]|Turkey|Türkiye|Япони[яи]|Japan|Коре[яи]|Korea|'
    'Германи[яи]|Germany|Франци[яи]|France|Итали[яи]|Italy|Великобритани[яи]|Англи[яи]|United Kingdom|UK|'
    'США|USA|United States|Канад[аы]|Canada|ОАЭ|UAE|United Arab Emirates|Египет|Египта|Egypt|'
    'Нидерланды|Нидерландов|Netherlands|Израиль|Израиля|Israel|Испани[яи]|Spain|'
    'Португали[яи]|Portugal|Австри[яи]|Austria|Бельги[яи]|Belgium|Швейцари[яи]|Switzerland|'
    'Швеци[яи]|Sweden|Дани[яи]|Denmark|Норвеги[яи]|Norway|Финлянди[яи]|Finland|'
    'Польш[аи]|Poland|Чехи[яи]|Czech Republic|Венгри[яи]|Hungary|Греци[яи]|Greece|'
    'Оман|Омана|Oman|Катар|Катара|Qatar|Саудовская Аравия|Саудовской Аравии|Saudi Arabia|'
    'ЮАР|South Africa|Гана|Ганы|Ghana|Нигери[яи]|Nigeria|Марокко|Morocco|'
    'Индонези[яи]|Indonesia|Малайзи[яи]|Malaysia|Сингапур|Сингапура|Singapore|'
    'Таиланд|Таиланда|Thailand|Вьетнам|Вьетнама|Vietnam|Пакистан|Пакистана|Pakistan|'
    'Австрали[яи]|Australia|Новая Зеландия|Новой Зеландии|New Zealand|'
    'Аргентин[аы]|Argentina|Мексик[аи]|Mexico|Чили|Chile|Серби[яи]|Serbia|Монголия|Монголии|Mongolia'
)
COUNTRY_RE=re.compile(r'(?<!\w)(?:'+COUNTRIES+r')(?!\w)',re.I)
LANGUAGE_RE=re.compile(r'английск|китайск|арабск|испанск|французск|немецк|португальск|японск|корейск|english|chinese|arabic|spanish|french|german|portuguese|japanese|korean',re.I)
NEGATED=re.compile(r'без\s+(?:синхронного\s+)?перевода|перевод\s+не\s+(?:предусмотрен|требуется)|no interpretation',re.I)
OPERATIONAL_TRANSLATION=re.compile(r'(?:предусмотр\w*|обеспеч\w*|будет|доступен|ведется|ведётся).{0,55}синхронн\w*\s+перевод|синхронн\w*\s+перевод.{0,55}(?:предусмотр\w*|обеспеч\w*|доклад|выступлен)|simultaneous\s+(?:interpretation|translation).{0,45}(?:provided|available)',re.I)
PRESENTATION_LANGUAGE=re.compile(r'(?:доклад\w*|выступлен\w*|лекци\w*|presentation|talk|speech).{0,70}\b(?:на|in)\s+(?:английск\w*|китайск\w*|арабск\w*|французск\w*|немецк\w*|испанск\w*|english|chinese|arabic|french|german|spanish)|язык\s+(?:доклада|выступления|лекции)\s*[:—–-]?\s*(?:английск\w*|китайск\w*|арабск\w*|французск\w*|немецк\w*|испанск\w*|english)',re.I)
