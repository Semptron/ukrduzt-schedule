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

## Підсумок тестів

| Середовище | Було | Стало |
|---|---|---|
| Backend pytest | 26 passed | 40 passed |
| Flutter test | 1 (smoke) | 13 (12 unit + smoke) |
| Dart analyze | n/a | 0 issues |
