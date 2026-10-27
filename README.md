# truthbrush_telegram

Fork projektu [stanfordio/truthbrush](https://github.com/stanfordio/truthbrush) rozszerzony o **automatyczny monitoring Truth Social i przekazywanie postow na Telegram**.

## Co robi?

Monitoruje wybranych uzytkownikow Truth Social i automatycznie przesyla ich nowe posty na kanal Telegram. Obsluguje:

- Zwykle posty (tekst + zdjecia)
- Retruthy (reposty)
- Cytaty
- Albumy (wiele zdjec/wideo), wideo (`sendVideo`) i `gifv` (`sendAnimation`)
- Automatyczne tlumaczenie na polski (lub inny jezyk)
- Wybor silnika tlumaczenia: **OpenAI GPT-4o-mini** (lepsza jakosc) lub **Google Translate** (darmowe)
- Sledzenie kosztow tlumaczenia (per model, dziennie i lacznie)

## Szybki start (Docker)

### Wymagania

- Docker + Docker Compose
- Konto Truth Social — **zalecany token z przegladarki** (patrz nizej), alternatywnie login + haslo
- Bot Telegram (stworzony przez [@BotFather](https://t.me/BotFather))
- Opcjonalnie: klucz API OpenAI (dla lepszych tlumaczen)

### Instalacja

```bash
git clone https://github.com/popek1990/truthbrush_telegram.git
cd truthbrush_telegram
cp .env.example .env
nano .env          # uzupelnij dane
docker compose up -d
```

### Zalecany tryb: token (`TRUTHSOCIAL_TOKEN`)

Najbezpieczniej jest uzyc tokena z zalogowanej przegladarki zamiast loginu i hasla.
Logowanie haslem (OAuth password grant) przy wygasniesciu sesji potrafi wpasc w petle
ponownych logowan, skonczyc sie bledem `429` i wymuszona weryfikacja SMS / blokada konta.

Jak zdobyc token:

1. Zaloguj sie na `truthsocial.com` w przegladarce.
2. Otworz DevTools (`F12`) → zakladka **Network**.
3. Kliknij dowolne zapytanie do `/api/v1/...` → **Headers**.
4. Z naglowka `authorization: Bearer <token>` skopiuj samego `<token>` (bez slowa `Bearer`).
5. Wklej go do `.env` jako `TRUTHSOCIAL_TOKEN=`.

> **Nie wylogowuj sie** w tej przegladarce — wylogowanie uniewaznia token.
> Gdy token przestanie dzialac, bot wysle alert na Telegram i sie wstrzyma (patrz nizej) —
> wtedy zdobadz nowy token, wklej go do `.env` i zrestartuj kontener.

### Konfiguracja `.env`

```bash
# Truth Social — ZALECANE: token z przegladarki (patrz wyzej)
TRUTHSOCIAL_TOKEN=<token_z_przegladarki>
# Alternatywa (ryzykowna): login + haslo
# TRUTHSOCIAL_USERNAME=twoj_login
# TRUTHSOCIAL_PASSWORD=twoje_haslo

# Telegram (wymagane)
TELEGRAM_BOT_TOKEN=<bot_token>
TELEGRAM_CHAT_ID=@nazwa_kanalu   # lub -100xxxxx dla prywatnego kanalu

# Tlumaczenie (opcjonalne)
TRANSLATE_TO=pl                   # pl, de, fr, es, ... lub zostaw puste

# OpenAI (opcjonalne — lepsza jakosc tlumaczen)
# Jesli ustawione, uzywa GPT-4o-mini zamiast Google Translate
OPENAI_API_KEY=<openai_api_key>

# Ochrona przed backlogiem po dluzszej przerwie
MAX_BACKFILL_AGE_SECONDS=21600    # 6h; 0 wylacza ochrone
MAX_POSTS_PER_POLL=20             # 0 wylacza limit batcha

# Alert, gdy od dawna nie udaje sie pobrac postow (siec / Cloudflare / 5xx)
ALERT_AFTER_NO_SUCCESS=7200       # 2h; 0 wylacza alert

# Proxy (opcjonalne, jesli geoblock)
# http_proxy=socks5://127.0.0.1:1080
# https_proxy=socks5://127.0.0.1:1080
```

### Tlumaczenie — jak dziala?

| Konfiguracja | Silnik | Koszt |
|-------------|--------|-------|
| `TRANSLATE_TO=pl` (bez klucza OpenAI) | Google Translate | Darmowe |
| `TRANSLATE_TO=pl` + `OPENAI_API_KEY=<openai_api_key>` | GPT-4o-mini | ~$0.01/dzien |
| Brak `TRANSLATE_TO` | Brak tlumaczenia | $0 |

Tlumaczona jest **tylko tresc posta** — usernames, linki i formatowanie pozostaja nietknete.

Koszty sa logowane przy kazdym tlumaczeniu:
```
Translation cost: $0.000123 | gpt-4o-mini 85in/92out | daily: $0.0012 | total: $0.0045
```

Pelna historia kosztow zapisywana jest w `/data/usage.json`:
```bash
docker compose exec trump cat /data/usage.json
```

### Ochrona przed backlogiem

Forwarder chroni Telegram i koszty GPT po dluzszej przerwie serwera.

- `MAX_BACKFILL_AGE_SECONDS=21600` — jesli ostatni poprawny poll jest starszy niz 6 godzin, skrypt zapisze najnowszy post jako punkt startowy i nie wysle historii.
- `MAX_POSTS_PER_POLL=20` — jesli jeden poll zwroci wiecej niz 20 postow, batch zostanie pominiety jako potencjalny backlog.
- Obie decyzje sa wykonywane przed formatowaniem, tlumaczeniem i wysylka na Telegram.
- Ustawienie wartosci `0` wylacza dana ochrone.

### Alert, gdy pobieranie nie dziala (siec / Cloudflare / 5xx)

Blad sieci, strona Cloudflare albo HTTP 5xx nie jest juz myluco traktowany jak „brak
nowych postow” — monitor rozpoznaje go jako nieudane pobranie:

- `last_check` **nie** jest aktualizowany, liczniki nie sa zerowane, bot **nie** jest
  wstrzymywany (to nie grozi blokada konta) — po lagodnym backoffie (do 5 min) probuje dalej.
- Jesli przez `--alert-after-no-success` sekund (domyslnie **7200 s = 2h**, env
  `ALERT_AFTER_NO_SUCCESS`, `0` wylacza) nie uda sie zadne pobranie, bot wysyla **jeden**
  alert na Telegram, a po powrocie do normy **jedna** wiadomosc „✅ Monitor Truth Social
  znow dziala”. Blad wysylki alertu nie wywala monitora.

### Obsluga mediow (zdjecia, wideo, albumy)

- Typ zalacznika jest zachowywany: zdjecie → `sendPhoto`, wideo → `sendVideo`,
  `gifv` → `sendAnimation`, album (kilka zdjec/wideo) → `sendMediaGroup` (moze mieszac
  zdjecia i wideo).
- Gdy tekst nie miesci sie w podpisie (limit 1024), media ida bez podpisu, a pelny tekst
  jako osobna wiadomosc. Po czesciowym sukcesie (media poszly, tekst nie) ponowienie
  **nie** wysyla mediow drugi raz.
- Gdy Telegram nie przyjmie medium z URL (zly typ / za duzy plik) albo zalacznik jest
  nieznanego typu (np. audio), jego link trafia do tekstu zamiast przepasc.

### Bezpieczny HTML i odporna wysylka

- Tresc, nazwy i wynik tlumaczenia sa escapowane (`&`, `<`, `>`) przed wyslaniem z
  `parse_mode=HTML`, wiec znak `<` czy `&` w poscie nie wywala parsera Telegrama i nie
  blokuje kolejki w nieskonczonosc.
- Blad dotyczacy **konkretnego posta** (400 „can't parse entities”, za dluga wiadomosc,
  blad medium): po 3 nieudanych probach bot wysyla wersje awaryjna (zwykly tekst bez HTML,
  z linkiem) i idzie dalej.
- Blad dotyczacy **calego kanalu/bota** (401 zly token bota, 403 bot wyrzucony, 400 „chat
  not found”) **nie** przewija postow — bot ponawia i nie gubi wiadomosci.

### Backoff przy bledach logowania / rate limit

Monitor nie odpytuje Truth Social w kolko po bledzie. Przy kolejnych bledach autoryzacji
(HTTP 401/403) lub rate limit (HTTP 429) nastepny poll odbywa sie dopiero po rosnacym
opoznieniu: **1, 2, 4, 8, 16, 30 minut** (maksymalnie 30 min). Udany poll zeruje licznik.

- Przy `429` z naglowkiem `Retry-After` / `x-ratelimit-reset` bot czeka `max(retry_after, backoff)`.
- **Tryb token-only:** przy 401/403 token nie jest kasowany (nie ma czym sie zalogowac) —
  w logach pojawia sie prosba o wklejenie nowego `TRUTHSOCIAL_TOKEN`.
- **Tryb haslo:** ponowne logowanie haslem dzieje sie tylko w ramach backoffu, nigdy czesciej.
- Czekanie jest przerywalne (SIGTERM) — `docker compose down` / restart dziala natychmiast.

### Twardy limit i alert na Telegram

Po `--max-auth-failures` kolejnych nieudanych probach autoryzacji (domyslnie **5**) bot:

1. wysyla **jedna** wiadomosc na kanal Telegram (np. „⚠️ Monitor Truth Social: logowanie
   nie dziala (5 prob). Prawdopodobnie wygasl token lub konto wymaga weryfikacji. Bot wstrzymany.”),
2. przestaje odpytywac Truth Social i czeka bezczynnie do restartu (nie konczy procesu,
   zeby `restart: unless-stopped` nie uruchomil petli od nowa).

Alertu nie ma w trybie `--dry-run` ani bez skonfigurowanego Telegrama. Blad wysylki alertu
nie wywala monitora. Zdrowie kontenera opiera sie na znaczniku „zyje” (`_heartbeat`)
odswiezanym nawet podczas backoffu i bezczynnego czekania, wiec Docker nie restartuje
kontenera w trakcie swiadomego oczekiwania.

### Bot Telegram — konfiguracja

1. Napisz do [@BotFather](https://t.me/BotFather) na Telegramie
2. Wyslij `/newbot`, podaj nazwe i username
3. Skopiuj token do `.env` (`TELEGRAM_BOT_TOKEN`)
4. Dodaj bota jako **administratora** kanalu (z uprawnieniem "Wysylanie wiadomosci")

**Chat ID kanalu:**
- Publiczny kanal: `@nazwa_kanalu`
- Prywatny kanal: przeslij wiadomosc z kanalu do [@getidsbot](https://t.me/getidsbot) — poda ID (zaczyna sie od `-100...`)

### Konfiguracja monitorowanego konta

Edytuj `docker-compose.yml`, jesli chcesz zmienic monitorowany username:

```yaml
services:
  trump:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - monitor-data:/data
    command: ["realDonaldTrump", "--interval", "60", "--state-file", "/data/truthbrush_state.json", "--max-backfill-age", "21600", "--max-posts-per-poll", "20"]
    logging:
      driver: json-file
      options:
        max-size: "10m"      # rotacja logow Dockera (bez limitu plik rosnie w nieskonczonosc)
        max-file: "3"

volumes:
  monitor-data:
```

### Przydatne komendy

```bash
docker compose logs -f           # logi na zywo
docker compose ps                # status kontenerow
docker compose restart           # restart
docker compose down              # zatrzymaj
./rebuild.sh                     # bezpieczna przebudowa (git pull + build, state zostaje)
./rebuild.sh --reset-state       # swiadomy reset state + rebuild

# Sprawdzenie kosztow tlumaczenia
docker compose exec trump cat /data/usage.json
```

## Uzycie bez Dockera

```bash
pip install .
export TRUTHSOCIAL_USERNAME=login
export TRUTHSOCIAL_PASSWORD=haslo
export TELEGRAM_BOT_TOKEN=token
export TELEGRAM_CHAT_ID=@kanal
export TRANSLATE_TO=pl
export OPENAI_API_KEY=<openai_api_key>     # opcjonalne

# Uruchomienie
truthbrush monitor realDonaldTrump --interval 60 --max-backfill-age 21600 --max-posts-per-poll 20

# Test bez Telegrama
truthbrush monitor realDonaldTrump --dry-run

# Alert gdy od 2h nie udaje sie pobrac postow (0 wylacza)
truthbrush monitor realDonaldTrump --alert-after-no-success 7200
```

## Oryginalne komendy truthbrush

Fork zachowuje pelna kompatybilnosc z oryginalnym truthbrush:

```bash
truthbrush statuses HANDLE           # posty uzytkownika
truthbrush user HANDLE               # metadane uzytkownika
truthbrush search --searchtype accounts QUERY  # wyszukiwanie
truthbrush trends                    # popularne posty
truthbrush tags                      # popularne tagi
truthbrush suggestions               # sugerowani uzytkownicy
truthbrush likes POST TOP_NUM        # polubienia posta
truthbrush comments POST TOP_NUM     # komentarze
truthbrush groupposts GROUP_ID       # posty z grupy
truthbrush grouptrends               # popularne grupy
truthbrush grouptags                 # tagi grup
truthbrush groupsuggestions          # sugerowane grupy
truthbrush ads                       # reklamy
```

## Co zmieniono wzgledem oryginalu

### Nowe funkcjonalnosci

| Funkcja | Opis |
|---------|------|
| `truthbrush monitor` | Monitoring postow + przesylanie na Telegram |
| Tlumaczenie OpenAI | GPT-4o-mini — naturalne, kontekstowe tlumaczenia |
| Tlumaczenie Google | Darmowy fallback gdy brak klucza OpenAI |
| Sledzenie kosztow | Koszty tlumaczenia per model, dziennie i lacznie (`/data/usage.json`) |
| Ochrona backlogu | Pomijanie historii po dluzszej awarii serwera |
| Docker | Pelna konteneryzacja z healthcheck i restart policy |
| `rebuild.sh` | Skrypt do czystej przebudowy |

### Naprawione bugi w oryginalnym kodzie

| Bug | Opis |
|-----|------|
| `_get()` crash | Brak `return None` po CurlError — powodowal `UnboundLocalError` |
| `_get_paginated()` crash | Brak obslugi bledu sieci — crashowal caly proces |
| `pull_statuses()` crash | Brak sprawdzenia None po `_get()` i `lookup()` |
| Token w logach | Token logowany w plaintext — wyciek danych |
| `datetime.utcnow()` | Deprecated od Python 3.12 |
| Niekompletny error | `"Cannot authenticate to ."` → `"Cannot authenticate to Truth Social."` |
| `!= None` | Niezgodnosc z PEP 8 |
| Backlog po awarii | Po starym `last_check` skrypt nie wysyla historii i nie uruchamia GPT |
| HTML blokuje kolejke | Nieescapowane `<`/`&` → Telegram 400 → post wracal w nieskonczonosc i tlumaczyl sie co cykl |
| Ciche bledy fetch | `CurlError`/5xx/Cloudflare wygladaly jak „brak nowych postow" — teraz `FetchError` + alert |
| Zgubione wideo | Kazde medium szlo jako `sendPhoto`; teraz wideo → `sendVideo`, `gifv` → `sendAnimation` |
| Ucięty tekst z mediami | Tekst >1024 znakow gubiony w podpisie; teraz pelny tekst jako osobna wiadomosc |
| `_truncate` tnie encje | Liczyl znaki Pythona, nie UTF-16, i ciął w srodku `&amp;` |
| `AttributeError` w `_check_ratelimit` | `x-ratelimit-remaining` bez `x-ratelimit-reset` → crash; dodano limit sleepa 300 s |
| OpenAI bez timeoutu | `OpenAI(timeout=30, max_retries=2)`; `finish_reason=="length"` → oryginal |
| Niatomowy `usage.json` | `mkstemp` + `os.replace` jak w `state.py` |
| Brak rotacji logow | `docker-compose.yml`: `json-file` z `max-size: 10m`, `max-file: 3` |

### Nowe pliki

```
truthbrush/
  telegram.py      # Telegram Bot API (urllib.request)
  formatter.py     # Konwersja postow na format Telegram
  state.py         # Persystencja stanu (atomowy zapis JSON)
  monitor.py       # Petla monitoringu
  translator.py    # Tlumaczenie (OpenAI GPT / Google Translate + usage tracking)
Dockerfile
docker-compose.yml
.dockerignore      # Sekrety i lokalne pliki nie trafiaja do kontekstu builda
.env.example
rebuild.sh
```

## Bezpieczenstwo sekretow

- Prawdziwe credentiale trzymaj tylko w lokalnym `.env`.
- `.env`, `.env.*`, pliki state, usage data i lokalny `AGENTS.md` sa ignorowane przez gita.
- `.dockerignore` wyklucza `.env`, `.git`, lokalne notatki i runtime artefakty z kontekstu `docker build`.
- `.env.example` zawiera tylko puste wartosci albo neutralne placeholdery.
- Nie commituj tokenow Telegram, Truth Social ani OpenAI.

## Koszty

| Usluga | Koszt |
|--------|-------|
| Truth Social API | Darmowe |
| Telegram Bot API | Darmowe |
| Google Translate | Darmowe |
| OpenAI GPT-4o-mini | ~$0.01/dzien (~50 postow) |

## Wazne

- Domyslny i zalecany interval to **60 s**; ponizej 30 s bot loguje ostrzezenie (ryzyko rate limit / ban)
- Zalecany tryb to `TRUTHSOCIAL_TOKEN` — login + haslo jest ryzykowny (patrz sekcja o tokenie)
- Przy bledach autoryzacji / 429 bot stosuje backoff (1→30 min), a po `--max-auth-failures` probach wysyla alert i sie wstrzymuje
- Truth Social moze blokowac ruch spoza USA — uzyj proxy (zmienna `http_proxy`)
- Bot **musi byc adminem** kanalu Telegram zeby moc pisac
- Przy pierwszym uruchomieniu zapisuje ostatni post i czeka na nowe — nie spamuje historia
- Po przerwie dluzszej niz `MAX_BACKFILL_AGE_SECONDS` skrypt pomija backlog i zaczyna od najnowszego posta

## Weryfikacja

```bash
python3 -m py_compile truthbrush/*.py
pytest test/test_monitor.py          # test_api.py wymaga sieci
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail 80 trump
```

## Licencja

Apache 2.0 (zgodnie z oryginalnym repo)

## Oryginalne repo

[stanfordio/truthbrush](https://github.com/stanfordio/truthbrush) — Stanford Internet Observatory
