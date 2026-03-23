# Ulepszenia w forku vs oryginalne repo

Oryginalne repozytorium: [stanfordio/truthbrush](https://github.com/stanfordio/truthbrush)
Fork: [popek1990/truthbrush_telegram](https://github.com/popek1990/truthbrush_telegram)

---

## Nowe pliki

| Plik | Opis |
|------|------|
| `truthbrush/telegram.py` | Klient Telegram Bot API (wysyłanie wiadomości, zdjęć, albumów) |
| `truthbrush/formatter.py` | Konwersja postów Truth Social → czytelne wiadomości Telegram |
| `truthbrush/state.py` | Persystencja stanu (last_seen_id) w pliku JSON z atomowym zapisem |
| `truthbrush/monitor.py` | Pętla monitoringu — polling → formatowanie → wysyłka → zapis stanu |
| `Dockerfile` | Obraz Docker (Python 3.12-slim) z entrypointem `truthbrush monitor` |
| `docker-compose.yml` | Konfiguracja Docker Compose z healthcheckiem, volume i restart policy |
| `.env.example` | Szablon zmiennych środowiskowych (Truth Social + Telegram + proxy) |
| `plan.md` | Plan architektoniczny z fazami implementacji i ADR-ami |

---

## Zmienione pliki

### `truthbrush/api.py` — naprawy bugów upstream

| # | Linia | Było (oryginał) | Jest (fork) | Dlaczego |
|---|-------|------------------|-------------|----------|
| 1 | ~98 | `logger.warning(f"Using token {self.auth_id}")` | `logger.debug("Authentication token acquired")` | Token logowany w plaintext — wyciek danych uwierzytelniających |
| 2 | ~117 | `datetime.utcnow().replace(tzinfo=...)` | `datetime.now(timezone.utc)` | `utcnow()` jest deprecated od Python 3.12 |
| 3 | ~142 | Brak `return` po złapaniu `CurlError` | `return None` | `resp` nie istniał po wyjątku → `_check_ratelimit(resp)` crashował z `UnboundLocalError` |
| 4 | ~162 | `_get_paginated` bez try/except | Dodany `try/except CurlError` | Błąd sieci podczas paginacji crashował cały proces |
| 5 | ~355 | `while posts != None:` | `while posts is not None:` | Niezgodność z PEP 8 (porównanie z None powinno używać `is`) |
| 6 | ~461 | `user_id = self.lookup(username)["id"]` | Sprawdzenie `if user_data is None` przed dostępem | `lookup()` może zwrócić None → `TypeError` przy problemie z siecią |
| 7 | ~487 | Brak sprawdzenia None po `_get()` | `if result is None: break` | `_get()` zwraca None przy błędzie → `"error" in None` crashuje z `TypeError` |
| 8 | ~574 | `"Cannot authenticate to ."` | `"Cannot authenticate to Truth Social."` | Niekompletna wiadomość błędu — brakowało nazwy serwisu |

### `truthbrush/cli.py` — nowa komenda `monitor`

| Zmiana | Opis |
|--------|------|
| Import `os` | Potrzebny do `os.getenv()` |
| Zmienne `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Odczyt z env vars przy starcie |
| Komenda `monitor` | Nowa komenda Click: `truthbrush monitor <username> [--interval 60] [--state-file ...] [--dry-run]` |

---

## Nowe funkcjonalności

### 1. Monitor Truth Social → Telegram (`truthbrush monitor`)

Polling co N sekund, sprawdza nowe posty wybranego usera i wysyła je na kanał Telegram.

```bash
# Uruchomienie
truthbrush monitor realDonaldTrump --interval 60

# Test bez Telegrama
truthbrush monitor realDonaldTrump --dry-run
```

### 2. Formatowanie postów

- Zwykłe posty → tekst + link do oryginału
- Retruthy (reposty) → prefiks "🔁 retruthed"
- Cytaty → sekcja z cytatem (┃ prefix)
- Media → zdjęcia/albumy wysyłane jako natywne media Telegram

### 3. Odporność na awarie

- Atomowy zapis stanu (`tempfile` → `os.replace()`) — crash nie uszkodzi pliku
- Stan per-post (nie per-batch) — restart wznawia od ostatniego wysłanego
- Re-autentykacja przy wygaśnięciu tokenu
- Graceful shutdown na SIGINT/SIGTERM (reaguje w <1s)
- Retry z exponential backoff dla Telegram API

### 4. Docker deployment

```bash
cp .env.example .env
# uzupełnij zmienne w .env
docker compose up -d
```

Healthcheck sprawdza czy `last_check` w pliku stanu nie jest starszy niż 5 minut.

---

## Zabezpieczenia w Telegram Sender

| Zabezpieczenie | Opis |
|----------------|------|
| Limit mediów | Max 10 elementów w albumie (limit API Telegram) |
| Obcinanie tekstu | Wiadomości: max 4096 znaków, podpisy: max 1024 znaków |
| Rate limit 429 | Automatyczny retry po `retry_after` z odpowiedzi Telegram |
| Retry + backoff | Max 3 próby z exponential backoff przy błędach sieci |
| Ukryty token | Token bota nie pojawia się w `__repr__` / stack trace |
| Błędy 4xx | Klient errors (poza 429) rzucają wyjątek natychmiast — brak retries |

---

## Automatyczne tłumaczenie (opcjonalne)

| Cecha | Opis |
|-------|------|
| Biblioteka | `deep-translator` (Google Translate, darmowe, bez klucza API) |
| Konfiguracja | `TRANSLATE_TO=pl` w `.env` (lub `de`, `fr`, `es`, ...) |
| Co jest tłumaczone | Tylko treść posta i cytatów |
| Co NIE jest tłumaczone | Usernames, linki, nagłówki, tagi HTML |
| Limit znaków | 4500 na request (Google Translate limit) |
| Fallback | Jeśli tłumaczenie się nie uda → oryginał po angielsku |

---

## Zależności

**Jedna nowa zależność:**
- `deep-translator` — do tłumaczenia postów (opcjonalne, Google Translate)

**Reszta kodu korzysta ze stdlib:**
- `urllib.request` (stdlib) — do Telegram API
- `html.parser` (stdlib) — do czyszczenia HTML
- `tempfile`, `json`, `signal`, `os` (stdlib) — do stanu i sygnałów
- `loguru` (już w projekcie) — do logowania
