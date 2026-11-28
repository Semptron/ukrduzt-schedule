# ukrduzt-schedule

Сервіс розкладу заочного навчання УкрДУЗТ: Python/FastAPI бекенд, що парсить
PDF-розклади з [порталу](https://kart.edu.ua/osvita/portal-rz), та Flutter
додаток для студентів.

## Структура

```
backend/   — FastAPI + pdfplumber + SQLite (WAL)
flutter/   — dart-джерела додатку (CI збирає з них ukrduzt_app/)
.github/   — CI: backend-тести + coverage, збірка APK
```

## Бекенд: локальний запуск

```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/ -v            # 40 тестів
uvicorn main:app --reload              # http://localhost:8000/docs
```

## Бекенд: Docker

```bash
cd backend
docker compose up -d --build
```

`docker-compose.yml` використовує **named volumes** (`schedule-data`,
`schedule-downloads`), тож проблем із правами на bind-mount немає. Якщо
хочете bind-mount — створіть каталоги з uid 10001:
```bash
mkdir -p data downloads && sudo chown -R 10001:10001 data downloads
```

## Деплой на Render

У репозиторії є `render.yaml` (Blueprint) з `rootDir: backend/`. Render читає
цей файл на корені й використовує `rootDir` як **build context** для
`backend/Dockerfile` — саме це потрібно, бо `Dockerfile` і `requirements.txt`
лежать у `backend/`, а не в корені.

### Якщо бачите помилку `"/requirements.txt": not found`

Це означає, що Render бере контекстом **корінь репозиторію**, а не `backend/`.
Типові причини та виправлення (будь-яке з них підходить, краще перше):

1. **Залиште сервіс як є і дайте Render перечитати `render.yaml`.** У дашборді:
   *Manual Deploy → Deploy latest commit*, або пушніть коміт із `render.yaml`.
   `rootDir: backend/` у коді має пріоритет над полем *Root Directory* у
   дашборді й застосовується на кожному деплої — навіть після ручного
   «Base directory updated».
2. **У дашборді: Service → Settings → *Root Directory* = `backend/`** (без
   слешу в кінці), потім *Save* → *Manual Deploy*. Це ручний дубль до `render.yaml`.
3. **Якщо з будь-якої причини контекстом лишається корінь** — є запасний
   кореневий `Dockerfile` + кореневий `requirements.txt` (копія `backend/`).
   Тоді *Dockerfile Path* у дашборді = `Dockerfile` (корінь) — і збірка теж
   проходить, бо цей Dockerfile робить `COPY backend/requirements.txt` та
   `COPY backend/ .`.

Рекомендований шлях — `render.yaml` (версіонований, не залежить від
налаштувань дашборду). Запасний `Dockerfile` лишено як страхову сітку.

### Змінні середовища

| Змінна | За замовч. | Призначення |
|---|---|---|
| `SYNC_INTERVAL_SECONDS` | `3600` | Період фонової синхронізації |
| `TZ` | `Europe/Kyiv` (у Dockerfile) | Часовий пояс контейнера — критично для виведення року коротких дат біля Нового року |
| `SYNC_TOKEN` | (порожньо) | Якщо встановлено, `POST /api/v1/sync?force=true` вимагає заголовок `X-Sync-Token: <значення>` (403 без нього). Захищає від DoS через примусовий репарсинг |
| `CORS_ALLOW_ORIGINS` | `*` | Список доменів через кому для credentialed CORS; `*` = permissive без credentials |

Генерувати `SYNC_TOKEN`:
```bash
openssl rand -hex 24
```

### Ручне завантаження розкладу (коли куратор скинув інший файл)

На сайті порталу іноді висить один розклад, а куратор групи скидає зовсім інший.
Щоб додати або замінити розклад вручну, є окремий API та вкладка у додатку:

- **API (довірений користувач):**
  - `POST /api/v1/admin/upload` — завантажити PDF (multipart, поле `file`, необов'язкове `note`). Парсить in-memory, зберігає з `source_type='manual'`. Ті самі (дата, група, пара), що й на порталі, — **переважають** над порталом; решта портальних пар лишається.
  - `GET /api/v1/admin/uploads` — список завантажених файлів.
  - `DELETE /api/v1/admin/uploads/{id}` — видалити файл; його пари зникають і для тих дат знову стає актуальним розклад з порталу.
  - Всі три ендпойнти вимагають `X-Sync-Token`, якщо на сервері встановлено `SYNC_TOKEN`.
- **Додаток (вкладка «Додати файл»):** вибирає PDF через `file_picker`, надсилає на `/api/v1/admin/upload`, показує список уже завантажених файлів з можливістю видалити. Токен зберігається локально (SharedPreferences).

### Перемикач «Розділ 1 портал / Розділ 2 офлайн»

На екранах «Розклад» та «Викладачі» є `SegmentedButton` з трьома режимами:

- **Розділ 1 (портал)** — лише розклад, зібраний скрапером з сайту УкрДУЗТ.
- **Розділ 2 (офлайн)** — лише пари, дати, групи та викладачі з **завантажених куратором файлів**. Сайтній розклад повністю виключається: інші групи/викладачі, яких немає в ручних файлах, не показуються.
- **Усі** — обидва джерела; для тих самих (дата, група, пара) ручний запис переважає над портальним (це режим за замовчуванням).

Реалізовано через параметр запиту `?source=portal|manual|all` для `/api/v1/groups`, `/api/v1/teachers` та `/api/v1/schedule`. Вибір зберігається в SharedPreferences (`ukrduzt_schedule_source_v1`) і синхронізується між двома екранами.

## Flutter

```bash
# Дев-збірка з продакшн-URL
flutter create --org ua.edu.kart.schedule ukrduzt_app
cp flutter/*.dart ukrduzt_app/lib/
cp flutter/test/*.dart ukrduzt_app/test/
# вставити залежності з flutter/pubspec_dependencies_snippet.txt
cd ukrduzt_app && flutter pub get
flutter analyze
flutter test
```

Перевизначити URL API під час збірки:
```bash
flutter run --dart-define=API_BASE_URL=https://staging.example.com
flutter build apk --release --dart-define=API_BASE_URL=https://...
```

## Що виправлено у цій версії

Ключові стабілізатори (деталі — у `docs/changes.md`):

1. **Колізія кириличних імен файлів** — скрапер парсить PDF з `BytesIO` у пам'яті,
   а не з тимчасового файлу; два різні кириличні PDF більше не перетирають одне
   одного у паралельному пулі завантажень.
2. **TZ контейнера** — `TZ=Europe/Kyiv` у Dockerfile + compose; виведення року
   для коротких дат більше не з'їжджає біля Нового року.
3. **503 під час повторної синхронізації** — `/api/v1/schedule` повертає 503
   (Retry-After) щоразу, коли йде синхронізація й даних ще немає; клієнт не
   кешує "не знайдено" на 5 хвилин.
4. **Race у стані синхронізації** — `threading.Event` встановлюється синхронно
   до старту потоку; `/health` і `/version` одразу бачать "sync in progress".
5. **CORS** — `allow_origins=["*"]` більше не поєднується з `allow_credentials`
   (заборонено спекою); credentialed-режим вмикається явним списком доменів.
6. **Авторизація `/api/v1/sync`** — bearer-токен `SYNC_TOKEN` для `force=true`.
7. **Docker volumes** — named volumes замість bind-mount, `user: "10001:10001"`.
8. **9-та пара** — `LESSON_NUM_RE` та `BELLS` підтримують 9-ту пару замість
   мовчазного перетворення на 8-му.
9. **Flutter** — `--dart-define=API_BASE_URL`, lifecycle-таймер (пауза у фоні),
   User-Agent на запитах, timeouts 15/30 с замість 65 с, Kyiv-час замість
   локального часу пристрою, dispose контролерів одразу після діалогу.
10. **Тестове покриття** — 40 backend-тестів (було 26) + 13 Flutter-тестів.
