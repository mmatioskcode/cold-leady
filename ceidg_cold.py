"""Leady do cold maili StykDesign: CEIDG (firmy z e-mailem) -> czy firma ma stronę -> Claude Haiku (filtr sieci, zgadywanie domen).

Działa w GitHub Actions (workflow „Cold leady”), bez włączonego Maca.
  python cold/ceidg_cold.py sonda   -> 2 zapytania do CEIDG; pokazuje tylko kody HTTP i nazwy pól (bez danych firm)
  python cold/ceidg_cold.py dzien   -> zbiera leady do CEL_LEADOW albo do MAKS_MINUT, zapisuje CSV do wynik/

Sekrety (tylko ze zmiennych środowiska, nigdy nie są wypisywane): CEIDG_TOKEN, ANTHROPIC_API_KEY.
Stan między przebiegami (katalog STAN, w cache GitHuba): widziane wpisy CEIDG, kursory stron, skróty (SHA-256) wydanych maili,
liczba leadów w partii. Repo jest publiczne: log zawiera tylko liczby, a CSV z danymi firm workflow szyfruje przed wysłaniem.
Partia = dzień roboczy, dla którego zbieramy (przebiegi wieczorem, w nocy i rano składają się na jedną partię po CEL_DZIEN leadów).
"""
import csv, datetime as dt, hashlib, json, os, re, socket, sys, time, urllib.error, urllib.parse, urllib.request

BASE = os.environ.get("CEIDG_BASE", "https://dane.biznes.gov.pl/api/ceidg/v3").rstrip("/")
STAN = os.environ.get("STAN", "stan")
WYNIK = os.environ.get("WYNIK", "wynik")
CEL_DZIEN = int(os.environ.get("CEL_LEADOW", "500"))
MAKS_MINUT = float(os.environ.get("MAKS_MINUT", "320"))
LIMIT_USD = float(os.environ.get("LIMIT_USD", "0.50"))
NA_STRONE = int(os.environ.get("NA_STRONE", "25"))
PAUZA = 3.7                      # limit CEIDG: 1000 zapytań na godzinę
MODEL, IN_USD, OUT_USD = "claude-haiku-5-5", 0.10, 0.50
DATA = time.strftime("%Y-%m-%d")
RUN = os.environ.get("GITHUB_RUN_ID", time.strftime("%H%M"))


def partia():
    """Dzień roboczy, dla którego zbieramy: czas polski minus 12 h, a sobota/niedziela przechodzą na poniedziałek."""
    try:
        from zoneinfo import ZoneInfo
        teraz = dt.datetime.now(ZoneInfo("Europe/Warsaw"))
    except Exception:
        teraz = dt.datetime.utcnow() + dt.timedelta(hours=1)
    d = (teraz + dt.timedelta(hours=12)).date()      # przebieg o 22:00 zbiera na następny dzień
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def skrot(email):
    return hashlib.sha256(email.encode()).hexdigest()[:24]
START = time.time()

FREE = {"gmail.com", "wp.pl", "o2.pl", "interia.pl", "onet.pl", "op.pl", "poczta.onet.pl", "tlen.pl", "gazeta.pl", "interia.eu",
        "vp.pl", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "poczta.fm", "go2.pl", "buziaczek.pl", "spoko.pl",
        "int.pl", "autograf.pl", "onet.eu", "protonmail.com", "live.com", "yahoo.pl", "outlook.pl", "wp.eu", "poczta.pl"}
PKD = [("komis samochodowy", "4511Z"), ("detailing / oklejanie", "4520Z"), ("kuchnie na wymiar", "3102Z"),
       ("meble na wymiar", "3109Z"), ("remonty i wykończenia", "4333Z"), ("remonty i wykończenia", "4334Z"),
       ("remonty i wykończenia", "4339Z"), ("ogrody", "8130Z"), ("sale weselne", "5621Z"), ("salony meblowe", "4759A"),
       ("medycyna estetyczna", "8622Z"), ("kliniki stomatologiczne", "8623Z"), ("schody", "1623Z"), ("suknie ślubne", "4771Z")]
EMAIL_RE = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[a-z]{2,}")


def minuty():
    return (time.time() - START) / 60


# ---------------- CEIDG ----------------
_ost = [0.0]
st = {"ceidg": 0, "bledy_ceidg": 0, "in": 0, "out": 0, "claude": 0}


def ceidg(sciezka, params=None):
    """GET do CEIDG z pauzą 3,7 s i ponawianiem (429, timeout, strona HTML zamiast JSON). None = nie udało się."""
    tok = os.environ.get("CEIDG_TOKEN")
    if not tok:
        sys.exit("Brak sekretu CEIDG_TOKEN")
    url = sciezka if sciezka.startswith("http") else BASE + sciezka
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + tok,
                                               "User-Agent": "styk-leady/1.0"})
    for proba in range(6):
        czekaj = PAUZA - (time.time() - _ost[0])
        if czekaj > 0:
            time.sleep(czekaj)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                _ost[0] = time.time(); st["ceidg"] += 1
                body = r.read()
                if r.status == 204 or not body:
                    print(f"CEIDG: pusta odpowiedź (HTTP {r.status})", flush=True)
                    return {}
                try:
                    return json.loads(body)
                except ValueError:
                    st["bledy_ceidg"] += 1
                    txt = body.decode("utf-8", "ignore")
                    tyt = re.search(r"<title[^>]*>(.*?)</title>", txt, re.S | re.I)
                    tekst = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)))
                    print(f"CEIDG: odpowiedź nie-JSON (HTTP {r.status}, tytuł: {tyt.group(1).strip()[:80] if tyt else '-'}; "
                          f"treść: {tekst.strip()[:200]}), ponawiam za {60 + 30 * proba} s", flush=True)
                    time.sleep(60 + 30 * proba)
        except urllib.error.HTTPError as e:
            _ost[0] = time.time(); st["ceidg"] += 1
            if e.code == 204:
                return {}
            if e.code == 429:
                st["bledy_ceidg"] += 1
                print("CEIDG: 429 (limit), czekam", flush=True); time.sleep(120 * (proba + 1)); continue
            if e.code in (401, 403):
                sys.exit(f"CEIDG HTTP {e.code}: token odrzucony albo brak dostępu z tego adresu IP")
            if e.code == 404:
                return {}
            st["bledy_ceidg"] += 1
            print(f"CEIDG HTTP {e.code}, ponawiam", flush=True); time.sleep(30 * (proba + 1))
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError):
            _ost[0] = time.time(); st["bledy_ceidg"] += 1
            print("CEIDG timeout/błąd sieci, ponawiam", flush=True); time.sleep(20 * (proba + 1))
    return None


def lista(d):
    if not isinstance(d, dict):
        return []
    for k in ("firmy", "firma", "items", "data"):
        if isinstance(d.get(k), list):
            return d[k]
    return []


def nastepna(d):
    links = d.get("links") if isinstance(d, dict) else None
    if isinstance(links, dict):
        n = links.get("next")
        if isinstance(n, dict):
            n = n.get("href")
        return n or None
    return None


def szukaj(obj, slowa, pomin=("link", "href", "self")):
    """Pierwsza niepusta wartość tekstowa pola, którego nazwa zawiera jedno ze słów (w całym zagnieżdżonym JSON-ie)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if kl in pomin:
                continue
            if any(s in kl for s in slowa) and isinstance(v, (str, int)) and str(v).strip():
                return str(v).strip()
        for k, v in obj.items():
            if k.lower() in pomin:
                continue
            w = szukaj(v, slowa, pomin)
            if w:
                return w
    elif isinstance(obj, list):
        for v in obj:
            w = szukaj(v, slowa, pomin)
            if w:
                return w
    return ""


def wiersz(det, branza, kod):
    adr = det.get("adresDzialalnosci") or {}
    wl = det.get("wlasciciel") or {}
    email = szukaj(det, ("email", "e-mail", "mail")).lower()
    m = EMAIL_RE.search(email)
    www = szukaj(det, ("www", "strona", "internet"))
    if "biznes.gov.pl" in www:
        www = ""
    return {"id": str(det.get("id") or ""), "nazwa_firmy": det.get("nazwa") or "", "miasto": szukaj(adr, ("miasto", "miejscowosc")),
            "wojewodztwo": szukaj(adr, ("wojewodztwo",)), "branza": branza, "pkd": kod, "email": m.group(0) if m else "",
            "www": www, "telefon": szukaj(det, ("telefon", "tel")), "imie": wl.get("imie") or "",
            "nip": wl.get("nip") or det.get("nip") or ""}


# ---------------- strona www ----------------
_cache_www = {}


def ma_strone(domena):
    """True, gdy domena ma działającą stronę (DNS + HTTP z treścią, nie parking)."""
    domena = re.sub(r"^https?://|/.*$", "", str(domena)).lower().strip()
    if not domena or "." not in domena:
        return False
    if domena in _cache_www:
        return _cache_www[domena]
    wynik = False
    try:
        socket.gethostbyname(domena)
        for schemat in ("https://", "http://"):
            try:
                req = urllib.request.Request(schemat + domena, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    html = r.read(200000).decode("utf-8", "ignore").lower()
                    parking = any(w in html for w in ("domena na sprzedaż", "domain is for sale", "parked", "this domain",
                                                      "home.pl - domena", "strona w budowie", "domena jest utrzymywana"))
                    wynik = len(html) > 1500 and not parking
                    break
            except Exception:
                continue
    except Exception:
        pass
    _cache_www[domena] = wynik
    return wynik


# ---------------- Claude ----------------
def claude_paczka(client, firmy):
    """Haiku ocenia do 20 firm: odrzuca tylko oczywiste sieci/franczyzy; podaje do 3 możliwych domen strony."""
    linie = "\n".join(f'{i}. {f["nazwa_firmy"]} | {f["miasto"]} | {f["branza"]}' for i, f in enumerate(firmy))
    prompt = ("Dla każdej polskiej jednoosobowej firmy z listy zwróć JSON: tablicę obiektów "
              '{"i":nr,"pasuje":true/false,"domeny":[do 3 najbardziej prawdopodobnych domen jej strony www]}. '
              '"pasuje": false TYLKO gdy to oczywista sieć lub franczyza albo nazwa wprost wskazuje zupełnie inną branżę. '
              "Imię i nazwisko albo ogólna nazwa to zawsze true. Tylko JSON, bez komentarzy.\n\n" + linie)
    try:
        r = client.messages.create(model=MODEL, max_tokens=3000, output_config={"effort": "low"},
                                   messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        print(f"Claude: błąd {type(e).__name__}, pomijam paczkę", flush=True)
        return {}
    st["in"] += r.usage.input_tokens; st["out"] += r.usage.output_tokens; st["claude"] += 1
    tekst = "".join(b.text for b in r.content if b.type == "text")
    m = re.search(r"\[.*\]", tekst, re.S)
    try:
        return {o["i"]: o for o in json.loads(m.group(0)) if isinstance(o, dict) and "i" in o} if m else {}
    except Exception:
        return {}


def koszt():
    return st["in"] / 1e6 * IN_USD + st["out"] / 1e6 * OUT_USD


# ---------------- stan ----------------
def wczytaj_zbior(nazwa):
    p = os.path.join(STAN, nazwa)
    return set(open(p, encoding="utf-8").read().split()) if os.path.exists(p) else set()


def zapisz_zbior(nazwa, zbior):
    os.makedirs(STAN, exist_ok=True)
    with open(os.path.join(STAN, nazwa), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(zbior)))


def wczytaj_json(nazwa, domyslnie):
    p = os.path.join(STAN, nazwa)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else domyslnie


# ---------------- tryby ----------------
def sonda():
    print("Adres API:", BASE)
    d = ceidg("/firmy", {"pkd": "4511Z", "status": "AKTYWNY", "limit": 2})
    if d is None:
        sys.exit("SONDA: CEIDG nie odpowiada z GitHuba (timeouty albo strona HTML)")
    print("SONDA lista: klucze", list(d.keys()), "| firm:", len(lista(d)), "| links:", list((d.get("links") or {}).keys()))
    fs = lista(d)
    if fs:
        print("SONDA klucze firmy na liście:", list(fs[0].keys()))
        det = ceidg(fs[0].get("link") or f"/firma/{fs[0].get('id')}")
        det1 = (lista(det) or [det])[0] if det else {}
        print("SONDA klucze szczegółów:", list(det1.keys()))
        w = wiersz(det1, "test", "4511Z")
        print("SONDA pola obecne:", {k: bool(w[k]) for k in ("email", "www", "telefon", "miasto")})
    print("SONDA OK, zapytań:", st["ceidg"], "błędów:", st["bledy_ceidg"])


def dzien():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Brak sekretu ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)
    widziane = wczytaj_zbior("widziane.txt")
    maile = wczytaj_zbior("maile_sha.txt")
    p_id = partia()
    partie = wczytaj_json("partie.json", {})
    juz = partie.get(p_id, 0)
    CEL = max(0, CEL_DZIEN - juz)
    print(f"Partia {p_id}: zebrane wcześniej {juz}, cel tego przebiegu {CEL}", flush=True)
    if CEL == 0:
        print("Cel partii osiągnięty, kończę bez pracy.")
        return
    kursory = wczytaj_json("kursory.json", {})      # kod PKD -> {"url": następna strona | None, "strona": nr, "koniec": bool}
    os.makedirs(WYNIK, exist_ok=True)
    plik = os.path.join(WYNIK, f"leady_{p_id}_{RUN}.csv")
    pola = ["klasa", "nazwa_firmy", "miasto", "wojewodztwo", "branza", "pkd", "email", "telefon", "imie", "nip"]
    licz = {"wpisy": 0, "z_mailem": 0, "ma_strone": 0, "odrzucone_branza": 0, "duplikat_maila": 0, "leady": 0}
    leady, bufor = [], []

    def przetworz(paczka):
        kand = []
        for r in paczka:
            dom = r["email"].split("@")[1]
            if r["www"] or (dom not in FREE and ma_strone(dom)):
                licz["ma_strone"] += 1
            else:
                kand.append(r)
        if not kand:
            return
        oc = claude_paczka(client, kand) if koszt() < LIMIT_USD else {}
        for j, r in enumerate(kand):
            o = oc.get(j, {})
            if o.get("pasuje") is False:
                licz["odrzucone_branza"] += 1; continue
            if any(ma_strone(d) for d in (o.get("domeny") or [])[:3]):
                licz["ma_strone"] += 1; continue
            r["klasa"] = "A" if r["email"].split("@")[1] in FREE and r["telefon"] else "B"
            leady.append(r); maile.add(skrot(r["email"])); licz["leady"] += 1

    aktywne = [p for p in PKD if not kursory.get(p[1], {}).get("koniec")]
    if not aktywne:                                  # wszystko przejrzane: zaczynamy od początku (nowe firmy, zmiany zgód)
        kursory, aktywne = {}, list(PKD)
    stop = ""
    while aktywne and not stop:
        for branza, kod in list(aktywne):
            if len(leady) >= CEL: stop = "cel"; break
            if minuty() > MAKS_MINUT: stop = "czas"; break
            if koszt() > LIMIT_USD: stop = "koszt"; break
            k = kursory.setdefault(kod, {"url": None, "strona": 1, "koniec": False})
            d = ceidg(k["url"]) if k["url"] else ceidg("/firmy", {"pkd": kod, "status": "AKTYWNY", "limit": NA_STRONE,
                                                                  "page": k["strona"]})
            if d is None:
                continue                              # CEIDG nie odpowiedział, spróbujemy w następnym okrążeniu
            firmy = lista(d)
            nxt = nastepna(d)
            k["strona"] += 1
            k["url"] = nxt
            if not firmy or (nxt is None and len(firmy) < NA_STRONE):
                k["koniec"] = True
                if (branza, kod) in aktywne: aktywne.remove((branza, kod))
            nowe = [f for f in firmy if str(f.get("id") or "") and str(f.get("id")) not in widziane]
            k["puste"] = 0 if nowe else k.get("puste", 0) + 1
            if k["puste"] >= 3 and (branza, kod) in aktywne:   # 3 strony bez nowych firm: koniec albo stronicowanie nie działa
                k["koniec"] = True; aktywne.remove((branza, kod))
            for f in nowe:
                fid = str(f.get("id"))
                if minuty() > MAKS_MINUT: stop = "czas"; break
                if len(leady) >= CEL: stop = "cel"; break
                det = ceidg(f.get("link") or f"/firma/{fid}")
                if det is None:
                    continue
                widziane.add(fid)
                det1 = (lista(det) or [det])[0] if det else {}
                r = wiersz(det1, branza, kod)
                licz["wpisy"] += 1
                if not r["email"]:
                    continue
                licz["z_mailem"] += 1
                if skrot(r["email"]) in maile:
                    licz["duplikat_maila"] += 1; continue
                bufor.append(r)
                if len(bufor) >= 20:
                    przetworz(bufor); bufor = []
            print(f"{kod}: wpisów {licz['wpisy']}, z mailem {licz['z_mailem']}, leadów {licz['leady']}, "
                  f"{minuty():.0f} min", flush=True)
            if stop:
                break
            # zapis stanu po każdej stronie, żeby przerwany przebieg nie tracił pracy
            zapisz_zbior("widziane.txt", widziane); zapisz_zbior("maile_sha.txt", maile)
            json.dump(kursory, open(os.path.join(STAN, "kursory.json"), "w"), indent=1)
    if bufor:
        przetworz(bufor)
    zapisz_zbior("widziane.txt", widziane); zapisz_zbior("maile_sha.txt", maile)
    json.dump(kursory, open(os.path.join(STAN, "kursory.json"), "w"), indent=1)
    partie[p_id] = juz + len(leady)
    partie = dict(sorted(partie.items())[-30:])
    json.dump(partie, open(os.path.join(STAN, "partie.json"), "w"), indent=1)

    with open(plik, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pola, extrasaction="ignore")
        w.writeheader(); w.writerows(sorted(leady, key=lambda x: x["klasa"]))
    c = koszt()
    s = dict(licz, partia=p_id, w_partii_razem=partie[p_id], data=DATA, powod_konca=stop or "przejrzane wszystkie PKD", minut=round(minuty()),
             zapytania_ceidg=st["ceidg"], bledy_ceidg=st["bledy_ceidg"], zapytania_claude=st["claude"],
             koszt_claude_usd=round(c, 5), usd_na_lead=round(c / max(1, licz["leady"]), 6),
             klasa_A=sum(1 for r in leady if r["klasa"] == "A"), widziane_razem=len(widziane), plik=os.path.basename(plik))
    json.dump(s, open(os.path.join(WYNIK, f"podsumowanie_{p_id}_{RUN}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Cold leady, partia " + p_id + "\n\n| | |\n|---|---|\n" + "".join(f"| {k} | {v} |\n" for k, v in s.items()))


if __name__ == "__main__":
    tryb = sys.argv[1] if len(sys.argv) > 1 else "sonda"
    sonda() if tryb == "sonda" else dzien()
