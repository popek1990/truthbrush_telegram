# Problem: backlog postow po dluzszej przerwie serwera

## Kontekst

Forwarder monitoruje konto Truth Social i przesyla nowe posty na Telegram.
Stan pracy jest zapisywany w pliku JSON jako `last_seen_id` oraz `last_check`.
Dzieki temu po restarcie skrypt wie, od ktorego posta ma wznowic prace.

Problem pojawil sie po awarii zasilania: serwer byl wylaczony przez okolo 7 dni.
Po ponownym uruchomieniu forwarder pobral wszystkie posty nowsze od ostatniego
zapisanego `last_seen_id` i wyslal je na Telegram.

Efekty:

- kanal Telegram zostal zaspamowany starymi postami,
- czesc uzytkownikow opuscila grupe,
- stare posty zostaly niepotrzebnie przetlumaczone przez API GPT,
- wygenerowalo to niepotrzebny koszt tokenow,
- obecne zachowanie jest ryzykowne przy kazdej dluzszej awarii serwera,
  kontenera, internetu albo Truth Social API.

## Przyczyna techniczna

Obecna logika w `truthbrush/monitor.py` zaklada, ze jesli istnieje
`last_seen_id`, to skrypt powinien zawsze nadrobic wszystkie posty od tego ID.

Uproszczony przeplyw:

```python
last_seen_id = state.get_last_seen_id(username)
posts = list(api.pull_statuses(username, since_id=last_seen_id))
```

Po krotkim restarcie jest to poprawne: jesli serwer byl wylaczony kilka minut,
warto wyslac posty opublikowane w tym czasie.

Po dlugiej przerwie jest to zle: skrypt traktuje 7 dni zaleglosci tak samo jak
kilka minut zaleglosci. Nie ma zadnej reguly, ktora rozroznia normalne wznowienie
od przestarzalego stanu.

Drugi problem: tlumaczenie dzieje sie dopiero po pobraniu listy postow, ale przed
wysylka. Jesli backlog ma kilkadziesiat lub kilkaset postow, koszt GPT pojawia
sie nawet zanim uzytkownik zauwazy spam.

## Cel naprawy

Po dluzszej nieaktywnosci forwarder nie powinien odtwarzac historii.
Powinien ustawic sie na najnowszy dostepny post i monitorowac dopiero nowe posty
opublikowane po ponownym uruchomieniu.

Docelowa zasada:

> Jesli ostatni poprawny poll jest starszy niz skonfigurowany limit, nie nadrabiaj
> backlogu. Zapisz najnowszy post jako punkt startowy i nie wysylaj nic na Telegram.

## Proponowane rozwiazanie

Dodac limit wieku wznowienia, np. `MAX_BACKFILL_AGE_SECONDS`.

Proponowana wartosc domyslna:

```text
21600 sekund = 6 godzin
```

Interpretacja:

- przerwa krotsza niz 6 godzin: forwarder nadrabia posty normalnie,
- przerwa dluzsza niz 6 godzin: forwarder pomija backlog i zaczyna od teraz.

Limit powinien byc konfigurowalny z CLI oraz z `.env`, np.:

```bash
truthbrush monitor realDonaldTrump \
  --interval 5 \
  --state-file /data/truthbrush_state.json \
  --max-backfill-age 21600
```

Opcjonalnie w `.env`:

```bash
MAX_BACKFILL_AGE_SECONDS=21600
```

## Szczegoly zachowania

### Pierwsze uruchomienie

Jesli nie ma `last_seen_id`, obecne zachowanie jest dobre:

1. pobierz kilka najnowszych postow,
2. znajdz najnowsze ID,
3. zapisz je jako `last_seen_id`,
4. nie wysylaj nic na Telegram.

To zachowanie nalezy zachowac.

### Normalny restart

Jesli `last_seen_id` istnieje, a `last_check` jest swiezy:

1. pobierz posty nowsze od `last_seen_id`,
2. posortuj od najstarszego do najnowszego,
3. przetlumacz,
4. wyslij na Telegram,
5. zapisuj `last_seen_id` po kazdym udanym wyslaniu.

To zachowanie nalezy zachowac.

### Restart po dlugiej przerwie

Jesli `last_seen_id` istnieje, ale `last_check` jest starszy niz
`MAX_BACKFILL_AGE_SECONDS`:

1. nie pobieraj calego backlogu przez `since_id`,
2. pobierz tylko kilka najnowszych postow,
3. znajdz najnowsze ID,
4. zapisz je jako nowe `last_seen_id`,
5. zaktualizuj `last_check`,
6. niczego nie tlumacz,
7. niczego nie wysylaj na Telegram,
8. zaloguj czytelna informacje, np.:

```text
State for @realDonaldTrump is stale by 7 days; skipping backlog and resuming after post ID 123456
```

### Brak albo bledny `last_check`

Jesli `last_check` nie istnieje albo nie da sie go sparsowac, nalezy potraktowac
stan jako potencjalnie przestarzaly i wykonac bezpieczny resync.

To jest bezpieczniejsze niz zalozenie, ze mozna nadrabiac historie.

## Dodatkowy bezpiecznik

Warto dodac drugi limit: maksymalna liczba postow obslugiwanych w jednym pollu,
np. `MAX_POSTS_PER_POLL=20`.

Jesli API zwroci wiecej postow niz limit, forwarder powinien uznac to za backlog
albo anomalia i przejsc w tryb bezpiecznego resyncu:

1. zapisac najnowsze ID z pobranego batcha,
2. nie tlumaczyc batcha,
3. nie wysylac batcha,
4. zalogowac ostrzezenie.

Ten bezpiecznik chroni przed:

- recznym cofnieciem pliku state,
- uszkodzonym state,
- bledem API,
- naglym zwrotem bardzo duzej liczby postow.

## Plan naprawy

### 1. Zmiany w `truthbrush/state.py`

Dopisac helper do pobierania `last_check` jako `datetime`, np.:

```python
def get_last_check_datetime(self, username: str) -> Optional[datetime]:
    ...
```

Mozna tez parsowanie wykonac w `monitor.py`, ale helper w `state.py` ograniczy
duplikacje i bedzie latwiejszy do testowania.

### 2. Zmiany w `truthbrush/monitor.py`

Dopisac parametry do `TruthMonitor.__init__()`:

```python
max_backfill_age_seconds: int | None = 21600
max_posts_per_poll: int | None = 20
```

Dopisac metode pomocnicza:

```python
def _reset_to_latest(self, reason: str) -> None:
    ...
```

Metoda powinna:

1. pobrac maksymalnie kilka najnowszych postow,
2. zapisac najnowszy `post["id"]`,
3. nie formatowac,
4. nie tlumaczyc,
5. nie wysylac.

Zmienic `_initialize()`:

1. jesli nie ma `last_seen_id`, wywolaj `_reset_to_latest("first run")`,
2. jesli jest `last_seen_id`, sprawdz wiek `last_check`,
3. jesli state jest przestarzaly, wywolaj `_reset_to_latest("stale state")`,
4. jesli state jest swiezy, kontynuuj normalny resume.

Zmienic `_poll()`:

1. po pobraniu postow sprawdz `len(posts)`,
2. jesli przekracza `max_posts_per_poll`, zapisz najnowszy ID i pomin batch,
3. dopiero po tych sprawdzeniach wykonuj `format_post(..., translator=...)`.

Najwazniejsze: wszystkie blokady backlogu musza byc przed tlumaczeniem.

### 3. Zmiany w `truthbrush/cli.py`

Dopisac opcje:

```python
@click.option(
    "--max-backfill-age",
    default=21600,
    type=int,
    help="Skip backlog if last successful check is older than this many seconds",
)
@click.option(
    "--max-posts-per-poll",
    default=20,
    type=int,
    help="Skip a batch if one poll returns more posts than this",
)
```

Przekazac te wartosci do `TruthMonitor`.

Opcjonalnie odczytac domyslne wartosci z env:

```python
MAX_BACKFILL_AGE_SECONDS
MAX_POSTS_PER_POLL
```

### 4. Zmiany w `.env.example`

Dopisac:

```bash
# Skip historical backlog after long downtime.
# 21600 = 6 hours. Set to 0 to disable stale-state protection.
MAX_BACKFILL_AGE_SECONDS=21600

# Maximum number of posts processed in one poll.
# Protects against state corruption or unexpected API backlog.
MAX_POSTS_PER_POLL=20
```

### 5. Zmiany w `docker-compose.yml`

Dodac jawne argumenty do komendy albo polegac na `.env`.

Wersja jawna:

```yaml
command:
  [
    "realDonaldTrump",
    "--interval", "5",
    "--state-file", "/data/truthbrush_state.json",
    "--max-backfill-age", "21600",
    "--max-posts-per-poll", "20"
  ]
```

### 6. Testy

Dodac testy jednostkowe dla monitora z mockowanym API, senderem i translatorem.

Scenariusze:

1. brak state: zapisuje najnowszy post i nic nie wysyla,
2. swiezy state: wysyla nowe posty normalnie,
3. state starszy niz limit: zapisuje najnowszy post, nic nie wysyla,
4. brak `last_check`: wykonuje bezpieczny resync,
5. bledny `last_check`: wykonuje bezpieczny resync,
6. batch wiekszy niz `max_posts_per_poll`: pomija batch i zapisuje najnowszy ID,
7. blad Telegrama: nie przesuwa `last_seen_id` po nieudanej wysylce.

## Kryteria akceptacji

Naprawa jest poprawna, jesli:

- po 7 dniach przerwy forwarder nie wysyla starych postow,
- po 7 dniach przerwy forwarder nie tlumaczy starych postow przez GPT,
- po krotkim restarcie forwarder nadal nadrabia nowe posty,
- `last_seen_id` nadal jest zapisywany po kazdym udanym wyslaniu,
- blad Telegrama nadal nie przesuwa state do przodu,
- logi jasno mowia, kiedy backlog zostal pominiety,
- ustawienia mozna zmienic bez edycji kodu.

## Rekomendowana konfiguracja dla tego serwera

Dla domowego serwera na Proxmox/Beelink:

```bash
MAX_BACKFILL_AGE_SECONDS=21600
MAX_POSTS_PER_POLL=20
```

Jesli priorytetem jest absolutny brak spamu po kazdej dluzszej przerwie, mozna
zmniejszyc limit do 1 godziny:

```bash
MAX_BACKFILL_AGE_SECONDS=3600
```

Wtedy forwarder nadrobi tylko bardzo krotkie przerwy, a po dluzszym postoju
zacznie monitorowanie od najnowszego posta.
