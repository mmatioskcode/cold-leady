#!/bin/bash
# Odszyfrowuje pliki leady_*.csv.enc z artefaktu „leady-…” pobranego z zakładki Actions.
# Użycie: bash odszyfruj.sh ~/Downloads/leady-123456789.zip   (albo folder z plikami .enc)
# Pyta o hasło z sekretu LEADY_HASLO. Wynik: pliki .csv obok zaszyfrowanych.
set -e
cel="${1:-.}"
if [[ "$cel" == *.zip ]]; then d="${cel%.zip}"; mkdir -p "$d"; unzip -o -q "$cel" -d "$d"; cel="$d"; fi
read -r -s -p "Hasło LEADY_HASLO: " LEADY_HASLO </dev/tty; echo; export LEADY_HASLO
for f in "$cel"/*.csv.enc; do
  [ -f "$f" ] || continue
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$f" -out "${f%.enc}" -pass env:LEADY_HASLO && echo "OK: ${f%.enc}"
done
