#!/usr/bin/env python3
"""Tests für platzbelegung.py (docs/platzbelegung.json). Kein Netzwerk; geschrieben wird nur unter tempfile, nie im Repo.

Aufruf (aus dem Repo-Stamm):  python3 test_platzbelegung.py          Exit 0 = alle Prüfungen bestanden, Zahlen am Ende
                              python3 -I -m unittest discover -s . -p 'test_platzbelegung.py'   (oder ohne -I:
                              python3 -m unittest test_platzbelegung)

Gelesen werden die echte spiele.csv und die Tabellen aus build_ics.py. Die Prüfungen folgen der Schnittstelle: Aufbau
und Schlüsselfolge, Verweise, Zahlen, Datenschutz, Beispieldatei, Format und Wiederholbarkeit, dazu zwei Läufe von
build_ics.py in Wegwerf-Ordnern (mit dem Modul und ohne, wie der Probelauf von tools/nachtrag.py). Abgesagte Spiele
(Spalte ergebnis = abgesagt) fehlen in der Datei; jedes Spiel auf unserem Platz hat feld (halb oder ganz), andere null.

Die Beispieldatei der Schnittstelle (aus dem Prototyp, Stand 30.10.2026) liegt nicht im Repo, sondern im Website-Repo
(tools/cache/orchestrierung-platzbelegung/schnittstelle/). Der Vergleich der statischen Teile läuft nur, wenn sie
gefunden wird (Pfad per Umgebungsvariable PLATZ_BEISPIELDATEI änderbar) und ihr Plan-Stand dem TRAINING_STAND des
Generators entspricht; sonst steht in der Ausgabe, dass er übersprungen wurde (der Plan hat sich dann rechtmäßig geändert).
Die Beispieldatei kennt weder feld noch lässt sie abgesagte Spiele aus: Die Spiele werden ohne feld verglichen, abgesagte
Spiele, die dort noch stehen, erscheinen in der Ausgabe als erwartete Abweichung (kein Fehler).
"""
import codecs
import collections
import contextlib
import csv
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)
import build_ics as B  # noqa: E402
import platzbelegung as P  # noqa: E402

HEUTE = datetime.date(2026, 10, 31)
CSV = os.path.join(HIER, "spiele.csv")
STAND_BEISPIEL = "19.10.2026"        # Plan-Stand der Beispieldatei; nur dann gelten die Zahlen und der Vergleich unten
BEISPIEL = os.environ.get("PLATZ_BEISPIELDATEI") or os.path.join(
    HIER, "..", "speuzer-website-prototyp", "tools", "cache", "orchestrierung-platzbelegung", "schnittstelle",
    "platzbelegung.fixture.json")

# Namen, die nie in der Datei stehen dürfen (Personen und Firmen, siehe Prüfregeln der Schnittstelle). Sie stehen hier nur
# als ROT13, damit sie nicht im Klartext im öffentlichen Repo stehen; die Ausgabe nennt nur ihre laufende Nummer.
VERBOTEN_NAMEN = [codecs.decode(n, "rot13") for n in
                  ("Encunryr", "Inffvyv", "QJF", "Oreraoret", "Ertvfgn", "Ömxna", "Frvcc")]
DATUM_ZEIT = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}:\d{2}")
ZIFFERNFOLGE = re.compile(r"\d(?:[\s\-./()+]*\d){6}")            # mindestens 7 Ziffern, auch mit Trennzeichen
ZELLBEREICH = re.compile(r"\b[A-Z]{1,3}\d{1,4}:[A-Z]{1,3}\d{1,4}\b")   # Excel-Zellbereich wie J5:J8

# Schnittstelle: Schlüssel und Reihenfolge
S_OBEN = ["version", "saison", "erzeugt", "stand", "platz", "teile", "belegung", "mannschaften", "regeln", "training",
          "halle", "belegt", "spiele"]
S_STAND = ["plan", "spiele"]
S_PLATZ = ["kurz", "adresse"]
S_TEIL = ["id", "name"]
S_MANNSCHAFT = ["id", "kurz", "name", "csv"]
S_REGELN = ["trainingVon", "trainingBis", "frei", "fenster", "minFrei", "dauer", "unserPlatz", "navigation"]
S_FREI = ["von", "bis", "name"]
S_FENSTER = ["werktag", "wochenende"]
S_DAUER = ["D", "E", "F", "G", "A", "Herren"]
S_TRAINING = ["t", "tag", "von", "bis", "platz", "teile"]
S_HALLE = ["t", "tag", "von", "bis", "ort", "ab", "bisDatum"]
S_BELEGT = ["tag", "von", "bis", "teile"]
S_SPIEL = ["d", "z", "t", "w", "g", "nr", "dauer", "ort", "feld"]
TEIL_IDS = ["tor1", "tor2", "tor3", "tor4", "kaefig", "court"]
BELEGUNG_SOLL = {
    "ganzer Platz": ["tor1", "tor2", "tor3", "tor4"], "linke Hälfte": ["tor1", "tor2"], "rechte Hälfte": ["tor3", "tor4"],
    "Tor 1": ["tor1"], "Tor 2": ["tor2"], "Tor 3": ["tor3"], "Tor 4": ["tor4"], "Käfig": ["kaefig"], "Court": ["court"],
    "Court und Käfig": ["kaefig", "court"],
}
ID_JE_KUERZEL = {"HERREN": "herren", "A": "a-jugend", "D1": "d1", "D2": "d2", "D3": "d3", "E1": "e1", "E2": "e2",
                 "E3": "e3", "F1": "f1", "F2": "f2", "G1": "g-jugend"}
DAUER_JE_KLASSE = {"HERREN": 120, "A": 120, "D": 105, "E": 90, "F": 75, "G": 75}       # Kinderfestival: 75
# Feldbedarf auf unserem Platz je Mannschaft (Kürzel in spiele.csv), bewusst einzeln und unabhängig von FELD in
# platzbelegung.py hingeschrieben: E braucht das halbe Feld, ab D das ganze; F und G wie E (Annahme).
FELD_JE_TEAM = {"HERREN": "ganz", "A": "ganz", "D1": "ganz", "D2": "ganz", "D3": "ganz", "E1": "halb", "E2": "halb",
                "E3": "halb", "F1": "halb", "F2": "halb", "G1": "halb"}
UHRZEIT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Pruefer:
    def __init__(self):
        self.anzahl = 0
        self.fehler = []
        self._start = None                  # (Prüfungen, Fehler) zu Beginn des laufenden Abschnitts

    def pruefe(self, bedingung, text):
        self.anzahl += 1
        if not bedingung:
            self.fehler.append(text)
            print("  FEHLER: " + text)
        return bool(bedingung)

    def abschnitt(self, titel):
        self.ende()
        print("-- " + titel)
        self._start = (self.anzahl, len(self.fehler))

    def ende(self):
        """Schließt den laufenden Abschnitt mit seinen Zahlen ab."""
        if self._start is not None:
            n, f = self.anzahl - self._start[0], len(self.fehler) - self._start[1]
            print("   %d Prüfungen%s" % (n, ", davon %d fehlgeschlagen" % f if f else ""))
            self._start = None


def lies_csv():
    with open(CSV, encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f, delimiter=";") if r.get("spielnr")]


def lies_bytes(pfad):
    """Inhalt der Datei oder None, wenn es sie nicht gibt."""
    if not os.path.exists(pfad):
        return None
    with open(pfad, "rb") as f:
        return f.read()


def zeichenketten(x):
    """Alle Schlüssel und alle Texte einer geladenen JSON-Struktur."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from zeichenketten(v)
    elif isinstance(x, list):
        for v in x:
            yield from zeichenketten(v)
    elif isinstance(x, str):
        yield x


def ziffernfolge(s):
    return ZIFFERNFOLGE.search(DATUM_ZEIT.sub(" ", s)) is not None


def treffer(s):
    """Welche verbotenen Muster in der Zeichenkette stehen (leer = sauber)."""
    klein = s.lower()
    t = []
    if "@" in s:
        t.append("@")
    if "http" in klein:
        t.append("http")
    if "tel:" in klein:
        t.append("tel:")
    if ziffernfolge(s):
        t.append("7 Ziffern")
    if ZELLBEREICH.search(s):
        t.append("Zellbereich")
    t += ["Name %d" % (i + 1) for i, n in enumerate(VERBOTEN_NAMEN) if n.lower() in klein]
    return t


def abgesagt(r):
    """Vermerk „abgesagt“ in der Spalte ergebnis einer CSV-Zeile (getrimmt, Groß-/Kleinschreibung egal), bewusst
    unabhängig von platzbelegung.py geschrieben."""
    return (r.get("ergebnis") or "").strip().lower() == "abgesagt"


def erwartetes_spiel(r):
    """Ein Heimspiel aus der CSV nach den Regeln der Schnittstelle, bewusst noch einmal einfach und unabhängig von
    platzbelegung.py geschrieben (zweite Meinung). Nur die Bereinigung des Gegnernamens kommt aus build_ics.py: dessen
    bereinige_gegner() ist die Referenz, nach der auch die App-Spielplanseiten die Namen anzeigen."""
    tag, monat, jahr = (int(x) for x in r["datum"].strip().split("."))
    team = r["team"].strip()
    wettbewerb = r["wettbewerb"].strip() or "Meisterschaft"
    staette = r["spielstaette"].strip()
    bei_uns = staette.startswith("Mainzer Landstr. 480")
    if bei_uns:
        dauer = 75 if wettbewerb == "Kinderfestival" else DAUER_JE_KLASSE[team if team == "HERREN" else team[0]]
        ort = "u"
        feld = FELD_JE_TEAM[team]
    else:
        dauer = None
        ort = re.sub(r"\s*/\s*", " / ", staette.split(",")[0]).strip() or "Ort offen"
        feld = None
    return (datetime.date(jahr, monat, tag).isoformat(), r["zeit"].strip(), ID_JE_KUERZEL[team], wettbewerb,
            B.bereinige_gegner(r["gast"].strip()), r["spielnr"].strip(), dauer, ort, feld)


def als_tupel(s):
    return tuple(s[k] for k in S_SPIEL)


def klasse_von(team):
    """Klasse aus dem Kürzel einer Mannschaft in spiele.csv (D1 -> D, E2 -> E, G1 -> G, A -> A, HERREN -> HERREN)."""
    return team if team == "HERREN" else team[0]


def montag_von(iso):
    d = datetime.date.fromisoformat(iso)
    return d - datetime.timedelta(days=d.weekday())


def lauf(ordner, mit_isolation, modul):
    """build_ics.py in einem Wegwerf-Ordner starten; mit_isolation=True wie in tools/nachtrag.py (python -I -B)."""
    for n in ("build_ics.py", "vorlage_index.html", "spiele.csv") + (("platzbelegung.py",) if modul else ()):
        shutil.copy(os.path.join(HIER, n), ordner)
    befehl = [sys.executable] + (["-I", "-B"] if mit_isolation else ["-B"]) + ["build_ics.py"]
    return subprocess.run(befehl, cwd=ordner, env=dict(os.environ, SEQ="1"), capture_output=True, text=True, timeout=120)


def pruefungen():
    p = Pruefer()
    zeilen = list(B.lade_spiele())
    daten = P.baue(zeilen, HEUTE)
    roh = lies_csv()
    alle_heim = [r for r in roh if r["heim"].strip().startswith("FFV Sportfreunde 04")]
    abgesagte = [r for r in alle_heim if abgesagt(r)]            # fehlen in der Datei
    heim = [r for r in alle_heim if not abgesagt(r)]
    zahlen = {}

    # ------------------------------------------------------------------------------------------------ Aufbau
    p.abschnitt("Aufbau und Schlüsselfolge")
    p.pruefe(list(daten) == S_OBEN, "oberste Schlüssel und Reihenfolge: %s" % list(daten))
    p.pruefe(daten["version"] == 1 and daten["saison"] == "2026/27", "version 1, saison 2026/27")
    p.pruefe(daten["erzeugt"] == "2026-10-31", "erzeugt = Tag des Laufs")
    p.pruefe(list(daten["stand"]) == S_STAND and daten["stand"]["plan"] == B.TRAINING_STAND
             and daten["stand"]["spiele"] == "31.10.2026", "stand: plan = TRAINING_STAND, spiele = Tag des Laufs")
    p.pruefe(re.fullmatch(r"\d\d\.\d\d\.\d{4}", daten["stand"]["plan"]) is not None, "stand.plan hat die Form TT.MM.JJJJ")
    p.pruefe(list(daten["platz"]) == S_PLATZ and daten["platz"]["kurz"] == "Mainzer Landstr. 480"
             and daten["platz"]["adresse"] == "Mainzer Landstraße 480, 60326 Frankfurt am Main"
             and B.SPORTPLATZ.endswith(daten["platz"]["adresse"]), "platz: kurz und adresse (adresse = SPORTPLATZ ohne Vereinsname)")
    p.pruefe([t["id"] for t in daten["teile"]] == TEIL_IDS and all(list(t) == S_TEIL for t in daten["teile"])
             and [t["name"] for t in daten["teile"]] == ["Tor 1", "Tor 2", "Tor 3", "Tor 4", "Käfig", "Court"],
             "teile: Ids, Namen und Reihenfolge")
    p.pruefe(json.dumps(daten["belegung"], ensure_ascii=False) == json.dumps(BELEGUNG_SOLL, ensure_ascii=False),
             "belegung: zehn Platzangaben, Zuordnung und Reihenfolge wie in der Schnittstelle")
    ids = [m["id"] for m in daten["mannschaften"]]
    p.pruefe(ids == ["herren", "a-jugend", "d1", "d2", "d3", "e1", "e2", "e3", "f1", "f2", "g-jugend"]
             and all(list(m) == S_MANNSCHAFT for m in daten["mannschaften"]), "mannschaften: elf, Ids, Schlüsselfolge")
    p.pruefe([m["csv"] for m in daten["mannschaften"]] == list(ID_JE_KUERZEL), "mannschaften: Kürzel in spiele.csv")
    p.pruefe({m["csv"] for m in daten["mannschaften"]} == set(B.TEAMS) | set(B.TRAINING) | set(B.HALLE)
             and set(B.TEAMS) == set(B.TRAINING), "jede Mannschaft des Generators steht in mannschaften und umgekehrt")
    r = daten["regeln"]
    p.pruefe(list(r) == S_REGELN, "regeln: Schlüsselfolge")
    p.pruefe(r["trainingVon"] == B.TRAINING_VON.isoformat() and r["trainingBis"] == B.TRAINING_BIS.isoformat(),
             "regeln: trainingVon und trainingBis aus dem Generator")
    p.pruefe(r["frei"] == [{"von": v.isoformat(), "bis": b.isoformat(), "name": n} for v, b, n in B.FREI]
             and all(list(f) == S_FREI for f in r["frei"]) and len(r["frei"]) == 6, "regeln: sechs frei-Zeiträume wie FREI")
    p.pruefe(list(r["fenster"]) == S_FENSTER and r["fenster"] == {"werktag": ["15:30", "21:30"],
                                                                  "wochenende": ["09:00", "18:00"]}, "regeln: fenster")
    p.pruefe(r["minFrei"] == 60 and r["unserPlatz"] == "Mainzer Landstr. 480", "regeln: minFrei, unserPlatz")
    p.pruefe(list(r["dauer"]) == S_DAUER and r["dauer"] == {"D": 105, "E": 90, "F": 75, "G": 75, "A": 120, "Herren": 120},
             "regeln: dauer je Klasse")
    alle_tage = [s["d"] for s in daten["spiele"]] + [r["trainingVon"], r["trainingBis"]]
    nav = r["navigation"]
    p.pruefe(len(nav) == 2 and all(montag_von(x) == datetime.date.fromisoformat(x) for x in nav),
             "regeln.navigation: zwei Montage %s" % nav)
    p.pruefe(nav == [montag_von(min(alle_tage)).isoformat(), montag_von(max(alle_tage)).isoformat()],
             "regeln.navigation: Montag der ersten und der letzten Woche mit Terminen")
    for name, liste, soll in (("training", daten["training"], S_TRAINING), ("halle", daten["halle"], S_HALLE),
                              ("belegt", daten["belegt"], S_BELEGT), ("spiele", daten["spiele"], S_SPIEL)):
        p.pruefe(all(list(e) == soll for e in liste), "%s: Schlüsselfolge jedes Eintrags" % name)
    for name, liste in (("training", daten["training"]), ("halle", daten["halle"]), ("belegt", daten["belegt"])):
        p.pruefe(all(isinstance(e["tag"], int) and 0 <= e["tag"] <= 4 for e in liste), "%s: tag 0 bis 4" % name)
        p.pruefe(all(UHRZEIT.match(e["von"]) and UHRZEIT.match(e["bis"]) and e["von"] < e["bis"] for e in liste),
                 "%s: Uhrzeiten HH:MM, von vor bis" % name)
    p.pruefe(all(re.fullmatch(r"\d{4}-\d\d-\d\d", s["d"]) and UHRZEIT.match(s["z"]) for s in daten["spiele"]),
             "spiele: Datum JJJJ-MM-TT und Uhrzeit HH:MM")

    # ------------------------------------------------------------------------------------------------ Verweise
    p.abschnitt("Verweise")
    mid = {m["id"] for m in daten["mannschaften"]}
    teil_ids = {t["id"] for t in daten["teile"]}
    p.pruefe(all(e["t"] in mid for e in daten["training"] + daten["halle"] + daten["spiele"]),
             "alle t verweisen auf mannschaften")
    p.pruefe(all(set(e["teile"]) <= teil_ids for e in daten["training"] + daten["belegt"])
             and all(set(v) <= teil_ids for v in daten["belegung"].values()), "alle teile verweisen auf teile[].id")
    p.pruefe(all(e["platz"] in daten["belegung"] and e["teile"] == daten["belegung"][e["platz"]]
                 for e in daten["training"]), "training[].teile = belegung[platz]")
    p.pruefe(all(h["ab"] == B.HALLE[next(m["csv"] for m in daten["mannschaften"] if m["id"] == h["t"])]["von"].isoformat()
                 and h["bisDatum"] == B.HALLE[next(m["csv"] for m in daten["mannschaften"] if m["id"] == h["t"])]["bis"].isoformat()
                 and h["ab"] <= h["bisDatum"] for h in daten["halle"]), "halle: Zeitraum wie HALLE")
    p.pruefe("herren" not in {e["t"] for e in daten["training"] + daten["halle"]},
             "Herren trainieren auswärts: weder training noch halle")
    p.pruefe({e["t"] for e in daten["training"]} == {ID_JE_KUERZEL[k] for k in B.TRAINING if k not in B.ORT_EXTERN},
             "training: jede Mannschaft mit Platzzeiten, ohne ORT_EXTERN")

    # ------------------------------------------------------------------------------------------------ Zahlen
    p.abschnitt("Zahlen")
    erwartet_training = sum(len(v) for k, v in B.TRAINING.items() if k not in B.ORT_EXTERN)
    erwartet_halle = sum(len(h["zeiten"]) for h in B.HALLE.values())
    p.pruefe(len(daten["training"]) == erwartet_training, "training: %d Einträge wie TRAINING" % erwartet_training)
    p.pruefe(len(daten["halle"]) == erwartet_halle, "halle: %d Einträge wie HALLE" % erwartet_halle)
    p.pruefe(len(daten["belegt"]) == len(P.BELEGT), "belegt: %d Einträge wie BELEGT" % len(P.BELEGT))
    if daten["stand"]["plan"] == STAND_BEISPIEL:
        p.pruefe((len(daten["training"]), len(daten["halle"]), len(daten["belegt"])) == (23, 3, 8),
                 "Plan-Stand %s: 23 training, 3 halle, 8 belegt" % STAND_BEISPIEL)
    else:
        print("   (Plan-Stand %s statt %s: feste Zahlen 23/3/8 nicht geprüft)" % (daten["stand"]["plan"], STAND_BEISPIEL))
    p.pruefe(len(daten["spiele"]) == len(heim),
             "spiele = Zeilen mit heim FFV Sportfreunde 04… ohne abgesagte: %d (%d abgesagte Heimspiele fehlen)"
             % (len(heim), len(abgesagte)))
    p.pruefe(not {erwartetes_spiel(r) for r in abgesagte} & {als_tupel(s) for s in daten["spiele"]},
             "kein abgesagtes Heimspiel (ergebnis = abgesagt) steht in spiele: alle %d Zeilen der CSV fehlen" % len(abgesagte))
    bei_uns = [r for r in heim if r["spielstaette"].strip().startswith("Mainzer Landstr. 480")]
    p.pruefe(sum(1 for s in daten["spiele"] if s["ort"] == "u") == len(bei_uns),
             "ort u genau bei Spielstätte Mainzer Landstr. 480…: %d" % len(bei_uns))
    p.pruefe(not [r for r in heim if "Mainzer Landstr" in r["spielstaette"]
                  and not r["spielstaette"].strip().startswith("Mainzer Landstr. 480")],
             "kein Heimspiel mit abweichender Schreibweise unseres Platzes")
    klasse = {m["id"]: ("HERREN" if m["csv"] == "HERREN" else m["csv"][0]) for m in daten["mannschaften"]}
    p.pruefe(all((type(s["dauer"]) is int and s["dauer"] == (75 if s["w"] == "Kinderfestival" else DAUER_JE_KLASSE[klasse[s["t"]]]))
                 if s["ort"] == "u" else s["dauer"] is None for s in daten["spiele"]),
             "dauer: ganzzahlig nach Klasse bei ort u, sonst null")
    kuerzel_je_id = {ident: kuerzel for kuerzel, ident in ID_JE_KUERZEL.items()}
    p.pruefe(set(FELD_JE_TEAM) == set(ID_JE_KUERZEL) and {s["feld"] for s in daten["spiele"]} <= {"ganz", "halb", None}
             and all(s["feld"] == (FELD_JE_TEAM[kuerzel_je_id[s["t"]]] if s["ort"] == "u" else None) for s in daten["spiele"]),
             "feld: bei ort u halb (E, F, G) oder ganz (D, A, Herren) je Mannschaft, bei anderem Spielort null")
    je_klasse = collections.Counter(klasse[s["t"]] for s in daten["spiele"] if s["ort"] == "u")
    je_feld = collections.Counter(s["feld"] for s in daten["spiele"] if s["ort"] == "u")
    p.pruefe(je_klasse == collections.Counter(klasse_von(r["team"].strip()) for r in bei_uns),
             "Spiele auf unserem Platz je Klasse wie in spiele.csv: %s" % dict(sorted(je_klasse.items())))
    p.pruefe(je_feld == collections.Counter(FELD_JE_TEAM[r["team"].strip()] for r in bei_uns),
             "Spiele auf unserem Platz je feld wie in spiele.csv (je Klasse gezählt): %s" % dict(sorted(je_feld.items())))
    p.pruefe(collections.Counter(als_tupel(s) for s in daten["spiele"]) == collections.Counter(erwartetes_spiel(r) for r in heim),
             "jedes Heimspiel stimmt mit der unabhängigen Rechnung aus spiele.csv überein (d, z, t, w, g, nr, dauer, ort, feld)")
    p.pruefe(collections.Counter(s["g"] for s in daten["spiele"])
             == collections.Counter(B.bereinige_gegner(r["gast"].strip()) for r in heim),
             "Gegnernamen wie auf den App-Spielplanseiten: g = bereinige_gegner(gast der CSV-Zeile) bei jedem Heimspiel")
    geaendert = [r["gast"].strip() for r in heim if B.bereinige_gegner(r["gast"].strip()) != r["gast"].strip()]
    print("   Gegnernamen: bei %d von %d Heimspielen (%d verschiedene Namen) ändert bereinige_gegner() den Namen aus der CSV"
          % (len(geaendert), len(heim), len(set(geaendert))))
    p.pruefe([(s["d"], s["z"], s["t"]) for s in daten["spiele"]] == sorted((s["d"], s["z"], s["t"]) for s in daten["spiele"]),
             "spiele nach Tag, Uhrzeit, Mannschaft sortiert")
    zahlen.update(training=len(daten["training"]), halle=len(daten["halle"]), belegt=len(daten["belegt"]),
                  heim=len(daten["spiele"]), bei_uns=sum(1 for s in daten["spiele"] if s["ort"] == "u"),
                  abgesagt=len(abgesagte), je_klasse=dict(sorted(je_klasse.items())), je_feld=dict(sorted(je_feld.items())),
                  woanders=sum(1 for s in daten["spiele"] if s["ort"] != "u"))

    # ------------------------------------------------------------------------------------------------ Datenschutz
    p.abschnitt("Datenschutz")
    p.pruefe(treffer("069 / 1234567") and treffer("a@b.de") and treffer("https://x.de") and treffer("tel:0171")
             and treffer("J5:J8") and treffer(VERBOTEN_NAMEN[0].upper()) and not treffer("Am Römerhof 9")
             and not treffer("Mainzer Landstraße 480, 60326 Frankfurt am Main") and not treffer("2026-10-31 17:30 31.10.2026"),
             "Gegenprobe: die Muster erkennen Telefonnummer, E-Mail, Adresse, Zellbereich und Namen, aber kein Datum")
    je_muster = collections.Counter()
    for s in zeichenketten(daten):
        for t in treffer(s):
            je_muster[t] += 1
    p.pruefe(not je_muster, "keine Zeichenkette (Schlüssel und Werte) enthält @, http, tel:, 7 Ziffern, Zellbereich oder "
                            "einen der %d Namen: %s" % (len(VERBOTEN_NAMEN), dict(je_muster) or "keine Treffer"))
    p.pruefe(all(list(e) == S_BELEGT for e in daten["belegt"]), "belegt: nur tag, von, bis, teile (keine Angabe, wer)")
    with open(os.path.join(HIER, "platzbelegung.py"), encoding="utf-8") as f:
        quelltext = f.read().lower()
    p.pruefe(not [i for i, n in enumerate(VERBOTEN_NAMEN) if n.lower() in quelltext] and "@" not in quelltext,
             "platzbelegung.py (öffentlich, auch die Kommentare der Tabelle BELEGT) enthält keinen der Namen und kein @")

    # ------------------------------------------------------------------------------------------------ Beispieldatei
    p.abschnitt("Beispieldatei der Schnittstelle")
    beispiel = None
    if os.path.exists(BEISPIEL):
        with open(BEISPIEL, encoding="utf-8") as f:
            beispiel = json.load(f)
    if beispiel is None:
        print("   übersprungen: Beispieldatei nicht gefunden (%s)" % os.path.normpath(BEISPIEL))
    elif beispiel["stand"]["plan"] != daten["stand"]["plan"]:
        print("   übersprungen: Plan-Stand der Beispieldatei %s, des Generators %s (der Plan wurde seither geändert)"
              % (beispiel["stand"]["plan"], daten["stand"]["plan"]))
    else:
        def j(x):
            return json.dumps(x, ensure_ascii=False)
        p.pruefe(list(beispiel) == list(daten), "oberste Schlüsselfolge wie die Beispieldatei")
        for k in ("version", "saison", "platz", "teile", "belegung", "mannschaften", "training", "halle", "belegt"):
            p.pruefe(j(daten[k]) == j(beispiel[k]), "%s gleich der Beispieldatei (Werte und Reihenfolge)" % k)
        p.pruefe(j({k: v for k, v in daten["regeln"].items() if k != "navigation"})
                 == j({k: v for k, v in beispiel["regeln"].items() if k != "navigation"}),
                 "regeln außer navigation gleich der Beispieldatei")
        # Die Beispieldatei enthält die Gegnernamen roh aus DFBnet; für den Vergleich der Spiele werden sie wie in
        # platzbelegung.py mit bereinige_gegner() angeglichen, damit nur echte Änderungen an spiele.csv übrig bleiben.
        # Sie kennt außerdem kein feld und lässt abgesagte Spiele nicht aus: Die Spiele von jetzt werden ohne feld
        # verglichen, und was nur in der Beispieldatei steht, wird gekennzeichnet, wenn spiele.csv es als abgesagt führt.
        roh_in_beispiel = sum(1 for s in beispiel["spiele"] if B.bereinige_gegner(s["g"]) != s["g"])
        neu = collections.Counter(j({k: v for k, v in s.items() if k != "feld"}) for s in daten["spiele"])
        alt = collections.Counter(j(dict(s, g=B.bereinige_gegner(s["g"]))) for s in beispiel["spiele"])
        abgesagt_in_csv = {(e[0], e[1], e[2], e[5]) for e in map(erwartetes_spiel, abgesagte)}     # d, z, t, nr

        def in_csv_abgesagt(zeichenkette):
            s = json.loads(zeichenkette)
            return (s["d"], s["z"], s["t"], s["nr"]) in abgesagt_in_csv
        nur_jetzt, nur_beispiel = neu - alt, alt - neu
        nur_beispiel_abgesagt = sum(n for z, n in nur_beispiel.items() if in_csv_abgesagt(z))
        print("   Die Beispieldatei kennt kein feld und lässt abgesagte Spiele nicht aus: verglichen werden die Spiele ohne "
              "feld; abgesagte Spiele der CSV, die dort noch stehen, sind unten gekennzeichnet (erwartet, kein Fehler)")
        print("   Unterschiede zur Beispieldatei, erwartet (jetzt / Beispieldatei): stand.spiele %s / %s%s; navigation %s / %s "
              "(dort der früheste und späteste Termin, hier der Montag dieser Wochen)" % (
                  daten["stand"]["spiele"], beispiel["stand"]["spiele"],
                  "" if daten["erzeugt"] == beispiel["erzeugt"] else "; erzeugt %s / %s" % (daten["erzeugt"], beispiel["erzeugt"]),
                  daten["regeln"]["navigation"], beispiel["regeln"]["navigation"]))
        print("   spiele: %d in der Beispieldatei, %d jetzt; nur jetzt %d, nur in der Beispieldatei %d, davon in spiele.csv "
              "abgesagt %d (Gegnernamen der Beispieldatei vorher mit bereinige_gegner() angeglichen; dort noch roh: %d "
              "Einträge)" % (len(beispiel["spiele"]), len(daten["spiele"]), sum(nur_jetzt.values()),
                             sum(nur_beispiel.values()), nur_beispiel_abgesagt, roh_in_beispiel))
        for z in sorted(nur_jetzt):
            s = json.loads(z)
            print("     + %s %s %s Nr. %s (%s)" % (s["d"], s["z"], s["t"], s["nr"], s["w"]))
        for z in sorted(nur_beispiel):
            s = json.loads(z)
            print("     - %s %s %s Nr. %s (%s)%s" % (s["d"], s["z"], s["t"], s["nr"], s["w"],
                                                   " [in spiele.csv abgesagt]" if in_csv_abgesagt(z) else ""))
        zahlen["spiele_beispiel"] = len(beispiel["spiele"])
        zahlen["spiele_nur_jetzt"] = sum(nur_jetzt.values())
        zahlen["spiele_nur_beispiel"] = sum(nur_beispiel.values())
        zahlen["spiele_nur_beispiel_abgesagt"] = nur_beispiel_abgesagt

    # ------------------------------------------------------------------------------------------------ Format, Wiederholung
    p.abschnitt("Format und Wiederholbarkeit")
    with tempfile.TemporaryDirectory(prefix="platzbelegung-test-") as tmp:
        dateien = []
        for i in (1, 2):
            pfad = os.path.join(tmp, "lauf%d.json" % i)
            with contextlib.redirect_stdout(io.StringIO()):
                P.schreibe(pfad, P.baue(list(B.lade_spiele()), HEUTE))
            dateien.append(lies_bytes(pfad))
    text = dateien[0].decode("utf-8")
    p.pruefe(dateien[0] == dateien[1], "zwei Läufe am selben Tag liefern dieselbe Datei (Byte für Byte)")
    p.pruefe(not dateien[0].startswith(b"\xef\xbb\xbf") and b"\r" not in dateien[0] and dateien[0].endswith(b"\n")
             and not dateien[0].endswith(b"\n\n"), "UTF-8 ohne BOM, Zeilenende \\n, genau ein Zeilenumbruch am Ende")
    p.pruefe(text == json.dumps(daten, ensure_ascii=False, indent=1) + "\n" and "\\u" not in text and "Käfig" in text,
             "Format: ensure_ascii=False, indent=1")
    p.pruefe(text.count('"feld": null') == zahlen["woanders"] and text.count('"feld": "halb"') == je_feld["halb"]
             and text.count('"feld": "ganz"') == je_feld["ganz"],
             "feld steht in der Datei als null (%d), \"halb\" (%d) und \"ganz\" (%d)"
             % (zahlen["woanders"], je_feld["halb"], je_feld["ganz"]))
    p.pruefe(json.loads(text) == daten, "die Datei enthält genau die Daten von baue()")
    p.pruefe(P.baue(list(B.lade_spiele()), HEUTE, B) == daten, "baue() mit übergebenem Generator-Modul = Standard")
    p.pruefe(P.baue(list(B.lade_spiele()), datetime.datetime(2026, 10, 31, 23, 59)) == daten, "heute als datetime gilt als Tag")

    # ------------------------------------------------------------------------------------------------ Fehlerfälle
    p.abschnitt("Fehlerfälle (laut abbrechen statt Lücke in der öffentlichen Datei)")
    fremd = dict(zeilen[0], team="X9", heim="FFV Sportfreunde 04 I", spielnr="999")
    try:
        P.baue(zeilen + [fremd], HEUTE)
        p.pruefe(False, "unbekannte Mannschaft in spiele.csv bricht ab")
    except ValueError as e:
        p.pruefe("X9" in str(e) and "MANNSCHAFTEN" in str(e), "unbekannte Mannschaft in spiele.csv: ValueError mit Hinweis")
    quelle = types.SimpleNamespace(**{k: getattr(B, k) for k in (
        "TRAINING", "HALLE", "FREI", "TRAINING_VON", "TRAINING_BIS", "TRAINING_STAND", "ORT_EXTERN", "WOCHENTAG", "FESTIVAL",
        "VEREIN", "SPORTPLATZ", "bereinige_gegner")})
    quelle.TRAINING = dict(B.TRAINING, D1=[("DI", "17:30", "19:30", "Tor 9")])
    try:
        P.baue(zeilen, HEUTE, quelle)
        p.pruefe(False, "unbekannte Platzangabe in TRAINING bricht ab")
    except ValueError as e:
        p.pruefe("Tor 9" in str(e) and "BELEGUNG" in str(e), "unbekannte Platzangabe in TRAINING: ValueError mit Hinweis")

    # ------------------------------------------------------------------------------------------------ Einzelfälle
    p.abschnitt("Einzelfälle (erfundene Zeilen, die die echte CSV heute nicht enthält)")

    def zeile_mit(**felder):
        zeile = dict(zeilen[0], heim="FFV Sportfreunde 04 I", gast="Gegner", spielnr="1", datum="05.09.2026", zeit="9:30",
                     spielstaette="Mainzer Landstr. 480, 60326 Frankfurt am Main", wettbewerb="Meisterschaft", team="E1",
                     ergebnis="")
        zeile.update(felder)
        return zeile

    def daten_mit(**felder):
        return P.baue([zeile_mit(**felder)], HEUTE)

    def spiele_von(**felder):
        return daten_mit(**felder)["spiele"]

    def spiel(**felder):
        return spiele_von(**felder)[0]
    p.pruefe(spiel(wettbewerb="Kinderfestival")["dauer"] == 75 and spiel()["dauer"] == 90 and spiel(team="HERREN")["dauer"] == 120,
             "Kinderfestival dauert 75 Minuten auch bei einer E-Mannschaft, E-Spiel 90, Herren 120")
    p.pruefe(spiel()["z"] == "09:30", "Uhrzeit wird auf HH:MM gebracht")
    p.pruefe(spiel(spielstaette="")["ort"] == "Ort offen" and spiel(spielstaette="")["dauer"] is None,
             "Heimspiel ohne Spielstätte: ort „Ort offen“, dauer null")
    p.pruefe(spiel(spielstaette="Holzhecke 16 /Sandhöfer Wiesen, 60528 Frankfurt am Main")["ort"] == "Holzhecke 16 / Sandhöfer Wiesen",
             "andere Anlage: Teil vor dem ersten Komma, Schrägstrich mit Leerzeichen")
    p.pruefe(spiel(wettbewerb="")["w"] == "Meisterschaft", "leerer Wettbewerb gilt als Meisterschaft (wie lade_spiele())")
    p.pruefe(spiel(gast="SV Blau-Gelb FFM 1")["g"] == "SV Blau-Gelb Frankfurt 1",
             "Gegnername „SV Blau-Gelb FFM 1“ wird wie auf den App-Spielplanseiten zu „SV Blau-Gelb Frankfurt 1“")
    p.pruefe(spiel(wettbewerb="Kinderfestival", gast="")["g"] == "", "Kinderfestival ohne Gegner: g bleibt leer")
    p.pruefe(P.baue([dict(zeilen[0], heim="TSG 51 Ffm", gast="FFV Sportfreunde 04 FFM")], HEUTE)["spiele"] == [],
             "Auswärtsspiel kommt nicht in spiele")

    # abgesagte Spiele: fehlen in der Ausgabe, auch in regeln.navigation
    p.pruefe(spiele_von(ergebnis="abgesagt") == [] and spiele_von(ergebnis="Abgesagt") == [],
             "ergebnis „abgesagt“ und „Abgesagt“: das Heimspiel fehlt in der Ausgabe")
    p.pruefe(all(spiele_von(ergebnis=v) == [] for v in ("ABGESAGT", "  abgesagt ", "abgesagt\t")),
             "ergebnis „ABGESAGT“ und mit Leerzeichen: das Heimspiel fehlt ebenfalls (getrimmt, Groß-/Kleinschreibung egal)")
    p.pruefe(all(len(spiele_von(ergebnis=v)) == 1 for v in ("", None, "2:1", "3:1", "0:0")),
             "leeres ergebnis (auch fehlend) und ein Ergebnis wie 2:1 ändern nichts: das Heimspiel erscheint")
    p.pruefe(P.baue([zeile_mit(ergebnis="abgesagt", spielstaette="")], HEUTE)["spiele"] == [],
             "abgesagt gilt auch bei Heimspielen auf anderer Anlage oder ohne Spielstätte")
    leer = P.baue([], HEUTE)["regeln"]["navigation"]
    spaet, frueh = "04.03.2031", "07.01.2020"
    p.pruefe(daten_mit(ergebnis="abgesagt", datum=spaet)["regeln"]["navigation"] == leer
             and daten_mit(ergebnis="abgesagt", datum=frueh)["regeln"]["navigation"] == leer
             and daten_mit(ergebnis="", datum=spaet)["regeln"]["navigation"] == [leer[0], montag_von("2031-03-04").isoformat()]
             and daten_mit(ergebnis="", datum=frueh)["regeln"]["navigation"] == [montag_von("2020-01-07").isoformat(), leer[1]],
             "ein abgesagtes Spiel weit vor oder nach dem Plan verlängert regeln.navigation nicht (ohne Vermerk schon)")
    p.pruefe(P.baue(zeilen + [zeile_mit(ergebnis="abgesagt", datum=spaet), zeile_mit(ergebnis="Abgesagt", datum=frueh)],
                    HEUTE) == daten,
             "zwei zusätzliche abgesagte Heimspielzeilen ändern die Datei nicht (Spiele und navigation)")

    # feld: Feldbedarf je Klasse auf unserem Platz, bei anderem Spielort null
    p.pruefe(len(FELD_JE_TEAM) == 11 and all(spiel(team=t)["feld"] == f for t, f in FELD_JE_TEAM.items()),
             "feld auf unserem Platz je Mannschaft: D1, D2, D3, A, HERREN ganz; E1, E2, E3, F1, F2, G1 halb")
    p.pruefe(spiel(team="F1", wettbewerb="Kinderfestival", gast="")["feld"] == "halb"
             and spiel(team="D1", wettbewerb="Kreispokal")["feld"] == "ganz",
             "feld hängt an der Klasse, nicht am Wettbewerb (Kinderfestival der F1 halb, Kreispokal der D1 ganz)")
    fremd_ort = "Am Römerhof 9, 60486 Frankfurt am Main"
    p.pruefe(all(spiel(team=t, spielstaette=fremd_ort)["feld"] is None for t in FELD_JE_TEAM)
             and spiel(spielstaette="")["feld"] is None and spiel(spielstaette="")["ort"] == "Ort offen",
             "anderer Spielort (auch „Ort offen“): feld null bei jeder Mannschaft")
    p.pruefe(S_SPIEL[-1] == "feld" and list(spiel()) == S_SPIEL and list(spiel(spielstaette=fremd_ort)) == S_SPIEL,
             "Schlüsselfolge des Spielobjekts: d, z, t, w, g, nr, dauer, ort, feld (feld zuletzt, auch bei null)")

    # ------------------------------------------------------------------------------------------------ build_ics.py
    p.abschnitt("build_ics.py (zwei Läufe in Wegwerf-Ordnern)")
    with tempfile.TemporaryDirectory(prefix="platzbelegung-lauf-") as tmp:
        ergebnisse = []
        for i in (1, 2):
            ordner = os.path.join(tmp, "mit%d" % i)
            os.makedirs(ordner)
            rc = lauf(ordner, False, True)
            ergebnisse.append((rc, lies_bytes(os.path.join(ordner, "docs", "platzbelegung.json"))))
        p.pruefe(all(rc.returncode == 0 for rc, _ in ergebnisse) and all(d is not None for _, d in ergebnisse),
                 "build_ics.py mit Modul: Exit 0 und docs/platzbelegung.json entsteht (%s)"
                 % [(rc.returncode, rc.stderr.strip()[-200:]) for rc, _ in ergebnisse if rc.returncode])
        if all(d is not None for _, d in ergebnisse):
            p.pruefe(ergebnisse[0][1] == ergebnisse[1][1], "zwei Läufe von build_ics.py: dieselbe platzbelegung.json")
            gebaut = json.loads(ergebnisse[0][1].decode("utf-8"))
            tag = datetime.date.fromisoformat(gebaut["erzeugt"])
            p.pruefe(abs((tag - datetime.date.today()).days) <= 1 and P.baue(list(B.lade_spiele()), tag) == gebaut,
                     "Inhalt der Datei = baue() mit dem Tag des Laufs")
        ohne = os.path.join(tmp, "ohne")
        os.makedirs(ohne)
        rc = lauf(ohne, True, False)
        p.pruefe(rc.returncode == 0 and not os.path.exists(os.path.join(ohne, "docs", "platzbelegung.json"))
                 and os.path.exists(os.path.join(ohne, "docs", "alle.ics")) and "uebersprungen" in rc.stdout,
                 "ohne platzbelegung.py (Probelauf von tools/nachtrag.py, python -I -B): Exit 0, Kalender entstehen, "
                 "keine platzbelegung.json (%s)" % rc.stderr.strip()[-200:])
    p.ende()
    return p, zahlen


def main():
    p, zahlen = pruefungen()
    print()
    print("Prüfungen: %d, bestanden: %d, fehlgeschlagen: %d" % (p.anzahl, p.anzahl - len(p.fehler), len(p.fehler)))
    print("Zahlen: training %(training)d, halle %(halle)d, belegt %(belegt)d, Heimspiele gesamt %(heim)d, "
          "davon auf unserem Platz %(bei_uns)d" % zahlen)
    print("Auf unserem Platz je Klasse: %s; je feld: %s. Heimspiele auf anderer Anlage (feld null): %d. "
          "Abgesagte Heimspiele, nicht in der Datei: %d"
          % (", ".join("%s %d" % (k, v) for k, v in zahlen["je_klasse"].items()),
             ", ".join("%s %d" % (k, v) for k, v in zahlen["je_feld"].items()), zahlen["woanders"], zahlen["abgesagt"]))
    if "spiele_nur_jetzt" in zahlen:
        print("Beispieldatei: spiele %d -> %d (Gegnernamen dort vorher bereinigt, feld nicht verglichen); abweichende "
              "Einträge: %d nur jetzt, %d nur in der Beispieldatei, davon %d in spiele.csv abgesagt (Einzelheiten oben; "
              "spiele.csv ist lebendig, die Beispieldatei ein Stand vom 30.10.2026)"
              % (zahlen["spiele_beispiel"], zahlen["heim"], zahlen["spiele_nur_jetzt"], zahlen["spiele_nur_beispiel"],
                 zahlen["spiele_nur_beispiel_abgesagt"]))
    return 1 if p.fehler else 0


class TestPlatzbelegung(unittest.TestCase):
    def test_alle_pruefungen(self):
        with contextlib.redirect_stdout(io.StringIO()):
            p, _ = pruefungen()
        self.assertEqual(p.fehler, [])


if __name__ == "__main__":
    sys.exit(main())
