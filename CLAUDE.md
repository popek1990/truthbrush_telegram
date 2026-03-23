# CLAUDE.md

## Projekt

Fork [stanfordio/truthbrush](https://github.com/stanfordio/truthbrush) rozszerzony o monitor Truth Social → Telegram.
Repo: [popek1990/truthbrush_telegram](https://github.com/popek1990/truthbrush_telegram)

## Architektura

```
truthbrush/
  api.py          # Upstream API client (curl_cffi + Chrome impersonation)
  cli.py          # Click CLI — komendy: statuses, search, monitor, ...
  monitor.py      # Polling loop: fetch → format → translate → send → save state
  formatter.py    # Post dict → FormattedPost (tekst + media URLs)
  translator.py   # Opcjonalne tłumaczenie via Google Translate (deep-translator)
  telegram.py     # Telegram Bot API client (stdlib urllib.request)
  state.py        # Persystencja last_seen_id (atomowy JSON)
```

## Przepływ danych

1. `monitor.py` co N sekund wywołuje `api.pull_statuses(username, since_id=last_seen_id)`
2. Nowe posty sortowane chronologicznie (oldest first)
3. `formatter.py` czyści HTML, wyciąga media, buduje tekst Telegram
4. `translator.py` tłumaczy **tylko treść** (nie usernames/URLs/HTML)
5. `telegram.py` wysyła na kanał (tekst / zdjęcie / album z fallbackiem)
6. `state.py` zapisuje `last_seen_id` po każdym udanym wysłaniu (atomowy zapis)

## Komendy budowania

```bash
pip install .                        # instalacja lokalna
python3 -m py_compile truthbrush/X.py  # weryfikacja składni
docker compose up -d --build         # Docker build + start
./rebuild.sh                         # pełna przebudowa z resetem stanu
```

## Kluczowe decyzje

- **urllib.request** do Telegram (nie curl_cffi) — Telegram nie ma Cloudflare
- **deep-translator** do tłumaczeń — darmowe, bez klucza API
- **Atomowy zapis stanu** — `tempfile.mkstemp()` → `os.replace()`
- **Pierwszy run** — zapisuje najnowszy post ID, NIE wysyła go na Telegram
- **Fallback mediów** — jeśli Telegram odrzuci media (wideo/wrong type), wysyła jako tekst
- **Tłumaczenie tylko treści** — usernames, URLs i tagi HTML nie są tłumaczone

## Zmienne środowiskowe

| Zmienna | Wymagana | Opis |
|---------|----------|------|
| `TRUTHSOCIAL_USERNAME` | Tak* | Login Truth Social |
| `TRUTHSOCIAL_PASSWORD` | Tak* | Hasło Truth Social |
| `TRUTHSOCIAL_TOKEN` | Tak* | Alternatywa: token z przeglądarki |
| `TELEGRAM_BOT_TOKEN` | Tak | Token bota z @BotFather |
| `TELEGRAM_CHAT_ID` | Tak | ID kanału (`@nazwa` lub `-100xxx`) |
| `TRANSLATE_TO` | Nie | Kod języka (np. `pl`, `de`, `fr`) |

*Wymagane: token LUB username+password

## Znane ograniczenia

- Truth Social może blokować ruch spoza USA (wymaga proxy)
- Google Translate limit 4500 znaków na request
- Telegram: max 4096 znaków wiadomość, 1024 caption, 10 mediów w albumie
- Rate limit Truth Social: 300 req/window — interval < 30s ryzykowny
