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

Die UID eines Termins bleibt stabil (Staffelkennung + DFBnet-Spielnummer), damit
eine Verlegung den bestehenden Termin aendert statt einen zweiten anzulegen.
"""
import csv, os, sys, datetime, pathlib

BASE = pathlib.Path(__file__).parent
CSV_FILE = BASE / "spiele.csv"
OUT = BASE / "docs"
OUT.mkdir(exist_ok=True)

VEREIN = "FFV Sportfreunde 04"
DOMAIN = "speuzer.sportfreunde04.de"
DAUER_STUNDEN = 2
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
}

GRUPPEN = {
    "herren": dict(titel="Herren", datei="app-herren.html"),
    "a":      dict(titel="A-Jugend", datei="app-a-jugend.html"),
    "d":      dict(titel="D-Jugend", datei="app-d-jugend.html"),
    "e":      dict(titel="E-Jugend", datei="app-e-jugend.html"),
}

# Label je Staffelkennung. Leer = nur eine Staffel, dann keine Zwischenüberschrift.
# Wenn der Kreis die E-Jugend nach der Quali einer Liga zuordnet: neue Kennung hier
# eintragen (z. B. "Hauptrunde · Kreisklasse B Gr. 3") und die Spiele in spiele.csv nachtragen.
RUNDE = {
    "340610": "Qualifikationsrunde",
    "341435": "Qualifikationsrunde",
    "341930": "Qualifikationsrunde",
}

HINWEIS_E = ("Die E-Jugend spielt zuerst eine Qualifikationsrunde. "
             "Die Spiele der Hauptrunde kommen dazu, sobald der Kreis sie ansetzt.")
HINWEIS_FG = ("F1, F2 und G-Jugend haben noch keine angesetzten Spiele \u2013 "
              "sobald der Kreis ansetzt, erscheinen sie hier.")

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
            yield row


def link(row):
    if row.get("fbid"):
        return FBDE_SPIEL + row["fbid"]
    return FBDE_TEAM_BASE + TEAMS[row["team"]]["teamid"]


def titel(row):
    kopf = "%s (%s) %s" % (TEAMS[row["team"]]["label"],
                           "H" if row["_heim"] else "A", row["_gegner"])
    return kopf + (" " + row["ergebnis"] if row["ergebnis"] else "")


def event(row, stamp):
    ende = row["_start"] + datetime.timedelta(hours=DAUER_STUNDEN)
    fmt = "%Y%m%dT%H%M%S"
    z = ["BEGIN:VEVENT",
         "UID:%s-%s@%s" % (row["staffel"], row["spielnr"], DOMAIN),
         "DTSTAMP:%s" % stamp, "LAST-MODIFIED:%s" % stamp, "SEQUENCE:%d" % SEQ,
         "DTSTART;TZID=Europe/Berlin:%s" % row["_start"].strftime(fmt),
         "DTEND;TZID=Europe/Berlin:%s" % ende.strftime(fmt),
         "SUMMARY:%s" % esc(titel(row)),
         "DESCRIPTION:%s\\nSpiel-Nr. %s \\, Staffel %s\\nQuelle: DFBnet \\, Details: %s"
         % (esc(row["heim"] + " - " + row["gast"]), row["spielnr"], row["staffel"], link(row)),
         "URL:%s" % link(row), "CATEGORIES:Fussball", "TRANSP:OPAQUE"]
    if row["spielstaette"]:
        z.append("LOCATION:%s" % esc(row["spielstaette"]))
    if ALARM:
        z += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Spiel morgen",
              "TRIGGER:-P1D", "END:VALARM"]
    z.append("END:VEVENT")
    return z


def schreibe_ics(datei, kalendername, spiele, stamp):
    z = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//%s//Spielplan//DE" % VEREIN,
         "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:%s" % esc(kalendername), "X-WR-TIMEZONE:Europe/Berlin",
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


# ------------------------------------------------------------------ App-Seiten
SEITE = """<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spielplan %TITEL%</title>
<script type="text/javascript" src="https://www.fussball.de/widgets.js"></script><style>
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;color:#1c2530;background:#fff;font-size:15px}
.tabs{display:flex;gap:6px;padding:12px 12px 0;overflow-x:auto}
.tab{flex:1 0 auto;min-width:74px;text-align:center;padding:9px 12px;border-radius:8px 8px 0 0;background:#eef2f7;color:#0b3c78;font-weight:600;cursor:pointer;border:none;font-size:15px}
.tab.on{background:#0b3c78;color:#fff}
.meta{padding:10px 14px 2px;font-size:.8rem;color:#5a6b80}
.widgets{padding:2px 4px 0}
.wbox{display:none}
.wbox.on{display:block}
.kapitel{display:flex;align-items:center;gap:10px;padding:18px 14px 6px;font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;color:#8798ab;font-weight:700}
.kapitel:after{content:"";flex:1;height:1px;background:#e6ebf2}
table{width:100%;border-collapse:collapse}
td{padding:9px 12px;border-bottom:1px solid #e6ebf2;vertical-align:top}
tr.next td{background:#eef6ff}
.d{white-space:nowrap;font-variant-numeric:tabular-nums;color:#0b3c78;font-weight:600;width:96px}
.t{font-size:.78rem;color:#5a6b80;font-weight:400}
.ha{display:inline-block;min-width:20px;font-weight:700;color:#0b3c78}
.res{float:right;font-weight:700;color:#0b3c78}
.fuss{padding:14px;font-size:.78rem;color:#6b7a8d;line-height:1.5}
td a{color:inherit;text-decoration:none;display:block}
.pfeil{color:#9fb0c4;font-weight:700;padding-left:6px}
tr.grp td{background:#f6f8fc;font-size:.7rem;letter-spacing:.09em;text-transform:uppercase;color:#7286a0;font-weight:700;padding:11px 12px}
</style></head><body>
<div class="tabs" id="tabs"></div><div class="meta" id="meta"></div>
<div class="widgets">%WIDGETS%</div>
<div class="kapitel">Ganze Saison</div>
<table><tbody id="liste"></tbody></table>
<div class="fuss">Oben die letzten und naechsten Spiele direkt von FUSSBALL.DE, unten die
komplette Saison aus dem DFBnet. H = Heimspiel, A = Auswaerts.
Tippen auf ein Spiel oeffnet die Spielseite auf FUSSBALL.DE.
Kurzfristige Absagen kommen per Push, nicht ueber diese Seite.%HINWEIS%</div>
<script>
const STAFFEL = %STAFFEL%;
const TEAMLINK = %TEAMLINK%;
const S = %DATEN%;
const heute = new Date();
let aktiv = Object.keys(STAFFEL)[0];
function render(){
  const keys = Object.keys(STAFFEL);
  document.getElementById("tabs").innerHTML = keys.length < 2 ? "" : keys.map(t =>
    `<button class="tab${t===aktiv?" on":""}" onclick="aktiv='${t}';render()">${t}</button>`).join("");
  document.getElementById("meta").textContent = STAFFEL[aktiv][1];
  keys.forEach(t => {
    const box = document.getElementById("w-" + STAFFEL[t][0]);
    if (box) box.className = "wbox" + (t===aktiv ? " on" : "");
  });
  const spiele = S.filter(s => s[0]===aktiv);
  const naechstes = spiele.find(s => new Date(s[2]+"T"+s[3]) >= heute);
  let gruppe = null;
  document.getElementById("liste").innerHTML = spiele.map(s => {
    const d = new Date(s[2]+"T"+s[3]);
    const tag = d.toLocaleDateString("de-DE",{weekday:"short",day:"2-digit",month:"2-digit"});
    const ist = naechstes && s[2]===naechstes[2] && s[4]===naechstes[4];
    const url = s[6] ? "https://www.fussball.de/spiel/x/-/spiel/"+s[6] : TEAMLINK[s[0]];
    let kopf = "";
    if (s[7] && s[7] !== gruppe) { gruppe = s[7]; kopf = `<tr class="grp"><td colspan="2">${s[7]}</td></tr>`; }
    return kopf + `<tr class="${ist?"next":""}"><td class="d">${tag}<br><span class="t">${s[3]} Uhr</span></td>`
      + `<td><a href="${url}" target="_blank" rel="noopener"><span class="ha">${s[1]}</span> ${s[4]}`
      + `${s[5]?`<span class="res">${s[5]}</span>`:`<span class="pfeil">\\u203a</span>`}</a></td></tr>`;
  }).join("");
}
render();
</script></body></html>
"""


def js_obj(d):
    return "{" + ",".join('"%s":%s' % (k, v) for k, v in d.items()) + "}"


def schreibe_app_seite(gruppe, spiele):
    teams = [k for k, v in TEAMS.items() if v["gruppe"] == gruppe]
    labels = {TEAMS[t]["label"]: '["%s","%s"]' % (t, TEAMS[t]["info"]) for t in teams}
    links = {TEAMS[t]["label"]: '"%s"' % (FBDE_TEAM_BASE + TEAMS[t]["teamid"]) for t in teams}
    daten = []
    for row in sorted(spiele, key=lambda r: (teams.index(r["team"]), r["_start"])):
        daten.append('["%s","%s","%s","%s","%s","%s","%s","%s"]' % (
            TEAMS[row["team"]]["label"], "H" if row["_heim"] else "A",
            row["_start"].strftime("%Y-%m-%d"), row["_start"].strftime("%H:%M"),
            row["_gegner"].replace('"', ""), row["ergebnis"], row["fbid"],
            RUNDE.get(row["staffel"], "")))
    hinweis = ""
    if gruppe == "e":
        hinweis = "\n" + HINWEIS_E
    if gruppe == "d":
        hinweis = "\n" + HINWEIS_FG
    widgets = "\n".join(
        '<div class="wbox%s" id="w-%s"><div class="fussballde_widget" '
        'data-id="%s" data-type="team-matches" style="width:100%%"></div></div>'
        % (" on" if i == 0 else "", t, TEAMS[t]["widget"]) for i, t in enumerate(teams))
    html = (SEITE.replace("%TITEL%", GRUPPEN[gruppe]["titel"])
                 .replace("%WIDGETS%", widgets)
                 .replace("%HINWEIS%", hinweis)
                 .replace("%STAFFEL%", js_obj(labels))
                 .replace("%TEAMLINK%", js_obj(links))
                 .replace("%DATEN%", "[\n" + ",\n".join(daten) + "]"))
    (OUT / GRUPPEN[gruppe]["datei"]).write_text(html, encoding="utf-8")
    print("%-18s %3d Spiele" % (GRUPPEN[gruppe]["datei"], len(daten)))


def schreibe_index():
    zeilen = []
    for t, v in TEAMS.items():
        zeilen.append('  { file:"%s.ics", name:"%s", meta:"%s" },'
                      % (t.lower(), v["label"], v["info"]))
    zeilen.append('  { file:"alle.ics", name:"Alle Mannschaften", meta:"Herren, A-, D- und E-Jugend in einem Kalender" },')
    zeilen.append('  { file:"jugend.ics", name:"Nur Jugend (D/E)", meta:"D1 bis E3 in einem Kalender - liegt so in der Vereins-App" }')
    html = (BASE / "vorlage_index.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(html.replace("%TEAMS%", "\n".join(zeilen)),
                                    encoding="utf-8")
    print("index.html         %3d Kalender" % (len(TEAMS) + 2))


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alle = list(lade_spiele())
    for t, v in TEAMS.items():
        schreibe_ics("%s.ics" % t.lower(), "Speuzer %s" % v["label"],
                     [r for r in alle if r["team"] == t], stamp)
    schreibe_ics("alle.ics", "Speuzer Spielplan", alle, stamp)
    # Nur Jugend (D + E): fuer das Abo in der Vereins-App, weil Herren und
    # A-Jugend dort schon im Vereinskalender stehen -> keine Doppeltermine.
    jugend = [r for r in alle if TEAMS[r["team"]]["gruppe"] in ("d", "e")]
    schreibe_ics("jugend.ics", "Speuzer Jugend (D/E)", jugend, stamp)
    for g in GRUPPEN:
        schreibe_app_seite(g, [r for r in alle if TEAMS[r["team"]]["gruppe"] == g])
    schreibe_index()
    (OUT / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
