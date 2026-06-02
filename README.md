# truthbrush_telegram

Fork projektu [stanfordio/truthbrush](https://github.com/stanfordio/truthbrush) rozszerzony o **automatyczny monitoring Truth Social i przekazywanie postow na Telegram**.

## Co robi?

Monitoruje wybranych uzytkownikow Truth Social i automatycznie przesyla ich nowe posty na kanal Telegram. Obsluguje:

- Zwykle posty (tekst + zdjecia)
- Retruthy (reposty)
- Cytaty
- Albumy (wiele zdjec)
- Automatyczne tlumaczenie na polski (lub inny jezyk)
- Wybor silnika tlumaczenia: **OpenAI GPT-4o-mini** (lepsza jakosc) lub **Google Translate** (darmowe)
- Sledzenie kosztow tlumaczenia (per model, dziennie i lacznie)

## Szybki start (Docker)

### Wymagania

- Docker + Docker Compose
- Konto Truth Social (login + haslo)
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

### Konfiguracja `.env`

```bash
# Truth Social — login + haslo LUB token
TRUTHSOCIAL_USERNAME=twoj_login
TRUTHSOCIAL_PASSWORD=twoje_haslo
# TRUTHSOCIAL_TOKEN=            # alternatywa zamiast loginu

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
    command: ["realDonaldTrump", "--interval", "5", "--state-file", "/data/truthbrush_state.json", "--max-backfill-age", "21600", "--max-posts-per-poll", "20"]

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
truthbrush monitor realDonaldTrump --interval 5 --max-backfill-age 21600 --max-posts-per-poll 20

# Test bez Telegrama
truthbrush monitor realDonaldTrump --dry-run
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

- Interval ponizej 3 sekund moze spowodowac rate limit / ban
- Przy 2+ kontach bezpieczny interval to 5 sekund
- Truth Social moze blokowac ruch spoza USA — uzyj proxy (zmienna `http_proxy`)
- Bot **musi byc adminem** kanalu Telegram zeby moc pisac
- Przy pierwszym uruchomieniu zapisuje ostatni post i czeka na nowe — nie spamuje historia
- Po przerwie dluzszej niz `MAX_BACKFILL_AGE_SECONDS` skrypt pomija backlog i zaczyna od najnowszego posta

## Weryfikacja

```bash
python3 -m py_compile truthbrush/monitor.py truthbrush/state.py truthbrush/cli.py
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail 80 trump
```

## Licencja

Apache 2.0 (zgodnie z oryginalnym repo)

## Oryginalne repo

[stanfordio/truthbrush](https://github.com/stanfordio/truthbrush) — Stanford Internet Observatory
