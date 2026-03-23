# Plan: Truth Social → Telegram Monitor

## Cel

Stabilny daemon monitorujący wybranego użytkownika Truth Social i przekazujący nowe posty na kanał ogłoszeniowy Telegram w czasie rzeczywistym.

---

## Architektura

```
┌─────────────────────────────────────────────────┐
│                    monitor.py                     │
│                                                   │
│  ┌───────────┐    ┌──────────┐    ┌───────────┐  │
│  │  Poller   │───▶│ Formatter│───▶│  Telegram  │  │
│  │           │    │          │    │  Sender    │  │
│  └─────┬─────┘    └──────────┘    └───────────┘  │
│        │                                          │
│        ▼                                          │
│  ┌───────────┐                                    │
│  │ State     │  (last_seen_id w pliku JSON)       │
│  │ Manager   │                                    │
│  └───────────┘                                    │
│        │                                          │
│        ▼                                          │
│  truthbrush.Api  (istniejąca biblioteka)          │
└─────────────────────────────────────────────────┘
```

**Przepływ danych:**
1. Poller co N sekund odpytuje `api.pull_statuses(username, since_id=last_seen_id)`
2. Nowe posty sortowane chronologicznie (od najstarszego)
3. Formatter czyści HTML, formatuje tekst + media
4. Telegram Sender wysyła na kanał przez Bot API
5. State Manager zapisuje `last_seen_id` po udanym wysłaniu

---

## Fazy implementacji

### Faza 0: Fix upstream bugów w truthbrush (`api.py`)

**Cel:** Naprawienie krytycznych bugów, które spowodują crashe monitora.

**Fix 1 — `_get()` UnboundLocalError (linia ~129):**
Jeśli `CurlError` zostanie złapany, `resp` nie istnieje → `_check_ratelimit(resp)` rzuca `UnboundLocalError`.
```python
# Naprawić: return None po CurlError
except curl_cffi.curl.CurlError as e:
    logger.error(f"Curl error: {e}")
    return None  # ← dodać
```

**Fix 2 — deprecated `datetime.utcnow()` (linia ~117):**
```python
# Było:
now = datetime.utcnow().replace(tzinfo=timezone.utc)
# Ma być:
now = datetime.now(timezone.utc)
```

**Fix 3 — token logowany w plaintext (linia ~98):**
```python
# Było:
logger.warning(f"Using token {self.auth_id}")
# Ma być:
logger.debug("Authentication token acquired")
```

**Fix 4 — niepełny error message (linia ~574):**
```python
# Było:
raise LoginErrorException("Cannot authenticate to .")
# Ma być:
raise LoginErrorException("Cannot authenticate to Truth Social.")
```

**Fix 5 — `!= None` (linia ~353):**
```python
# Było:
while posts != None:
# Ma być:
while posts is not None:
```

**Plik:** `truthbrush/api.py`

---

### Faza 1: Telegram Sender (`telegram.py`)

**Cel:** Moduł wysyłający wiadomości na Telegram.

**Co zrobić:**
- Klasa `TelegramSender` używająca `urllib.request` (stdlib, zero zależności)
- `curl_cffi` NIE używamy do Telegrama — istnieje tu tylko do impersonacji Chrome wobec Cloudflare. Telegram Bot API nie ma ochrony anti-bot.
- Metoda `send_message(chat_id, text, parse_mode="HTML")` → `POST /bot{token}/sendMessage`
- Metoda `send_photo(chat_id, photo_url, caption)` → `POST /bot{token}/sendPhoto`
- Metoda `send_media_group(chat_id, media)` → `POST /bot{token}/sendMediaGroup` (dla postów z wieloma obrazkami)
- Retry z exponential backoff (max 3 próby)
- Obsługa błędów: rate limit Telegrama (429 + `retry_after`), sieć, nieprawidłowy token

**Zmienne env:**
- `TELEGRAM_BOT_TOKEN` — token bota z @BotFather
- `TELEGRAM_CHAT_ID` — ID kanału (format: `@nazwa_kanalu` lub `-100xxxxx`)

**Plik:** `truthbrush/telegram.py`

---

### Faza 2: Formatter postów (`formatter.py`)

**Cel:** Konwersja posta Truth Social na czytelną wiadomość Telegram.

**Co zrobić:**
- Dataclass `FormattedPost(text: str, media_urls: list[str], is_album: bool)`
- Funkcja `format_post(post: dict) -> FormattedPost`
- Strip HTML tagów z `post["content"]` za pomocą `html.parser` (stdlib) — bez BeautifulSoup
- Ekstrakcja URL mediów z `post["media_attachments"]`
- Obsługa reblogów (`post["reblog"]`) — prefiks "🔁 Retruth od..."
- Obsługa quote-postów
- Jeden obrazek → `send_photo` z captionem
- Wiele obrazków → `send_media_group` (album)
- Tylko tekst → `send_message`

**Format wiadomości:**
```
{display_name} (@{username})

{treść posta}

🔗 {link do posta}
```

**Plik:** `truthbrush/formatter.py`

---

### Faza 3: State Manager (`state.py`)

**Cel:** Persystencja stanu między restartami (jaki post widzieliśmy ostatnio).

**Co zrobić:**
- Plik JSON: `~/.truthbrush_state.json` (konfigurowalny przez env/CLI)
- Struktura: `{"username": {"last_seen_id": "123456", "last_check": "2026-03-23T12:00:00+00:00"}}`
- Zapis atomowy (write to temp file → `os.replace()`) — odporność na crashe
- `last_check` aktualizowany przy każdym pollu (nie tylko przy nowym poście)
- Ładowanie przy starcie, zapis po każdym udanym wysłaniu
- Jeśli plik nie istnieje → stwórz pusty state (pierwszy run pobierze tylko najnowsze posty, nie całą historię)

**Plik:** `truthbrush/state.py`

---

### Faza 4: Monitor / Poller (`monitor.py`)

**Cel:** Główna pętla pollingu.

**Co zrobić:**
- Klasa `TruthMonitor` łącząca wszystkie komponenty
- Pętla: `while True` → poll → format → send → save state → sleep
- Konfigurowalny interwał (domyślnie 60s)
- Graceful shutdown na SIGINT/SIGTERM
- Logowanie przez `loguru` (spójne z resztą truthbrush)

**Logika pollingu:**
```python
posts = list(api.pull_statuses(username, since_id=last_seen_id))
posts.sort(key=lambda p: int(p["id"]))  # chronologicznie
for post in posts:
    formatted = formatter.format_post(post)
    sender.send(formatted)
    state.save(username, post["id"])
```

**Logika re-auth przy wygaśnięciu tokenu:**
```python
try:
    posts = list(api.pull_statuses(...))
except (LoginErrorException, Exception) as e:
    if "401" in str(e) or "403" in str(e):
        logger.warning("Token expired, re-authenticating...")
        api.auth_id = None  # wymusza ponowne logowanie
        continue  # retry w następnej iteracji
```

**Logika pierwszego uruchomienia (brak state):**
- Pobrać TYLKO najnowszy post → zapisać jego ID jako `last_seen_id`
- NIE wysyłać go na Telegram (unikamy spamu historycznymi postami)
- Od następnego pollu → normalna praca

**Obsługa błędów:**
- Błąd API Truth Social → log + retry za interwał
- Błąd Telegrama → retry 3x z backoff, potem log + kontynuuj (nie blokuj kolejnych postów)
- Błąd sieci → log + retry za interwał
- Crash → state zachowany, restart od last_seen_id

**Plik:** `truthbrush/monitor.py`

---

### Faza 5: CLI command

**Cel:** Dodanie komendy `truthbrush monitor` do istniejącego CLI.

**Co zrobić:**
- Nowa komenda Click w `cli.py`:
  ```
  truthbrush monitor <username> --interval 60
  ```
- Opcje:
  - `--interval` / `-i` — interwał w sekundach (default: 60)
  - `--state-file` — ścieżka do pliku stanu
  - `--dry-run` — loguj posty bez wysyłania na Telegram
- Walidacja przy starcie: sprawdź czy TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID są ustawione (chyba że --dry-run)

**Plik:** edycja `truthbrush/cli.py`

---

### Faza 6: Docker + deployment

**Cel:** Łatwe uruchomienie jako kontener.

**Co zrobić:**
- `Dockerfile` — Python 3.12-slim, pip install, entrypoint
- `docker-compose.yml` — serwis z `.env` i volume na state file
- Restart policy: `unless-stopped`
- Healthcheck: sprawdź czy `last_check` w state file nie jest starsze niż `3 * interval`
- `.env.example` — szablon zmiennych

**Pliki:** `Dockerfile`, `docker-compose.yml`, `.env.example`

---

## Nowe pliki

```
truthbrush/
├── telegram.py      # Telegram Bot API client (urllib.request)
├── formatter.py     # Post → wiadomość Telegram
├── state.py         # Persystencja last_seen_id (JSON)
├── monitor.py       # Pętla pollingu + re-auth
Dockerfile
docker-compose.yml
.env.example         # Szablon zmiennych
```

## Zmienione pliki

```
truthbrush/api.py    # Fix: CurlError crash, utcnow(), token logging, error msg
truthbrush/cli.py    # + komenda "monitor"
```

---

## Zmienne środowiskowe

| Zmienna | Wymagana | Opis |
|---------|----------|------|
| `TRUTHSOCIAL_USERNAME` | Tak* | Login do Truth Social |
| `TRUTHSOCIAL_PASSWORD` | Tak* | Hasło do Truth Social |
| `TRUTHSOCIAL_TOKEN` | Tak* | Alternatywa: token z przeglądarki |
| `TELEGRAM_BOT_TOKEN` | Tak | Token bota z @BotFather |
| `TELEGRAM_CHAT_ID` | Tak | ID kanału docelowego |
| `MONITOR_INTERVAL` | Nie | Interwał pollingu w sekundach (default: 60) |
| `STATE_FILE` | Nie | Ścieżka pliku stanu (default: ~/.truthbrush_state.json) |

*Wymagane: token LUB username+password

---

## Ryzyka i mitygacja

| Ryzyko | Prawdopodobieństwo | Mitygacja |
|--------|-------------------|-----------|
| Cloudflare blokuje requesty | Średnie | `curl_cffi` z impersonacją Chrome — już w truthbrush |
| Rate limit Truth Social (300 req/window) | Niskie | Truthbrush ma wbudowany auto-sleep na rate limit |
| Geoblock (API niedostępne z PL) | Średnie | Proxy/VPS w USA (`http_proxy` env var) |
| Token wygasa | Średnie | Re-auth w monitorze: `api.auth_id = None` → wymusza ponowne logowanie |
| Telegram rate limit (30 msg/s) | Niskie | Jeden user = maks kilka postów/minutę |
| Crash / restart | Pewne | State file + Docker restart policy |
| Truth Social zmienia API | Niskie (Mastodon) | Bazowy API to Mastodon — stabilne |
| `_get()` CurlError crash | Pewne bez fixa | Faza 0 — naprawiamy przed budowaniem monitora |

---

## Kolejność pracy

```
Faza 0 → Faza 1 ─┐
         Faza 2 ──┼── Faza 4 → Faza 5 → Faza 6
         Faza 3 ──┘
```

Fazy 1-3 są niezależne — można je robić równolegle po Fazie 0.
Faza 4 wymaga 1-3.
Fazy 5-6 wymagają 4.

---

## Decyzje architektoniczne

### ADR-001: Plik JSON zamiast bazy danych do stanu

**Kontekst:** Potrzebujemy persystencji `last_seen_id` między restartami.

**Decyzja:** Prosty plik JSON z atomowym zapisem (`os.replace()`).

**Dlaczego:** Monitorujemy jednego usera. Jeden klucz do zapisania. MongoDB/SQLite to overkill — dodatkowa zależność i infrastruktura bez wartości.

**Kompromis:** Przy wielu userach (>10) warto rozważyć SQLite.

### ADR-002: `urllib.request` (stdlib) do Telegram API

**Kontekst:** Potrzebujemy wysłać wiadomość na kanał Telegram.

**Decyzja:** Stdlib `urllib.request` zamiast `curl_cffi` lub `python-telegram-bot`.

**Dlaczego:**
- `curl_cffi` istnieje w projekcie **wyłącznie** do impersonacji Chrome (Cloudflare bypass). Telegram nie ma anti-bot — używanie go jest semantycznie mylące i ciągnie niepotrzebne narzędzie.
- `python-telegram-bot` wnosi 15+ MB zależności dla 3 endpointów (sendMessage, sendPhoto, sendMediaGroup).
- `urllib.request` to stdlib — zero zależności, 30 linii kodu, wystarczające do prostych POST.

### ADR-003: Polling zamiast webhooków

**Kontekst:** Truth Social nie oferuje webhooków ani streaming API.

**Decyzja:** Polling co 60s z `since_id` filtrowaniem.

**Dlaczego:** Jedyna dostępna opcja. 60s to dobry balans — wystarczająco szybko dla ogłoszeń, bezpiecznie wobec rate limitu (300 req/window = max 1 req/1.2s).

### ADR-004: Pierwszy run nie wysyła historii

**Kontekst:** Przy pierwszym uruchomieniu brak state → `since_id` jest pusty.

**Decyzja:** Pierwszy poll pobiera najnowszy post, zapisuje jego ID, ale NIE wysyła go na Telegram.

**Dlaczego:** Bez tego monitor przy każdym pierwszym uruchomieniu zalałby kanał setkami starych postów. User chce monitorować "od teraz", nie archiwum.
