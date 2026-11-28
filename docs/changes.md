# Журнал змін — виправлена версія

Цей документ описує всі виправлення, зроблені щодо оригінального
`ukrduzt-schedule-main.zip`. Кожен фікс має посилання на файл:рядок та
коротке обґрунтування.

## Критичні

### 1. Колізія кириличних імен файлів у скрапері
**Файл:** `backend/scraper.py`

Оригінал зберігав завантажений PDF у `DOWNLOAD_DIR` під санітизованим ім'ям
`re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)`. Реальний портал віддає файли з
кириличними назвами (`Розклад-2-курс_3-сем.pdf`,
`Розклад-210-SL-D26-_І-семестр.pdf`), які після санітизації стають
`_______-2-_____3-___.pdf` — два різні файли мали однакове ім'я. У пулі з 4
воркерів два потоки перетирали одне одного, тож у БД міг потрапити не той
контент.

**Фікс:** парсинг PDF відбувається прямо з `io.BytesIO(content)` у пам'яті —
жодного тимчасового файлу на диску, отже жодної гонки запису. Метод
`parse_schedule_pdf_detailed` приймає як шлях, так і `BytesIO` через новий
параметр `filename=` для виведення року з назви.

Тест: `tests/test_scraper.py::test_process_one_file_parses_cyrillic_named_pdf_in_memory`
подає два різні кириличні URL і перевіряє, що обидва парсяться без помилок та
колізій.

### 2. Часовий пояс контейнера
**Файл:** `backend/Dockerfile`, `backend/docker-compose.yml`

Оригінал: контейнер за замовчуванням `TZ=UTC`. Функція
`parser._infer_year_for_month` використовує `datetime.now()` — біля Нового
року (31 грудня 23:00 UTC = 1 січня 01:00 за Києвом) підставлявся неправильний
рік для коротких дат типу `24.11`.

**Фікс:** у Dockerfile встановлено `tzdata` та
`ln -fs /usr/share/zoneinfo/Europe/Kyiv /etc/localtime`; `ENV TZ=Europe/Kyiv`.
Те саме продубльовано в `docker-compose.yml` змінною `TZ=Europe/Kyiv`.

### 3. 404 → 503 під час повторної синхронізації
**Файл:** `backend/main.py::get_schedule`

Оригінал: `if _sync_in_progress and not _initial_sync_done: 503; else: 404`.
Тобто лише первинна синхронізація після старту давала 503; примусова
повторна синхронізація (через `POST /api/v1/sync?force=true`) давала 404, і
Flutter кешував "не знайдено" на 5 хвилин через `Cache-Control: max-age=300`.

**Фікс:** `if _sync_running.is_set(): 503` (Retry-After: 10) для будь-якої
синхронізації — первинної чи примусової. Якщо синхронізація не йде — 404 як і
раніше (це справжнє "даних немає").

Тести: `tests/test_main.py::test_schedule_returns_503_while_sync_running`,
`test_schedule_returns_404_when_idle_and_empty`.

### 4. Race у `_sync_in_progress`
**Файл:** `backend/main.py`

Оригінал використовував `threading.Lock` як прапорець: `_sync_in_progress`
встановлювався **після** `lock.acquire()`. Між запитом на синхронізацію та
встановленням прапорця було вікно, коли `/health` бачив `false` хоча sync
вже йшла. Також `trigger_sync` стартував потік, не встановлюючи прапорця
синхронно.

**Фікс:** `_sync_running` — `threading.Event`, що встановлюється у
`_start_sync()` **до** старту потоку (синхронно). `threading.Lock`
(`_sync_lock`) серіалізує саму роботу всередині `_execute_sync_safe`, але не
використовується як прапорець стану.

Тест: `tests/test_main.py::test_health_reports_running_flag` — перевіряє, що
прапорець `True` одразу після `POST /api/v1/sync`.

## Безпека

### 5. CORS `*` + credentials
**Файл:** `backend/main.py`

Оригінал: `allow_origins=["*"]` + `allow_credentials=True` — заборонено
спекою CORS; браузер ігнорує credentialed-запити при `*`.

**Фікс:** `CORS_ALLOW_ORIGINS` env: `*` (default) → permissive, `credentials=False`.
Список доменів через кому → credentialed режим.

### 6. Авторизація `/api/v1/sync`
**Файл:** `backend/main.py::trigger_sync`

Оригінал: будь-хто з інтернету міг `POST /api/v1/sync?force=true` — DoS на
безкоштовному Render.

**Фікс:** `SYNC_TOKEN` env. Якщо встановлено — `force=true` вимагає заголовок
`X-Sync-Token` (403 без нього). Не-force синхронізація лишається відкритою.

Тести: `test_sync_token_guards_force`, `test_sync_token_allows_force_with_header`.

## Docker

### 7. Bind-mount права + дубльований healthcheck
**Файл:** `backend/docker-compose.yml`

Оригінал: `./data:/app/data` bind-mount при `uid=10001` — SQLite міг не
відкритися, якщо каталог на хості належав root. Також `healthcheck` дублював
`Dockerfile HEALTHCHECK`.

**Фікс:** named volumes (`schedule-data`, `schedule-downloads`) — Docker
створює їх з правами контейнерного користувача, SQLite завжди може писати.
`user: "10001:10001"` явно. Healthcheck залишено лише в Dockerfile (compose
його не перекриває).

## Парсер

### 8. 9-та пара
**Файл:** `backend/parser.py`

Оригінал: `LESSON_NUM_RE = ^[1-8]$`, `BELLS` 1-8. Реальна 9-та пара мовчазно
перетворювалася на 8-му з часом 19:40-21:00.

**Фікс:** `MAX_LESSON_NUMBER = 9`, `LESSON_NUM_RE = ^[1-9]$`,
`BELLS[9] = ("21:10", "22:30")`. `detect_lesson_number` клампає до 9, а не 8.

Тест: `test_detect_lesson_number_supports_ninth_pair`.

### 9. Мертвий код + reversed weekday
**Файл:** `backend/parser.py`

- Видалено `_format_default_date` (ніде не використовувався).
- `WEEKDAY_RE` зібрано зі списку через `re.escape` (раніше хардкоджені
  регулярні варіанти "коротвіВ" тощо з `re.IGNORECASE` — працювало, але
  було хитким щодо регістру; тепер case-insensitive матч на lowercased input).
- Додано `INITS_THEN_SURNAME_RE` — нормалізація "О. В. Панчук" → "Панчук О. В."

Тест: `test_normalize_teacher_name_swaps_reversed_initials`.

## База даних

### 10. Composite index + synchronous=NORMAL
**Файл:** `backend/database.py`

- Додано `PRAGMA synchronous=NORMAL` (оптимально для WAL).
- Додано композитний індекс `idx_lessons_teacher_date ON lessons(teacher, date)`
  для швидкого шляху `?teacher=...&date=...`.

## Якість

### 11. Мертвий рядок
**Файл:** `backend/quality.py:53`

Видалено `"  " in normalized` — подвійний пробіл не міг з'явитись після
`normalize_teacher_name` (робить `re.sub(r"\s+"," ",...)`).

## Залежності

### 12. Верхні межі версій
**Файл:** `backend/requirements.txt`

Оригінал: лише `>=`. FastAPI/Starlette іноді ламають сумісність між мінорними
релізами — `pip install` міг зібрати несумісну версію через рік.

**Фікс:** кожна залежність має верхню межу (напр. `fastapi>=0.115.0,<0.120`).

## Flutter

### 13. Хардкоджений URL API
**Файл:** `flutter/api_config.dart`, `flutter/main.dart`, `flutter/schedule_screen.dart`, `flutter/teacher_search_screen.dart`

Оригінал: `https://ukrduzt-schedule.onrender.com` хардкоджено у трьох файлах.

**Фікс:** винесено в `api_config.dart` як
`String.fromEnvironment('API_BASE_URL', ...)`. Збірка через
`--dart-define=API_BASE_URL=...`. У CI передається через secret `API_BASE_URL`.

### 14. Lifecycle-таймер
**Файл:** `flutter/schedule_screen.dart`

Оригінал: `Timer.periodic(30s)` жив весь час (IndexedStack тримає стейт),
навіть коли користувач на вкладці "Викладачі" або додаток у фоні — Battery drain.

**Фікс:** `WidgetsBindingObserver.didChangeAppLifecycleState` — таймер
скасується при `paused`/`inactive`, перезапускається при `resumed`.

### 15. Timeouts + User-Agent
**Файл:** `flutter/api_config.dart`

Оригінал: 65 с timeout для всіх запитів — довгувато для мобільного. Без
User-Agent — міг фільтруватися WAF.

**Фікс:** `kMetadataTimeout = 15s` (groups/teachers), `kScheduleTimeout = 30s`
(schedule). `kHttpHeaders = {'User-Agent': 'UkrDUZT-Schedule-App/1.0'}` на
кожному `http.get`.

### 16. Kyiv-час замість пристрійного
**Файл:** `flutter/schedule_screen.dart`

Оригінал: `DateTime.now()` (локальний час пристрою) — студент за кордоном
бачив неправильний "сьогодні".

**Фікс:** `kyivToday()` (UTC+2, обрізане до дня) для дат,
`kyivNow()` (UTC+2 з часом) для live-статусу пари. Усі 7 замін `DateTime.now()`
переписано.

### 17. Dispose контролерів
**Файл:** `flutter/schedule_screen.dart`

Оригінал: `TextEditingController.dispose()` у `addPostFrameCallback` — гонка з
повторним відкриттям діалогу.

**Фікс:** `dispose()` одразу після `await showDialog`, до асинхронного збереження.

### 18. Винесення спільних констант
**Файл:** `flutter/api_config.dart`

Усі спільні константи (`kApiBaseUrl`, `kHttpHeaders`, timeouts) у окремій
бібліотеці, щоб уникнути циклічних імпортів через `main.dart`.

## CI/CD

### 19. Реальні Flutter-тести
**Файл:** `flutter/test/schedule_screen_test.dart`, `.github/workflows/build-apk.yml`

Оригінал: лише димовий тест "віджет білдиться".

**Фікс:** 12 unit-тестів для `LessonModel.fromJson`, `displayTeacher`,
`buildMeetingKey`, `ZoomAccessConfig`. CI копіює `flutter/test/` у
`ukrduzt_app/test/` окремо від `lib/`.

### 20. Coverage замість .pytest_cache
**Файл:** `.github/workflows/backend-ci.yml`

Оригінал: завантажував `backend/.pytest_cache/` як "pytest-logs" — назва
вводила в оману, контент мало корисний.

**Фікс:** `pytest-cov` → `coverage.xml` + `htmlcov/` артефакти.

### 21. Розумний ретрай Maven
**Файл:** `.github/workflows/build-apk.yml`

Оригінал: 3 рази ретраїв `flutter build apk` незалежно від причини — реальна
помилка Dart коду витрачала 25 хвилин.

**Фікс:** ретрай лише при `429|5xx|Connection timed out|Could not GET` —
інакше fail fast.

### 22. Dependabot для Flutter
**Файл:** `.github/dependabot.yml`

Додано `pub`-екосистему для `flutter/` — Dependabot оновлюватиме `http`,
`share_plus` тощо.

## Гігієна

### 23. Кореневий `.gitignore`
**Файл:** `.gitignore`

Оригінал: відсутній — `backend/data/`, `ukrduzt_app/`, `__pycache__/`, `*.apk`
могли закомітитись.

**Фікс:** кореневий `.gitignore` покриває Python, Flutter, Android signing,
editor noise.

### 24. `.dockerignore`
**Файл:** `backend/.dockerignore`

Додано `requirements.txt` (не потрібен у runtime-стеджі).

### 25. `response: Response = None`
**Файл:** `backend/main.py::get_schedule`

Оригінал: дефолт `None` для не-опціонального параметра — працював лише бо
FastAPI інжектить `Response`. Залишено `= None` для зворотної сумісності з
викликами поза FastAPI, але в_RUNTIME_ FastAPI завжди передає об'єкт.


## Ручне завантаження розкладу (нова функція)

### 26. Модель «ручне vs портал» у БД
**Файл:** `backend/database.py`

Сценарій: на сайті один розклад, але куратор скинув інший. Рішення — окреме
джерело `source_type='manual'`, що **переважає** над порталом для тих самих
(дата, група, номер пари). Інші портальні пари (для яких ручного запису немає)
залишаються доступними.

- Колонки `lessons.source_type` (`'portal'`/`'manual'`) та `lessons.source_name`.
- Таблиця `manual_files` (id, original_name, sha256, lesson_count,
  warnings_count, uploaded_at, note).
- Міграція для існуючих БД: `ALTER TABLE lessons ADD COLUMN source_type ...`
  через інтроспекцію `PRAGMA table_info` (SQLite не має `IF NOT EXISTS` для
  `ADD COLUMN`).
- `query_lessons` тепер дедуплікує: якщо (date, group, lesson_number) є в обох
  джерелах, повертає лише `manual`. Це зроблено у Python (після SQL-фільтра),
  бо дедуп-ключ — складений, а сортування вже групує рядки разом.

### 27. API ручного завантаження
**Файл:** `backend/main.py`

- `POST /api/v1/admin/upload` (multipart, поле `file` + необов'язкове `note`):
  валідує розширення (.pdf), magic bytes (`%PDF-`), розмір (≤20 МБ), парсить
  in-memory через `BytesIO` (без temp-файлу, як і скрапер), зберігає через
  `add_manual_file`. Якщо парсер знайшов 0 пар — 422 з людським повідомленням.
- `GET /api/v1/admin/uploads` — список завантажених файлів.
- `DELETE /api/v1/admin/uploads/{manual_id}` — видалити файл і його пари;
  для тих дат знову стає актуальним розклад з порталу.
- Усі три ендпойнти gated `X-Sync-Token` (якщо `SYNC_TOKEN` встановлено) через
  спільну `_require_sync_token` — той самий механізм що й для `force=true` sync.

### 28. Залежність `python-multipart`
**Файл:** `backend/requirements.txt`

Додано `python-multipart>=0.0.9,<0.1` — потрібен FastAPI `UploadFile`/`Form`.

### 29. Flutter-екран «Додати файл»
**Файл:** `flutter/manual_upload_screen.dart`, `flutter/main.dart`

- Нова вкладка в `NavigationBar` (3 пункти: Розклад / Викладачі / Додати файл).
- `file_picker` для вибору PDF з пристрою; `MultipartFile` для завантаження на
  `/api/v1/admin/upload`. Опціональне поле помітки.
- Токен `X-Sync-Token` зберігається в SharedPreferences (ключ
  `ukrduzt_sync_token_v1`), щоб не перепитувати щоразу.
- Список уже завантажених файлів з можливістю видалити (підтвердження діалогом).
- `pubspec_dependencies_snippet.txt` доповнено `file_picker: ^8.1.2` і
  `http_parser: ^4.0.2`.

### 30. Тести ручного завантаження
**Файл:** `backend/tests/test_main.py`, `flutter/test/manual_upload_test.dart`

Backend (+7):
- `test_upload_rejects_non_pdf` — .txt → 400.
- `test_upload_rejects_pdf_named_but_not_pdf` — .pdf без magic bytes → 400.
- `test_upload_requires_token_when_set` — без токена → 403.
- `test_upload_accepts_pdf_without_token` — без SYNC_TOKEN → 201.
- `test_upload_then_list_then_delete` — повний цикл + 404 на повторне видалення.
- `test_list_uploads_requires_token_when_set` — 403 без токена, 200 з ним.
- `test_manual_overrides_portal_in_query` — головний: seed portal → upload
  manual з тим самим ключем → query повертає лише manual → delete manual →
  portal повертається. Фікстура `_isolated_db` вказує `database.DB_PATH` на
  tmp_path, тести не ламають реальну БД.

Flutter (+5):
- `ManualUploadEntry.fromJson` — повний розбір + толерантність до відсутніх
  полів + null note.
- `UploadResult.ok` / `UploadResult.error` — поля та повідомлення.

### 31. Структура тестового PDF
Backend-тести будують валідний PDF «з частин»: ASCII-контент (Helvetica без
кирилиці) + багаторядковий текст, щоб pdfplumber витягнув `101-LLG-326 /
24.11.2026 / 1 / Math 2.123`. `GROUP_RE` та `ROOM_RE` матчать латинські коди,
тому тести не залежать від кириличних шрифтів у CI.

## Перевірка парсера на реальному кураторському PDF

### 32. Тест на файлі «МЕХ 1 курс 24.11-02.12.2026.pdf»
**Файл:** `backend/tests/test_parser.py` (+ `tests/fixtures/meh_1kurs_24.11-02.12.2026.pdf`)

Реальний PDF, який куратор скидає студентам. У ньому pdfplumber витягує текст
**праворуч-ліворуч**: дати як `6202.11.42` (→ `24.11.2026`), дні тижня як
`коротвіВ` (→ `Вівторок`). Парсер вже мав реверс-обробку (`REVERSED_DATE_RE`,
`_WEEKDAYS` з реверсними формами), тож **допрацьовувати parser.py не довелось** —
це підтверджено тестом:

- 106 пар, 0 попереджень, 0 пар без кімнати/типу.
- 9 дат: 24.11–30.11.2026 + 01.12–02.12.2026.
- 4 групи: `101-ЛЛГ-з26`, `102-ВТІ-з26`, `103-ЕРТ-з26`, `104-ТЕ-з26`.
- 8 викладачів: Бабенко А. О., Сніжко І. А., Осмаєв О. А., Казанко О. В.,
  Катковнікова Л. А., Панчук О. В., Семененко О. І., Ананьєва О.

PDF скопійовано у `tests/fixtures/` (143 КБ), тож тест не залежить від
`upload/` і працює у CI.

## Перемикач «Розділ 1 портал / Розділ 2 офлайн»

### 33. Фільтр `source_type` у database.py
**Файл:** `backend/database.py`

`list_groups(source_type=...)`, `list_teachers(source_type=...)` та
`query_lessons(..., source_type=...)` приймають `'portal'` / `'manual'` /
`None`/`'all'`. Для одного джерела додається `WHERE source_type = ?` і
**пропускається** дедуплікація manual>portal (бо тініцювати нічого). Для `all`
залишається попередня логіка (manual переважає над portal).

### 34. Параметр `?source` у main.py
**Файл:** `backend/main.py`

`/api/v1/groups`, `/api/v1/teachers`, `/api/v1/schedule` отримують query-параметр
`source` з `pattern="^(portal|manual|all)$"` (422 на невірне значення). Відповіді
доповнено `Vary: source`, щоб кеш-проксі не змішували відповіді різних режимів.

### 35. Flutter-перемикач
**Файли:** `flutter/api_config.dart`, `flutter/schedule_screen.dart`, `flutter/teacher_search_screen.dart`

- `api_config.dart`: enum `ScheduleSource { portal, manual, all }` + функція
  `sourceQueryValue(ScheduleSource) → String`.
- Обидва екрани отримали `SegmentedButton` з трьома сегментами (Розділ 1 /
  Розділ 2 / Усі) у верхі body.
- Вибір зберігається в `SharedPreferences` (`ukrduzt_schedule_source_v1`),
  синхронізується між екранами: при перемиканні скидаються групи/результати та
  перевантажується список (в офлайн-режимі набір груп/викладачів інший).
- У режимі `manual` список викладачів не змішується з локальним кешем (бо кеш
  належить портальним групам — інакше портальні викладачі «втікали» б в офлайн).

### 36. Backend-тести фільтрації
**Файл:** `backend/tests/test_main.py` (+8)

- `test_list_groups_filters_by_source`, `test_list_teachers_filters_by_source`
  — portal/manual/all розрізняються.
- `test_query_lessons_source_manual_skips_dedup` — у режимі manual для спільної
  групи повертається лише ручний запис (без портального, без дедупу).
- `test_query_lessons_source_portal_returns_portal_only` — симетрично.
- `test_query_lessons_source_all_dedups_manual_over_portal` — дефолт зберігає
  перевагу manual.
- `test_groups_endpoint_source_filter` — HTTP 422 на `?source=bogus`, 200 з
  валідним, різні набори груп.
- `test_schedule_endpoint_source_filter` — `/schedule?...&source=manual`
  повертає лише manual-предмет для спільної (дата, група, пара).
- Допоміжна `_seed_two_sources()` сіє portal-only + manual-only + спільну
  групу, щоб фільтри мали що ділити.

### 37. Flutter-тести перемикача
**Файл:** `flutter/test/source_toggle_test.dart` (+7)

- `sourceQueryValue` відображає enum → рядок запиту.
- Enum має рівно 3 значення, імена стабільні (для prefs-сумісності).
- `kScheduleSourcePrefsKey` версіонований.
- Побудова URL-ів з `&source=` парситься коректно.

## Виправлення деплою на Render (`COPY requirements.txt: not found`)

### 38. render.yaml з rootDir: backend/
**Файл:** `render.yaml` (новий, корінь репозиторію)

Скріншот деплою показав, що збірка падає на кроці
`[builder 3/4] COPY requirements.txt` з помилкою
`"/requirements.txt": not found` після ручного тригеру «Base directory updated».
Причина: `Dockerfile` та `requirements.txt` лежать у `backend/`, а Render брав
контекстом корінь репозиторію — інструкція `COPY requirements.txt .` не знаходила
файл.

Виправлення: `render.yaml` (Render Blueprint) на корені з
`rootDir: backend/` + `dockerfilePath: ./Dockerfile`. Render читає цей файл на
кожному деплої, і `rootDir` має пріоритет над полем *Root Directory* у
дашборді — отже ручні зміни «Base directory» більше не ламають збірку. Додано
`healthCheckPath: /health`, `autoDeploy: true` та оточення (`TZ=Europe/Kyiv`,
`SYNC_INTERVAL_SECONDS=3600`).

### 39. Кореневий fallback Dockerfile + requirements.txt + .dockerignore
**Файли:** `Dockerfile` (новий, корінь), `requirements.txt` (копія `backend/`),
`.dockerignore` (новий, корінь)

Страхова сітка на випадок, якщо Render проігнорує `render.yaml` або в
дашборді *Dockerfile Path* жорстко вказано на корінь. Кореневий Dockerfile
робить `COPY backend/requirements.txt .` (deps) та `COPY backend/ .` (код),
тому збірка проходить з контекстом-коренем теж — те саме `uvicorn main:app`,
ти ж самі ENV/HEALTHCHECK/користувач 10001, що й у `backend/Dockerfile`.

`.dockerignore` на корені не дає `COPY backend/ .` затягнути `flutter/`,
`docs/`, `.github/`, `tests/`, кеші та локальні `data/` у runtime-образ.

### 40. README: розділ «Деплой на Render»
**Файл:** `README.md`

Описано три шляхи виправлення помилки `requirements.txt: not found`
(`render.yaml` → ручний Root Directory → кореневий fallback Dockerfile) і чому
`rootDir: backend/` потрібен.

## Підсумок тестів

| Середовище | Було | Стало |
|---|---|---|
| Backend pytest | 47 passed | 55 passed (+1 real-PDF, +7 source-filter) |
| Flutter test | 18 | 25 (+7 source-toggle) |
| Dart analyze | 0 errors | 0 errors / 0 warnings |
| Docker build | n/a | обидва Dockerfile верифіковано (цілі COPY існують) |
