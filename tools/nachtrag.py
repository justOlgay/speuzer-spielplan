#!/usr/bin/env python3
"""Spieltabelle fortschreiben: spiele.csv aus den öffentlichen fussball.de-Mannschaftsplänen.

spiele.csv ist die einzige Quelle der Kalender (build_ics.py, GitHub-Aktion, GitHub Pages). Dieses Skript liest die
Mannschaftspläne (ajax.team.matchplan) aus dem Cache des Spielplan-Abgleichs (tools/abgleich.py; den Cache füllt der
tägliche Mac-Task, die G-Jugend ruft das Skript selbst ab), vergleicht sie mit der CSV und schreibt sie fort - aber nur
mit vier eng umrissenen, rückgängig machbaren Änderungen (Whitelist):
  ergänzt            Spiel steht bei fussball.de (angesetzt, Datum ab heute), fehlt in der CSV: neue Zeile
  verlegt            gleiche Staffel + Spielnummer, anderes Datum und/oder andere Uhrzeit: datum/zeit der Zeile ändern
  abgesagt           bei fussball.de abgesetzt, „zg.“ oder Nichtantritt, in der CSV angesetzt: ergebnis = abgesagt
  wieder angesetzt   in der CSV „abgesagt“, bei fussball.de wieder angesetzt: ergebnis leeren
Alles andere (überzählige Zeilen, Heim/Auswärts-, Gegner-, Wettbewerbs- und Nummernabweichungen, doppelte Schlüssel,
Vergangenheit, Zeilen ohne Spielnummer) wird nur gemeldet, nie geändert, nie gelöscht. Ebenso unberührt bleiben vorläufige
Spiele (Symbol „vorläufig“ bei fussball.de) und Spiele mit unklarem Vermerk (Ausfall, Annuliert …). Ebenfalls nur gemeldet: Termine
nach dem Saisonende (30.06.) oder mehr als 400 Tage voraus, Vereinsnamen mit unzulässigen Zeichen (<, >, Steuerzeichen, U+FFFD,
Mojibake) oder Platzhaltern („Sieger Spiel 12“, „N.N.“), eine Schreibweise unseres Vereins, die build_ics.py nicht als Heimspiel
erkennt, und eine Absetzung zu einem anderen Termin als dem der CSV-Zeile. Eine „abgesagt“ der CSV nimmt das Skript nur zurück,
wenn fussball.de das Spiel in einem früheren Cache-Stand selbst als abgesetzt gezeigt hat (sonst könnte eine von Hand eingetragene
Absage über Nacht verschwinden; Schalter WIEDER_ANSETZEN_NUR_MIT_VERLAUF).

Zusatzquellen: Eine Mannschaft kann neben ihrem eigenen Plan weitere fussball.de-Pläne haben (abgleich.ZUSATZQUELLEN). Die
Freundschaftsspiele der fussball.de-Mannschaft D4 stehen in der CSV unter D3; der Plan der D4 wird mit dem der D3 zusammengeführt
(nur aus dem Cache, nie abgerufen). Neue Zeilen tragen das Kürzel der Mannschaft (D3); Mengenbremse, Verlauf und Plausibilität
gelten für die Mannschaft als Ganzes. Fällt der eigene Plan aus, bleiben auch die Zusatzquellen ungenutzt. Fällt nur eine
Zusatzquelle aus (kein, veralteter, unbrauchbarer oder unplausibler Cache, Plan nennt unseren Verein nicht), wird die Mannschaft
nie übersprungen: die Quelle wird mit einem Hinweis weggelassen („ihre Spiele sind in diesem Lauf nicht geprüft“).

Schutzregeln: Mengenbremse (mehr als 10 Änderungen insgesamt oder mehr als 6 je Mannschaft: nichts schreiben); Quelle
muss plausibel sein (sonst Mannschaft überspringen); je Mannschaft zählt der neueste brauchbare Cache-Stand (beschädigte Dateien
und Sperrseiten werden übersprungen, eine beim Abruf erhaltene Sperrseite kommt als *.html.fehler beiseite); ohne kommende
CSV-Zeilen (Sommerpause) sind Cache-Sorgen kein Fehler; ein zweiter Lauf ändert nichts; unveränderte Zeilen bleiben Byte für
Byte gleich (CRLF, BOM, Spalten); Prüfung vor dem Schreiben (CSV neu lesen, build_ics.py in temporärer Kopie, Zahl der
Termine); Git nur für spiele.csv und ohne Gewalt, liegen gebliebene Commits nur, wenn sie von diesem Werkzeug stammen (Betreff,
nur spiele.csv) und die Prüfung bestehen; Not-Aus-Datei.

Rücksicht auf fussball.de: Dieses Skript ruft nur die G-Jugend ab (die übrigen Pläne liegen schon im Cache, auch der Plan der
Zusatzquelle D4: er kommt aus dem Cache des täglichen Mac-Tasks und wird hier nie abgerufen), höchstens einmal je 20 Stunden
über abgleich.hole (erkennbarer User-Agent, nur ajax.team.matchplan, nach einem Fehlschlag kein neuer Versuch binnen
20 Stunden) und nur im Anwendungsmodus. Berichtsmodus, --nur-cache und --heute rufen nie ab.

Aufruf (aus dem Repo-Stamm; unter python3 -I trägt sich das eigene Verzeichnis selbst in sys.path ein):
  python3 -I tools/nachtrag.py [--csv DATEI] [--cache ORDNER] [--heute JJJJ-MM-TT] [--bericht-ordner ORDNER]
                               [--nur-cache] [--max-alter STUNDEN] [--anwenden [--push]]
  ohne Optionen   NUR-BERICHT: nichts geschrieben, nichts abgerufen, kein Git
  --anwenden      schreibt die erlaubten Änderungen in die CSV-Datei (kein Git; der Arbeitsbaum ist danach „schmutzig“)
  --push          nur mit --anwenden: Arbeitsbaum muss sauber sein; pull --rebase, committen, pushen
  --nur-cache     nichts abrufen; im Berichtsmodus ohne Altersgrenze (zum Auswerten und Testen)
  --max-alter     höchstes Alter eines Cache-Standes in Stunden (Standard 30, 0 = keine Grenze): im Anwendungsmodus wird
                  eine ältere Mannschaft übersprungen, im Berichtsmodus nur vermerkt
  --heute         Datum zum Testen; schaltet auch jeden Abruf aus
Not-Aus: Existiert ~/Library/Application Support/speuzer-spielplan-nachtrag/AUS, bleibt es beim Bericht.
Die letzte Ausgabezeile beginnt immer mit „NACHTRAG: “ und nennt genau einen Zustand: unverändert | n geändert |
Nur-Bericht | Auffällig, nicht übernommen | Fehler (bei mehreren steht der schwerste vorn); das gilt auch für einen ungültigen
Aufruf (Fehler – ungültiger Aufruf) und für SIGTERM/SIGINT (Fehler – abgebrochen: das Original wird wiederhergestellt, soweit der
Lauf schon geschrieben und noch nicht committet hat, die Sperre wird freigegeben).
Exit-Code: 0 = in Ordnung (auch „n geändert“ mit Hinweisen), 2 = auffällig (Mannschaft übersprungen, Mengenbremse, Not-Aus mit
offenen Änderungen, nur Hinweise), 1 = Fehler (Schreiben, Git, Prüfung, CSV, ungültiger Aufruf, anderer Lauf aktiv, Abbruch).
Gleichzeitige Läufe: Im Anwendungsmodus hält jeder Lauf eine Sperre (flock auf lauf.lock neben der Not-Aus-Datei, also außerhalb
des Repos); ein zweiter Lauf meldet „Fehler – anderer Lauf aktiv“ und rührt weder CSV noch Git an. Stirbt ein Lauf, gibt das
Betriebssystem die Sperre frei.
"""
import sys

sys.dont_write_bytecode = True  # GANZ OBEN, vor jedem weiteren Import (auch vor abgleich): kein tools/__pycache__, auch ohne -B

import argparse
import glob
import hashlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

try:
    import fcntl  # Sperre gegen gleichzeitige Läufe (nur Unix/macOS)
except ImportError:  # pragma: no cover
    fcntl = None

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:  # unter python3 -I fehlt das Skriptverzeichnis in sys.path
    sys.path.insert(0, HIER)
import abgleich as A  # noqa: E402  (Parser, CSV-Leser, Vergleich, Cache, Mannschaftsliste TEAMS und Zusatzquellen A.quellen())

REPO = os.path.dirname(HIER)
CSV_STANDARD = os.path.join(REPO, 'spiele.csv')
DATEN_ORDNER = (os.path.join(A.HOME, 'Library', 'Application Support', 'speuzer-spielplan-nachtrag')
                if sys.platform == 'darwin' else os.path.join(A.HOME, '.local', 'state', 'speuzer-spielplan-nachtrag'))
NOTAUS_STANDARD = os.path.join(DATEN_ORDNER, 'AUS')
BERICHTE_STANDARD = os.path.join(DATEN_ORDNER, 'berichte')

SPALTEN = ['team', 'staffel', 'spielnr', 'datum', 'zeit', 'heim', 'gast', 'spielstaette', 'ergebnis', 'fbid', 'wettbewerb']
I_TEAM, I_STAFFEL, I_NR, I_DATUM, I_ZEIT, I_HEIM, I_GAST, I_ORT, I_ERG, I_FBID, I_WB = range(11)
ARTEN = ('Meisterschaft', 'Kreispokal', 'Freundschaftsspiel', 'Kinderfestival')  # erlaubte Werte der Spalte wettbewerb

MAX_GESAMT = 10              # Mengenbremse: mehr Änderungen insgesamt in einem Lauf: nichts schreiben
MAX_JE_MANNSCHAFT = 6        # Mengenbremse: mehr Änderungen in einer Mannschaft: nichts schreiben
MIN_ANTEIL = 0.6             # Quelle plausibel: gelesene Spiele mindestens 60 % der CSV-Zeilen der Mannschaft
MIN_EIGENE = 0.8             # Quelle plausibel: so viele Spiele müssen unseren Verein nennen
MAX_ALTER_STUNDEN = 30       # ein Cache-Stand gilt bis zu diesem Alter als frisch
ABRUF_ABSTAND_STUNDEN = 20   # eigener Abruf (G-Jugend) höchstens einmal in dieser Zeit
SELBST_ABRUFEN = ('G1',)     # Mannschaften, deren Plan nicht der tägliche Mac-Task liefert (Zusatzquellen werden nie abgerufen)
ZUSATZ_NICHT_GEPRUEFT = 'ihre Spiele sind in diesem Lauf nicht geprüft'   # Folge einer ausgefallenen Zusatzquelle
ZEIT_VON, ZEIT_BIS = '07:00', '22:00'   # außerhalb ist eine Anstoßzeit unplausibel (z. B. 00:00 als Platzhalter)
USER_AGENT = 'Mozilla/5.0 (compatible; SpeuzerSpielplanNachtrag/1.0; rein lesend, ein Abruf je Tag für die G-Jugend)'
NUR_MELDEN = ('heimgast', 'gegner', 'wettbewerb', 'nummer', 'csv_doppelt')   # Abweichungen, die nie geändert werden
ABGESETZT = ('abgesetzt', 'zurueckgezogen', 'nichtantritt')   # Zustände bei fussball.de, in denen ein Spiel nicht stattfindet
# Eine „abgesagt“ in der CSV nimmt das Skript nur zurück, wenn fussball.de das Spiel früher selbst als abgesetzt gezeigt hat
# (Cache-Stände der letzten Wochen). Sonst könnte eine von Hand eingetragene Absage über Nacht wieder verschwinden, solange
# fussball.de sie noch nicht kennt. False = wörtlich nach PLAN.md: immer zurücknehmen, wenn fussball.de „angesetzt“ zeigt.
WIEDER_ANSETZEN_NUR_MIT_VERLAUF = True
DATUM_TEXT = re.compile(r'\d{2}\.\d{2}\.\d{4}')   # Vermerk bei fussball.de, der nur ein neuer Termin ist
VEREIN = 'FFV Sportfreunde 04'      # wie build_ics.VEREIN: dort ist ein Spiel nur dann Heim, wenn heim so BEGINNT (Groß-/Kleinschreibung zählt)
COMMIT_ANFANG = 'Spieltabelle automatisch aktualisiert'   # Betreff der Commits dieses Werkzeugs (nur solche werden nachgepusht)
MAX_TAGE_VORAUS = 400                # weiter voraus liegende Termine werden nur gemeldet
SPERRDATEI = 'lauf.lock'             # Sperre gegen gleichzeitige Läufe, liegt neben der Not-Aus-Datei (außerhalb des Repos)
# Zeichen, die in einem Namen nichts zu suchen haben: build_ics.py maskiert Namen in der App-Seite (JavaScript-Daten, innerHTML) nicht
NAME_VERBOTEN = re.compile(r'[<>\\\ufffd]')
NAME_KATEGORIEN = ('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp')     # Steuer-, Format-, Zeilentrennzeichen (NUL, U+202E, U+2028 …)
MOJIBAKE = re.compile(r'[ÃÂ][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc\u2013\u2014\u2018-\u201e'
                      r'\u2020-\u2022\u2026\u2030\u2039\u203a\u20ac\u2122]|â€|Ã¢')       # „Ã¤“, „ÃŸ“, „â€““ …
PLATZHALTER = re.compile(r'\b(Sieger|Verlierer|Gewinner|Freilos|spielfrei)\b|(?<![A-Za-z])N\.?\s?N\.?(?![A-Za-z])', re.I)   # „Sieger Spiel 12“, „N.N.“
GIT_SPERREN = ('rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD')
FALLBACK_AUTOR = ('Spielplan-Nachtrag (automatisch)', 'nachtrag@speuzer.invalid')   # nur, wenn Git keine Identität kennt
ZEILEN_MARKEN = ('row-headline', 'row-competition')                       # Spielzeilen einer matchplan-Seite
RAHMEN_MARKEN = ('id-team-matchplan-table', 'fixtures-matches-table')    # Rahmen auch eines Plans ohne Spiele
ART_TEXT = (('ergaenzt', 'ergänzt'), ('verlegt', 'verlegt'), ('abgesagt', 'abgesagt'), ('wieder_angesetzt', 'wieder angesetzt'))


class FormFehler(Exception):
    """Die CSV hat nicht die erwartete Form: dann wird nichts geschrieben."""


class LaufFehler(Exception):
    """Ein Fehler, der den Lauf beendet; der Text kommt in die Schlusszeile."""


# ---------------------------------------------------------------- Optionen

class Optionen:
    """Alle Einstellungen eines Laufs (die Kommandozeile füllt sie, Tests setzen sie direkt)."""

    def __init__(self, **kw):
        self.csv = CSV_STANDARD
        self.cache = A.CACHE_STANDARD
        self.anwenden = False
        self.push = False
        self.heute = None                    # JJJJ-MM-TT
        self.bericht_ordner = None           # Berichtsdatei nur, wenn angegeben (im Anwendungsmodus Standardordner)
        self.nur_cache = False
        self.max_alter = MAX_ALTER_STUNDEN
        self.notaus_datei = NOTAUS_STANDARD
        self.sperrdatei = None               # Standard: lauf.lock neben der Not-Aus-Datei
        self.abrufen = True                  # False verbietet jeden Abruf (die Kommandozeile setzt es mit --heute)
        self.url_vorlage = A.URL
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError('unbekannte Option: ' + k)
            setattr(self, k, v)


# ---------------------------------------------------------------- Kleine Hilfen

def tag_kurz(d_iso):
    d = date.fromisoformat(d_iso)
    return '%s %s' % (A.WOCHENTAG[d.weekday()], d.strftime('%d.%m.'))


def datum_csv(d_iso):
    return date.fromisoformat(d_iso).strftime('%d.%m.%Y')


def zeit_plausibel(z):
    return bool(re.fullmatch(r'\d{2}:\d{2}', z or '')) and ZEIT_VON <= z <= ZEIT_BIS


def zeichenfehler(t):
    """Grund, warum ein Feld nicht in die CSV darf, sonst None: Trenner, Anführungszeichen, Zeilenumbruch, Randleerzeichen,
    „<“, „>“, Backslash, U+FFFD, Steuer- und Formatzeichen (NUL, U+202E …), Mojibake. build_ics.py maskiert Namen in der
    App-Seite nicht (JavaScript-Daten und innerHTML): ein „</script>“ im Namen würde die öffentliche Seite zerstören."""
    if not isinstance(t, str):
        return 'keinen Text'
    if t != t.strip():
        return 'Leerzeichen am Rand'
    if re.search(r'[;"\r\n]', t):
        return 'ein Trennzeichen, Anführungszeichen oder Zeilenumbruch'
    if NAME_VERBOTEN.search(t):
        return 'ein unzulässiges Zeichen (<, >, Backslash oder U+FFFD)'
    if any(unicodedata.category(c) in NAME_KATEGORIEN for c in t):
        return 'ein Steuer- oder Formatzeichen'
    if MOJIBAKE.search(t):
        return 'zerstörte Umlaute (Mojibake)'
    return None


def feld_sauber(t):
    return zeichenfehler(t) is None


def sichtbar(t, n=60):
    """Text für Meldungen: Steuer- und Formatzeichen (NUL, U+202E …) als Escape, damit sie nichts im Bericht verstecken."""
    return repr(t)[1:-1][:n]


def anzeige(t):
    """Fremde Texte in Ausgabe und Bericht (Vereinsnamen, Vermerke, Git-Meldungen): Steuer- und Formatzeichen als Escape, damit
    weder Terminal noch Bericht verwirrt oder Text umgeordnet wird (NUL, ESC, U+202E …); alles andere bleibt, wie es ist."""
    return ''.join(c.encode('unicode_escape').decode('ascii') if unicodedata.category(c) in NAME_KATEGORIEN else c for c in str(t))


def namen_problem(g):
    """Grund, warum die Vereinsnamen eines fussball.de-Spiels nicht verwertet werden (Zeichen, Platzhalter), sonst None."""
    for n in (g['heim'], g['gast']):
        grund = zeichenfehler(n)
        if grund and grund != 'Leerzeichen am Rand':
            return 'Vereinsname „%s“ enthält %s' % (sichtbar(n), grund)
        if PLATZHALTER.search(n):
            return 'Platzhalter als Gegner („%s“)' % sichtbar(n)
    return None


def paarung_text(art, ha, gegner):
    """Wie build_ics.titel(): Kinderfestival bei uns/bei X, sonst Heim gegen / Auswärts bei, mit Wettbewerbsvorsatz."""
    if art == 'Kinderfestival':
        return 'Kinderfestival bei uns' if ha == 'H' else 'Kinderfestival bei %s' % gegner
    vor = {'Kreispokal': 'Pokal, ', 'Freundschaftsspiel': 'Freundschaftsspiel, '}.get(art, '')
    return '%s%s %s' % (vor, 'Heim gegen' if ha == 'H' else 'Auswärts bei', gegner)


def anzahl(n, einzahl, mehrzahl):
    return '%d %s' % (n, einzahl if n == 1 else mehrzahl)


def schwaerzen(t):
    """Zugangsdaten in Git-Meldungen (https://user:token@host) unkenntlich machen."""
    return re.sub(r'(://)[^@/\s]+@', r'\1***@', t or '')


def sha(daten):
    return hashlib.sha256(daten).hexdigest()


# ---------------------------------------------------------------- spiele.csv Byte für Byte

class CsvDatei:
    """Die spiele.csv als Zeilenliste. Zeilen bleiben unangetastet, solange niemand sie ersetzt; Kopfzeile, BOM und
    Zeilenende (CRLF) werden erkannt und beim Schreiben wieder verwendet. Weicht die Form ab, wird nichts geschrieben."""

    def __init__(self, daten):
        self.original = daten
        self.bom = b'\xef\xbb\xbf' if daten.startswith(b'\xef\xbb\xbf') else b''
        try:
            text = daten[len(self.bom):].decode('utf-8')
        except UnicodeDecodeError as e:
            raise FormFehler('keine gültige UTF-8-Datei (%s)' % e)
        if '"' in text:
            raise FormFehler('Anführungszeichen in der Datei (unerwartete Form)')
        n_crlf, n_lf, n_cr = text.count('\r\n'), text.count('\n'), text.count('\r')
        if n_crlf:
            if n_lf != n_crlf or n_cr != n_crlf:
                raise FormFehler('Zeilenenden uneinheitlich (CRLF gemischt mit LF oder CR)')
            self.eol = '\r\n'
        elif n_cr:
            raise FormFehler('Zeilenenden nur CR (unerwartete Form)')
        else:
            self.eol = '\n' if n_lf else '\r\n'
        self.endet_mit_eol = text.endswith(self.eol)
        zeilen = text.split(self.eol)
        if self.endet_mit_eol:
            zeilen.pop()
        if not zeilen or zeilen[0].split(';') != SPALTEN:
            raise FormFehler('Kopfzeile weicht ab (erwartet: %s)' % ';'.join(SPALTEN))
        for i, z in enumerate(zeilen[1:], start=2):
            if z.count(';') + 1 != len(SPALTEN):
                raise FormFehler('Zeile %d hat %d statt %d Feldern' % (i, z.count(';') + 1, len(SPALTEN)))
        self.zeilen = zeilen

    def felder(self, i):
        return self.zeilen[i].split(';')

    def zeilen_felder(self):
        return [self.felder(i) for i in range(1, len(self.zeilen))]

    def bytes_aus(self, zeilen):
        return self.bom + (self.eol.join(zeilen) + (self.eol if self.endet_mit_eol else '')).encode('utf-8')


# ---------------------------------------------------------------- fussball.de-Seite lesen

def spiel_marken(s):
    """Je Spiel (Reihenfolge und Zeilenlogik wie abgleich.parse_spielplan, genau ein Eintrag je Spiel): (Spiel-ID, vorläufig).
    Die Spiel-ID (fbid) ist leer, wenn die Zeile keinen Spiel-Link hat (z. B. beim Kinderfestival). „Vorläufig“: eine der
    Zeilen des Spiels trägt das Symbol icon-pre-publish (laut Legende: noch nicht vom Staffelleiter freigegeben)."""
    marken, offen, vorlaeufig = [], False, False
    for z in re.findall(r'<tr(?: [^>]*)?>.*?</tr>', s, re.S):
        kl = re.match(r'<tr([^>]*)>', z).group(1)
        if 'row-headline' in kl:
            vorlaeufig = 'icon-pre-publish' in z
        elif 'row-competition' in kl:
            offen, vorlaeufig = True, vorlaeufig or 'icon-pre-publish' in z
        elif offen and 'column-club' in z:
            m = re.search(r'/spiel/[^"/]*/-/spiel/([0-9A-Za-z]{16,40})"', z)
            marken.append((m.group(1) if m else '', vorlaeufig or 'icon-pre-publish' in z))
            offen, vorlaeufig = False, False
    return marken


def seite_defekt(text):
    """Grund, warum `text` kein Mannschaftsplan von fussball.de sein kann (zu kurz, Sperr- oder Fehlerseite), sonst None. Ein
    gültiger Plan ohne Spiele (Sommerpause) ist kein Defekt."""
    if len(text or '') < 500:
        return 'Seite zu kurz (%d Zeichen)' % len(text or '')
    if not any(m in text for m in ZEILEN_MARKEN + RAHMEN_MARKEN):
        return 'kein Spielplan erkannt (Seitenaufbau geändert oder Sperrseite)'
    return None


def seite_lesen(text):
    """-> (Spiele als abgleich-Dicts mit zusätzlichen Feldern fbid und vorlaeufig, Fehlertext oder None)."""
    grund = seite_defekt(text)
    if grund:
        return [], grund
    if not any(m in text for m in ZEILEN_MARKEN):
        return [], None           # ein gültiger, aber leerer Spielplan (z. B. Sommerpause)
    try:
        roh = A.parse_spielplan(text)
        marken = spiel_marken(text)
        if len(marken) != len(roh):
            return [], 'Seitenaufbau unerwartet (%d Spiele, %d Spiel-Markierungen)' % (len(roh), len(marken))
        fb = []
        for g, (fbid, vorlaeufig) in zip(roh, marken):
            f = A.fb_normalisieren(g)
            f['fbid'], f['vorlaeufig'] = fbid, vorlaeufig
            fb.append(f)
    except Exception as e:  # eine defekte Seite darf den Lauf nie gefährden
        return [], 'Seite nicht lesbar (%s)' % type(e).__name__
    return fb, None


def quelle_plausibel(fb, csv_zeilen):
    """Fehlertext, wenn die gelesene Seite nicht als Plan dieser Mannschaft taugt, sonst None."""
    n_csv = len(csv_zeilen)
    gelesen = [g for g in fb if g['status'] != 'spielfrei' and g['nr'] is not None]
    if not gelesen and not n_csv:
        return None               # nichts gelesen und nichts zu vergleichen: kein Fehler (z. B. Sommerpause)
    if not gelesen:
        return 'keine Spiele gelesen, die CSV hat %d Zeilen (Seitenaufbau geändert oder Plan leer)' % n_csv
    if n_csv and len(gelesen) < MIN_ANTEIL * n_csv:
        return 'nur %d Spiele gelesen, die CSV hat %d Zeilen (mindestens %d %% nötig)' % (len(gelesen), n_csv, round(MIN_ANTEIL * 100))
    if n_csv >= 3:      # eine Seite, die kaum eine CSV-Zeile kennt, gehört nicht zu dieser Mannschaft (vertauschte Ablage)
        schluessel = {(g['staffel'], g['nr']) for g in fb if g['nr'] is not None}
        gefunden = sum(1 for r in csv_zeilen if (r['staffel'], r['nr']) in schluessel)
        if gefunden < MIN_ANTEIL * n_csv:
            return 'nur %d von %d CSV-Zeilen bei fussball.de wiederzufinden (falsche Mannschaft oder neue Staffelnummern?)' % (gefunden, n_csv)
    eigene = sum(1 for g in gelesen if A.EIGEN in A.norm(g['heim']) or A.EIGEN in A.norm(g['gast']))
    if eigene < MIN_EIGENE * len(gelesen):
        return 'die Seite nennt unseren Verein nur in %d von %d Spielen (falsche Mannschaft?)' % (eigene, len(gelesen))
    return None


def frueher_abgesetzt(cache, slug, key, vor):
    """Hat ein älterer Cache-Stand dieser Mannschaft (älter als `vor`) das Spiel als abgesetzt, zurückgezogen oder als
    Nichtantritt gezeigt? Dann ist eine Rückkehr zu „angesetzt“ eine echte Neuansetzung bei fussball.de."""
    for pfad in sorted(glob.glob(os.path.join(cache, slug, '*.html')), reverse=True):
        try:
            stempel = datetime.strptime(os.path.basename(pfad)[:15], '%Y%m%d-%H%M%S')
        except ValueError:
            continue
        if vor is not None and stempel >= vor:
            continue
        text, grund = cache_datei_lesen(pfad)      # eine beschädigte ältere Datei (kein UTF-8, Sperrseite) wird übersprungen
        if text is None:
            continue
        fb, fehler = seite_lesen(text)
        if not fehler and any((g['staffel'], g['nr']) == key and g['status'] in ABGESETZT for g in fb):
            return True
    return False


# ---------------------------------------------------------------- Konventionen der CSV

class Konventionen:
    """Schreibweisen und Spielstätten aus den bestehenden Zeilen ableiten (nichts erfinden). `tabelle`: je CSV-Zeile
    ein dict(idx, team, ha, gegner, ort, heim, gast); `ausser` lässt eine Zeile weg (Rekonstruktionstest)."""

    def __init__(self, tabelle, ausser=None):
        namen, heim_orte, aus_orte = defaultdict(Counter), defaultdict(Counter), defaultdict(set)
        for z in tabelle:
            if z['idx'] == ausser:
                continue
            for n in (z['heim'], z['gast']):
                if n and A.EIGEN not in A.norm(n):
                    namen[A.norm(n)][n] += 1
            if z['ort']:
                if z['ha'] == 'H':
                    heim_orte[z['team']][z['ort']] += 1
                elif z['ha'] == 'A' and z['gegner']:
                    aus_orte[(z['team'], A.norm(z['gegner']))].add(z['ort'])
        self.namen = {k: v for k, v in ((k, haeufigster(c)) for k, c in namen.items()) if v}
        self.heim = {k: v for k, v in ((k, haeufigster(c)) for k, c in heim_orte.items()) if v}
        self.aus = {k: next(iter(v)) for k, v in aus_orte.items() if len(v) == 1}

    def name(self, n):
        """Vereinsname in der Schreibweise der CSV (DFBnet: „SG Bornheim / GW 2“), sonst wie bei fussball.de."""
        if not n or A.EIGEN in A.norm(n):
            return n
        return self.namen.get(A.norm(n), n)

    def ort(self, team, ha, gegner):
        """Heim: Standard der Mannschaft; Auswärts: Adresse desselben Gegners aus früheren Zeilen; sonst leer."""
        if ha == 'H':
            return self.heim.get(team, '')
        return self.aus.get((team, A.norm(gegner)), '')


def haeufigster(zaehler):
    """Der eindeutig häufigste Wert eines Counters, sonst None (bei Gleichstand wird nichts geraten)."""
    top = zaehler.most_common()
    return top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else None


def tabelle_aus(csv_zeilen, datei):
    return [dict(idx=r['zeile'] - 1, team=r['team'], ha=r['ha'], gegner=r['gegner'], ort=datei.felder(r['zeile'] - 1)[I_ORT],
                 heim=r['heim'], gast=r['gast']) for r in csv_zeilen]


def csv_felder(kuerzel, g, konv):
    """Neue CSV-Zeile (Liste der 11 Felder) aus einem fussball.de-Spiel -> (Felder, None) oder (None, Grund)."""
    if g['art'] == 'Kinderfestival':
        gastgeber = konv.name(A.ohne_zusatz(g['heim']))      # Gastgeber ohne den Zusatz „- Kinderfestival“
        heim = gastgeber
        gast = '' if g['ha'] == 'H' else konv.name(A.sauber(g['gast']))
    else:
        heim, gast = konv.name(g['heim']), konv.name(g['gast'])
    felder = [kuerzel, g['staffel'], str(g['nr']), datum_csv(g['datum']), g['zeit'], heim, gast,
              konv.ort(kuerzel, g['ha'], g['gegner']), '', g['fbid'], g['art']]
    felder = [unicodedata.normalize('NFC', f) for f in felder]     # Umlaute einheitlich (nicht zerlegt) in die CSV
    for f in felder:
        grund = zeichenfehler(f)
        if grund:
            return None, 'ein Feld („%s“) enthält %s' % (sichtbar(f), grund)
    # build_ics.py: Heimspiel nur, wenn heim mit VEREIN BEGINNT (Groß-/Kleinschreibung); sonst würde ein Heimspiel als Auswärtsspiel gebaut
    if felder[I_HEIM].startswith(VEREIN) != (g['ha'] == 'H'):
        return None, 'unser Vereinsname („%s“) weicht von „%s“ ab, build_ics.py würde Heim und Auswärts vertauschen' % (
            sichtbar(felder[I_HEIM] if g['ha'] == 'H' else felder[I_GAST]), VEREIN)
    return felder, None


# ---------------------------------------------------------------- Plan: welche Änderungen sind erlaubt

class Aenderung:
    """Eine geplante Änderung an genau einer CSV-Zeile (art: ergaenzt | verlegt | abgesagt | wieder_angesetzt)."""

    def __init__(self, art, team, name, key, neu, alt=None, idx=None, datum='', zeit='', text='', detail=''):
        self.art, self.team, self.name, self.key = art, team, name, key
        self.neu, self.alt, self.idx = neu, alt, idx          # Felder der Zeile danach/davor, Index in CsvDatei.zeilen
        self.datum, self.zeit = datum, zeit                   # Termin nach der Änderung (ISO, hh:mm)
        self.text, self.detail = text, detail
        self.zeile_neu = None                                 # Zeilennummer in der neuen Datei (nach neue_datei)

    def sortierschluessel(self):
        return (self.datum, self.zeit, self.key)


class Plan:
    def __init__(self):
        self.aenderungen, self.hinweise, self.stumm = [], [], []


def zu_weit(datum, heute, bis):
    """Grund, wenn ein Termin nach dem Saisonende oder mehr als MAX_TAGE_VORAUS Tage voraus liegt (dann nur melden), sonst None."""
    if datum > bis:
        return 'nach dem Saisonende (%s)' % date.fromisoformat(bis).strftime('%d.%m.%Y')
    if (date.fromisoformat(datum) - date.fromisoformat(heute)).days > MAX_TAGE_VORAUS:
        return 'mehr als %d Tage voraus' % MAX_TAGE_VORAUS
    return None


def gleiche_partie(g, r):
    """fussball.de-Spiel g und CSV-Zeile r sind dieselbe Paarung (Heim/Auswärts und Gegner stimmen überein)."""
    if g['ha'] != r['ha'] or '?' in (g['ha'], r['ha']):
        return False
    if g['gegner'] or r['gegner']:
        return A.gegner_gleich(g['gegner'], r['gegner']) != 'anders'
    return True


def plane(team, fb, csv_je, heute, ctx):
    """Erlaubte Änderungen, Hinweise und stille Notizen einer Mannschaft. `ctx`: konv (Konventionen), datei (CsvDatei),
    schluessel (alle (Staffel, Nr) der CSV), neu (in diesem Lauf vergebene Schlüssel), neue_staffeln (schon gemeldet),
    bis (Saisonende, ISO),
    verlauf (Funktion: Schlüssel -> hat fussball.de das Spiel früher als abgesetzt gezeigt?)."""
    kuerzel, name = team[0], team[2]
    p = Plan()
    diffs = A.vergleiche(team, fb, csv_je, heute)[0]

    je_key, spielfrei = defaultdict(list), set()
    for g in fb:
        if g['nr'] is None:
            continue
        if g['status'] == 'spielfrei':
            spielfrei.add((g['staffel'], g['nr']))
        else:
            je_key[(g['staffel'], g['nr'])].append(g)
    fb_je = {k: A.waehle(v) for k, v in je_key.items()}
    mehrdeutig = {k for k, v in je_key.items() if len([g for g in v if g['status'] != 'verlegt']) > 1}
    csv_je_key, doppelt = {}, set()
    for r in csv_je:
        k = (r['staffel'], r['nr'])
        if k in csv_je_key:
            doppelt.add(k)
        csv_je_key[k] = r
    blockiert = doppelt | mehrdeutig      # für diese Schlüssel wird nichts automatisch geändert
    for d in diffs:
        if d['art'] in NUR_MELDEN:
            blockiert.add((d['staffel'], d['nr']))
            if d['csv']:
                blockiert.add((d['csv']['staffel'], d['csv']['nr']))

    def kopf(x):
        return '%s %s%s' % (name, tag_kurz(x['datum']) if x['datum'] else 'ohne Datum', (' ' + x['zeit']) if x['zeit'] else '')

    def paar(x):
        return paarung_text(x['art'], x['ha'], x['gegner'])

    def termin_text(x):
        return '%s %s' % (tag_kurz(x['datum']) if x['datum'] else 'ohne Datum', x['zeit'] or '(ohne Uhrzeit)')

    def hinweis(x, grund):
        p.hinweise.append('%s · %s: %s' % (kopf(x), paar(x), grund))

    def roh(r):
        return ctx['datei'].felder(r['zeile'] - 1)

    def absagen(g, r, grund):
        if r['datum'] < heute:        # Vergangenheit bleibt unberührt
            return
        f = roh(r)
        if f[I_ERG].strip() and f[I_ERG].strip().lower() != 'abgesagt':
            hinweis(r, 'bei fussball.de %s, in der CSV steht aber ein Ergebnis (%s): nicht geändert' % (grund, f[I_ERG]))
        elif not gleiche_partie(g, r):
            hinweis(r, 'bei fussball.de %s, die Paarung stimmt aber nicht eindeutig überein: nicht geändert' % grund)
        elif g['datum'] != r['datum'] or (g['zeit'] and g['zeit'] != r['zeit']):
            # nur der Eintrag zum selben Termin gilt: steht in der CSV von Hand ein neuer Termin, ist der nicht abgesagt
            hinweis(r, 'bei fussball.de %s zum Termin %s, die CSV hat %s: nicht geändert' % (grund, termin_text(g), termin_text(r)))
        else:
            neu = list(f)
            neu[I_ERG] = 'abgesagt'
            p.aenderungen.append(Aenderung(
                'abgesagt', kuerzel, name, (r['staffel'], r['nr']), neu, alt=f, idx=r['zeile'] - 1, datum=r['datum'], zeit=r['zeit'],
                text='abgesagt: %s · %s (bei fussball.de %s)' % (kopf(r), paar(r), grund), detail='ergebnis: „%s“ → „abgesagt“' % f[I_ERG]))

    behandelt = set()
    # 1. Nichtantritt vor dem Spieltag zählt wie eine Absetzung (abgleich.vergleiche sieht darin ein „weiter angesetzt“)
    for k, g in fb_je.items():
        r = csv_je_key.get(k)
        if g['status'] == 'nichtantritt' and r and k not in blockiert and not g['vorlaeufig']:
            behandelt.add(k)
            if not r['abgesagt']:
                absagen(g, r, 'Nichtantritt')

    # 2. Alle anderen Unterschiede; Vergangenheit (d['kommend'] falsch) bleibt unberührt und wird nur still vermerkt
    vergangen = sum(1 for d in diffs if not d['kommend'])
    if vergangen:
        p.stumm.append('%s: %s aus der Vergangenheit, unberührt' % (name, anzahl(vergangen, 'Unterschied', 'Unterschiede')))
    for d in diffs:
        if not d['kommend']:
            continue
        k, art, g, r = (d['staffel'], d['nr']), d['art'], d['fb'], d['csv']
        if art in NUR_MELDEN:
            hinweis(r or g, A.DIFF_TITEL[art] + (' (%s)' % d['text'] if d['text'] else '') + ': nicht geändert')
            continue
        if art == 'ueberzaehlig':
            if k not in doppelt:
                hinweis(r, 'nur in der CSV, bei fussball.de %s: nicht gelöscht' % (
                    'jetzt spielfrei (Spiel entfallen?)' if k in spielfrei else 'nicht (mehr) vorhanden'))
            continue
        if k in blockiert or k in behandelt:
            if k in mehrdeutig:
                hinweis(g or r, 'Staffel + Spielnummer bei fussball.de nicht eindeutig: nicht geändert')
            continue
        if g['vorlaeufig']:       # noch nicht vom Staffelleiter freigegeben: weder ergänzen noch ändern
            p.stumm.append('%s · %s: bei fussball.de noch vorläufig, nicht geändert' % (kopf(g), paar(g)))
            continue
        if g['status'] == 'angesetzt' and g['status_txt'] and not DATUM_TEXT.match(g['status_txt']) and g['datum'] >= heute:
            hinweis(g, 'bei fussball.de steht der Vermerk „%s“ (kein Termin): nicht geändert' % g['status_txt'])
            continue
        grund = namen_problem(g)       # unlesbare, gefährliche oder Platzhalter-Namen: weder ergänzen noch ändern
        if grund:
            hinweis(g, grund + (': nicht ergänzt' if art == 'fehlt' else ': nicht geändert'))
            continue

        if art == 'fehlt':
            if g['status'] != 'angesetzt':
                p.stumm.append('%s: Nichtantritt, nicht ergänzt' % kopf(g))
            elif g['datum'] < heute:
                continue
            elif g['nr'] is None or not g['staffel']:
                hinweis(g, 'bei fussball.de ohne Staffel oder Spielnummer: nicht ergänzt')
            elif not g['zeit']:
                p.stumm.append('%s · %s: bei fussball.de noch ohne Uhrzeit, nicht eintragbar' % (kopf(g), paar(g)))
            elif not zeit_plausibel(g['zeit']):
                hinweis(g, 'Uhrzeit %s unplausibel: nicht ergänzt' % g['zeit'])
            elif not g['heim'] or not g['gast']:
                hinweis(g, 'Heim oder Gast nicht gelesen: nicht ergänzt')
            elif g['art'] not in ARTEN or (g['art'] == 'Kreispokal' and not g['wettbewerb'].lower().startswith('kreispokal')):
                hinweis(g, 'Wettbewerb „%s“ nicht unter den erlaubten Arten: nicht ergänzt' % g['wettbewerb'])
            elif g['ha'] == '?':
                hinweis(g, 'Heim/Auswärts nicht bestimmbar: nicht ergänzt')
            elif k in ctx['schluessel'] or k in ctx['neu']:
                hinweis(g, 'Staffel + Spielnummer %s·%s sind in der CSV schon vergeben: nicht ergänzt' % k)
            elif zu_weit(g['datum'], heute, ctx['bis']):
                hinweis(g, 'Termin liegt %s: nicht ergänzt' % zu_weit(g['datum'], heute, ctx['bis']))
            else:
                felder, grund = csv_felder(kuerzel, g, ctx['konv'])
                if felder is None:
                    hinweis(g, grund + ': nicht ergänzt')
                else:
                    ctx['neu'].add(k)
                    p.aenderungen.append(Aenderung('ergaenzt', kuerzel, name, k, felder, datum=g['datum'], zeit=g['zeit'],
                                                   text='ergänzt: %s %s' % (kopf(g), paar(g)), detail=';'.join(felder)))
                    if g['art'] == 'Meisterschaft' and (kuerzel, g['staffel']) not in ctx['neue_staffeln'] \
                            and not any(r['staffel'] == g['staffel'] for r in csv_je):
                        ctx['neue_staffeln'].add((kuerzel, g['staffel']))     # einmalig: build_ics.py und Widgets brauchen Handarbeit
                        p.hinweise.append('%s: neue Meisterschaftsstaffel %s, build_ics.py (TEAMS, INFO_STAFFEL, RUNDE) und das '
                                          'Tabellen-Widget von Hand nachziehen (README: Staffelwechsel)' % (name, g['staffel']))
        elif art == 'termin':
            if g['status'] != 'angesetzt' or r['abgesagt']:
                continue
            if not g['datum'] or not g['zeit']:
                hinweis(r, 'bei fussball.de verlegt, aber ohne vollständigen Termin: nicht geändert')
            elif g['datum'] < heute:
                hinweis(r, 'bei fussball.de auf %s %s gelegt, das liegt in der Vergangenheit: nicht geändert' % (tag_kurz(g['datum']), g['zeit']))
            elif zu_weit(g['datum'], heute, ctx['bis']):
                hinweis(r, 'bei fussball.de auf %s %s gelegt, das liegt %s: nicht geändert' % (
                    tag_kurz(g['datum']), g['zeit'], zu_weit(g['datum'], heute, ctx['bis'])))
            elif not zeit_plausibel(g['zeit']):
                hinweis(r, 'bei fussball.de neue Uhrzeit %s unplausibel: nicht geändert' % g['zeit'])
            elif not gleiche_partie(g, r):
                hinweis(r, 'bei fussball.de verlegt, die Paarung stimmt aber nicht eindeutig überein: nicht geändert')
            else:
                f = roh(r)
                neu = list(f)
                neu[I_DATUM], neu[I_ZEIT] = datum_csv(g['datum']), g['zeit']
                nach = g['zeit'] if g['datum'] == r['datum'] else '%s %s' % (tag_kurz(g['datum']), g['zeit'])
                p.aenderungen.append(Aenderung(
                    'verlegt', kuerzel, name, k, neu, alt=f, idx=r['zeile'] - 1, datum=g['datum'], zeit=g['zeit'],
                    text='verlegt: %s → %s · %s' % (kopf(r), nach, paar(r)),
                    detail='datum;zeit: %s;%s → %s;%s' % (f[I_DATUM], f[I_ZEIT], neu[I_DATUM], neu[I_ZEIT])))
        elif art == 'csv_aktiv':
            absagen(g, r, 'Gegner zurückgezogen (zg.)' if g['status'] == 'zurueckgezogen' else 'abgesetzt')
        elif art == 'csv_abgesagt':
            if g['status'] != 'angesetzt' or g['datum'] < heute:
                continue        # Nichtantritt oder Vergangenheit: die Absage stimmt oder ist erledigt
            if not g['zeit']:
                p.stumm.append('%s · %s: wieder angesetzt, aber noch ohne Uhrzeit' % (kopf(g), paar(g)))
            elif zu_weit(g['datum'], heute, ctx['bis']):
                hinweis(r, 'bei fussball.de wieder angesetzt, der Termin liegt %s: nicht geändert' % zu_weit(g['datum'], heute, ctx['bis']))
            elif not zeit_plausibel(g['zeit']):
                hinweis(r, 'bei fussball.de wieder angesetzt, Uhrzeit %s unplausibel: nicht geändert' % g['zeit'])
            elif not gleiche_partie(g, r):
                hinweis(r, 'bei fussball.de wieder angesetzt, die Paarung stimmt aber nicht eindeutig überein: nicht geändert')
            elif WIEDER_ANSETZEN_NUR_MIT_VERLAUF and not ctx['verlauf'](k):
                hinweis(r, 'in der CSV „abgesagt“, bei fussball.de angesetzt, aber dort früher nie abgesetzt (von Hand abgesagt?): nicht geändert')
            else:
                f = roh(r)
                neu = list(f)
                neu[I_ERG], neu[I_DATUM], neu[I_ZEIT] = '', datum_csv(g['datum']), g['zeit']
                zusatz = '' if (f[I_DATUM], f[I_ZEIT]) == (neu[I_DATUM], neu[I_ZEIT]) else '; datum;zeit: %s;%s → %s;%s' % (
                    f[I_DATUM], f[I_ZEIT], neu[I_DATUM], neu[I_ZEIT])
                p.aenderungen.append(Aenderung(
                    'wieder_angesetzt', kuerzel, name, k, neu, alt=f, idx=r['zeile'] - 1, datum=g['datum'], zeit=g['zeit'],
                    text='wieder angesetzt: %s · %s' % (kopf(g), paar(r)), detail='ergebnis: „%s“ → „“%s' % (f[I_ERG], zusatz)))
    return p


# ---------------------------------------------------------------- Änderungen anwenden (im Speicher)

def anker_index(datei, felder):
    """Hinter welche Zeile gehört eine neue Zeile? Ans Ende der Zeilen derselben Mannschaft und Staffel, sonst derselben
    Mannschaft und Wettbewerbsart, sonst derselben Mannschaft; ohne Treffer: ans Dateiende (None)."""
    treffer = {1: None, 2: None, 3: None}
    for i in range(1, len(datei.zeilen)):
        f = datei.felder(i)
        if f[I_TEAM] != felder[I_TEAM]:
            continue
        treffer[3] = i
        if f[I_WB] == felder[I_WB]:
            treffer[2] = i
        if f[I_STAFFEL] == felder[I_STAFFEL]:
            treffer[1] = i
    return next((treffer[s] for s in (1, 2, 3) if treffer[s] is not None), None)


def neue_datei(datei, aenderungen):
    """Neue Dateibytes: nur geänderte Zeilen werden ersetzt, neue hinter ihren Anker eingefügt, alles andere bleibt Byte
    für Byte gleich. Setzt Aenderung.zeile_neu (1 = Kopfzeile)."""
    zeilen = list(datei.zeilen)
    einfuegen, am_ende = defaultdict(list), []
    for a in aenderungen:
        if a.art == 'ergaenzt':
            anker = anker_index(datei, a.neu)
            (am_ende if anker is None else einfuegen[anker]).append(a)
        else:
            zeilen[a.idx] = ';'.join(a.neu)
    ergebnis, stelle = [], {}
    for i, z in enumerate(zeilen):
        ergebnis.append(z)
        stelle[i] = len(ergebnis)
        for a in sorted(einfuegen.get(i, []), key=Aenderung.sortierschluessel):
            ergebnis.append(';'.join(a.neu))
            a.zeile_neu = len(ergebnis)
    for a in sorted(am_ende, key=Aenderung.sortierschluessel):
        ergebnis.append(';'.join(a.neu))
        a.zeile_neu = len(ergebnis)
    for a in aenderungen:
        if a.art != 'ergaenzt':
            a.zeile_neu = stelle[a.idx]
    return datei.bytes_aus(ergebnis)


def formtreue_pruefen(alt, neu, aenderungen):
    """Probleme, wenn mehr als die geplanten Zeilen anders sind (Kopfzeile, BOM, Zeilenende, unveränderte Zeilen)."""
    probleme = []
    if neu.bom != alt.bom:
        probleme.append('BOM-Zustand geändert')
    if neu.eol != alt.eol or neu.endet_mit_eol != alt.endet_mit_eol:
        probleme.append('Zeilenende geändert')
    if neu.zeilen[0] != alt.zeilen[0]:
        probleme.append('Kopfzeile geändert')
    ersetzt = {a.idx for a in aenderungen if a.art != 'ergaenzt'}
    neue_zeilen = Counter(';'.join(a.neu) for a in aenderungen)
    erwartet = len(alt.zeilen) + sum(1 for a in aenderungen if a.art == 'ergaenzt')
    if len(neu.zeilen) != erwartet:
        probleme.append('Zeilenzahl %d statt %d' % (len(neu.zeilen), erwartet))
    unveraendert_alt = [z for i, z in enumerate(alt.zeilen) if i not in ersetzt]
    rest = []
    for z in neu.zeilen:
        if neue_zeilen.get(z):
            neue_zeilen[z] -= 1
        else:
            rest.append(z)
    if unveraendert_alt != rest or any(neue_zeilen.values()):
        probleme.append('unveränderte Zeilen weichen ab')
    return probleme


def loeschfreiheit_pruefen(alt, neu):
    """Für fremde, liegen gebliebene Commits (kein Plan der Änderungen bekannt): Form wie vorher und keine Zeile weniger
    (dieses Werkzeug löscht nie)."""
    probleme = []
    if neu.bom != alt.bom or neu.eol != alt.eol or neu.endet_mit_eol != alt.endet_mit_eol:
        probleme.append('BOM oder Zeilenende geändert')
    if neu.zeilen[0] != alt.zeilen[0]:
        probleme.append('Kopfzeile geändert')

    def schluessel(d):
        return Counter((f[I_TEAM], f[I_STAFFEL], f[I_NR]) for f in d.zeilen_felder())
    fehlen = schluessel(alt) - schluessel(neu)
    if fehlen:
        n = sum(fehlen.values())
        probleme.append('%s %s (dieses Werkzeug löscht nie)' % (anzahl(n, 'Zeile', 'Zeilen'), 'fehlt' if n == 1 else 'fehlen'))
    return probleme


# ---------------------------------------------------------------- Prüfung vor dem Schreiben (temporäre Kopie)

def ics_lesen(pfad):
    """-> (Zahl der VEVENT, {(Staffel, Nr): (DTSTART, SUMMARY)}) aus einer .ics-Datei (gefaltete Zeilen aufgelöst)."""
    with open(pfad, encoding='utf-8') as f:
        text = f.read().replace('\r\n ', '').replace('\n ', '')
    n, termine = 0, {}
    for block in re.findall(r'BEGIN:VEVENT\r?\n(.*?)END:VEVENT', text, re.S):
        n += 1
        uid = re.search(r'^UID:(\d+)-(\d+)@', block, re.M)
        start = re.search(r'^DTSTART[^:\r\n]*:(\S+)', block, re.M)
        titel = re.search(r'^SUMMARY:(.*)$', block, re.M)
        if uid:
            termine[(uid.group(1), int(uid.group(2)))] = (start.group(1) if start else '', titel.group(1).strip() if titel else '')
    return n, termine


def bauen(generator_ordner, csv_bytes, ziel):
    """build_ics.py in einer temporären Kopie ausführen -> (Rückgabecode, letzte Ausgabe); Ergebnis in ziel/docs."""
    for datei in ('build_ics.py', 'vorlage_index.html'):
        quelle = os.path.join(generator_ordner, datei)
        if not os.path.exists(quelle):
            return 1, '%s fehlt in %s' % (datei, generator_ordner)
        shutil.copy(quelle, os.path.join(ziel, datei))
    with open(os.path.join(ziel, 'spiele.csv'), 'wb') as f:
        f.write(csv_bytes)
    try:
        r = subprocess.run([sys.executable, '-I', '-B', 'build_ics.py'], cwd=ziel, env=dict(os.environ, SEQ='0'),
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return 1, '%s: %s' % (type(e).__name__, e)
    return r.returncode, (r.stdout + r.stderr).strip()[-400:]


def validiere(alt_bytes, neu_bytes, aenderungen, generator_ordner):
    """Prüft die geplante neue CSV, bevor irgendetwas geschrieben wird -> Liste der Probleme (leer = in Ordnung). Mit
    aenderungen=None (liegen gebliebene Commits, kein Plan bekannt) gilt: keine Zeile gelöscht, der Zuwachs steht in der CSV."""
    try:
        alt, neu = CsvDatei(alt_bytes), CsvDatei(neu_bytes)
    except FormFehler as e:
        return ['neue CSV hat unerwartete Form: %s' % e]
    probleme = loeschfreiheit_pruefen(alt, neu) if aenderungen is None else formtreue_pruefen(alt, neu, aenderungen)
    with tempfile.TemporaryDirectory(prefix='nachtrag-pruefung-') as tmp:
        gelesen = {}
        for name, daten in (('alt', alt_bytes), ('neu', neu_bytes)):
            pfad = os.path.join(tmp, name + '.csv')
            with open(pfad, 'wb') as f:
                f.write(daten)
            gelesen[name] = A.csv_lesen(pfad)
        # Die Änderung darf nichts verschlechtern; schon vorhandene Mängel (unlesbare oder doppelte Zeilen) stehen in den Hinweisen
        if len(gelesen['neu'][1]) > len(gelesen['alt'][1]):
            probleme.append('CSV nicht mehr lesbar: ' + '; '.join(gelesen['neu'][1][:3]))

        def doppelt(schluessel):
            return sum(n - 1 for n in Counter(schluessel).values() if n > 1)
        if doppelt((r['team'], r['staffel'], r['nr']) for r in gelesen['neu'][0]) > doppelt((r['team'], r['staffel'], r['nr']) for r in gelesen['alt'][0]):
            probleme.append('Schlüssel (Team, Staffel, Spielnummer) nicht mehr eindeutig')
        if doppelt((f[I_STAFFEL], f[I_NR]) for f in neu.zeilen_felder()) > doppelt((f[I_STAFFEL], f[I_NR]) for f in alt.zeilen_felder()):
            probleme.append('neue Termin-UID doppelt vergeben (Staffel + Spielnummer)')
        if probleme:
            return probleme
        ordner = {}
        for name, daten in (('alt', alt_bytes), ('neu', neu_bytes)):
            ordner[name] = os.path.join(tmp, name)
            os.makedirs(ordner[name])
            rc, ausgabe = bauen(generator_ordner, daten, ordner[name])
            if rc != 0:
                letzte = ausgabe.splitlines()[-1] if ausgabe else 'Code %s' % rc
                return ['build_ics.py scheitert mit dem %s Stand (%s)' % ('bisherigen' if name == 'alt' else 'neuen', letzte)]
        n_alt, t_alt = ics_lesen(os.path.join(ordner['alt'], 'docs', 'alle.ics'))
        n_neu, t_neu = ics_lesen(os.path.join(ordner['neu'], 'docs', 'alle.ics'))
        if aenderungen is None:
            zuwachs = (Counter(f[I_TEAM] for f in neu.zeilen_felder() if f[I_NR])
                       - Counter(f[I_TEAM] for f in alt.zeilen_felder() if f[I_NR]))
            aenderungen = []
        else:
            zuwachs = Counter(a.team for a in aenderungen if a.art == 'ergaenzt')
        if n_neu != n_alt + sum(zuwachs.values()):
            probleme.append('Gesamtfeed alle.ics hat %d statt %d Termine' % (n_neu, n_alt + sum(zuwachs.values())))
        for team in sorted(set(zuwachs) | {a.team for a in aenderungen}):
            erwartet = ics_lesen(os.path.join(ordner['alt'], 'docs', team.lower() + '.ics'))[0] + zuwachs[team]
            gefunden = ics_lesen(os.path.join(ordner['neu'], 'docs', team.lower() + '.ics'))[0]
            if gefunden != erwartet:
                probleme.append('Kalender %s.ics hat %d statt %d Termine' % (team.lower(), gefunden, erwartet))
        for a in aenderungen:
            k = (a.key[0], a.key[1])
            titel = t_neu.get(k, ('', ''))[1]
            if a.art == 'ergaenzt' and (k not in t_neu or k in t_alt):
                probleme.append('Termin %s·%s fehlt im neuen Feed oder war schon vergeben' % k)
            if a.art in ('verlegt', 'wieder_angesetzt') and t_neu.get(k, ('',))[0] != '%sT%s00' % (a.datum.replace('-', ''), a.zeit.replace(':', '')):
                probleme.append('Termin %s·%s hat im neuen Feed nicht den neuen Start' % k)
            if a.art == 'abgesagt' and 'abgesagt' not in titel:
                probleme.append('Termin %s·%s steht im neuen Feed nicht als abgesagt' % k)
            if a.art == 'wieder_angesetzt' and 'abgesagt' in titel:
                probleme.append('Termin %s·%s steht im neuen Feed noch als abgesagt' % k)
    return probleme


# ---------------------------------------------------------------- Schreiben und Veröffentlichen

def atomar_schreiben(pfad, daten):
    """Über eine Nachbardatei und os.replace: nie eine halb geschriebene spiele.csv."""
    tmp = '%s.nachtrag-%d.tmp' % (pfad, os.getpid())
    try:
        with open(tmp, 'wb') as f:
            f.write(daten)
        shutil.copymode(pfad, tmp)
        os.replace(tmp, pfad)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def gegenlesen(pfad, erwartet):
    """Steht auf der Platte genau das, was geprüft wurde? Sonst LaufFehler (löst die Wiederherstellung aus)."""
    with open(pfad, 'rb') as f:
        if f.read() != erwartet:
            raise LaufFehler('spiele.csv weicht nach dem Schreiben vom geprüften Stand ab')


def git(repo, *args, timeout=60):
    """-> (Rückgabecode, stdout, stderr). Keine Rückfragen (GIT_TERMINAL_PROMPT=0), englische Meldungen (LC_ALL=C: die
    Auswertung von „rejected“ & Co. hängt nicht von der Sprache ab), Zugangsdaten geschwärzt."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    try:
        r = subprocess.run([shutil.which('git') or '/usr/bin/git', '-C', repo] + list(args), capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout.rstrip(), schwaerzen(r.stderr.strip())
    except subprocess.TimeoutExpired:
        return 124, '', 'Zeitüberschreitung bei git %s' % args[0]
    except Exception as e:
        return 99, '', '%s: %s' % (type(e).__name__, e)


def git_bytes(repo, *args, timeout=60):
    """-> (Rückgabecode, rohe stdout-Bytes), z. B. für git show HEAD:spiele.csv."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    try:
        r = subprocess.run([shutil.which('git') or '/usr/bin/git', '-C', repo] + list(args), capture_output=True, timeout=timeout, env=env)
        return r.returncode, r.stdout
    except Exception:
        return 99, b''


def letzte_zeile(text, standard=''):
    """Kurzer Fehlertext aus einer Git-Meldung: die aussagekräftigen Zeilen (rejected, denied, fatal, remote: …) statt der
    allgemeinen Schlusszeile („failed to push some refs to …“); ohne Hinweise (hint:) und ohne die Adresse des Remotes."""
    zeilen = [z.strip() for z in (text or '').splitlines() if z.strip() and not z.startswith(('hint:', 'To '))]
    treffer = [z for z in zeilen if re.search(r'rejected|denied|declined|fatal|remote:|could not|unable|Permission|Authentication|conflict', z, re.I)
               and 'failed to push some refs' not in z]
    wahl = treffer[:2] or zeilen[-1:]
    return ' | '.join(wahl)[:200] if wahl else standard


def git_vorbereiten(repo, csvname):
    """Zustand prüfen und mit origin/main abgleichen -> Zahl lokaler, noch nicht gepushter Commits (nur spiele.csv).
    Liegen gebliebene Commits werden nur übernommen, wenn sie von diesem Werkzeug stammen (Betreff beginnt mit COMMIT_ANFANG, nur
    spiele.csv geändert) und die CSV die Prüfung besteht. LaufFehler bei: kein Repo, nicht auf main, schmutziger Baum, laufendes
    Rebase, fremde lokale Commits, Netzfehler."""
    rc, wurzel, _ = git(repo, 'rev-parse', '--show-toplevel')
    if rc != 0 or os.path.realpath(wurzel) != os.path.realpath(repo):
        raise LaufFehler('Git: %s ist kein Repo-Stamm' % repo)
    rc, zweig, _ = git(repo, 'symbolic-ref', '--short', 'HEAD')
    if rc != 0 or zweig != 'main':
        raise LaufFehler('Git: nicht auf main (%s)' % (zweig or 'losgelöster HEAD'))
    if any(os.path.exists(os.path.join(repo, '.git', n)) for n in GIT_SPERREN):
        raise LaufFehler('Git: Rebase oder Merge läuft noch')
    rc, status, _ = git(repo, 'status', '--porcelain', '--untracked-files=no')
    if rc != 0:
        raise LaufFehler('Git: Status nicht lesbar')
    if status:
        raise LaufFehler('Arbeitsbaum nicht sauber (%s) – nichts geschrieben' % ', '.join(z[3:] for z in status.splitlines()[:4]))
    rc, _, err = git(repo, 'fetch', 'origin', 'main', timeout=120)
    if rc != 0:
        raise LaufFehler('Git: fetch fehlgeschlagen (%s)' % letzte_zeile(err, 'Code %s' % rc))
    rc, zahlen, _ = git(repo, 'rev-list', '--left-right', '--count', 'HEAD...origin/main')
    if rc != 0:
        raise LaufFehler('Git: origin/main nicht gefunden')
    vor, hinten = (int(x) for x in zahlen.split())
    if vor:
        _, dateien, _ = git(repo, 'log', '--name-only', '--format=', 'origin/main..HEAD')
        fremd = sorted({d for d in dateien.splitlines() if d and d != csvname})
        if fremd:
            raise LaufFehler('Git: lokale, nicht gepushte Commits ändern mehr als %s (%s), nicht angefasst' % (csvname, ', '.join(fremd[:3])))
        _, betreffe, _ = git(repo, 'log', '--format=%s', 'origin/main..HEAD')
        fremde = [b for b in betreffe.splitlines() if not b.startswith(COMMIT_ANFANG)]
        if fremde:
            raise LaufFehler('Git: lokale, nicht gepushte Commits stammen nicht von diesem Werkzeug („%s“), nichts gepusht' % fremde[0][:60])
    if hinten:
        rc, _, err = git(repo, 'pull', '--rebase', 'origin', 'main', timeout=120)
        if rc != 0:
            git(repo, 'rebase', '--abort')
            raise LaufFehler('Git: pull --rebase fehlgeschlagen und abgebrochen (%s)' % letzte_zeile(err, 'Code %s' % rc))
    if vor:
        rc1, alt_stand = git_bytes(repo, 'show', 'origin/main:' + csvname)
        rc2, neu_stand = git_bytes(repo, 'show', 'HEAD:' + csvname)
        if rc1 != 0 or rc2 != 0:
            raise LaufFehler('Git: %s der liegen gebliebenen Commits nicht lesbar, nichts gepusht' % csvname)
        probleme = validiere(alt_stand, neu_stand, None, repo)
        if probleme:
            raise LaufFehler('Git: die liegen gebliebenen Commits bestehen die Prüfung nicht (%s), nichts gepusht' % '; '.join(probleme[:3]))
    return vor


def git_push(repo):
    """Einmal pushen; wurde abgelehnt (origin war weiter), einmal pull --rebase und erneut -> None oder Fehlertext."""
    for versuch in (1, 2):
        rc, _, err = git(repo, 'push', 'origin', 'main', timeout=120)
        if rc == 0:
            return None
        if versuch == 2 or not re.search(r'rejected|non-fast-forward|fetch first', err or ''):
            return 'Push fehlgeschlagen (%s)' % letzte_zeile(err, 'Code %s' % rc)
        rc, _, err = git(repo, 'pull', '--rebase', 'origin', 'main', timeout=120)
        if rc != 0:
            git(repo, 'rebase', '--abort')
            return 'Push abgelehnt, pull --rebase scheiterte (%s)' % letzte_zeile(err, 'Code %s' % rc)
    return None


def git_commit(repo, csvname, betreff, text):
    """spiele.csv (und nur sie) committen -> None oder Fehlertext; bei Fehler wird die Vormerkung zurückgenommen."""
    rc, _, err = git(repo, 'add', '--', csvname)
    if rc != 0:
        return 'git add fehlgeschlagen (%s)' % letzte_zeile(err, 'Code %s' % rc)
    rc, namen, _ = git(repo, 'diff', '--cached', '--name-only')
    if rc != 0 or namen.splitlines() != [csvname]:
        git(repo, 'restore', '--staged', '--', csvname)
        return 'vorgemerkt wäre mehr als %s (%s)' % (csvname, namen.replace('\n', ', '))
    ident = []
    if git(repo, 'var', 'GIT_AUTHOR_IDENT')[0] != 0 or git(repo, 'var', 'GIT_COMMITTER_IDENT')[0] != 0:
        ident = ['-c', 'user.name=' + FALLBACK_AUTOR[0], '-c', 'user.email=' + FALLBACK_AUTOR[1]]
    rc, _, err = git(repo, *(ident + ['commit', '-m', betreff, '-m', text]))
    if rc != 0:
        git(repo, 'restore', '--staged', '--', csvname)
        return 'git commit fehlgeschlagen (%s)' % letzte_zeile(err, 'Code %s' % rc)
    return None


class Abbruch(BaseException):
    """Ein Signal (SIGTERM, SIGINT) beendet den Lauf. BaseException, damit kein „except Exception“ den Abbruch verschluckt: wer
    schon etwas geschrieben hat, räumt vorher auf (Original wiederherstellen) und gibt den Abbruch dann weiter."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


def pid_lebt(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError, OSError):      # unsinnige PID in einer beschädigten Sperrdatei
        return False
    return True


class Sperre:
    """Exklusive Sperre gegen gleichzeitige Läufe: flock auf eine Datei außerhalb des Repos (neben der Not-Aus-Datei). Stirbt der
    Besitzer (auch mit kill -9), gibt das Betriebssystem die Sperre frei; eine liegen gebliebene Datei blockiert also nie. Sie
    enthält die PID des Besitzers (nur zur Auskunft; ein beendeter Besitzer wird als „veraltet“ erkannt)."""

    def __init__(self, pfad):
        self.pfad, self.f, self.besitzer, self.veraltet = pfad, None, None, None

    def holen(self):
        """True: die Sperre gehört jetzt diesem Lauf (oder die Plattform kennt keine). False: ein anderer Lauf hält sie
        (self.besitzer = seine PID, soweit lesbar). OSError: Sperrdatei nicht anlegbar."""
        if fcntl is None:
            return True
        os.makedirs(os.path.dirname(os.path.abspath(self.pfad)), exist_ok=True)
        f = open(self.pfad, 'a+')
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            self.besitzer = f.read().strip() or '?'
            f.close()
            return False
        except OSError:
            f.close()
            raise
        f.seek(0)
        alt = f.read().strip()
        if alt.isdigit() and int(alt) != os.getpid() and not pid_lebt(int(alt)):
            self.veraltet = int(alt)           # die Datei stammt von einem beendeten Lauf: übernommen
        f.seek(0)
        f.truncate()
        f.write('%d\n' % os.getpid())
        f.flush()
        self.f = f
        return True

    def gueltig(self):
        """Gehört die Sperre diesem Lauf noch? Nur der Besitzer darf schreiben und das Original wiederherstellen. Wurde die
        Sperrdatei ersetzt oder gelöscht, könnte schon ein anderer Lauf eine neue halten."""
        if fcntl is None:
            return True
        if self.f is None or self.f.closed:
            return False
        try:
            fd, pfad = os.fstat(self.f.fileno()), os.stat(self.pfad)
        except OSError:
            return False
        return (fd.st_dev, fd.st_ino) == (pfad.st_dev, pfad.st_ino)

    def freigeben(self):
        if self.f is not None:
            try:
                fcntl.flock(self.f, fcntl.LOCK_UN)
            finally:
                self.f.close()
                self.f = None


def schreibe_und_veroeffentliche(opt, datei, neu_bytes, aenderungen, pushen, repo, csvname, sperre, res):
    """Geprüfte Bytes schreiben und gegenlesen; mit --push committen und pushen. Bei jedem Fehler und bei einem Signal vor dem
    Commit wird das Original wiederhergestellt, aber nur vom Besitzer der Lauf-Sperre. `res` (angewendet, commit, gepusht,
    wiederhergestellt, fehler) wird Schritt für Schritt gefüllt, damit auch ein Abbruch durch Signal den Stand kennt."""
    res.update(angewendet=False, commit=False, gepusht=False, wiederhergestellt=False, fehler=None)
    if sperre is None or not sperre.gueltig():
        res['fehler'] = 'die Lauf-Sperre gehört diesem Lauf nicht, nichts geschrieben'
        return res
    try:
        atomar_schreiben(opt.csv, neu_bytes)
        res['angewendet'] = True
        gegenlesen(opt.csv, neu_bytes)
        if pushen:
            n = len(aenderungen)
            betreff = '%s (%d %s)' % (COMMIT_ANFANG, n, 'Änderung' if n == 1 else 'Änderungen')
            fehlertext = git_commit(repo, csvname, betreff, '\n'.join('- ' + a.text for a in aenderungen))
            if fehlertext:
                raise LaufFehler(fehlertext)
            res['commit'] = True
            fehlertext = git_push(repo)
            if fehlertext:
                res['fehler'] = '%s; der Commit bleibt lokal, der nächste Lauf pusht ihn nach' % fehlertext
            else:
                res['gepusht'] = True
    except BaseException as e:
        if res['commit']:
            res['fehler'] = str(e)
        elif not sperre.gueltig():
            res['fehler'] = '%s – Lauf-Sperre verloren, spiele.csv NICHT zurückgeschrieben, bitte prüfen!' % e
        else:
            try:
                atomar_schreiben(opt.csv, datei.original)
                gegenlesen(opt.csv, datei.original)
                res.update(angewendet=False, wiederhergestellt=True)
                res['fehler'] = '%s – spiele.csv wiederhergestellt, nichts committet' % e
            except Exception as e2:
                res['fehler'] = 'Wiederherstellung fehlgeschlagen (%s) – spiele.csv sofort prüfen!' % e2
        if not isinstance(e, Exception):
            raise                                   # Signal: erst aufgeräumt, dann weitergegeben
    return res


# ---------------------------------------------------------------- Quelle holen (Cache; G-Jugend selbst)

def staende(cache, slug, grenze=12):
    """Cache-Dateien einer Mannschaft, neueste zuerst: [(Pfad, Zeitstempel)] (höchstens `grenze`)."""
    ergebnis = []
    for pfad in sorted(glob.glob(os.path.join(cache, slug, '*.html')), reverse=True):
        try:
            ergebnis.append((pfad, datetime.strptime(os.path.basename(pfad)[:15], '%Y%m%d-%H%M%S')))
        except ValueError:
            continue
        if len(ergebnis) >= grenze:
            break
    return ergebnis


def cache_datei_lesen(pfad):
    """-> (Text, None) oder (None, Grund): lesbar (UTF-8) und als Mannschaftsplan brauchbar (nicht zu kurz, keine Sperrseite)?"""
    try:
        with open(pfad, encoding='utf-8') as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as ex:
        return None, 'nicht lesbar (%s)' % type(ex).__name__
    grund = seite_defekt(text)
    return (None, grund) if grund else (text, None)


def quarantaene(cache, slug, stempel):
    """Eine eben abgerufene, aber unbrauchbare Seite (Sperr- oder Captcha-Seite) beiseitelegen (*.html.fehler, nichts wird
    gelöscht), damit sie den guten Stand nicht verdrängt, und einen Fehlversuch vermerken (wie abgleich.hole: 20 Stunden Ruhe)."""
    ordner = os.path.join(cache, slug)
    name = stempel.strftime('%Y%m%d-%H%M%S')
    try:
        os.rename(os.path.join(ordner, name + '.html'), os.path.join(ordner, name + '.html.fehler'))
        with open(os.path.join(ordner, 'fehlversuch.txt'), 'w') as f:
            f.write(name)
    except OSError:
        pass


def quelle_holen(team, opt, jetzt, von, bis, abbruch, anwendungsmodus, abrufen):
    """-> (Text, Zeitstempel, Ausfall, Notizen). Ausfall = (Art, Text, Stand): Mannschaft überspringen, bei einer Zusatzquelle diese
    weglassen (Art 'kein_cache', 'unbrauchbar', 'zukunft' oder 'veraltet'). Notizen: Liste von (Art, Text) zum Vermerken
    ('veraltet' = Berichtsmodus mit zu altem Stand, 'abruf' = Abruf nicht möglich oder unbrauchbar, 'cache' = neuester Stand
    unbrauchbar, ein älterer wird genommen)."""
    kuerzel, slug, name, tid = team
    notizen = []
    if kuerzel in SELBST_ABRUFEN and abrufen:
        alt_abstand, alt_agent = A.MIN_ABSTAND_TAGE, A.USER_AGENT
        A.MIN_ABSTAND_TAGE, A.USER_AGENT = ABRUF_ABSTAND_STUNDEN / 24.0, USER_AGENT
        try:
            text, quelle, stempel, fehler = A.hole(opt.cache, team, von, bis, False, jetzt, abbruch, opt.url_vorlage)
        finally:
            A.MIN_ABSTAND_TAGE, A.USER_AGENT = alt_abstand, alt_agent
        if fehler:
            notizen.append(('abruf', 'Abruf nicht möglich (%s), der Cache wird genommen' % fehler))
        elif quelle == 'Abruf':
            grund = seite_defekt(text)
            if grund is None:
                return text, stempel, None, notizen
            quarantaene(opt.cache, slug, stempel)      # die gute ältere Seite bleibt die neueste
            notizen.append(('abruf', 'Abruf lieferte keine Spielplanseite (%s), sie liegt als *.html.fehler im Cache; der Cache wird genommen' % grund))
    ausschuss, gewaehlt = [], None
    for pfad, stempel in staende(opt.cache, slug):
        text, grund = cache_datei_lesen(pfad)
        if text is None:
            ausschuss.append((stempel, grund))
            continue
        gewaehlt = (stempel, text)
        break
    if gewaehlt is None:
        if ausschuss:
            return None, None, ('unbrauchbar', 'kein brauchbarer Cache-Stand (%s)' % '; '.join(
                '%s: %s' % (s.strftime('%d.%m. %H:%M'), g) for s, g in ausschuss[:3]), None), notizen
        return None, None, ('kein_cache', 'kein Cache-Stand vorhanden', None), notizen
    stempel, text = gewaehlt
    if ausschuss:
        notizen.append(('cache', 'neuester Cache-Stand vom %s unbrauchbar (%s), der Stand vom %s wird genommen' % (
            ausschuss[0][0].strftime('%d.%m. %H:%M'), ausschuss[0][1], stempel.strftime('%d.%m. %H:%M'))))
    alter = jetzt - stempel
    if -alter > timedelta(hours=2):
        return None, None, ('zukunft', 'Cache-Stand vom %s liegt in der Zukunft (Systemuhr falsch?)' % stempel.strftime('%d.%m.%Y %H:%M'), stempel), notizen
    if opt.max_alter and alter > timedelta(hours=opt.max_alter) and not (opt.nur_cache and not anwendungsmodus):
        grund = 'Cache-Stand vom %s ist %d Stunden alt (Grenze %g)' % (stempel.strftime('%d.%m. %H:%M'), alter.total_seconds() // 3600, opt.max_alter)
        if anwendungsmodus:
            return None, None, ('veraltet', grund, stempel), notizen
        notizen.append(('veraltet', grund))
    return text, stempel, None, notizen


def zusatzquellen_lesen(team, opt, jetzt, von, bis, abbruch, anwendungsmodus, kommend):
    """Die Zusatzquellen einer Mannschaft (abgleich.quellen(team)[1:], z. B. die D4 der D3) aus dem Cache lesen -> dict(fb, stand,
    verlauf, meldungen): fb = Spiele aller brauchbaren Zusatzquellen, stand = {Name: Zeitstempel} der gelesenen Stände, verlauf =
    [(Slug, Zeitstempel)] der verwendeten Quellen, meldungen = [(Art, Text)] mit Art 'hinweis' oder 'stumm'. Eine Zusatzquelle wird
    NIE abgerufen (abrufen=False, auch nicht, wenn ihre Mannschaft in SELBST_ABRUFEN stünde): der Plan kommt aus dem Cache des
    Mac-Tasks, mit denselben Regeln wie der eigene Plan (neuester brauchbarer Stand, Altersgrenze, Zukunft). Fällt sie aus (kein,
    unbrauchbarer, veralteter oder unplausibler Stand, unerwartete Ausnahme), wird sie weggelassen und gemeldet; die Mannschaft
    bleibt dabei nie auf der Strecke. Ohne kommende CSV-Zeilen der Mannschaft sind Cache-Sorgen nur eine stille Notiz."""
    z = dict(fb=[], stand={}, verlauf=[], meldungen=[])

    def melden(art, text):
        z['meldungen'].append((art, text))
    for quelle in A.quellen(team)[1:]:
        slug, qname = quelle[1], quelle[2]
        try:
            text, stempel, ausfall, notizen = quelle_holen(quelle, opt, jetzt, von, bis, abbruch, anwendungsmodus, False)
            for art, notiz in notizen:
                if not kommend:
                    melden('stumm', '%s: %s' % (qname, notiz))
                elif art == 'veraltet':
                    melden('hinweis', '%s: %s; im Anwendungsmodus wären ihre Spiele nicht geprüft' % (qname, notiz))
                else:
                    melden('hinweis', '%s: %s' % (qname, notiz))
            if ausfall:
                if not kommend and ausfall[0] in ('kein_cache', 'unbrauchbar', 'veraltet'):
                    melden('stumm', '%s: keine kommenden Spiele in der CSV und %s, nichts zu tun' % (qname, ausfall[1]))
                else:
                    melden('hinweis', '%s: %s; %s' % (qname, ausfall[1], ZUSATZ_NICHT_GEPRUEFT))
                continue
            z['stand'][qname] = stempel
            fb, grund = seite_lesen(text)
            grund = grund or quelle_plausibel(fb, [])      # lesbar und „nennt unseren Verein“ (die CSV-Zeilen der Mannschaft gehören dem Gesamtplan)
            if grund:
                melden('hinweis', '%s: Quelle unplausibel (%s); %s' % (qname, grund, ZUSATZ_NICHT_GEPRUEFT))
                continue
            z['fb'] += fb
            z['verlauf'].append((slug, stempel))
        except Exception as ex:   # eine kaputte Zusatzquelle darf die Mannschaft nie mitreißen
            melden('hinweis', '%s: unerwarteter Fehler (%s: %s); %s' % (qname, type(ex).__name__, ex, ZUSATZ_NICHT_GEPRUEFT))
    return z


# ---------------------------------------------------------------- Lauf

def ausfaelle_melden(ausfaelle, max_alter):
    """Übersprungene Mannschaften als Fehlertexte; gleiche Ursache bei mehreren Mannschaften wird zu einer Meldung."""
    gruppen = {}
    for name, art, text, stand in ausfaelle:
        gruppen.setdefault(art, []).append((name, text, stand))
    meldungen = []
    for art, liste in gruppen.items():
        namen = ', '.join(n for n, _, _ in liste)
        if len(liste) == 1 or art in ('unplausibel', 'ausnahme'):
            meldungen += ['%s übersprungen: %s' % (n, t) for n, t, _ in liste]
        elif art == 'veraltet':
            jung = max(st for _, _, st in liste)
            meldungen.append('%d Mannschaften übersprungen, Cache älter als %g Stunden (jüngster Stand vom %s): %s' % (
                len(liste), max_alter, jung.strftime('%d.%m. %H:%M'), namen))
        elif art == 'zukunft':
            meldungen.append('%d Mannschaften übersprungen, Cache-Stand liegt in der Zukunft (Systemuhr falsch?): %s' % (len(liste), namen))
        elif art == 'unbrauchbar':
            meldungen.append('%d Mannschaften übersprungen, kein brauchbarer Cache-Stand: %s' % (len(liste), namen))
        else:
            meldungen.append('%d Mannschaften übersprungen, kein Cache-Stand vorhanden: %s' % (len(liste), namen))
    return meldungen


def mengenbremse(aenderungen):
    """Text, wenn die Mengenbremse greift (dann wird nichts geschrieben), sonst None."""
    je_team = Counter(a.team for a in aenderungen)
    if len(aenderungen) > MAX_GESAMT:
        return '%d Änderungen in einem Lauf (Grenze %d)' % (len(aenderungen), MAX_GESAMT)
    if je_team and max(je_team.values()) > MAX_JE_MANNSCHAFT:
        t = max(je_team, key=je_team.get)
        return '%d Änderungen bei %s in einem Lauf (Grenze %d je Mannschaft)' % (je_team[t], next(x[2] for x in A.TEAMS if x[0] == t), MAX_JE_MANNSCHAFT)
    return None


def ergebnis_neu(notaus):
    return dict(zeilen=[], aenderungen=[], angewendet=False, commit=False, gepusht=False, nachgepusht=0, code=0, notaus=notaus,
                hinweise=[], fehler=[], stumm=[], stand={}, bremse=None, uebersprungen=[], schreibstand={}, schluss='')


def beenden(e, text, code):
    e.update(schluss='NACHTRAG: ' + text, code=code)
    return e


def bereinigt(e):
    """Nichts Rohes in Ausgabe und Bericht: alle Texte des Ergebnisses laufen durch anzeige()."""
    for k in ('zeilen', 'hinweise', 'fehler', 'stumm'):
        e[k] = [anzeige(t) for t in e[k]]
    e['schluss'] = anzeige(e['schluss'])
    return e


def lauf(opt, jetzt=None):
    """Ein kompletter Lauf -> dict(zeilen, schluss, code, aenderungen, angewendet, gepusht, hinweise, fehler, stumm, …). Im
    Anwendungsmodus hält der Lauf die Lauf-Sperre (flock): ein zweiter Lauf bricht sofort ab und rührt weder CSV noch Git an."""
    return bereinigt(_lauf_mit_sperre(opt, jetzt))


def _lauf_mit_sperre(opt, jetzt):
    jetzt = jetzt or datetime.now()
    notaus = os.path.exists(opt.notaus_datei)
    e = ergebnis_neu(notaus)
    sperre = None
    try:
        if opt.anwenden and not notaus:
            sperre = Sperre(opt.sperrdatei or os.path.join(os.path.dirname(os.path.abspath(opt.notaus_datei)), SPERRDATEI))
            try:
                frei = sperre.holen()
            except OSError as ex:
                text = 'Lauf-Sperre nicht möglich (%s: %s)' % (type(ex).__name__, ex)
                e['fehler'].append(text)
                e['zeilen'].append('Fehler: ' + text)
                return beenden(e, 'Fehler – ' + text, 1)
            if not frei:
                e['fehler'].append('anderer Lauf aktiv')
                e['zeilen'].append('Fehler: anderer Lauf aktiv (PID %s, Sperrdatei %s), nichts getan' % (sperre.besitzer, sperre.pfad))
                return beenden(e, 'Fehler – anderer Lauf aktiv', 1)
            if sperre.veraltet:
                e['stumm'].append('Sperrdatei eines beendeten Laufs (PID %d) übernommen' % sperre.veraltet)
        return _lauf(opt, jetzt, e, sperre)
    except Abbruch as ab:
        stand = e['schreibstand']
        if stand.get('wiederhergestellt'):
            zusatz = 'spiele.csv wiederhergestellt, nichts committet'
        elif stand.get('commit'):
            zusatz = 'der Commit bleibt lokal, der nächste Lauf pusht ihn nach'
        elif stand.get('angewendet'):
            zusatz = stand.get('fehler') or 'spiele.csv wurde schon geschrieben, bitte prüfen'
        else:
            zusatz = 'nichts geschrieben'
        text = 'abgebrochen (%s); %s' % (ab.name, zusatz)
        e['fehler'].append(text)
        e['zeilen'].append('Fehler: ' + text)
        return beenden(e, 'Fehler – ' + text, 1)
    finally:
        if sperre is not None:
            sperre.freigeben()


def _lauf(opt, jetzt, e, sperre):
    heute = opt.heute or jetzt.date().isoformat()
    h = date.fromisoformat(heute)
    sj = h.year if h.month >= 7 else h.year - 1
    von, bis = '%d-07-01' % sj, '%d-06-30' % (sj + 1)
    notaus = e['notaus']
    schreiben = opt.anwenden and not notaus
    pushen = schreiben and opt.push
    abrufen = schreiben and not opt.nur_cache and opt.abrufen
    repo, csvname = os.path.dirname(os.path.abspath(opt.csv)), os.path.basename(opt.csv)
    hinweise, fehler, stumm = e['hinweise'], e['fehler'], e['stumm']

    # 1. Git abgleichen (nur beim Pushen): sauberer Baum, pull --rebase, liegen gebliebene Commits nachpushen
    if pushen:
        try:
            vor = git_vorbereiten(repo, csvname)
            if vor:
                fehlertext = git_push(repo)
                if fehlertext:
                    raise LaufFehler('liegen gebliebene Commits nicht gepusht: ' + fehlertext)
                e['nachgepusht'] = vor
        except LaufFehler as ex:
            fehler.append(str(ex))
            e['zeilen'].append('Fehler: %s' % ex)
            return beenden(e, 'Fehler – %s' % ex, 1)

    # 2. CSV lesen
    try:
        with open(opt.csv, 'rb') as f:
            datei = CsvDatei(f.read())
        csv_alle, csv_fehler = A.csv_lesen(opt.csv)
    except (FormFehler, OSError) as ex:
        fehler.append('spiele.csv: %s' % ex)
        e['zeilen'].append('Fehler: spiele.csv: %s' % ex)
        return beenden(e, 'Fehler – spiele.csv nicht lesbar oder unerwartete Form (%s)' % ex, 1)
    if csv_fehler:
        hinweise.append('%s in spiele.csv nicht lesbar (%s), z. B. ohne Spielnummer oder mit ungültigem Datum' % (
            anzahl(len(csv_fehler), 'Zeile', 'Zeilen'), ', '.join(f.split(':')[0] for f in csv_fehler[:5])))
    csv_je = defaultdict(list)
    for r in csv_alle:
        if von <= r['datum'] <= bis:
            csv_je[r['team']].append(r)
    ctx = dict(konv=Konventionen(tabelle_aus(csv_alle, datei)), datei=datei, bis=bis, neu=set(), neue_staffeln=set(),
               schluessel={(r['staffel'], r['nr']) for r in csv_alle})

    # 3. Je Mannschaft: Quelle holen (dazu ihre Zusatzquellen aus dem Cache), prüfen, Änderungen planen; eine kaputte Mannschaft hält die anderen nicht auf
    plaene, abbruch, veraltet, ausfaelle = {}, [], [], []
    for team in A.TEAMS:
        kuerzel, slug, name, tid = team
        # Ohne kommende CSV-Zeilen (Sommerpause, Saisonende) sind Cache-Sorgen kein Fall für den Bericht, nur für die stille Notiz
        kommend = any(r['datum'] >= heute for r in csv_je.get(kuerzel, []))
        try:
            text, stempel, ausfall, notizen = quelle_holen(team, opt, jetzt, von, bis, abbruch, schreiben, abrufen)
            for art, notiz in notizen:
                if not kommend:
                    stumm.append('%s: %s' % (name, notiz))
                elif art == 'veraltet':
                    veraltet.append(name)
                else:
                    hinweise.append('%s: %s' % (name, notiz))
            if ausfall:
                if not kommend and ausfall[0] in ('kein_cache', 'unbrauchbar', 'veraltet'):
                    stumm.append('%s: keine kommenden Spiele in der CSV und %s, nichts zu tun' % (name, ausfall[1]))
                else:
                    ausfaelle.append((name,) + ausfall)
                continue
            e['stand'][name] = stempel
            fb, grund = seite_lesen(text)
            zusatz = None
            if not grund:       # die Zusatzquellen nur, wenn der eigene Plan lesbar ist; die Plausibilität gilt dem Gesamtplan
                zusatz = zusatzquellen_lesen(team, opt, jetzt, von, bis, abbruch, schreiben, kommend)
                fb = fb + zusatz['fb']
                grund = quelle_plausibel(fb, csv_je.get(kuerzel, []))
            if grund:
                ausfaelle.append((name, 'unplausibel', 'Quelle unplausibel (%s)' % grund, None))
                continue
            for art, notiz in zusatz['meldungen']:      # erst jetzt: fällt die Mannschaft aus, bleiben ihre Zusatzquellen unerwähnt
                (hinweise if art == 'hinweis' else stumm).append(notiz)
            e['stand'].update(zusatz['stand'])
            # Verlauf (Wiederansetzen nur mit früherer Absetzung): über alle verwendeten Pläne, je Plan vor dem Zeitstempel seines Stands
            staende_genutzt = tuple([(slug, stempel)] + zusatz['verlauf'])
            ctx['verlauf'] = lambda key, staende_genutzt=staende_genutzt: any(
                frueher_abgesetzt(opt.cache, q, key, vor) for q, vor in staende_genutzt)
            plaene[kuerzel] = plane(team, fb, csv_je.get(kuerzel, []), heute, ctx)
        except Exception as ex:
            ausfaelle.append((name, 'ausnahme', 'unerwarteter Fehler (%s: %s)' % (type(ex).__name__, ex), None))
    fehler += ausfaelle_melden(ausfaelle, opt.max_alter)
    e['uebersprungen'] = [a[0] for a in ausfaelle]
    if veraltet:
        hinweise.append('Quelle älter als %g Stunden (%s); im Anwendungsmodus würden diese Mannschaften übersprungen' % (opt.max_alter, ', '.join(veraltet)))
    aenderungen = []
    for t in A.TEAMS:
        if t[0] in plaene:
            aenderungen += sorted(plaene[t[0]].aenderungen, key=Aenderung.sortierschluessel)
            hinweise += plaene[t[0]].hinweise
            stumm += plaene[t[0]].stumm
    e['aenderungen'] = aenderungen

    # 4. Mengenbremse, Prüfung, Schreiben
    e['bremse'] = bremse = mengenbremse(aenderungen)
    neu_bytes = problem = None
    if aenderungen:
        try:
            neu_bytes = neue_datei(datei, aenderungen)       # setzt auch die Zeilennummern für die Anzeige
            if not bremse:
                generator = repo if os.path.exists(os.path.join(repo, 'build_ics.py')) else REPO
                probleme = validiere(datei.original, neu_bytes, aenderungen, generator)
                if probleme:
                    problem = 'Prüfung vor dem Schreiben fehlgeschlagen, nichts geschrieben: ' + '; '.join(probleme[:3])
        except Exception as ex:
            problem = 'Änderungen nicht anwendbar, nichts geschrieben (%s: %s)' % (type(ex).__name__, ex)
        if problem is None and schreiben and not bremse:
            with open(opt.csv, 'rb') as f:
                if sha(f.read()) != sha(datei.original):
                    problem = 'spiele.csv hat sich während des Laufs geändert, nichts geschrieben'
        if problem is None and schreiben and not bremse:
            res = schreibe_und_veroeffentliche(opt, datei, neu_bytes, aenderungen, pushen, repo, csvname, sperre, e['schreibstand'])
            e.update(angewendet=res['angewendet'], commit=res['commit'], gepusht=res['gepusht'])
            problem = res['fehler']
    if problem:
        fehler.append(problem)

    # 5. Ausgabe: nur Änderungen, Hinweise, Fehler und die Schlusszeile
    if e['angewendet']:
        vorspann = ''
    elif bremse or problem:
        vorspann = 'nicht übernommen: '
    elif notaus:
        vorspann = 'wäre fällig: '
    else:
        vorspann = 'Vorschlag '
    for a in aenderungen:
        e['zeilen'].append(vorspann + a.text)
        if not opt.anwenden:    # Berichtsmodus: die CSV-Zeile zeigen, wie sie geschrieben würde
            e['zeilen'].append('    CSV Zeile %s%s: %s' % (a.zeile_neu or '?', ' (neu)' if a.art == 'ergaenzt' else '', a.detail))
    if notaus:
        e['zeilen'].append('Not-Aus aktiv (Datei %s): es wird nichts geschrieben.' % opt.notaus_datei)
    e['zeilen'] += ['Hinweis: ' + t for t in hinweise] + ['Fehler: ' + t for t in fehler]
    if bremse:
        e['zeilen'].append('Auffällig: %s, nichts geschrieben.' % bremse)
    return schluss_bauen(e, aenderungen, schreiben, pushen, problem)


def schluss_bauen(e, aenderungen, schreiben, pushen, problem):
    """Die Schlusszeile: genau ein Zustand vorn (der schwerste), Einzelheiten dahinter. Exit-Code: 1 = Fehler (Schreiben, Git,
    Prüfung, CSV), 2 = auffällig (Mannschaft übersprungen, Mengenbremse, Not-Aus mit offenen Änderungen, nur Hinweise), sonst 0."""
    teile, code = [], 0
    if e['fehler']:
        teile.append('Fehler – ' + '; '.join(e['fehler'][:2]) + (' (+%d weitere)' % (len(e['fehler']) - 2) if len(e['fehler']) > 2 else ''))
        code = 1 if (problem or any(f.startswith('spiele.csv') for f in e['fehler'])) else 2
    if e['bremse']:
        teile.append('Auffällig, nicht übernommen – %s' % e['bremse'])
        code = code or 2
    elif e['notaus'] and aenderungen:
        teile.append('Auffällig, nicht übernommen – Not-Aus aktiv, %s' % (
            'eine Änderung wäre fällig' if len(aenderungen) == 1 else '%d Änderungen wären fällig' % len(aenderungen)))
        code = code or 2
    if e['angewendet']:
        arten = Counter(a.art for a in aenderungen)
        detail = ', '.join('%d %s' % (arten[k], w) for k, w in ART_TEXT if arten[k])
        teile.append('%d geändert (%s)%s' % (len(aenderungen), detail, ', gepusht' if e['gepusht'] else ('' if pushen else ', nicht gepusht')))
    elif aenderungen and not schreiben and not e['bremse'] and not e['notaus'] and not problem:
        teile.append('Nur-Bericht – %s vorgeschlagen, nichts geschrieben' % anzahl(len(aenderungen), 'Änderung', 'Änderungen'))
    if e['nachgepusht']:
        teile.append('%s nachgepusht' % anzahl(e['nachgepusht'], 'liegen gebliebener Commit', 'liegen gebliebene Commits'))
    if e['hinweise'] and not teile:
        teile.append('Auffällig, nicht übernommen – %s (siehe oben)' % anzahl(len(e['hinweise']), 'Hinweis', 'Hinweise'))
        code = code or 2
    elif e['hinweise']:
        teile[-1] += ', %s' % anzahl(len(e['hinweise']), 'Hinweis', 'Hinweise')
    return beenden(e, '; '.join(teile) if teile else 'unverändert', code)


# ---------------------------------------------------------------- Berichtsdatei und Kommandozeile

def bericht_schreiben(ordner, e, opt, jetzt):
    """Markdown-Bericht mit allen Einzelheiten (nur bei Änderungen, Hinweisen oder Fehlern; nie ins Repo)."""
    md = ['# Nachtrag spiele.csv · %s' % jetzt.strftime('%d.%m.%Y %H:%M'), '', '`%s`' % e['schluss'], '',
          'Modus: %s%s' % ('Anwenden' if opt.anwenden else 'Nur-Bericht', ' + Push' if opt.push else ''), '']
    for titel, liste in (('Ausgabe', e['zeilen']), ('Stille Notizen', e['stumm'])):
        if liste:
            md += ['## ' + titel, ''] + ['- ' + z.strip() for z in liste] + ['']
    md += ['## Quellen', ''] + ['- %s: Cache-Stand %s' % (n, t.strftime('%d.%m.%Y %H:%M')) for n, t in sorted(e['stand'].items())] + ['']
    os.makedirs(ordner, exist_ok=True)
    pfad = os.path.join(ordner, 'nachtrag-%s.md' % jetzt.strftime('%Y%m%d-%H%M%S'))
    with open(pfad, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    return pfad


class AufrufFehler(Exception):
    """Ungültiger Aufruf (statt des SystemExit(2) von argparse): main meldet ihn mit Schlusszeile und Exit-Code 1."""

    def __init__(self, text, usage=''):
        super().__init__(text)
        self.usage = usage


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AufrufFehler(message, self.format_usage().strip())


def optionen_aus(argv=None):
    ap = _Parser(description='spiele.csv aus den fussball.de-Mannschaftsplänen fortschreiben (Standard: nur berichten)')
    ap.add_argument('--csv', default=CSV_STANDARD)
    ap.add_argument('--cache', default=A.CACHE_STANDARD)
    ap.add_argument('--anwenden', action='store_true', help='erlaubte Änderungen in die CSV-Datei schreiben')
    ap.add_argument('--push', action='store_true', help='nur mit --anwenden: committen und pushen (Arbeitsbaum muss sauber sein)')
    ap.add_argument('--heute', help='JJJJ-MM-TT (nur zum Testen; ruft nie ab)')
    ap.add_argument('--bericht-ordner', help='Ordner für die Berichtsdatei (im Anwendungsmodus Standard: %s)' % BERICHTE_STANDARD)
    ap.add_argument('--nur-cache', action='store_true', help='nichts abrufen; im Berichtsmodus ohne Altersgrenze')
    ap.add_argument('--max-alter', type=float, default=MAX_ALTER_STUNDEN, help='höchstes Cache-Alter in Stunden (0 = keine Grenze)')
    ap.add_argument('--notaus-datei', default=NOTAUS_STANDARD, help=argparse.SUPPRESS)   # nur für Tests
    a = ap.parse_args(argv)
    if a.push and not a.anwenden:
        ap.error('--push gibt es nur zusammen mit --anwenden')
    if a.heute:
        try:
            date.fromisoformat(a.heute)
        except ValueError:
            ap.error('--heute erwartet JJJJ-MM-TT')
    return Optionen(csv=a.csv, cache=a.cache, anwenden=a.anwenden, push=a.push, heute=a.heute, bericht_ordner=a.bericht_ordner,
                    nur_cache=a.nur_cache, max_alter=a.max_alter, notaus_datei=a.notaus_datei, abrufen=not a.heute)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    try:
        opt = optionen_aus(argv)
    except AufrufFehler as ex:            # --help beendet weiter mit Exit 0 (SystemExit), alles andere ist ein Fehler mit Schlusszeile
        print(ex.usage, file=sys.stderr)
        print('NACHTRAG: Fehler – ungültiger Aufruf (%s)' % anzeige(ex))
        return 1

    def signal_abbruch(signum, frame):
        raise Abbruch(signal.Signals(signum).name)
    alte = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            alte[sig] = signal.signal(sig, signal_abbruch)
        except ValueError:                # nicht im Hauptthread: dann bleibt es bei den Standardsignalen
            pass
    jetzt = datetime.now()
    try:
        try:
            e = lauf(opt, jetzt)
        except Exception as ex:   # nichts darf ohne Schlusszeile enden
            import traceback
            traceback.print_exc(file=sys.stderr)
            print('NACHTRAG: Fehler – unerwartet (%s: %s)' % (type(ex).__name__, anzeige(ex)))
            return 1
        print('\n'.join(e['zeilen'] + [e['schluss']]))
        ordner = opt.bericht_ordner or (BERICHTE_STANDARD if opt.anwenden else None)
        if ordner and (e['aenderungen'] or e['hinweise'] or e['fehler']):     # bei „unverändert“ keine Datei
            try:
                bericht_schreiben(ordner, e, opt, jetzt)
            except Exception as ex:
                print('Hinweis: Berichtsdatei nicht geschrieben (%s)' % ex, file=sys.stderr)
        return e['code']
    except Abbruch as ab:                 # ein Signal nach dem Lauf (lauf() meldet seinen Abbruch selbst)
        print('NACHTRAG: Fehler – abgebrochen (%s)' % ab.name)
        return 1
    finally:
        for sig, alt in alte.items():
            signal.signal(sig, alt)


if __name__ == '__main__':
    sys.exit(main())
