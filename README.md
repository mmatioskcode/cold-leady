# Cold leady

Codzienne zbieranie leadów do cold maili: firmy z CEIDG, które mają e-mail i nie mają strony www.
Działa w GitHub Actions (workflow „Cold leady”), w dni robocze, partia 500 leadów na dzień.

Repo jest publiczne, ale dane firm nie są: plik z leadami jest szyfrowany hasłem z sekretu `LEADY_HASLO`,
a log i podsumowanie zawierają tylko liczby.

## Sekrety (Settings → Secrets and variables → Actions)

| Sekret | Co to |
|---|---|
| `CEIDG_TOKEN` | token API hurtowni danych CEIDG |
| `ANTHROPIC_API_KEY` | klucz API Claude (Haiku 5.5 ocenia nazwy firm i zgaduje domeny) |
| `LEADY_HASLO` | dowolne długie hasło do szyfrowania pliku z leadami |

## Pobranie leadów

1. Actions → Cold leady → przebieg → Artifacts → `leady-…` (pobiera się .zip).
2. `bash odszyfruj.sh ~/Downloads/leady-….zip` i wpisz hasło. Obok powstaje `leady_RRRR-MM-DD_….csv`.

Ręczne uruchomienie: Actions → Cold leady → Run workflow (`sonda` = test połączenia z CEIDG).
