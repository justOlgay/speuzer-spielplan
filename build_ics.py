#!/usr/bin/env python3
"""Erzeugt aus spiele.csv abonnierbare Kalender und die App-Seiten.

Aufruf:  python3 build_ics.py [--alarm]
Umgebung: SEQ -> Sequenznummer (in GitHub Actions: GITHUB_RUN_NUMBER)

Ausgabe in docs/:
  <team>.ics          je Mannschaft, abonnierbar in der Vereins-App und im Handy
  alle.ics            alle Mannschaften in einem Kalender
  jugend.ics          nur D- und E-Jugend (Abo in der Vereins-App)
  index.html          Abo-Seite fuer Eltern
  app-<gruppe>.html   Spielplan-Seiten fuer die Vereins-App (eine je Altersklasse)
  import-<team>.ics   zum Hochladen in einen appack-Kalender ("ICS importieren"): erzeugt
                      echte App-Termine mit Zu-/Absage. Start = Treffpunkt, nur kuenftige Spiele.

Die UID eines Termins bleibt stabil (Staffelkennung + DFBnet-Spielnummer), damit
eine Verlegung den bestehenden Termin aendert statt einen zweiten anzulegen.
"""
import csv, os, re, sys, datetime, pathlib

BASE = pathlib.Path(__file__).parent
CSV_FILE = BASE / "spiele.csv"
OUT = BASE / "docs"
OUT.mkdir(exist_ok=True)

VEREIN = "FFV Sportfreunde 04"
DOMAIN = "speuzer.sportfreunde04.de"
DAUER_STUNDEN = 2
TREFFPUNKT_MIN = 60          # Treffpunkt = Anstoss minus 60 Minuten (Regel des Vereins)
ALARM = "--alarm" in sys.argv
SEQ = int(os.environ.get("SEQ", "0"))

FBDE_SPIEL = "https://www.fussball.de/spiel/x/-/spiel/"
FBDE_TEAM_BASE = "https://www.fussball.de/mannschaft/x/-/saison/2627/team-id/"

# Reihenfolge = Reihenfolge der Tabs in der App
TEAMS = {
    "HERREN": dict(label="Herren", staffel="341727", gruppe="herren",
                   info="Kreisliga A \u00b7 KLA Frankfurt Gr. 1 \u00b7 Kreis Frankfurt",
                   widget="0bc2cd05-999e-4098-8f5a-01faac49eb40",
                   teamid="011MICFB14000000VTVG0001VTR8C1K7"),
    "A":      dict(label="A-Jugend", staffel="343410", gruppe="a",
                   info="Gruppenliga \u00b7 AJGL Frankfurt \u00b7 Region Frankfurt",
                   widget="7f1d494c-ad2d-47d0-bb61-f4f0106ac2c4",
                   teamid="019ORCB50G000000VV0AG80NVUQ1MD7G"),
    "D1":     dict(label="D1", staffel="340782", gruppe="d",
                   info="Kreisliga A \u00b7 DJKL F Gr 1 \u00b7 Kreis Frankfurt",
                   widget="97c0160c-4b7a-40e1-ac86-3c8a4b3c6f76",
                   teamid="011MIE88PK000000VTVG0001VTR8C1K7"),
    "D2":     dict(label="D2", staffel="341874", gruppe="d",
                   info="1. Kreisklasse \u00b7 DJ KK F Gr. 05 \u00b7 Kreis Frankfurt",
                   widget="1e1dcbe7-34a6-4727-9744-68096138a415",
                   teamid="011MIF9LDC000000VTVG0001VTR8C1K7"),
    "D3":     dict(label="D3", staffel="341873", gruppe="d",
                   info="1. Kreisklasse \u00b7 DJ KK F Gr. 04 \u00b7 Kreis Frankfurt",
                   widget="81d9f5d7-9465-4aa5-ba6e-9830577ec0e6",
                   teamid="02USD4SHUG000000VS5489BRVS0D3BPJ"),
    "E1":     dict(label="E1", staffel="340610", gruppe="e",
                   info="1. Kreisklasse \u00b7 EJ Quali Gr. 08 \u00b7 Kreis Frankfurt",
                   widget="946f72f6-9ef2-4199-8b9d-6051948a2103",
                   teamid="011MIDBD8C000000VTVG0001VTR8C1K7"),
    "E2":     dict(label="E2", staffel="341435", gruppe="e",
                   info="1. Kreisklasse \u00b7 EJ Quali Gr. 18 \u00b7 Kreis Frankfurt",
                   widget="52cf66bf-3feb-436d-80fa-831c186dbb64",
                   teamid="011MIE1KH4000000VTVG0001VTR8C1K7"),
    "E3":     dict(label="E3", staffel="341930", gruppe="e",
                   info="1. Kreisklasse \u00b7 EJ Quali Gr. 22 \u00b7 Kreis Frankfurt",
                   widget="15839ff7-ac56-422d-9853-b240229822ec",
                   teamid="02PS0CFJCO000000VS5489B1VVQNIHJA"),
    "F1":     dict(label="F1", staffel="340233", gruppe="f",
                   info="Kinderfu\u00dfball \u00b7 F-Junioren 4+1 Gr. 2 \u00b7 Kreis Frankfurt",
                   widget=None, teamid=None),
    "F2":     dict(label="F2", staffel="340231", gruppe="f",
                   info="Kinderfu\u00dfball \u00b7 F-Junioren 4vs4 Gr. 4 \u00b7 Kreis Frankfurt",
                   widget=None, teamid=None),
    "G1":     dict(label="G1", staffel="340221", gruppe="g",
                   info="Kinderfu\u00dfball \u00b7 G-Junioren 3vs3 Gr. 4 \u00b7 Kreis Frankfurt",
                   widget=None, teamid=None),
}

# Wettbewerbe ausserhalb der Meisterschaft: eigene Staffelkennungen, eigene Titel.
FESTIVAL = "Kinderfestival"

GRUPPEN = {
    "herren": dict(titel="Herren", datei="app-herren.html"),
    "a":      dict(titel="A-Jugend", datei="app-a-jugend.html"),
    "d":      dict(titel="D-Jugend", datei="app-d-jugend.html"),
    "e":      dict(titel="E-Jugend", datei="app-e-jugend.html"),
    "f":      dict(titel="F-Jugend", datei="app-f-jugend.html"),
    "g":      dict(titel="G-Jugend", datei="app-g-jugend.html"),
}

# Team-Label fuer die Kopfzeile der App-Seite, wie es auch auf den Mannschaftsseiten
# der Website steht (z. B. "D3-Jugend" statt nur "D3"; data/teams.json im Repo
# speuzer-website-prototyp). Nur fuer die Kopfzeile - die Reiter selbst behalten das
# kurze Kuerzel TEAMS[t]["label"], sonst passt die URL-Raute (#D3) nicht mehr zum
# Reiter-Text. Mannschaften in Ein-Team-Gruppen (Herren, A, G1) stehen hier bewusst
# nicht drin, die Kopfzeile nimmt dort den Gruppentitel (siehe schreibe_app_seite).
VOLLLABEL = {
    "D1": "D1-Jugend", "D2": "D2-Jugend", "D3": "D3-Jugend",
    "E1": "E1-Jugend", "E2": "E2-Jugend", "E3": "E3-Jugend",
    "F1": "F1-Jugend", "F2": "F2-Jugend",
}

# Label je Staffelkennung. Leer = nur eine Staffel, dann keine Zwischenüberschrift.
# Wenn der Kreis die E-Jugend nach der Quali einer Liga zuordnet: neue Kennung hier
# eintragen (z. B. "Hauptrunde · Kreisklasse B Gr. 3") und die Spiele in spiele.csv nachtragen.
# Mannschaften, deren Termine in der App aus einer anderen Quelle kommen und deshalb NICHT in
# app-kalender.ics gehoeren - sonst stehen sie doppelt im Terminkalender.
#   D3 -> TEAMPUNKT-Feed (Spiele + Training)
#   D2 -> echte App-Termine mit Teilnahme-Rueckmeldung (Pilot Zu-/Absage)
# Leere Menge = alles kommt wieder aus diesem Generator.
NICHT_IN_APP_FEED = {"D3", "D2"}

# Spieltage, die in der App schon von Hand angelegt sind, sollen NICHT nochmal importiert
# werden. Je Mannschaft das Datum, ab dem der Import starten darf (Tag mitgezaehlt).
# D2: 23.08., 30.08., 05.09., 13.09. und 20.09.2026 liegen bereits als App-Termine.
IMPORT_AB = {"D2": "21.09.2026"}

RUNDE = {
    "340610": "Qualifikationsrunde",
    "341435": "Qualifikationsrunde",
    "341930": "Qualifikationsrunde",
}

HINWEIS_E = ("Die E-Jugend spielt zuerst eine Qualifikationsrunde. "
             "Die Spiele der Hauptrunde kommen dazu, sobald der Kreis sie ansetzt.")
HINWEIS_KINDER = ("Im Kinderfu\u00dfball gibt es keine Ligaspiele, sondern Kinderfestivals \u2013 "
                  "entweder bei uns oder bei einem anderen Verein. Der Kreis setzt sie in "
                  "Bl\u00f6cken an, deshalb reicht der Plan nur wenige Wochen voraus.")
HINWEIS_QUALI = ("Zurzeit stehen nur die Spiele der Qualifikationsrunde fest. Die Spiele der "
                 "Hauptrunde kommen automatisch in dieses Abo, sobald der Kreis sie ansetzt \u2013 "
                 "neu abonnieren muss niemand.")

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Berlin
X-LIC-LOCATION:Europe/Berlin
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 73:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def lade_spiele():
    with CSV_FILE.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            if not row.get("spielnr"):
                continue
            row = {k: (v or "").strip() for k, v in row.items()}
            tag, monat, jahr = (int(x) for x in row["datum"].split("."))
            stunde, minute = (int(x) for x in row["zeit"].split(":"))
            row["_start"] = datetime.datetime(jahr, monat, tag, stunde, minute)
            row["_heim"] = row["heim"].startswith(VEREIN)
            row["_gegner"] = row["gast"] if row["_heim"] else row["heim"]
            row["_wb"] = row.get("wettbewerb") or "Meisterschaft"
            yield row


def link(row):
    """Leerer String, wenn es weder Spiel- noch Mannschaftsseite gibt (Kinderfussball)."""
    if row.get("fbid"):
        return FBDE_SPIEL + row["fbid"]
    tid = TEAMS[row["team"]].get("teamid")
    return FBDE_TEAM_BASE + tid if tid else ""


def mannschaft(team):
    """Unsere Teams heissen ueberall gleich: Speuzer Herren, Speuzer D2, ..."""
    return "Speuzer %s" % TEAMS[team]["label"]


def paarung(row):
    """Heim - Gast, unsere Seite immer als 'Speuzer <Team>'."""
    uns = mannschaft(row["team"])
    if row["_wb"] == FESTIVAL:
        return "Kinderfestival %s" % ("bei uns" if row["_heim"] else "bei %s" % row["_gegner"])
    if row["_heim"]:
        return "%s \u2013 %s" % (uns, row["gast"])
    return "%s \u2013 %s" % (row["heim"], uns)


def titel(row, mit_ergebnis=True):
    """Einheitlicher Termintitel. Kinderfestivals haben keinen einzelnen Gegner,
    Pokal und Freundschaftsspiele bekommen den Wettbewerb dazu."""
    uns = mannschaft(row["team"])
    if row["_wb"] == FESTIVAL:
        kopf = "%s \u00b7 Kinderfestival %s" % (
            uns, "zu Hause" if row["_heim"] else "bei %s" % row["_gegner"])
    else:
        wo = "Heim gegen" if row["_heim"] else "Ausw\u00e4rts bei"
        vor = {"Kreispokal": "Pokal, ", "Freundschaftsspiel": "Freundschaftsspiel, "}.get(row["_wb"], "")
        kopf = "%s \u00b7 %s%s %s" % (uns, vor, wo, row["_gegner"])
    if mit_ergebnis and row["ergebnis"]:
        kopf += " \u00b7 %s" % row["ergebnis"]
    return kopf


def treffpunkt(row):
    return row["_start"] - datetime.timedelta(minutes=TREFFPUNKT_MIN)


def untertitel(row):
    """Text fuer das Feld 'Untertitel' in appack. Der Import kann das Feld nicht fuellen,
    darum steht die Zeile zum Kopieren oben in der Beschreibung."""
    if row["_wb"] == FESTIVAL:
        return "Treffpunkt %s Uhr \u00b7 Beginn %s Uhr \u00b7 %s" % (
            treffpunkt(row).strftime("%H:%M"), row["zeit"],
            "bei uns" if row["_heim"] else "ausw\u00e4rts")
    return "Treffpunkt %s Uhr \u00b7 Ansto\u00df %s Uhr \u00b7 %s" % (
        treffpunkt(row).strftime("%H:%M"), row["zeit"],
        "Heimspiel" if row["_heim"] else "Ausw\u00e4rtsspiel")


def beschreibung(row):
    """Immer gleich aufgebaut, damit jeder Termin gleich aussieht."""
    t = TEAMS[row["team"]]
    wb = t["info"] if row["_wb"] == "Meisterschaft" else "%s \u00b7 Kreis Frankfurt" % row["_wb"]
    z = [paarung(row),
         "Mannschaft: %s" % mannschaft(row["team"]),
         "Wettbewerb: %s" % wb]
    if RUNDE.get(row["staffel"]):
        z.append("Runde: %s" % RUNDE[row["staffel"]])
    z.append("%s \u00b7 Spielnummer %s \u00b7 Staffel %s"
             % ("Heimspiel" if row["_heim"] else "Ausw\u00e4rtsspiel",
                row["spielnr"], row["staffel"]))
    if row["spielstaette"]:
        z.append("Spielst\u00e4tte: %s" % row["spielstaette"])
    elif row["_wb"] == FESTIVAL:
        z.append("Spielst\u00e4tte: beim gastgebenden Verein \u2013 Adresse gibt der Trainer bekannt")
    else:
        z.append("Spielst\u00e4tte: siehe FUSSBALL.DE")
    if link(row):
        z.append("Alle Infos zum Spiel: %s" % link(row))
    return "\\n".join(esc(x) for x in z)


def event(row, stamp):
    ende = row["_start"] + datetime.timedelta(hours=DAUER_STUNDEN)
    fmt = "%Y%m%dT%H%M%S"
    z = ["BEGIN:VEVENT",
         "UID:%s-%s@%s" % (row["staffel"], row["spielnr"], DOMAIN),
         "DTSTAMP:%s" % stamp, "LAST-MODIFIED:%s" % stamp, "SEQUENCE:%d" % SEQ,
         "DTSTART;TZID=Europe/Berlin:%s" % row["_start"].strftime(fmt),
         "DTEND;TZID=Europe/Berlin:%s" % ende.strftime(fmt),
         "SUMMARY:%s" % esc(titel(row)),
         "DESCRIPTION:%s" % beschreibung(row),
         "CATEGORIES:Fussball", "TRANSP:OPAQUE"]
    if link(row):
        z.insert(-2, "URL:%s" % link(row))
    if row["spielstaette"]:
        z.append("LOCATION:%s" % esc(row["spielstaette"]))
    if ALARM:
        z += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Spiel morgen",
              "TRIGGER:-P1D", "END:VALARM"]
    z.append("END:VEVENT")
    return z


def schreibe_ics(datei, kalendername, spiele, stamp, hinweis=""):
    beschr = ("Spielplan des FFV Sportfreunde 04, Saison 2026/27, Quelle DFBnet. "
              "Verlegungen aktualisieren den bestehenden Termin.")
    if hinweis:
        beschr += " " + hinweis
    z = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//%s//Spielplan//DE" % VEREIN,
         "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:%s" % esc(kalendername),
         "X-WR-CALDESC:%s" % esc(beschr), "X-WR-TIMEZONE:Europe/Berlin",
         "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H", VTIMEZONE]
    for row in sorted(spiele, key=lambda r: r["_start"]):
        z += event(row, stamp)
    z.append("END:VCALENDAR")
    flach = []
    for e in z:
        flach.extend(e.replace("\r\n", "\n").split("\n"))
    (OUT / datei).write_text("\r\n".join(fold(x) for x in flach) + "\r\n",
                             encoding="utf-8", newline="")
    print("%-18s %3d Spiele" % (datei, len(spiele)))


# ---------------------------------------------------------------- Trainingszeiten
# Quelle: "Aufteilung Sportplatz/Aufteilung Trainingsplatz 26-27.xlsx", Version 1.2,
# von Olgay am 26.08.2026 bestaetigt. Wochentag, Beginn, Ende, Platz.
WOCHENTAG = {"MO": 0, "DI": 1, "MI": 2, "DO": 3, "FR": 4}

TRAINING = {
    "HERREN": [("MI", "19:30", "21:00", "SW Griesheim (Bezirkssportanlage am Rebstock)"),
               ("FR", "19:30", "21:00", "SW Griesheim (Bezirkssportanlage am Rebstock)")],
    "A":      [("MO", "19:00", "21:00", "linke H\u00e4lfte, Tor 1"),
               ("MI", "19:00", "20:30", "linke H\u00e4lfte, Tor 1")],
    "D1":     [("DI", "17:30", "19:30", "linke H\u00e4lfte, Tor 1"),
               ("MI", "17:30", "19:00", "linke H\u00e4lfte, Tor 1"),
               ("FR", "17:30", "19:30", "linke H\u00e4lfte, Tor 1")],
    "D2":     [("DI", "17:30", "19:30", "linke H\u00e4lfte, Tor 2"),
               ("MI", "17:30", "19:00", "linke H\u00e4lfte, Tor 2"),
               ("FR", "17:30", "19:30", "linke H\u00e4lfte, Tor 2")],
    "D3":     [("DI", "17:30", "19:30", "rechte H\u00e4lfte, Tor 1"),
               ("FR", "17:30", "19:30", "rechte H\u00e4lfte, Tor 1")],
    "E1":     [("MO", "17:30", "19:00", "rechte H\u00e4lfte"),
               ("MI", "17:30", "19:00", "rechte H\u00e4lfte"),
               ("DO", "17:30", "19:00", "linke H\u00e4lfte, Tor 1")],
    "E2":     [("MO", "17:30", "19:00", "linke H\u00e4lfte, Tor 2"),
               ("DI", "16:30", "18:00", "rechte H\u00e4lfte, Tor 1"),
               ("DO", "17:30", "19:00", "rechte H\u00e4lfte, Tor 1")],
    "E3":     [("MO", "16:00", "17:30", "rechte H\u00e4lfte"),
               ("MI", "16:00", "17:30", "rechte H\u00e4lfte")],
    "F1":     [("MO", "17:30", "19:00", "linke H\u00e4lfte, Tor 1"),
               ("MI", "17:30", "19:00", "rechte H\u00e4lfte, Tor 2")],
    "F2":     [("DI", "17:30", "19:00", "K\u00e4fig"),
               ("DO", "17:30", "19:00", "rechte H\u00e4lfte, Tor 2")],
    "G1":     [("MO", "17:30", "19:00", "Court")],
}

# Mannschaften ohne Pflichtspiele stehen nicht in TEAMS - Label fuer den Kalendernamen.
TRAINING_LABEL = {"F1": "F1", "F2": "F2", "G1": "G1"}

# Die D3 liefert ihre Trainings schon ueber den TEAMPUNKT-Feed in die App -
# deshalb gehoert sie nicht in den Sammelkalender, sonst steht alles doppelt.
NICHT_IN_TRAINING_SAMMEL = {"D3"}

TRAINING_VON = datetime.date(2026, 8, 31)   # erster Montag nach der Bestaetigung
TRAINING_BIS = datetime.date(2027, 6, 27)   # letzter Tag vor den Sommerferien

# Schulferien Hessen 26/27 und Feiertage, die auf einen Trainingstag fallen.
# Quelle: feiertage-deutschland.de / schulferien.eu, geprueft am 26.08.2026.
FREI = [
    (datetime.date(2026, 10, 5),  datetime.date(2026, 10, 17), "Herbstferien"),
    (datetime.date(2026, 12, 23), datetime.date(2027, 1, 12),  "Weihnachtsferien"),
    (datetime.date(2027, 3, 22),  datetime.date(2027, 4, 2),   "Osterferien"),
    (datetime.date(2027, 5, 6),   datetime.date(2027, 5, 6),   "Christi Himmelfahrt"),
    (datetime.date(2027, 5, 17),  datetime.date(2027, 5, 17),  "Pfingstmontag"),
    (datetime.date(2027, 5, 27),  datetime.date(2027, 5, 27),  "Fronleichnam"),
]
SPORTPLATZ = "FFV Sportfreunde 04, Mainzer Landstra\u00dfe 480, 60326 Frankfurt am Main"
ORT_EXTERN = {
    "HERREN": "Bezirkssportanlage am Rebstock, Am R\u00f6merhof 9, 60486 Frankfurt am Main",
}


def ist_frei(tag):
    for von, bis, _ in FREI:
        if von <= tag <= bis:
            return True
    return False


def trainingstermine(team):
    """Einzeltermine statt Serie - appack importiert Serien nicht zuverlaessig."""
    out = []
    for kuerzel, beginn, ende, platz in TRAINING[team]:
        tag = TRAINING_VON
        while tag.weekday() != WOCHENTAG[kuerzel]:
            tag += datetime.timedelta(days=1)
        while tag <= TRAINING_BIS:
            if not ist_frei(tag):
                out.append((tag, beginn, ende, platz))
            tag += datetime.timedelta(days=7)
    return sorted(out)


def training_events(team, stamp, label=None):
    label = label or (TEAMS[team]["label"] if team in TEAMS else TRAINING_LABEL[team])
    z = []
    for tag, beginn, ende, platz in trainingstermine(team):
        d = tag.strftime("%Y%m%d")
        z += ["BEGIN:VEVENT",
              "UID:training-%s-%s-%s@%s" % (team.lower(), d, beginn.replace(":", ""), DOMAIN),
              "DTSTAMP:%s" % stamp, "SEQUENCE:%d" % SEQ,
              "DTSTART;TZID=Europe/Berlin:%sT%s00" % (d, beginn.replace(":", "")),
              "DTEND;TZID=Europe/Berlin:%sT%s00" % (d, ende.replace(":", "")),
              "SUMMARY:%s" % esc("Speuzer %s \u00b7 Training" % label),
              "DESCRIPTION:%s" % "\\n".join(esc(x) for x in [
                  "Training %s" % label,
                  "Platz: %s" % platz,
                  "%s bis %s Uhr" % (beginn, ende),
                  "Bitte Schienbeinschoner und ausreichend Wasser mitbringen.",
                  "Absagen und \u00c4nderungen kommen \u00fcber die Vereins-App."]),
              "LOCATION:%s" % esc(ORT_EXTERN.get(team, SPORTPLATZ)),
              "CATEGORIES:Training", "TRANSP:OPAQUE", "END:VEVENT"]
    return z


def training_kopf(name, beschr):
    return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//%s//Training//DE" % VEREIN,
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
            "X-WR-CALNAME:%s" % esc(name), "X-WR-CALDESC:%s" % esc(beschr),
            "X-WR-TIMEZONE:Europe/Berlin",
            "REFRESH-INTERVAL;VALUE=DURATION:PT24H", "X-PUBLISHED-TTL:PT24H", VTIMEZONE]


def training_datei(datei, zeilen, anzahl, was):
    flach = []
    for e in zeilen:
        flach.extend(e.replace("\r\n", "\n").split("\n"))
    (OUT / datei).write_text("\r\n".join(fold(x) for x in flach) + "\r\n",
                             encoding="utf-8", newline="")
    print("%-18s %3d %s" % (datei, anzahl, was))


def schreibe_training_sammel(stamp):
    """Ein Kalender fuer die Vereins-App: alle Mannschaften ausser den TEAMPUNKT-Piloten."""
    teams = [t for t in TRAINING if t not in NICHT_IN_TRAINING_SAMMEL]
    beschr = ("Trainingszeiten aller Mannschaften, Saison 2026/27. In den hessischen Schulferien "
              "und an Feiertagen ist kein Training eingetragen.")
    if NICHT_IN_TRAINING_SAMMEL:
        beschr += (" Die Trainings von %s kommen in der App aus dem TEAMPUNKT-Kalender."
                   % ", ".join(sorted(NICHT_IN_TRAINING_SAMMEL)))
    z = training_kopf("Speuzer \u2013 Trainingszeiten aller Mannschaften", beschr)
    n = 0
    for t in teams:
        ev = training_events(t, stamp)
        z += ev
        n += ev.count("BEGIN:VEVENT")
    z.append("END:VCALENDAR")
    training_datei("training-alle.ics", z, n, "Einheiten (%d Mannschaften)" % len(teams))


def schreibe_training(team, stamp):
    label = TEAMS[team]["label"] if team in TEAMS else TRAINING_LABEL[team]
    name = "Speuzer %s \u2013 Training" % label
    beschr = ("Trainingszeiten der Saison 2026/27, Stand 26.08.2026. In den hessischen "
              "Schulferien und an Feiertagen ist kein Training eingetragen. "
              "Kurzfristige Absagen kommen \u00fcber die Vereins-App.")
    z = training_kopf(name, beschr) + training_events(team, stamp, label)
    z.append("END:VCALENDAR")
    training_datei("training-%s.ics" % team.lower(), z, len(trainingstermine(team)), "Einheiten")


# ------------------------------------------------------- Import-Dateien fuer appack
IMPORT_HINWEIS = ("Bitte per Daumen hoch / Fragezeichen / Daumen runter zur\u00fcckmelden, "
                  "ob euer Kind dabei ist \u2013 am besten bis zum Abend vor dem Spiel.")


def import_beschreibung(row):
    """Wie beschreibung(), aber aus Elternsicht und mit der Untertitel-Zeile zum Kopieren.
    Achtung: appack macht beim Import aus den Zeilenumbruechen einen Absatz."""
    t = TEAMS[row["team"]]
    z = ["Untertitel (zum Kopieren): %s" % untertitel(row),
         paarung(row),
         "Treffpunkt: %s Uhr" % treffpunkt(row).strftime("%H:%M"),
         ("Beginn: %s Uhr" if row["_wb"] == FESTIVAL else "Ansto\u00df: %s Uhr") % row["zeit"],
         "%s \u00b7 %s" % ("Heimspiel" if row["_heim"] else "Ausw\u00e4rtsspiel",
                            t["info"] if row["_wb"] == "Meisterschaft" else row["_wb"])]
    if RUNDE.get(row["staffel"]):
        z.append("Runde: %s" % RUNDE[row["staffel"]])
    z.append("Spielst\u00e4tte: %s" % (row["spielstaette"] or "siehe FUSSBALL.DE"))
    if link(row):
        z.append("Alle Infos zum Spiel: %s" % link(row))
    z.append(IMPORT_HINWEIS)
    return "\\n".join(esc(x) for x in z)


def import_event(row, stamp):
    """Ein Termin fuer den appack-Import: Start = Treffpunkt, Ende = Ansto\u00df + 2 h.
    Bewusst OHNE LOCATION - appack legt daraus sonst bei jedem Import einen neuen Ort an."""
    fmt = "%Y%m%dT%H%M%S"
    ende = row["_start"] + datetime.timedelta(hours=DAUER_STUNDEN)
    z = ["BEGIN:VEVENT",
         "UID:%s-%s-app@%s" % (row["staffel"], row["spielnr"], DOMAIN),
         "DTSTAMP:%s" % stamp, "SEQUENCE:%d" % SEQ,
         "DTSTART;TZID=Europe/Berlin:%s" % treffpunkt(row).strftime(fmt),
         "DTEND;TZID=Europe/Berlin:%s" % ende.strftime(fmt),
         "SUMMARY:%s" % esc(titel(row, mit_ergebnis=False)),
         "DESCRIPTION:%s" % import_beschreibung(row)]
    if link(row):
        z.append("URL:%s" % link(row))
    z += ["CATEGORIES:Fussball", "TRANSP:OPAQUE", "END:VEVENT"]
    return z


def schreibe_import(team, spiele, stamp):
    """Eine Datei je Mannschaft, nur kuenftige Spiele - Vergangenes will niemand importieren."""
    ab = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if IMPORT_AB.get(team):
        tag, monat, jahr = (int(x) for x in IMPORT_AB[team].split("."))
        ab = max(ab, datetime.datetime(jahr, monat, tag))
    kuenftig = sorted((r for r in spiele if r["_start"] >= ab), key=lambda r: r["_start"])
    name = "Import %s \u2013 Spieltage f\u00fcr die App" % mannschaft(team)
    beschr = ("Zum Hochladen in einen appack-Kalender \u00fcber \u201eICS importieren\u201c. "
              "Terminstart ist der Treffpunkt (Ansto\u00df minus %d Minuten). Nach dem Import je "
              "Termin noch: Untertitel einsetzen, Teilnahme-R\u00fcckmeldung anschalten, "
              "Gruppe einladen. Diese Datei nur EINMAL importieren, sonst stehen die Termine "
              "doppelt." % TREFFPUNKT_MIN)
    z = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//%s//Import//DE" % VEREIN,
         "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:%s" % esc(name), "X-WR-CALDESC:%s" % esc(beschr),
         "X-WR-TIMEZONE:Europe/Berlin", VTIMEZONE]
    for row in kuenftig:
        z += import_event(row, stamp)
    z.append("END:VCALENDAR")
    flach = []
    for e in z:
        flach.extend(e.replace("\r\n", "\n").split("\n"))
    datei = "import-%s.ics" % team.lower()
    (OUT / datei).write_text("\r\n".join(fold(x) for x in flach) + "\r\n",
                             encoding="utf-8", newline="")
    print("%-18s %3d Spieltage (Treffpunkt als Start)" % (datei, len(kuenftig)))


# ------------------------------------------------------------------ App-Seiten
# Gegnernamen aus DFBnet sind fuer die App-Seite manchmal nicht direkt lesbar. Die
# Bereinigung betrifft NUR die Anzeige auf der App-Seite - spiele.csv und die
# .ics-Dateien (Kalender, Import) bleiben unveraendert.
#
# 1) Rohartefakt "<Name> / <nur Ziffern>" (z. B. "FC Kalbach I / 1 2",
#    "SG Harheim 2 / 2") -> der Teil ab dem Schraegstrich faellt weg.
#    Vorsicht: Manche Mannschaften heissen wirklich "<Verein> / <Verein> <Nr>"
#    (z. B. "SG Bornheim / GW 2", "JFV Nidda / Schotten 1") - das sind echte,
#    aus zwei Vereinen zusammengesetzte Namen und werden nicht angefasst. Der
#    Unterschied: beim echten Namen stehen nach dem Schraegstrich Buchstaben,
#    beim Rohartefakt nur Ziffern.
_DOPPELTE_ZAHL = re.compile(r"\s*/\s*[0-9]+(?:\s+[0-9]+)*$")
# 2) "FFM"/"Ffm"/"Ffm." -> "Frankfurt", aber nur als Ortskuerzel am Ende des
#    Namens (ggf. gefolgt von einer Mannschaftsnummer wie "2" oder "II") - nicht
#    mitten im Namen, wo es das nicht gibt.
_ORTSKUERZEL_FFM = re.compile(r"\b[Ff][Ff][Mm]\.?(?=\s+[IVXivx0-9]+$|$)")
# 3) "VFR" (komplett gross, wie es in spiele.csv steht) -> "VfR".
_VFR = re.compile(r"\bVFR\b")


def bereinige_gegner(name):
    """Macht einen Gegnernamen aus DFBnet fuer die App-Seite lesbar. Reine
    Anzeige-Bereinigung, siehe Kommentare an den einzelnen Mustern oben. Im
    Zweifel (Muster passt nicht eindeutig) bleibt der Name unveraendert."""
    if not name:
        return name
    name = _DOPPELTE_ZAHL.sub("", name)
    name = _VFR.sub("VfR", name)
    name = _ORTSKUERZEL_FFM.sub("Frankfurt", name)
    return name.strip()


SEITE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spielplan %TITEL%</title>
<script>
// Meldet die eigene Hoehe an die einbettende Seite in der Vereins-App,
// damit dort kein zweiter Scrollbalken entsteht.
function speuzerHoehe(){try{parent.postMessage({speuzerHeight:document.documentElement.scrollHeight},"*");}catch(e){}}
addEventListener("load",function(){speuzerHoehe();setTimeout(speuzerHoehe,1200);setTimeout(speuzerHoehe,4000);
  if(window.ResizeObserver){new ResizeObserver(speuzerHoehe).observe(document.body);}});
</script><style>
@font-face{font-family:'Inter';font-style:normal;font-weight:400 700;font-display:swap;src:url("https://justolgay.github.io/speuzer-website-prototyp/assets/fonts/inter.woff2") format('woff2')}
*{box-sizing:border-box}body{margin:0;font-family:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#12142B;background:#F5F6FB;font-size:15px}
.tabs{display:flex;gap:4px;margin:12px 12px 0;padding:4px;background:#E4E7FA;border-radius:999px;overflow-x:auto}
.tab{flex:1 0 auto;min-width:74px;text-align:center;padding:9px 12px;border-radius:999px;background:transparent;color:#191793;font-weight:600;cursor:pointer;border:none;font-size:15px}
.tab.on{background:#fff;color:#0B0E4A;box-shadow:0 1px 2px rgba(11,14,74,.06),0 1px 1px rgba(11,14,74,.04)}
.meta{padding:10px 14px 2px;font-size:.8rem;color:#3F4360}
.kapitel{display:flex;align-items:center;gap:10px;padding:18px 14px 6px;font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;color:#5B6079;font-weight:700}
.kapitel:after{content:"";flex:1;height:1px;background:#D8DBEA}
table{width:100%;border-collapse:collapse}
td{padding:9px 12px;border-bottom:1px solid #D8DBEA;vertical-align:top}
tr.next td{background:#E4E7FA}
tr.vorbei td{color:#7A7F99}tr.vorbei .d{color:#7A7F99}tr.vorbei .ha{opacity:.55}
.d{white-space:nowrap;font-variant-numeric:tabular-nums;color:#0B0E4A;font-weight:600;width:96px}
.t{font-size:.78rem;color:#3F4360;font-weight:400}
.ha{display:inline-block;box-sizing:border-box;width:48px;text-align:center;font-size:.66rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:3px 0;border-radius:5px;margin-right:8px;vertical-align:1px}
.ha.h{background:#0B0E4A;color:#fff}
.ha.a{background:#E4E7FA;color:#191793;border:1px solid #D8DBEA}
.chev{width:44px;text-align:right;white-space:nowrap}
.res{font-weight:700;color:#0B0E4A}
.fuss{padding:14px;font-size:.78rem;color:#5B6079;line-height:1.5}
td a,td .ohne{color:inherit;text-decoration:none;display:block}
/* Gegnername laeuft buendig neben dem Heim/Ausw.-Chip weiter statt darunter */
td.n a,td.n .ohne{display:flex;align-items:flex-start;gap:8px}
td.n .ha{flex:0 0 48px;margin:1px 0 0}
.pfeil{color:#5B6079;font-weight:700;padding-left:6px}
tr.grp td{background:#F3F5FC;font-size:.7rem;letter-spacing:.09em;text-transform:uppercase;color:#5B6079;font-weight:700;padding:11px 12px}
</style></head><body>
<div class="tabs" id="tabs"></div><div class="meta" id="meta"></div>
<div class="kapitel">%KAPITEL%</div>
<table><tbody id="liste"></tbody></table>
<div class="fuss">%KLICK%Kurzfristige Absagen kommen vom Trainerteam – per Nachricht oder direkt.%HINWEIS%</div>
<script>
const STAFFEL = %STAFFEL%;
const TEAMLINK = %TEAMLINK%;
const S = %DATEN%;
const heute = new Date();
// Team-Vorauswahl per Adresse: app-d-jugend.html#D3 oeffnet den Reiter D3
// (Website-Teamseiten der Sportfreunde nutzen das, 21.09.2026).
function ausAdresse(){ try { const k = decodeURIComponent(location.hash.slice(1)); return STAFFEL[k] ? k : null; } catch (e) { return null; } }
let aktiv = ausAdresse() || Object.keys(STAFFEL)[0];
// ?einzeln: eingebettet auf der Teamseite (Website/App) – nur dieses Team,
// kein Umschalter zu den anderen Teams der Altersklasse.
const EINZELN = /[?&]einzeln(?:&|=|$)/.test(location.search);
if (EINZELN) { const h = document.getElementById("hinweis-kinder"); if (h) h.remove(); document.getElementById("meta").hidden = true; document.querySelector(".fuss").hidden = true; }
window.addEventListener("hashchange", function(){ const k = ausAdresse(); if (k && k !== aktiv) { aktiv = k; render(); } });
function hin(url, inhalt){
  return url ? `<a href="${url}" target="_blank" rel="noopener">${inhalt}</a>` : `<span class="ohne">${inhalt}</span>`;
}
function render(){
  const keys = EINZELN ? [aktiv] : Object.keys(STAFFEL);
  const tabsEl = document.getElementById("tabs");
  // Bei nur einer Mannschaft keine Reiterleiste - sonst bleibt die leere Pille
  // (Hintergrund/Rand des Umschalters) sichtbar, obwohl kein Reiter drin ist.
  tabsEl.style.display = keys.length < 2 ? "none" : "flex";
  tabsEl.innerHTML = keys.length < 2 ? "" : keys.map(t =>
    `<button class="tab${t===aktiv?" on":""}" onclick="aktiv='${t}';history.replaceState(null,'','#'+encodeURIComponent('${t}'));render()">${t}</button>`).join("");
  // STAFFEL[aktiv][2] ist die Kopfzeilen-Bezeichnung (Gruppentitel bei nur einer
  // Mannschaft, sonst das Team-Label wie auf der Website, z. B. "D3-Jugend") -
  // bewusst nicht "aktiv" selbst, das bleibt das kurze Reiter-/URL-Kuerzel.
  document.getElementById("meta").textContent = "Speuzer " + STAFFEL[aktiv][2] + " \u00b7 " + STAFFEL[aktiv][1];
  const spiele = S.filter(s => s[0]===aktiv);
  const naechstes = spiele.find(s => new Date(s[2]+"T"+s[3]) >= heute);
  let gruppe = null;
  document.getElementById("liste").innerHTML = spiele.map(s => {
    const d = new Date(s[2]+"T"+s[3]);
    const tag = d.toLocaleDateString("de-DE",{weekday:"short",day:"2-digit",month:"2-digit"});
    const ist = naechstes && s[2]===naechstes[2] && s[4]===naechstes[4];
    // Kinderfussball (F/G) hat weder Spiel- noch Mannschaftsseite auf FUSSBALL.DE:
    // dann Zeile ohne Link und ohne Pfeil statt href="undefined".
    const url = s[6] ? "https://www.fussball.de/spiel/x/-/spiel/"+s[6] : (TEAMLINK[s[0]] || "");
    let kopf = "";
    if (s[7] && s[7] !== gruppe) { gruppe = s[7]; kopf = `<tr class="grp"><td colspan="3">${s[7]}</td></tr>`; }
    // Ergebnis bzw. Pfeil steht in einer eigenen, schmalen Spalte, damit er am
    // Zeilenende nicht allein in eine neue Zeile umbricht.
    const vorbei = !ist && d < heute;
    return kopf + `<tr class="${ist?"next":(vorbei?"vorbei":"")}"><td class="d">${tag}<br><span class="t">${s[3]} Uhr</span></td>`
      + `<td class="n">${hin(url, `<span class="ha ${s[1]==="H"?"h":"a"}">${s[1]==="H"?"Heim":"Ausw."}</span><span>${s[4]}</span>`)}</td>`
      + `<td class="chev">${hin(url, s[5] ? `<span class="res">${s[5]}</span>` : (url ? `<span class="pfeil">\\u203a</span>` : ""))}</td></tr>`;
  }).join("");
}
render();
</script></body></html>
"""


def staffel_lesbar(info):
    """Wie staffelLesbar() der Website (src/vorlagen/hilfen.mjs): Liga plus
    Gruppe statt DFBnet-Kuerzel, im Kinderfussball die Spielform ausgeschrieben.
    "1. Kreisklasse \u00b7 DJ KK F Gr. 04 \u00b7 Kreis Frankfurt" -> "1. Kreisklasse, Gruppe 4"."""
    teile = [x.strip() for x in info.split("\u00b7")]
    liga, rest = teile[0], (teile[1] if len(teile) > 1 else "")
    m = re.search(r"Gr\.?\s*0*(\d+)\s*$", rest)
    gruppe = ", Gruppe\u00a0%s" % m.group(1) if m else ""
    if liga == "Kinderfu\u00dfball":
        plus = re.search(r"(\d+)\+1", rest)
        vs = re.search(r"(\d+)\s*vs\s*(\d+)", rest, re.I)
        form = ("%s gegen %s plus Torwart" % (plus.group(1), plus.group(1)) if plus
                else "%s gegen %s" % vs.groups() if vs else "")
        return "%s \u00b7 %s%s" % (liga, form, gruppe) if form else liga + gruppe
    return liga + gruppe


def js_obj(d):
    return "{" + ",".join('"%s":%s' % (k, v) for k, v in d.items()) + "}"


def schreibe_app_seite(gruppe, spiele):
    teams = [k for k, v in TEAMS.items() if v["gruppe"] == gruppe]
    # Kopfzeile: bei nur einer Mannschaft in der Gruppe der Gruppentitel
    # ("Speuzer G-Jugend" statt "Speuzer G1"), sonst das Team-Label wie auf der
    # Website ("D3-Jugend"). Die Reiter selbst behalten das kurze Kuerzel
    # TEAMS[t]["label"], sonst passt die URL-Raute (z.B. #D3) nicht mehr dazu.
    kopf = {t: (GRUPPEN[gruppe]["titel"] if len(teams) < 2
                else VOLLLABEL.get(t, TEAMS[t]["label"])) for t in teams}
    labels = {TEAMS[t]["label"]: '["%s","%s","%s"]' % (t, staffel_lesbar(TEAMS[t]["info"]), kopf[t])
              for t in teams}
    links = {TEAMS[t]["label"]: '"%s"' % (FBDE_TEAM_BASE + TEAMS[t]["teamid"])
             for t in teams if TEAMS[t].get("teamid")}
    daten = []
    for row in sorted(spiele, key=lambda r: (teams.index(r["team"]), r["_start"])):
        gegnername = bereinige_gegner(row["_gegner"])
        if row["_wb"] == FESTIVAL:
            gegner = "Kinderfestival %s" % ("bei\u00a0uns" if row["_heim"]
                                            else "bei %s" % gegnername)
        elif row["_wb"] != "Meisterschaft":
            gegner = "%s: %s" % (row["_wb"].replace("Kreispokal", "Pokal"), gegnername)
        else:
            gegner = gegnername
        # Mannschaftsnummer ("2", "IV") nie allein in die naechste Zeile
        gegner = re.sub(r" (\d{1,2}|[IVX]{1,4})$", "\u00a0\\1", gegner)
        daten.append('["%s","%s","%s","%s","%s","%s","%s","%s"]' % (
            TEAMS[row["team"]]["label"], "H" if row["_heim"] else "A",
            row["_start"].strftime("%Y-%m-%d"), row["_start"].strftime("%H:%M"),
            gegner.replace('"', ""), row["ergebnis"], row["fbid"],
            RUNDE.get(row["staffel"], "")))
    hinweis = ""
    if gruppe == "e":
        hinweis = "\n" + HINWEIS_E
    if gruppe in ("f", "g"):
        # eingebettet (?einzeln) erklaert die Teamseite das Kinderfestival selbst
        hinweis = '\n<span id="hinweis-kinder">' + HINWEIS_KINDER + '</span>'
    # Das FUSSBALL.DE-Spielplan-Widget ist an die Vereinsdomain gebunden und zeigt in
    # der App nur eine Fehlermeldung (Befund 31.08.2026, Ticket 2623142 bei appack).
    # Deshalb steht hier keins mehr - die Liste unten kommt ohnehin aus dem DFBnet.
    html = (SEITE.replace("%TITEL%", GRUPPEN[gruppe]["titel"])
                 .replace("%HINWEIS%", hinweis)
                 .replace("%KAPITEL%", "Bisher angesetzt" if gruppe in ("f", "g") else "Ganze Saison")
                 # Kinderfussball hat keine Spielseiten auf FUSSBALL.DE (Zeilen ohne Link)
                 .replace("%KLICK%", "" if gruppe in ("f", "g")
                          else "Ein Klick bzw. Tipp auf ein Spiel öffnet die Spielseite auf FUSSBALL.DE.\n")
                 .replace("%STAFFEL%", js_obj(labels))
                 .replace("%TEAMLINK%", js_obj(links))
                 .replace("%DATEN%", "[\n" + ",\n".join(daten) + "]"))
    (OUT / GRUPPEN[gruppe]["datei"]).write_text(html, encoding="utf-8")
    print("%-18s %3d Spiele" % (GRUPPEN[gruppe]["datei"], len(daten)))


def schreibe_index():
    zeilen = []
    for t, v in TEAMS.items():
        note = HINWEIS_QUALI if v["gruppe"] == "e" else ""
        zeilen.append('  { file:"%s.ics", name:"Speuzer %s", meta:"%s", note:"%s" },'
                      % (t.lower(), v["label"], v["info"], note))
    zeilen.append('  { file:"alle.ics", name:"Speuzer \u2013 alle Mannschaften", '
                  'meta:"Herren, A-, D- und E-Jugend in einem Kalender \u00b7 so liegt er auch in der Vereins-App", '
                  'note:"%s" },' % HINWEIS_QUALI)
    zeilen.append('  { file:"jugend.ics", name:"Speuzer \u2013 nur Jugend", '
                  'meta:"D1 bis E3 in einem Kalender, ohne Herren und A-Jugend", '
                  'note:"%s" },' % HINWEIS_QUALI)
    zeilen.append('  { file:"training-alle.ics", '
                  'name:"Speuzer \u2013 Training aller Mannschaften", '
                  'meta:"alle Trainingszeiten in einem Kalender \u00b7 so liegt er auch in der '
                  'Vereins-App", note:"" },')
    for t in TRAINING:
        label = TEAMS[t]["label"] if t in TEAMS else TRAINING_LABEL[t]
        tage = " \u00b7 ".join("%s %s\u2013%s" % (k, a, b) for k, a, b, _ in TRAINING[t])
        note = ("Die Trainings der D3 stehen in der App schon im TEAMPUNKT-Kalender \u2013 "
                "wer beides abonniert, sieht sie doppelt." if t in NICHT_IN_TRAINING_SAMMEL else "")
        zeilen.append('  { file:"training-%s.ics", name:"Speuzer %s \u2013 Training", '
                      'meta:"%s", note:"%s" },' % (t.lower(), label, tage, note))
    zeilen[-1] = zeilen[-1].rstrip(",")
    html = (BASE / "vorlage_index.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(html.replace("%TEAMS%", "\n".join(zeilen)),
                                    encoding="utf-8")
    print("index.html         %3d Kalender" % (len(TEAMS) + 3 + len(TRAINING)))


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alle = list(lade_spiele())
    for t, v in TEAMS.items():
        schreibe_ics("%s.ics" % t.lower(), "Speuzer %s" % v["label"],
                     [r for r in alle if r["team"] == t], stamp,
                     HINWEIS_QUALI if v["gruppe"] == "e" else "")
    schreibe_ics("alle.ics", "Speuzer Spielplan (alle Mannschaften)", alle, stamp,
                 HINWEIS_QUALI)
    # Nur Jugend (D + E): fuer das Abo in der Vereins-App, weil Herren und
    # A-Jugend dort schon im Vereinskalender stehen -> keine Doppeltermine.
    jugend = [r for r in alle if TEAMS[r["team"]]["gruppe"] in ("d", "e")]
    schreibe_ics("jugend.ics", "Speuzer Jugend (D & E)", jugend, stamp, HINWEIS_QUALI)
    # Der Feed, den die Vereins-App abonniert: alles ausser den TEAMPUNKT-Pilotmannschaften.
    appfeed = [r for r in alle if r["team"] not in NICHT_IN_APP_FEED]
    hinweis_app = HINWEIS_QUALI
    if NICHT_IN_APP_FEED:
        hinweis_app += (" Die Termine von %s liegen in der App in eigenen Kalendern."
                        % ", ".join(sorted(NICHT_IN_APP_FEED)))
    schreibe_ics("app-kalender.ics", "Speuzer Spielplan Mannschaften", appfeed, stamp, hinweis_app)
    for t in TEAMS:
        schreibe_import(t, [r for r in alle if r["team"] == t], stamp)
    for t in TRAINING:
        schreibe_training(t, stamp)
    schreibe_training_sammel(stamp)
    for g in GRUPPEN:
        schreibe_app_seite(g, [r for r in alle if TEAMS[r["team"]]["gruppe"] == g])
    schreibe_index()
    (OUT / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
