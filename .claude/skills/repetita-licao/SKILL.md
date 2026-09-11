---
name: repetita-licao
description: Zamienia notatki z lekcji portugalskiego w materiał do nauki w repeticie — "dodaj słówka z lekcji", "mam nowe zdania z kursu", "wrzuć to do repetity". Pisze plik kursu w modelu notatka→karty, waliduje, sprawdza stabilność id i przeładowuje treść bez restartu.
---

# Materiał z lekcji → notatki w kursie

Michał chodzi na lekcje. Wieczorem przynosi słówka, temat gramatyczny albo
ćwiczenie z książki. Ten skill zamienia to w materiał, który apka poda **jeszcze
dziś** — pole `lesson` z ostatnich trzech dni omija bramkę nowego materiału.

Zastępuje `roda-licao`, który pisał do zakładki `pt` w hubie. Model jest inny i
to nie jest zmiana kosmetyczna: tam jedna pozycja była jednym pytaniem w jednej
formie, tu **jedna notatka daje kilka kart**, każdą z własnym harmonogramem.

## Krok 0 — zapisz surowe notatki

`~/Documents/Research/portuguese/licoes/YYYY-MM-DD.md`, jeden plik na lekcję,
bez wygładzania. Jeśli Michał wkleił tekst w rozmowie, zapisz go tam najpierw.
To jest wejście; plik kursu jest produktem.

## Krok 1 — dobierz typ notatki

To jest **cała robota** tego skilla. Zły typ znaczy notatkę, która niczego nie
uczy albo uczy nie tego.

| Materiał | `notetype` | Co z tego wychodzi |
|---|---|---|
| pojedyncze słowo, które ma być znane w obie strony | `vocab` | **dwie karty**: rozpoznanie (PT→PL) i produkcja (PL→PT), a przy `audio` trzecia — ze słuchu |
| luka w zdaniu portugalskim | `gap` | jedna karta; forma zależy od tego, czy są dystraktory |
| całe zdanie do przetłumaczenia z polskiego | `sentence` | jedna karta produkcyjna |
| zdanie + polecenie („w liczbie mnogiej", „w pretérito") | `transform` | jedna karta |
| fraza do powiedzenia na głos | `phrase` | jedna karta, samoocena |
| obrazek do nazwania | `picture` | wymaga pola `license` przy obrazku — bez tego walidator odrzuci |

**`ordem` nie jest typem.** W hubie był; tutaj układanie z rozsypanki to
**forma** `wordbank`, którą silnik może pokazać dowolną kartę zdaniową. Nie
deklaruj jej — wybierze ją prezenter.

**Nie wybieraj `vocab` odruchowo.** Dwie karty na słowo to dwa razy więcej
powtórek. Ma sens dla słownictwa, które musi być czynne. Dla słowa, które
wystarczy rozpoznać, `gap` w zdaniu jest tańszy i uczy kontekstu.

## Krok 2 — napisz plik

W katalogu kursu, w `units/<jednostka>/notes/`. Jednostka to temat, nie data —
`licao-setembro` zbiera wrześniowe lekcje, a nie każdy dzień osobno.

```yaml
notetype: gap                 # domyślny dla całego pliku; notatka może nadpisać
tags: [vocabulario, licao-setembro]
lesson: 2026-09-15            # data lekcji, ISO, obowiązkowo
notes:
  - id: pechincha             # STABILNE NA ZAWSZE — patrz niżej
    prompt: Consegui uma ___ boa na feira hoje.
    answers: [pechincha]
    cue: okazja cenowa, dobry interes      # po polsku, NIGDY nie zawiera odpowiedzi
    explain: pechincha = okazja; pechinchar = targować się.
    translation: Trafiłem dziś na targu na dobrą okazję.

  - id: sair-eu
    notetype: vocab
    l2: saio
    l1: wychodzę
    explain: sair — eu saio, você sai, nós saímos, eles saem.
```

### Pięć pułapek, które wracają za każdym razem

1. **`id` jest kluczem harmonogramu — nigdy nie zmieniaj go w pliku.** Zmiana
   `id` w YAML po cichu kasuje cały postęp na tej pozycji i nic w interfejsie
   tego nie pokaże. Poprawiaj treść, zostaw `id`.

   Jeśli `id` naprawdę trzeba zmienić, jest na to polecenie, które przenosi
   historię razem z ćwiczeniem i zapisuje zmianę tam, gdzie czyta ją CI:

   ```bash
   repetita rename-id <stare> <nowe>
   ```
2. **`cue` nie może zawierać odpowiedzi.** Notatka trafia wtedy do kwarantanny i
   wypada z nauki. Przy słowach bez polskiego odpowiednika (`saudade`, `acarajé`)
   `cue` **opisuje** („tęsknota za czymś, co minęło"), a słowo czeka w `explain`.
3. **Odpowiedź nie może pojawić się w żadnym widocznym polu.** Sprawdzanie idzie
   **per karta**, nie per notatka: w `vocab` pole `l1` jest pytaniem dla karty
   produkcyjnej i **odpowiedzią** dla rozpoznawczej. Przykład w `example_l1`
   zawierający glosę zdradza tę drugą.
4. **YAML czyta `no`, `yes`, `on`, `off` jako wartości logiczne.** A `no` (em + o)
   to bardzo częsta odpowiedź. Pisz `answers: ["no"]`. Walidator odrzuca, zamiast
   konwertować — konwersja dałaby notatkę, której poprawną odpowiedzią jest
   tekst `"False"`.
5. **Dwukropek w niecytowanej wartości rozwala plik.** `prompt: 'Liczba: 21'`.

## Krok 3 — trzy polecenia, zawsze te same

```bash
cd ~/Documents/codes/repetita
.venv/bin/repetita validate courses/<kurs> --strict
.venv/bin/repetita check-ids --base origin/main --courses courses/
curl -s -X POST http://127.0.0.1:5116/api/reload
```

`validate` z niezerowym kodem znaczy, że **coś nie wejdzie do nauki**. Napraw i
uruchom ponownie; nie zostawiaj kwarantanny „na potem", bo materiał po cichu
znika z puli.

`reload` odpowiada tym, co **faktycznie się zmieniło** — `added`, `removed`,
`cards`. Przeładowanie, które nic nie wczytało, wygląda identycznie jak takie,
które zadziałało, więc **przeczytaj tę odpowiedź**, zamiast zakładać sukces.

Jeśli `reload` nie odpowiada, serwer nie działa — uruchom go ponownie sam
(`bash scripts/restart-host.sh`, który najpierw robi kopię bazy) i powiedz, że to
zrobiłeś. **Nie udawaj, że treść została przeładowana** — plik YAML i tak jest
już na dysku i wczyta się przy najbliższym starcie.

Kod 422 z `reload` znaczy, że kurs jest zepsuty i **poprzedni został utrzymany**.
Nauka Michała nie ucierpiała; napraw plik i powtórz.

## Krok 4 — powiedz, co wejdzie

```bash
curl -s http://127.0.0.1:5116/api/state | python3 -m json.tool
```

Potwierdź po polsku: ile notatek, ile z nich kart (to nie jest ta sama liczba),
i czy brama jest otwarta. Przy zamkniętej bramie wchodzi tylko materiał z
`lesson` z ostatnich trzech dni, do dwunastu pozycji dziennie — więc powiedz,
ile z dzisiejszej lekcji realnie trafi do kolejki.

## Uwagi

* Port **5116**. Repetita nie ma jeszcze uwierzytelniania, więc chodzi wyłącznie
  na `127.0.0.1` i tylko z Maca.
* Nie zgaduj tłumaczeń, których Michał nie podał. Przy wątpliwym słowie
  **zapytaj** — błędne słowo w bazie jest gorsze niż jego brak, bo zostanie
  wyuczone.
* Pierwszy kontakt z notatką apka pokaże w formie łatwiejszej niż zadeklarowana
  (wybór wielokrotny albo układanie z rozsypanki). To drabinka prezentacji, nie
  błąd w pliku.
