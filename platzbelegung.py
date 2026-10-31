#!/usr/bin/env python3
"""Platzbelegung: baut docs/platzbelegung.json, die öffentlichen Daten der Seite „Platzbelegung“ (Website und App).

Die Datei sagt, wer wann welchen Platzteil nutzt (Training auf dem Platz, Winterhalle, andere Nutzung), nennt alle
Heimspiele mit Spielort und enthält die Regeln, nach denen die Seite im Browser die freien Zeiten ausrechnet. Die
Seite liest sie zur Laufzeit von https://justolgay.github.io/speuzer-spielplan/platzbelegung.json. Hier wird nur die
Datei gebaut, gerechnet und gezeichnet wird im Browser.

Aufruf: build_ics.py ruft baue() und schreibe() am Ende jedes Laufs auf (dort schreibe_platzbelegung()); die
GitHub-Aktion tut das täglich mit den Kalendern. Nur Python 3, nur Standardbibliothek. Test: test_platzbelegung.py.

Quellen (die Tabellen stehen in build_ics.py und werden hier nicht noch einmal geführt):
  TRAINING                           Training auf dem Platz je Mannschaft (wer in ORT_EXTERN steht, trainiert
                                     auswärts und fehlt in der Datei, heute die Herren)
  HALLE                              Winterhalle: im Zeitraum ist die Mannschaft nicht auf dem Platz
  FREI                               Ferien und Feiertage ohne Training
  TRAINING_VON, TRAINING_BIS         Zeitraum des Plans
  TRAINING_STAND                     Stand des Plans, steht als stand.plan auch auf der Seite
  Zeilen aus spiele.csv              Heimspiele (build_ics.lade_spiele())
Hier stehen nur die Angaben, die es sonst nirgends gibt: BELEGT (andere Nutzung), Platzteile, Mannschaftsliste mit den
Ids der Seite und die Rechenregeln der Seite (Öffnungsfenster, Mindestlänge einer freien Zeit, Spieldauer).

ÖFFENTLICH, OHNE NAMEN: Die Datei und dieses Repo sind öffentlich. Keine Namen von Personen oder Firmen, keine
Telefonnummern, E-Mail-Adressen, Zellkoordinaten oder Kommentare aus der Excel, auch nicht in Kommentaren hier.
"""
import datetime
import json
import os
import re

VERSION = 1                          # Die Seite zeigt bei einer anderen Zahl den Fehlerhinweis.
SAISON = "2026/27"
UNSER_PLATZ = "Mainzer Landstr. 480"  # so beginnt die Spielstätte in spiele.csv bei einem Spiel auf unserem Platz
ORT_OFFEN = "Ort offen"              # Anzeige, wenn ein Heimspiel keine Spielstätte hat

# Platzteile in der Reihenfolge der Anzeige (links nach rechts). Tor 1 bis 4 sind die Platzviertel (links Tor 1 und 2,
# rechts Tor 3 und 4); Käfig und Court liegen neben dem Feld.
TEILE = [("tor1", "Tor 1"), ("tor2", "Tor 2"), ("tor3", "Tor 3"), ("tor4", "Tor 4"),
         ("kaefig", "Käfig"), ("court", "Court")]

# Platzangabe (so schreiben sie TRAINING und BELEGT) -> belegte Platzteile.
BELEGUNG = {
    "ganzer Platz": ["tor1", "tor2", "tor3", "tor4"],
    "linke Hälfte": ["tor1", "tor2"],
    "rechte Hälfte": ["tor3", "tor4"],
    "Tor 1": ["tor1"], "Tor 2": ["tor2"], "Tor 3": ["tor3"], "Tor 4": ["tor4"],
    "Käfig": ["kaefig"],
    "Court": ["court"],
    "Court und Käfig": ["kaefig", "court"],
}

# Mannschaften: Id (Verweis in der ganzen Datei und auf der Seite), Kurzname, Name, Kürzel in spiele.csv und TRAINING.
# Eine neue Mannschaft in build_ics.py (TEAMS, TRAINING) gehört auch hierher, sonst bricht der Lauf mit einer Meldung ab.
MANNSCHAFTEN = [
    ("herren", "Herren", "1. Herrenmannschaft", "HERREN"),
    ("a-jugend", "A-Jugend", "A-Jugend", "A"),
    ("d1", "D1", "D1-Jugend", "D1"),
    ("d2", "D2", "D2-Jugend", "D2"),
    ("d3", "D3", "D3-Jugend", "D3"),
    ("e1", "E1", "E1-Jugend", "E1"),
    ("e2", "E2", "E2-Jugend", "E2"),
    ("e3", "E3", "E3-Jugend", "E3"),
    ("f1", "F1", "F1-Jugend", "F1"),
    ("f2", "F2", "F2-Jugend", "F2"),
    ("g-jugend", "G-Jugend", "G-Jugend (Bambinis)", "G1"),
]
IDS = {csv_kuerzel: ident for ident, _, _, csv_kuerzel in MANNSCHAFTEN}

# Regeln, nach denen die Seite rechnet (Annahmen des Vereins, nicht aus spiele.csv).
FENSTER = {"werktag": ("15:30", "21:30"), "wochenende": ("09:00", "18:00")}   # Öffnungsfenster für freie Zeiten
MIN_FREI = 60                        # Minuten; eine kürzere Lücke zählt nicht als frei
DAUER = {"D": 105, "E": 90, "F": 75, "G": 75, "A": 120, "Herren": 120}        # Spieldauer in Minuten je Klasse
DAUER_FESTIVAL = 75                  # Kinderfestival

# Andere Nutzung des Platzes (nicht das Training einer Mannschaft): Wochentag, von, bis, Platzangabe (Schreibweise wie
# in BELEGUNG). Jeder Eintrag: andere Nutzung, öffentlich ohne Angabe, wer; darum steht hier auch kein Name.
# Quelle: Excel „Aufteilung Trainingsplatz 26-27“ (Stand der Datei 10.09.2026, Version 1.3), jeder Block, der keiner
# Mannschaft gehört. Gilt wie das Training im Zeitraum von TRAINING_VON bis TRAINING_BIS und nicht in den Ferien und an
# den Feiertagen (FREI). Von Hand gepflegt: ändert sich die Excel, ändert sich diese Tabelle, und TRAINING_STAND in
# build_ics.py bekommt das Datum der Änderung. Die Reihenfolge der Einträge bleibt stabil, damit sich die Datei nicht
# ohne Grund ändert.
BELEGT = [
    ("DI", "15:00", "17:30", "Käfig"),
    ("DI", "15:30", "17:00", "Tor 1"),
    ("MI", "15:00", "17:30", "Käfig"),
    ("DO", "17:00", "19:00", "Käfig"),
    ("FR", "14:00", "19:00", "Käfig"),
    ("MO", "20:00", "21:30", "Käfig"),
    ("DI", "20:00", "21:30", "Käfig"),
    ("FR", "19:30", "21:30", "ganzer Platz"),
]


def _generator():
    """Das Modul mit den Tabellen des Generators. Der Import steht hier und nicht oben, weil build_ics.py dieses Modul
    seinerseits lädt (kein Import im Kreis beim Laden). Läuft build_ics.py als Skript, übergibt es sich selbst an
    baue(), damit es nicht ein zweites Mal geladen wird."""
    import build_ics
    return build_ics


def _f(zeile, feld):
    """Feld einer CSV-Zeile als bereinigter Text (fehlend wie leer, wie in lade_spiele())."""
    return (zeile.get(feld) or "").strip()


def _id(csv_kuerzel, wo):
    try:
        return IDS[csv_kuerzel]
    except KeyError:
        raise ValueError("platzbelegung.py: Mannschaft „%s“ (%s) fehlt in MANNSCHAFTEN" % (csv_kuerzel, wo)) from None


def _teile(platz, wo):
    try:
        return list(BELEGUNG[platz])
    except KeyError:
        raise ValueError("platzbelegung.py: Platzangabe „%s“ (%s) fehlt in BELEGUNG" % (platz, wo)) from None


def _adresse(g):
    """Anschrift des Platzes aus SPORTPLATZ („FFV Sportfreunde 04, Mainzer Landstraße 480, 60326 Frankfurt am Main“)
    ohne den Vereinsnamen davor."""
    vorsatz = g.VEREIN + ", "
    return g.SPORTPLATZ[len(vorsatz):] if g.SPORTPLATZ.startswith(vorsatz) else g.SPORTPLATZ


def ort_kurz(spielstaette):
    """Spielstätte bis zum ersten Komma („Am Römerhof 9, 60486 Frankfurt am Main“ -> „Am Römerhof 9“); ein Schrägstrich
    bekommt auf beiden Seiten ein Leerzeichen („Holzhecke 16 /Sandhöfer Wiesen“ -> „Holzhecke 16 / Sandhöfer Wiesen“)."""
    return re.sub(r"\s*/\s*", " / ", spielstaette.split(",")[0]).strip()


def spieldauer(csv_kuerzel, wettbewerb, festival):
    """Spieldauer in Minuten: Kinderfestival DAUER_FESTIVAL, sonst nach Klasse (D, E, F, G, A, Herren)."""
    if wettbewerb == festival:
        return DAUER_FESTIVAL
    return DAUER["Herren" if csv_kuerzel == "HERREN" else csv_kuerzel[0]]


def montag(tag):
    """Montag der Woche, in der der Tag (JJJJ-MM-TT) liegt, als JJJJ-MM-TT."""
    d = datetime.date.fromisoformat(tag)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def baue(zeilen, heute, quelle=None):
    """Die Daten der Seite als dict; die Schlüssel stehen in der festen Reihenfolge der Schnittstelle (nicht umsortieren,
    sonst zeigt der tägliche Lauf Änderungen, die keine sind).

    zeilen: alle Zeilen aus spiele.csv (build_ics.lade_spiele()), Heim- und Auswärtsspiele aller Mannschaften
    heute:  Tag des Laufs (datetime.date), steht als „erzeugt“ und als Stand der Spiele in der Datei
    quelle: Modul mit den Tabellen und bereinige_gegner() des Generators; Standard build_ics (build_ics.py übergibt
            sich selbst)
    Ein Fehler in den Quellen (unbekannte Mannschaft oder Platzangabe) bricht mit ValueError ab, statt eine Lücke
    in die öffentliche Datei zu schreiben."""
    g = quelle if quelle is not None else _generator()
    if isinstance(heute, datetime.datetime):
        heute = heute.date()

    # Training auf dem Platz. Wer auswärts trainiert (ORT_EXTERN, heute die Herren), fehlt.
    training = []
    for kuerzel, eintraege in g.TRAINING.items():
        if kuerzel in g.ORT_EXTERN:
            continue
        for wochentag, von, bis, platz in eintraege:
            training.append({"t": _id(kuerzel, "TRAINING"), "tag": g.WOCHENTAG[wochentag], "von": von, "bis": bis,
                             "platz": platz, "teile": _teile(platz, "TRAINING %s" % kuerzel)})

    # Winterhalle: im Zeitraum ist die Mannschaft nicht auf dem Platz (training bleibt unverändert, die Seite blendet
    # es aus).
    halle = []
    for kuerzel, h in g.HALLE.items():
        for wochentag, von, bis, ort in h["zeiten"]:
            halle.append({"t": _id(kuerzel, "HALLE"), "tag": g.WOCHENTAG[wochentag], "von": von, "bis": bis,
                          "ort": ort.split(",")[0].replace("Sporthalle ", ""),
                          "ab": h["von"].isoformat(), "bisDatum": h["bis"].isoformat()})

    belegt = [{"tag": g.WOCHENTAG[wochentag], "von": von, "bis": bis, "teile": _teile(platz, "BELEGT")}
              for wochentag, von, bis, platz in BELEGT]

    # Alle Heimspiele (heim beginnt mit unserem Vereinsnamen), auch die auf anderer Anlage. Der Gegnername ist wie auf
    # den App-Spielplanseiten bereinigt (bereinige_gegner() in build_ics.py, nur Anzeige: spiele.csv bleibt unverändert).
    spiele = []
    for z in zeilen:
        if not _f(z, "heim").startswith(g.VEREIN):
            continue
        datum = datetime.datetime.strptime(_f(z, "datum"), "%d.%m.%Y").date()
        stunde, minute = (int(x) for x in _f(z, "zeit").split(":"))
        wettbewerb = _f(z, "wettbewerb") or "Meisterschaft"
        spielstaette = _f(z, "spielstaette")
        bei_uns = spielstaette.startswith(UNSER_PLATZ)
        spiele.append({
            "d": datum.isoformat(),
            "z": "%02d:%02d" % (stunde, minute),
            "t": _id(_f(z, "team"), "spiele.csv, Spiel %s" % _f(z, "spielnr")),
            "w": wettbewerb,
            "g": g.bereinige_gegner(_f(z, "gast")),
            "nr": _f(z, "spielnr"),
            "dauer": spieldauer(_f(z, "team"), wettbewerb, g.FESTIVAL) if bei_uns else None,
            "ort": "u" if bei_uns else (ort_kurz(spielstaette) or ORT_OFFEN),
        })
    spiele.sort(key=lambda s: (s["d"], s["z"], s["t"]))      # stabil: bei gleicher Zeit bleibt die Reihenfolge der CSV

    # Anzeigbare Wochen: von der Woche des ersten bis zur Woche des letzten Termins (Spiel oder Planzeitraum),
    # angegeben als Montag der ersten und Montag der letzten Woche.
    tage = [s["d"] for s in spiele] + [g.TRAINING_VON.isoformat(), g.TRAINING_BIS.isoformat()]

    return {
        "version": VERSION,
        "saison": SAISON,
        "erzeugt": heute.isoformat(),
        "stand": {"plan": g.TRAINING_STAND, "spiele": heute.strftime("%d.%m.%Y")},
        "platz": {"kurz": UNSER_PLATZ, "adresse": _adresse(g)},
        "teile": [{"id": ident, "name": name} for ident, name in TEILE],
        "belegung": {platz: list(teile) for platz, teile in BELEGUNG.items()},
        "mannschaften": [{"id": ident, "kurz": kurz, "name": name, "csv": kuerzel}
                         for ident, kurz, name, kuerzel in MANNSCHAFTEN],
        "regeln": {
            "trainingVon": g.TRAINING_VON.isoformat(),
            "trainingBis": g.TRAINING_BIS.isoformat(),
            "frei": [{"von": von.isoformat(), "bis": bis.isoformat(), "name": name} for von, bis, name in g.FREI],
            "fenster": {art: list(zeiten) for art, zeiten in FENSTER.items()},
            "minFrei": MIN_FREI,
            "dauer": dict(DAUER),
            "unserPlatz": UNSER_PLATZ,
            "navigation": [montag(min(tage)), montag(max(tage))],
        },
        "training": training,
        "halle": halle,
        "belegt": belegt,
        "spiele": spiele,
    }


def schreibe(pfad, daten):
    """Schreibt die Datei: UTF-8 ohne BOM, ensure_ascii=False, indent=1, Zeilenende \\n, am Ende ein Zeilenumbruch."""
    text = json.dumps(daten, ensure_ascii=False, indent=1) + "\n"
    with open(pfad, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    bei_uns = sum(1 for s in daten["spiele"] if s["ort"] == "u")
    print("%-18s %3d Heimspiele (%d auf unserem Platz), %d Trainings, %d Hallenzeiten, %d belegt"
          % (os.path.basename(str(pfad)), len(daten["spiele"]), bei_uns, len(daten["training"]),
             len(daten["halle"]), len(daten["belegt"])))
