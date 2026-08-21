# Speuzer Spielplan-Kalender

Erzeugt aus dem DFBnet-Spielplan abonnierbare Kalender (`.ics`) für die D-Jugend des
FFV Sportfreunde 04. Die Dateien liegen unter `docs/` und werden über GitHub Pages
mit dem richtigen Content-Type `text/calendar` ausgeliefert – das ist Voraussetzung
dafür, dass die Vereins-App (Appack) sie als externen iCal-Kalender akzeptiert.

## Einmalige Einrichtung (ca. 10 Minuten)

1. **Repository anlegen:** github.com → *New repository* → Name z. B. `speuzer-spielplan`,
   **Public**, ohne README. Dann *uploading an existing file* und den kompletten Inhalt
   dieses Ordners hochladen (Drag & Drop im Browser genügt, inklusive Ordner `.github`).
2. **GitHub Pages einschalten:** Repo → *Settings* → *Pages* →
   Source: **Deploy from a branch**, Branch: **main**, Folder: **/docs** → *Save*.
   Nach ein bis zwei Minuten ist die Seite erreichbar unter
   `https://<dein-github-name>.github.io/speuzer-spielplan/`
3. **Actions erlauben:** Repo → *Settings* → *Actions* → *General* → unten bei
   *Workflow permissions* auf **Read and write permissions** stellen → *Save*.
   (Nur nötig, damit der Workflow die neu gebauten Dateien zurückschreiben darf.)
4. **In der App einbinden:** Appack-CMS → *Kalender* → **+** →
   Name z. B. `Spielplan D2` → Häkchen bei *Einen Kalender von einer externen Quelle
   abonnieren (iCal)* → ICal-Adresse:
   `https://<dein-github-name>.github.io/speuzer-spielplan/d2.ics` → Speichern.
   Erscheint **keine** gelbe Warnung, ist der Feed akzeptiert.
   Lesezugriff nach Bedarf auf die Mannschaftsgruppe einschränken.
5. **Für Eltern direkt:** die Seite
   `https://<dein-github-name>.github.io/speuzer-spielplan/` zeigt Abo-Buttons für
   iPhone, Android und Outlook. Diese URL lässt sich auch als Seitenlink-Modul in die
   App legen.

## Dateien

| Datei | Zweck |
|---|---|
| `spiele.csv` | die einzige Datei, die gepflegt wird – ein Spiel pro Zeile, Semikolon-getrennt |
| `build_ics.py` | erzeugt `docs/d1.ics`, `d2.ics`, `d3.ics` und `alle.ics` |
| `docs/index.html` | Abo-Seite für Eltern, baut die Links selbst aus der eigenen Adresse |
| `.github/workflows/build.yml` | baut bei jeder Änderung an `spiele.csv` neu, zusätzlich täglich |

## Pflege im Alltag

**Spielverlegung:** `spiele.csv` im Browser öffnen (Stift-Symbol), Datum und/oder Uhrzeit
in der betroffenen Zeile ändern, *Commit changes*. Der Workflow baut die Kalender neu;
weil die UID gleich bleibt (`Staffelkennung-Spielnummer`), verschiebt sich in allen
abonnierten Kalendern der **bestehende** Termin – es entsteht kein Doppel-Eintrag.

**Ergebnis nachtragen (optional):** in der Spalte `ergebnis` z. B. `3:1` eintragen.
Der Termin heißt dann `D2 (H) VFR Bockenheim 2 3:1`.

**Spiel fällt aus:** Zeile löschen oder in `ergebnis` `abgesagt` schreiben. Beim Löschen
verschwindet der Termin bei den Abonnenten allerdings nicht überall zuverlässig –
sauberer ist, das Datum stehen zu lassen und `abgesagt` einzutragen.

**Neue Saison:** Spielplan in DFBnet über *Ergebnismeldung → Staffeln → Mannschaft wählen
→ Spieltag „Alle auswählen"* aufrufen und `spiele.csv` neu füllen. Der Vereinsspielplan
unter *Vereinsmeldung* taugt dafür nicht, er erlaubt nur Zeiträume von maximal 3 Monaten.

## Bewusst nicht automatisiert

Der Workflow holt die Daten **nicht** selbst von fussball.de. Dort werden Spielstände
gezielt mit verschleierten Schriftarten ausgeliefert (Scraping-Abwehr), und automatisiertes
Auslesen ist nutzungsrechtlich heikel. Der offizielle Weg ist der DFBnet-Zugang – und
Verlegungen sind in der D-Jugend selten genug, dass eine Zeile in der CSV schneller
geändert ist als jede Bastellösung gepflegt wäre.

## Spielstätten

Für Heimspiele ist `Sportplatz Mainzer Landstrasse, Frankfurt am Main` eingetragen,
für Auswärtsspiele ist die Spalte leer. Wer die Auswärtsplätze ergänzen will, findet sie
in DFBnet in der Staffelsicht im Reiter *Spielstätte*.

## Die App-Seite (Spielplan in der Vereins-App)

`build_ics.py` erzeugt zusätzlich `docs/app-seite.html` – eine in sich geschlossene Seite
mit Tabs für D1, D2 und D3, hervorgehobenem nächsten Spiel und den Daten direkt im HTML
(kein Nachladen, funktioniert also auch im App-Container ohne CORS-Fragen).

Diese Seite liegt im Appack-CMS als **Seiten → `Spielplan-D-Jugend.html`** und ist über die
Liste `Spieltag_Tabelle_db` im Modul *Übersicht aller Spiele* verlinkt.

**Nach jeder Änderung an `spiele.csv`:** `docs/app-seite.html` neu erzeugen lassen und den
Inhalt im CMS unter *Seiten → Spielplan-D-Jugend.html → Quellcode editieren* komplett
ersetzen (alles markieren, einfügen, speichern). Der Kalender-Feed aktualisiert sich dagegen
von allein, sobald der Commit durch ist.

## Links zu den Spielen auf FUSSBALL.DE

Die Spalte `fbid` in `spiele.csv` enthält die Spiel-ID von FUSSBALL.DE. Ist sie gefüllt,
verlinkt die App-Seite den Eintrag direkt auf die Spielseite und die Kalendertermine
bekommen ein `URL:`-Feld (in Apple- und Outlook-Kalender als Link sichtbar):
`https://www.fussball.de/spiel/x/-/spiel/<fbid>` – der Slug ist beliebig, FUSSBALL.DE
leitet auf die richtige Adresse um. Ist `fbid` leer, geht der Link auf den
Mannschaftsspielplan der jeweiligen Mannschaft.

**IDs nachfüllen:** FUSSBALL.DE zeigt im Mannschaftsspielplan immer nur die nächsten zehn
Spiele, deshalb sind zunächst nur diese verlinkt. Zum Auffrischen die Seite
`https://www.fussball.de/ajax.team.matchplan/-/mode/PAGE/team-id/<TEAM-ID>` im eingeloggten
Browser öffnen und in der Konsole ausführen:

```js
const rows=[...document.querySelectorAll('tr')], p=[];
for(let i=0;i<rows.length;i++){
  const m=rows[i].innerText.match(/\|\s*(\d{9})/);
  if(m) for(let j=i;j<i+3&&j<rows.length;j++){
    const a=rows[j].querySelector('a[href*="/spiel/"]');
    if(a){ p.push(m[1]+' = '+a.getAttribute('href').split('/spiel/').pop()); break; }
  }
}
console.log(p.join('\n'));
```

Ausgegeben wird `<Staffelkennung><Spielnummer dreistellig> = <fussball.de-ID>`, also z. B.
`341874004` für Staffel 341874, Spiel 4. Die IDs in `spiele.csv` in Spalte `fbid` eintragen.

Team-IDs: D1 `011MIE88PK000000VTVG0001VTR8C1K7` ·
D2 `011MIF9LDC000000VTVG0001VTR8C1K7` · D3 `02USD4SHUG000000VS5489BRVS0D3BPJ`.
Vereinsseite: `https://www.fussball.de/verein/ffv-sportfr-ffm-hessen/-/id/00ES8GN9L8000059VV0AG08LVUPGND5I`

## Tabellen-Widgets von FUSSBALL.DE

Am 20.08.2026 wurden auf FUSSBALL.DE (Profil → Meine Widgets) drei Tabellen-Widgets angelegt,
Einbindungs-Domain `cdn.appack.de`:

| Mannschaft | Wettbewerb | Widget-ID (`data-id`) |
|---|---|---|
| D1 | DJKL F Gr 1 | `1e9caaff-1559-444a-ad35-bb60f75e5dca` |
| D2 | DJ KK F Gr. 05 | `a0e8a901-ccc9-4810-9fe4-c16f8dc6ca86` |
| D3 | DJ KK F Gr. 04 | `f861a1b4-7b87-4b44-ae96-b217b8ba1758` |

Einbindung (so steht es in der App-Seite `Tabellen-D-Jugend.html`):

```html
<script type="text/javascript" src="https://www.fussball.de/widgets.js"></script>
<div class="fussballde_widget" data-id="…" data-type="table" style="width: 100%"></div>
```

Die Tabellen kommen live von FUSSBALL.DE, es gibt dafür also keine Pflege. **Pflicht:** In die
Datenschutzerklärung der App muss ein Hinweis auf das Widget und dessen Webtracking – so
verlangt es FUSSBALL.DE von Seitenbetreibern.

---

## Stand 20.08.2026: alle Mannschaften

`spiele.csv` enthält jetzt 134 Spiele aus DFBnet:

| Kürzel | Mannschaft | Wettbewerb | Staffel | Spiele | fussball.de team-id |
|---|---|---|---|---|---|
| HERREN | Herren | Kreisliga A, KLA Frankfurt Gr. 1 | 341727 | 30 | `011MICFB14000000VTVG0001VTR8C1K7` |
| A | A-Jugend | Gruppenliga, AJGL Frankfurt | 343410 | 26 | `019ORCB50G000000VV0AG80NVUQ1MD7G` |
| D1 | D-Junioren I | Kreisliga A, DJKL F Gr 1 | 340782 | 20 | `011MIE88PK000000VTVG0001VTR8C1K7` |
| D2 | D-Junioren II | 1. Kreisklasse, DJ KK F Gr. 05 | 341874 | 22 | `011MIF9LDC000000VTVG0001VTR8C1K7` |
| D3 | D-Junioren III | 1. Kreisklasse, DJ KK F Gr. 04 | 341873 | 22 | `02USD4SHUG000000VS5489BRVS0D3BPJ` |
| E1 | E-Junioren I | 1. Kreisklasse, EJ Quali Gr. 08 | 340610 | 5 | `011MIDBD8C000000VTVG0001VTR8C1K7` |
| E2 | E-Junioren II | 1. Kreisklasse, EJ Quali Gr. 18 | 341435 | 5 | `011MIE1KH4000000VTVG0001VTR8C1K7` |
| E3 | E-Junioren III | 1. Kreisklasse, EJ Quali Gr. 22 | 341930 | 4 | `02PS0CFJCO000000VS5489B1VVQNIHJA` |

**Nicht enthalten und warum:**

- **F1, F2, G-Jugend** sind gemeldet, haben aber noch keine angesetzten Spiele. Sobald der Kreis
  ansetzt: Staffelkennung ergänzen, Zeilen in `spiele.csv` schreiben, in `build_ics.py` unter
  `TEAMS` eintragen (Gruppe `f`) und in `GRUPPEN` eine Seite `app-f-jugend.html` anlegen.
- **Pokal und Freundschaftsspiele** sind bewusst draußen: Herren Kreispokal 940158, A-Jugend
  Kreispokal 940282 und Kreis-Freundschaftsspiele 540202/540049, D1 Kreispokal 940397 und
  Kreis-FS 540072, E1 Kreis-FS 540210. Wer sie will, trägt sie mit ihrer eigenen Staffelkennung
  als weitere Zeilen ein – UID und Links funktionieren genauso.
- **E-Jugend:** aktuell läuft nur die Qualifikationsrunde (5 Spieltage). Die Hauptrunde wird
  später in einer neuen Staffel angesetzt und muss dann nachgetragen werden.

**Erzeugte Dateien:** `herren.ics`, `a.ics`, `d1.ics`–`d3.ics`, `e1.ics`–`e3.ics`, `alle.ics`,
`index.html` (Abo-Seite mit allen neun Kalendern) sowie drei App-Seiten:
`app-aktive.html` (Herren + A-Jugend), `app-d-jugend.html` (D1–D3), `app-e-jugend.html` (E1–E3).

## Tabellen-Widgets (Stand 20.08.2026)

| Mannschaft | Wettbewerb | Widget-ID (`data-id`) |
|---|---|---|
| Herren | KLA Frankfurt Gr.1 | `3cd98588-f2bd-4716-a6f8-c663afcd9a52` |
| A-Jugend | AJGL Frankfurt | *fehlt – siehe unten* |
| D1 | DJKL F Gr 1 | `1e9caaff-1559-444a-ad35-bb60f75e5dca` |
| D2 | DJ KK F Gr. 05 | `a0e8a901-ccc9-4810-9fe4-c16f8dc6ca86` |
| D3 | DJ KK F Gr. 04 | `f861a1b4-7b87-4b44-ae96-b217b8ba1758` |
| E1 | EJ Quali Gr. 08 | `210e4f4f-cd22-4771-ae3b-423b6061afe1` |
| E2 | EJ Quali Gr. 18 | `47a2d17f-97d4-45ab-87cd-cb6faf6e0d93` |
| E3 | EJ Quali Gr. 22 | `8a18ef30-e4ac-4936-8049-18dfd14d08f4` |

**A-Jugend offen:** Das Widget für die Gruppenliga (A-Junioren, Region Frankfurt, AJGL Frankfurt)
lässt sich derzeit nicht anlegen – FUSSBALL.DE antwortet mit „Es ist ein unerwarteter Fehler
aufgetreten. Bitte später noch einmal versuchen." Die Auswahl selbst funktioniert, es scheitert
erst beim Speichern. In ein paar Tagen erneut versuchen; alle anderen sieben Widgets sind da.

---

## Stand 20.08.2026, 16:30: Spielplan-Widgets von FUSSBALL.DE

Der Wunsch war der Look des DOSB-Beispiels (`Beispiel-Fussball-Widget.html`, dort
`data-type="competition"`). Umgesetzt ist die Variante, die pro Mannschaft passt:
**„Letzte/Nächste Spiele Mannschaft"** (`data-type="team-matches"`, je 10 Spiele) – mit Wappen,
Ergebnis, Pokal- und Freundschaftsspielen, live von FUSSBALL.DE, ohne jede Pflege.

Zusätzlich gibt es ein Vereins-Widget über alle Mannschaften
(`data-type="club-matches"`, 10 Spiele): `49e9745b-dc1c-4ae2-9292-b06c0f7a6c6e`
(Akzentfarbe auf Vereinsblau `#0b3c78` gestellt).

| Mannschaft | Widget-ID (`data-id`), `data-type="team-matches"` |
|---|---|
| Herren | `0bc2cd05-999e-4098-8f5a-01faac49eb40` |
| A-Jugend | `7f1d494c-ad2d-47d0-bb61-f4f0106ac2c4` |
| D1 | `97c0160c-4b7a-40e1-ac86-3c8a4b3c6f76` |
| D2 | `1e1dcbe7-34a6-4727-9744-68096138a415` |
| D3 | `81d9f5d7-9465-4aa5-ba6e-9830577ec0e6` |
| E1 | `946f72f6-9ef2-4199-8b9d-6051948a2103` |
| E2 | `52cf66bf-3feb-436d-80fa-831c186dbb64` |
| E3 | `15839ff7-ac56-422d-9853-b240229822ec` |

Einbindungs-Domain aller Widgets: `cdn.appack.de`. Das A-Jugend-Widget liess sich beim zweiten
Anlauf ohne Fehler anlegen – der Serverfehler von vormittags war also nur temporär. Das
A-Jugend-**Tabellen**-Widget fehlt weiterhin.

### Seitenstruktur in der App (geändert)

Herren und A-Jugend stehen jetzt auf eigenen Seiten – gemeinsam auf einer Seite passte nicht.
Jede Seite ist gleich aufgebaut: oben das FUSSBALL.DE-Widget, darunter „Ganze Saison" aus DFBnet.

| App-Seite (appack Seiten-Workspace) | Datei im Repo | Inhalt |
|---|---|---|
| `Spielplan-Herren.html` | `docs/app-herren.html` | Herren, 30 Spiele |
| `Spielplan-A-Jugend.html` | `docs/app-a-jugend.html` | A-Jugend, 26 Spiele |
| `Spielplan-D-Jugend.html` | `docs/app-d-jugend.html` | Tabs D1/D2/D3, 64 Spiele |
| `Spielplan-E-Jugend.html` | `docs/app-e-jugend.html` | Tabs E1/E2/E3, 14 Spiele |

`Spielplan-Aktive.html` wurde in `Spielplan-Herren.html` umbenannt; `app-aktive.html` gibt es
nicht mehr. Beim Tab-Wechsel wird das jeweils passende Widget ein- und die anderen ausgeblendet
(`.wbox` / `.wbox.on`), die Widgets laden alle beim Seitenaufruf.

### Liste `Spieltag_Tabelle_db` (Modul „Übersicht aller Spiele")

Vorhanden: Spielplan Herren, Spielplan A-Jugend, Spielplan D-Jugend, Spielplan E-Jugend,
Tabellen D-Jugend, Vereinsspielplan.

Noch offen (CMS-Session war abgelaufen):

1. „Tabellen D-Jugend" umbenennen in „Tabellen" / Untertitel „Alle Mannschaften" – die Seite
   zeigt längst alle sieben Tabellen.
2. „Vereinsspielplan" zeigt noch auf `Beispiel-Fussball-Widget.html` (fremde E-Junioren-Staffel
   aus dem DOSB-Beispiel!). Neue Seite mit dem Vereins-Widget anlegen und dort verlinken:
   ```html
   <div class="fussballde_widget" data-id="49e9745b-dc1c-4ae2-9292-b06c0f7a6c6e"
        data-type="club-matches" style="width:100%"></div>
   ```
3. Reihenfolge sortieren: Vereinsspielplan, Herren, A-Jugend, D-Jugend, E-Jugend, Tabellen.
4. `Spielplan D-Jugend` Untertitel „D1, D2, D3" nachtragen.

### C-Junioren!

Die Mannschaftsauswahl auf FUSSBALL.DE zeigt für den Verein auch
**C-Junioren I** (`011MIE7DM4000000VTVG0001VTR8C1K7`) und
**C-Junioren II** (`02TH4OBKUG000000VS5489BRVU522QNF`) sowie
F-Junioren I/II, G-Junioren I und eine E-Junioren (FS). Die C-Jugend war in der Aufzählung
bisher nicht dabei – bitte prüfen, ob sie in die App gehört.

### Speichern im appack-Quellcode-Editor

Der Editor speichert nur, wenn vorher wirklich getippt wurde. Wer den Inhalt per Skript
einsetzt, muss danach ein Zeichen tippen und dann `cmd+s` drücken – sonst meldet die Oberfläche
teils „Speichern erfolgreich", die Datei auf `cdn.appack.de` bleibt aber alt. Kontrolle:
Seite auf `https://cdn.appack.de/sportfreunde04/workspace/<Datei>.html` neu laden.

---

## Staffelwechsel: E-Jugend Quali → Hauptrunde (und jeder Auf-/Abstieg)

Die E-Jugend spielt zuerst eine Qualifikationsrunde und wird danach einer Liga zugeordnet –
eine **neue Staffel mit neuer Kennung**. Dasselbe passiert bei jedem Auf- und Abstieg und zu
jedem Saisonwechsel. Die drei Bausteine verhalten sich dabei unterschiedlich:

| Baustein | Gebunden an | Verhalten beim Staffelwechsel |
|---|---|---|
| FUSSBALL.DE-Widget oben auf der Seite | die **Mannschaft** (team-id) | zieht die neuen Spiele automatisch, **null Pflege** |
| Eigene Saisonliste + ICS-Kalender | **Staffel + Spielnummer** | neue Staffel = Zeilen nachtragen, rein additiv |
| Tabellen-Widget | die **Staffel** | zeigt still weiter die **alte, beendete Tabelle** – muss ersetzt werden |

Das Widget ist also genau für die E-Jugend das stärkste Stück: sobald der Kreis die Hauptrunde
ansetzt, stehen die Spiele oben auf der Seite, ohne dass jemand etwas tut. Nur die
Vollständigkeit der Saisonliste und die Tabelle brauchen einen Handgriff.

### Checkliste, sobald die Hauptrunde angesetzt ist

1. **DFBnet** → Ergebnismeldung → Staffeln, neue Staffelkennung je E-Mannschaft notieren,
   Spieltag „Alle auswählen", Spielplan exportieren.
2. **`spiele.csv`**: neue Zeilen anhängen (Team-Kürzel bleibt `E1`/`E2`/`E3`, nur `staffel`
   ist neu). Die Quali-Spiele bleiben stehen – sie sind Historie und stören nicht.
3. **`build_ics.py`** → `RUNDE`: die neuen Kennungen mit Label eintragen, z. B.
   `"342xxx": "Hauptrunde · Kreisklasse B Gr. 3"`. Die App-Seite setzt dann automatisch eine
   Zwischenüberschrift und trennt Quali von Hauptrunde.
4. `python3 build_ics.py` – fertig für Kalender **und** App-Seite.
5. **CMS**: `docs/app-e-jugend.html` in `Spielplan-E-Jugend.html` einsetzen (Quellcode
   editieren, ein Zeichen tippen, `cmd+s`, danach auf `cdn.appack.de` gegenprüfen).
6. **FUSSBALL.DE**: drei neue **Tabellen**-Widgets für die neuen Staffeln anlegen
   (Meine Widgets → Tabelle → Saison 26/27 → Hessen → Meisterschaften → E-Junioren → …),
   Domain `cdn.appack.de`, und die `data-id` in `Tabellen-D-Jugend.html` austauschen.
   Die alten Quali-Widgets können bleiben oder gelöscht werden.
7. Die ICS-Abos der Eltern brauchen **nichts**: Die UID ist `<staffel>-<spielnr>`, die neuen
   Spiele kommen als zusätzliche Termine an, nichts wird überschrieben, niemand muss neu
   abonnieren.

Aufwand insgesamt: rund 20–30 Minuten für alle drei E-Mannschaften.

### Warum es keine automatische Variante gibt

Es gibt bei FUSSBALL.DE kein Widget „Tabelle der aktuellen Liga meiner Mannschaft" – Tabellen
sind immer an eine Staffel gebunden. Und DFBnet hat keine offene Schnittstelle, aus der sich
der Spielplan skriptgesteuert ziehen liesse; der Export läuft über die angemeldete Sitzung.
Deshalb bleibt der Spielplan-Nachtrag Handarbeit – dafür ist er additiv und dank stabiler UID
ohne Nebenwirkungen.

---

## Stand 20.08.2026, 17:30 – im CMS erledigt

Alle fünf Seiten sind live auf `cdn.appack.de` und geprüft (Länge per XHR gegengelesen):
`Spiele-Alle-Mannschaften.html` (1,9 KB), `Spielplan-Herren.html` (6,4 KB),
`Spielplan-A-Jugend.html` (6,2 KB), `Spielplan-D-Jugend.html` (9,8 KB),
`Spielplan-E-Jugend.html` (6,6 KB). Die Staffel-Gruppierung ist in allen vier Spielplan-Seiten
eingebaut; bei der E-Jugend steht über der Liste „Qualifikationsrunde".

Reihenfolge im Modul „Übersicht aller Spiele": Alle Mannschaften, Spielplan Herren,
Spielplan A-Jugend, Spielplan D-Jugend, Spielplan E-Jugend, Tabellen.

**Zwei Eigenheiten des appack-Editors**, die beim Nachtragen Zeit sparen:

1. Klick-Koordinaten der Browser-Automatisierung sind Screenshot-Koordinaten, nicht CSS-Pixel
   (hier Faktor 1,064).
2. Inhalt per Skript einsetzen setzt das Dirty-Flag nicht. Erst ein echtes Zeichen tippen,
   dann auf das Disketten-Icon im Editor-Tab klicken – `cmd+s` allein greift nicht immer.
   Danach die CDN-URL neu laden und die Dateigröße prüfen.

**C-Junioren:** existieren in DFBnet, spielen aber nicht – bleiben draußen.

---

## Stand 20.08.2026, 19:00 – Phase 1 (Eltern-Plan) abgeschlossen

### Datenschutzerklärung: FUSSBALL.DE-Absatz ist drin

`Datenschutzerklaerung.html` im CMS enthält jetzt als **Abschnitt 11** den Absatz
„Einbindung von Inhalten Dritter – FUSSBALL.DE-Widgets": Anbieter (DFB GmbH & Co. KG,
DFB-Campus, Kennedyallee 274, 60528 Frankfurt am Main), welche Daten beim Aufruf an
FUSSBALL.DE gehen (IP-Adresse, Geräte-/Browserdaten, Zeitpunkt, aufgerufene Seite),
Hinweis auf Cookies, Rechtsgrundlage Art. 6 Abs. 1 lit. f DSGVO, Verweis auf
`www.fussball.de/privacy` und der Hinweis, dass die Verbindung nur beim Öffnen der
Spielplan-Seiten entsteht.

> **Nicht juristisch geprüft.** Der Text ist fachlich sauber formuliert, aber vor dem
> Live-Gang von jemandem mit Datenschutz-Mandat zu lesen – idealerweise von derselben
> Stelle, die den Rest der Erklärung erstellt hat (Generator „Mönchengladbach externer
> Datenschutzbeauftragter" / WBS-LAW, siehe Fußtext der Seite).

### Zwei alte Fehler in derselben Datei mitbehoben

1. **Umlaut-Mojibake.** Die Datei hatte keine Zeichensatz-Angabe, deshalb stand in der
   Vorschau „DatenschutzerklÃ¤rung". Alle 517 Nicht-ASCII-Zeichen **unterhalb des
   `<style>`-Blocks** sind jetzt HTML-Entities (`&auml;`, `&bdquo;` …). Damit ist die
   Anzeige unabhängig davon, mit welchem Zeichensatz die App die Seite liest. Das eine
   „ü" **innerhalb** des `<style>`-Blocks wurde bewusst nicht angefasst – in CSS werden
   Entities nicht aufgelöst.
2. **42 kaputte Absatz-Tags.** Aus einem alten Word-Import stammten 21 Absätze der Form
   `<p·style="margin-left: 34.0pt;">…</p·style="margin-left:>` mit einem geschützten
   Leerzeichen im Tag. Der Browser hat daraus unbekannte Inline-Elemente gemacht, die
   Absätze liefen ineinander. Jetzt saubere `<p style="…">…</p>` – Abschnitt 6 („Ihre
   Rechte als betroffene Person") ist dadurch erstmals richtig gegliedert.

Kontrolliert wurde per Textvergleich: der sichtbare Text der Seite ist zeichengleich der
alte, plus genau die 1.386 Zeichen des neuen Abschnitts 11. Datei 48.626 Bytes, auf
`cdn.appack.de` gegengeprüft.

### Kalender-Abo (1.3): bleibt offen, und zwar aus einem Grund

Es fehlt ein Ort, der die `.ics`-Dateien mit `Content-Type: text/calendar` ausliefert.
Geprüft und ausgeschlossen:

- **appack-Mediathek** nimmt nur Bild, Video, Audio und PDF – kein `.ics`.
- **appack-Seiten** liefern immer `text/html`, deshalb der alte Fehler
  „Expected [BEGIN], read [<?xml version]".
- **FUSSBALL.DE** hat keinen abonnierbaren Feed. Der „Matchkalender" ist eine
  Umgebungssuche nach Spielen, kein iCal-Export; an der Mannschaftsseite hängt kein
  `.ics`-Link.

Bleibt also GitHub Pages (oder ein anderer Webspace) – Schritt 1.3 aus dem Eltern-Plan,
und der einzige Punkt in Phase 1, der ohne Olgays Zugang nicht geht.

Der leere Testkalender heißt jetzt **„Spielplan-Feed (inaktiv, wartet auf iCal-URL)"**,
das Abonnement ist abgeschaltet. Damit läuft der tägliche Sync-Fehler nicht mehr auf, und
sobald die Feed-URL existiert, wird in diesem Kalender nur das Häkchen „Einen Kalender von
einer externen Quelle abonnieren (iCal)" gesetzt und die URL eingetragen.

### Nützlich für später: appack-Kalender geben selbst einen iCal-Feed aus

Im Kalender-Menü („…") steht **„ICal-Feed kopieren"**. Alles, was einmal als echter
App-Termin existiert, können Eltern also direkt aus der App heraus im Handy-Kalender
abonnieren – ohne GitHub. Für Phase 2 (Zu-/Absage über Anmeldeformulare) ist das der
interessantere Weg: echte Termine in der App, Feed zum Abonnieren obendrauf.

---

## Stand 21.08.2026 – Kalender-Abo ist live

### Wo alles liegt

| Was | Wo |
|---|---|
| Repository | `github.com/justOlgay/speuzer-spielplan` (öffentlich) |
| Abo-Seite für Eltern | `https://justolgay.github.io/speuzer-spielplan/` |
| Kalender je Mannschaft | `…/herren.ics`, `a.ics`, `d1.ics` … `e3.ics` |
| Alle Mannschaften | `…/alle.ics` (134 Spiele) |
| **Nur Jugend (D + E)** | `…/jugend.ics` (78 Spiele) – **das steckt in der App** |

GitHub Pages liefert `.ics` mit `Content-Type: text/calendar` – geprüft, appack akzeptiert es.
Pages-Quelle: Branch `main`, Ordner `/docs`. Actions-Rechte stehen auf **Read and write**,
damit der Workflow die neu gebauten Dateien zurückschreiben darf.

### Warum in der App nur die Jugend hängt

Der **Vereinskalender** in der App enthielt bereits die Spiele von **Herren und A-Junioren**
(Format „Heim – Gast (Mannschaft)", 1:45 Dauer, gepflegt von jemand anderem). Ein Abo auf
`alle.ics` hätte diese Spiele doppelt angezeigt. Deshalb:

- **Vereinskalender** → bleibt zuständig für Herren und A-Junioren.
- **„Spielplan Jugend (D & E)"** → Abo auf `jugend.ics`, deckt D1–D3 und E1–E3 ab, also genau
  das, was dort noch fehlte. 78 Termine, keine Doppelten.

Wer später zusammenlegen will, hat zwei Wege: die Spiele im Vereinskalender löschen und das
Abo auf `alle.ics` umstellen – oder es so lassen. Kein technischer Zwang.

### Kalender in der App (appack)

- Name: **Spielplan Jugend (D & E)**, Farbe `1E4DD8`, Lesezugriff unbegrenzt,
  Schreibzugriff niemand (es ist ein Abo).
- appack legt aus `CATEGORIES` automatisch die Kategorie **Fussball** an – Eltern können damit
  in der App filtern.
- Das Modul **Termine** hat eine eigene Kalender-Auswahl
  (*Einstellungen → Kalender*). Ein neuer Kalender ist dort **nicht automatisch aktiv** –
  Haken setzen, sonst tauchen die Termine nirgends auf. Ebenfalls dort: *Zeitraum → Events
  anzeigen bis Ende*; das stand auf „Dieses Jahr" und zeigte deshalb keine Rückrunde. Jetzt
  **„Nächstes Jahr: 01.01.26 – 31.12.27"**.
- Der Sync läuft von selbst (beobachtet: 07:42 und 07:54 Uhr) und lässt sich über
  „…" → *Kalender synchronisieren* sofort auslösen. Achtung: die Bestätigung sagt, dass beim
  Synchronisieren **alle bestehenden Termine des Kalenders gelöscht** werden – bei einem
  Abo-Kalender ist das harmlos, in einem selbst gepflegten Kalender nicht.

### Zeitabweichung, die geprüft werden muss

A-Jugend am **22.08.2026**: unser DFBnet-Export sagt **15:00**, der Vereinskalender sagt
**16:00**. Eine der beiden Quellen ist veraltet. FUSSBALL.DE bzw. DFBnet entscheidet – und
danach die Zeile in `spiele.csv` oder der Termin im Vereinskalender korrigieren.

### Was der Workflow tut

`.github/workflows/build.yml` läuft täglich um 04:12 UTC und bei jeder Änderung an
`spiele.csv` oder `build_ics.py`, baut alle `.ics`-Dateien neu und committet sie. Neue Spiele
nachtragen heißt also: **eine Zeile in `spiele.csv` – fertig.** Die UID bleibt
`<staffel>-<spielnummer>`, deshalb ändert sich ein verlegtes Spiel bei den Eltern im Handy,
statt ein zweites Mal aufzutauchen.

---

## Stand 21.08.2026, 09:30 – eine Quelle, einheitliche Darstellung

### Alle acht Mannschaften kommen jetzt aus dem Feed

Der Kalender in der App heißt **„Spielplan (alle Mannschaften)"** und abonniert `alle.ics`
(134 Spiele, Herren bis E3). Der **Vereinskalender** ist im Modul *Termine* **abgewählt** –
er enthielt 23 von Hand eingetragene Spiele von Herren und A-Junioren (nur Hinrunde, letzter
Eintrag 06.12.), die jetzt doppelt gewesen wären. **Gelöscht wurde nichts**: der Kalender und
seine Einträge liegen unverändert im CMS, sie werden nur nicht mehr angezeigt. Wer ihn
loswerden will, löscht ihn unter *Kalender* → „…" → *Löschen*; sinnvoller ist, ihn für echte
Vereinstermine zu verwenden (Jahreshauptversammlung, Sommerfest, Weihnachtsfeier) – dafür ist
ein „Vereinskalender" gedacht.

Nebeneffekt: die falsche Uhrzeit der A-Jugend am 22.08. (16:00 statt 15:00) ist damit weg.
Solange Termine an zwei Stellen gepflegt werden, entstehen solche Abweichungen immer wieder –
jetzt gibt es nur noch eine Quelle, und die ist DFBnet.

### Einheitliche Termindarstellung

Titel, für alle Mannschaften gleich aufgebaut:

```
Speuzer D2 · Heim gegen VFR Bockenheim 2
Speuzer A-Jugend · Auswärts bei FV Bad Vilbel 1
Speuzer Herren · Heim gegen SG Praunheim 1908 · 2:1      (wenn ein Ergebnis in der CSV steht)
```

Mannschaft zuerst, weil das der Teil ist, der auf dem Handy in der Monatsansicht übrig bleibt.
Danach Heim/Auswärts – die Frage, die Eltern als Nächstes haben – dann der Gegner.
Unsere Teams heißen überall **„Speuzer <Mannschaft>"**, in den Kalendernamen, in den Titeln
und in der Beschreibung.

Die Beschreibung hat in jedem Termin dieselben Zeilen:

```
Speuzer D2 – VFR Bockenheim 2
Mannschaft: Speuzer D2
Wettbewerb: 1. Kreisklasse · DJ KK F Gr. 05 · Kreis Frankfurt
Runde: Qualifikationsrunde                      (nur wenn es eine gibt, siehe E-Jugend)
Heimspiel · Spielnummer 4 · Staffel 341874
Spielstätte: Sportplatz Mainzer Landstrasse, Frankfurt am Main
Alle Infos zum Spiel: https://www.fussball.de/spiel/...
```

Zusätzlich trägt jeder Kalender ein `X-WR-CALDESC` mit einer Kurzbeschreibung, und jeder Termin
die Kategorie `Fussball` – appack legt daraus automatisch eine Kategorie an, nach der Eltern in
der App filtern können.

Auf den App-Seiten steht statt des kryptischen „H" / „A" jetzt ein Chip **HEIM** (gefüllt) bzw.
**AUSW.** (outline), und über der Liste steht „Speuzer E1 · 1. Kreisklasse · EJ Quali Gr. 08".

### E-Jugend-Quali: was der Kalender daraus macht

Die E-Jugend spielt zuerst eine Qualifikationsrunde, danach ordnet der Kreis die Mannschaften
einer Liga zu. Für die Kalender heißt das:

- Jeder Quali-Termin trägt in der Beschreibung die Zeile **„Runde: Qualifikationsrunde"** –
  das steuert der `RUNDE`-Eintrag je Staffelkennung in `build_ics.py`.
- Die Kalender der E-Mannschaften und die Sammelkalender (`alle.ics`, `jugend.ics`) haben in
  `X-WR-CALDESC` den Hinweis, dass zurzeit nur die Quali feststeht und die Hauptrunde
  automatisch dazukommt.
- Auf der Abo-Seite steht derselbe Hinweis als hervorgehobener Kasten unter E1, E2, E3 und den
  Sammelkalendern – damit niemand denkt, der Kalender sei kaputt, weil nur fünf Spiele drin sind.
- Wenn die Hauptrunde angesetzt ist: neue Zeilen in `spiele.csv`, neue Staffelkennung in `RUNDE`
  mit Label eintragen (z. B. `"342xxx": "Hauptrunde · Kreisklasse B Gr. 3"`), pushen. Actions
  baut, Pages veröffentlicht, appack synchronisiert. **Die Quali-Spiele bleiben stehen** (andere
  Staffel = andere UID), es entstehen keine Doppelten, und niemand muss neu abonnieren.

### App-Seiten im CMS: Stand 21.08.2026

Alle vier Spielplan-Seiten sind auf die neue Optik gebracht und auf `cdn.appack.de`
gegengeprüft: `Spielplan-Herren.html` (6.686 B), `Spielplan-A-Jugend.html` (6.558 B),
`Spielplan-D-Jugend.html` (10.223 B), `Spielplan-E-Jugend.html` (6.830 B). Sie enthalten
HEIM-/AUSW.-Chips, „Speuzer <Mannschaft>" in der Kopfzeile und richtige Umlaute.

**Kniff, der viel Zeit spart:** Der CMS-Editor kann die Datei direkt von GitHub Pages holen –
im Quellcode-Editor
`const neu = await fetch('https://justolgay.github.io/speuzer-spielplan/app-d-jugend.html').then(r=>r.text())`
und per `executeEdits` einsetzen. GitHub Pages schickt `Access-Control-Allow-Origin: *`,
deshalb klappt das aus `cms.appack.de` heraus. Danach ein Zeichen tippen, wieder löschen und
auf das Disketten-Icon im Editor-Tab klicken; das Icon wandert mit der Länge des Dateinamens,
also vorher einen Screenshot machen statt zu raten.

---

## TEAMPUNKT-Pilot D3 (ab 21.08.2026)

Über die TEAMPUNKT-App lässt sich je Mannschaft eine Kalender-Synchronisierung einrichten;
dabei entsteht ein Link `https://teampunkt.dfbnet.org/teamapi/ical/<Token>`. appack akzeptiert
diesen Feed (Content-Type stimmt). Für die **D3** läuft er als Pilot.

**Was drin ist:** 112 künftige Termine – 22 Ligaspiele **und 90 Trainingseinheiten**, mit
echter Adresse (Mainzer Landstraße 480) statt nur Platzname. Gepflegt wird das von DFBnet,
also kommen Verlegungen und die E-Jugend-Hauptrunde später von selbst.

**Damit nichts doppelt erscheint:** `build_ics.py` kennt jetzt `PILOT_TEAMPUNKT = {"D3"}` und
erzeugt zusätzlich **`docs/app-kalender.ics`** – alles außer den Pilotmannschaften
(aktuell 112 Spiele). Der App-Kalender „Spielplan Mannschaften" abonniert diese Datei,
`alle.ics` bleibt vollständig für Eltern, die alles wollen. Pilot beenden heißt: `PILOT_TEAMPUNKT`
leeren, pushen, in appack wieder `alle.ics` eintragen und den Pilotkalender abwählen.

**Kalender in appack:** „Spielplan Mannschaften" (blau `1E4DD8`, 112 Spiele aus dem Generator)
und „Speuzer D3 - Spiele und Training (Pilot)" (grün `1F7757`, TEAMPUNKT). Beide im Modul
*Termine* aktiv. Geprüft: 222 künftige Termine, **0 Doppelte**, D3-Spiele genau einmal.

**Der sichtbare Unterschied – und der Grund, TEAMPUNKT eher als Datenquelle zu nutzen:**

| Quelle | Titel im Kalender |
|---|---|
| Unser Generator | `Speuzer E1 · Heim gegen VfL Germania 1894 1` |
| TEAMPUNKT roh | `VfL Germania 1894 2 - Speuzer D3 (26/27) (D-Junioren)` |
| TEAMPUNKT roh | `Freitag Training` (ohne Mannschaft, ohne Ort im Titel) |

TEAMPUNKT liefert also die besseren **Daten** (automatisch, mit Training und Adresse), unser
Generator die bessere **Darstellung** (Mannschaft zuerst, Heim/Auswärts, Kategorie „Fussball",
Rundenangabe, Link zum Spiel). Der nächste sinnvolle Schritt ist deshalb, die TEAMPUNKT-Feeds
im Workflow einzulesen und durch `build_ics.py` zu normalisieren – dann null Handarbeit **und**
einheitliche Optik. Dafür braucht es die Token-Links aller Mannschaften; die gehören **nicht**
ins öffentliche Repo, sondern in ein GitHub-Actions-Secret.
---

## Zu-/Absage-Pilot D2 - Stand 21.08.2026

Zweiter Pilot, andere Frage: nicht „woher kommen die Daten“, sondern „wie sagen Eltern zu
oder ab“. Das kann ein abonnierter iCal-Kalender grundsätzlich nicht - er ist nur lesbar.
Also braucht es **echte App-Termine**.

**Was appack dafür mitbringt:** eine eingebaute **Teilnahme-Rückmeldung** (Daumen hoch /
Fragezeichen / Daumen runter). Ein Anmeldeformular ist nicht nötig. Sobald sie aktiv ist,
erscheinen zwei weitere Felder: **Gruppeneinladung** (alle Mitglieder der Gruppe werden
automatisch eingeladen und zur Rückmeldung aufgefordert) und **Sichtbarkeit der Feedbackliste**.

**Gebaut:** Kalender „Speuzer D2 - Spieltage (Pilot Zu-/Absage)“, orange `D9822B`,
Schreibzugriff App-Administratoren + Trainer. Darin vier Termine - 23.08., 30.08., 05.09.,
13.09.2026 - mit

* Titel im Feed-Format: `Speuzer D2 · Heim gegen VFR Bockenheim 2` / `... · Auswärts bei ...`
* Untertitel `Treffpunkt 10:30 Uhr · Anstoß 11:30 Uhr · Sportplatz Mainzer Landstraße`
* **Start = Treffpunkt = Anstoß minus 60 Minuten** (Heim und Auswärts gleich), Ende = Anstoß + 2 h
* Ort *FFV Sportfreunde 04, Mainzer Landstraße 480* bei Heimspielen, damit die Navigation in der App funktioniert
* Details-Link auf das Spiel bei FUSSBALL.DE
* Teilnahme-Rückmeldung an, Gruppeneinladung **Mannschaft D2**, Feedbackliste nur für
  Terminverantwortliche, Onlinekonferenz aus

**Damit nichts doppelt steht:** `NICHT_IN_APP_FEED = {"D3", "D2"}` in `build_ics.py`.
`docs/app-kalender.ics` enthält jetzt **90 Spiele** (alles außer D2 und D3), `alle.ics` bleibt
mit 134 Spielen vollständig. Geprüft im Modul *Termine*: jeder D2-Spieltag genau einmal.

**Grenze des Ansatzes:** Verlegungen aus dem DFBnet laufen nur im generierten Feed mit. Ein
selbst angelegter Spieltag muss von Hand nachgezogen werden - deshalb rollierend zwei bis drei
Spieltage im Voraus anlegen, nicht die ganze Saison. Die Kurzanleitung für Trainer liegt als
`Speuzer App - Zu-Absage Anleitung Trainer.pdf` im Vereinsordner.
