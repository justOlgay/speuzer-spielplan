#!/usr/bin/env python3
"""Spielplan-Abgleich: spiele.csv gegen die öffentlichen Mannschafts-Spielpläne bei fussball.de.

Prüft die Spieltermine in spiele.csv (daraus baut build_ics.py die Kalender-Abos) gegen die öffentlichen Spielpläne der elf
Mannschaften des FFV Sportfreunde 04 Frankfurt und berichtet NUR die Unterschiede (Markdown). Die D3 hat eine Zusatzquelle: die
Freundschaftsspiele der fussball.de-Mannschaft D4 stehen in spiele.csv unter D3 (ZUSATZQUELLEN, quellen()); beide Pläne werden
zusammengeführt und gemeinsam mit den D3-Zeilen der CSV verglichen. Das Skript ist REIN LESEND: Es schreibt nie in spiele.csv
oder sonst ins Repo, committet und pusht nichts und sendet keine Mails. Es gehört nicht zum Kalender-Workflow
(.github/workflows/build.yml), der fussball.de bewusst nicht ausliest.

Rücksicht auf fussball.de:
  - nur die öffentlichen Mannschafts-Spielpläne (ajax.team.matchplan), erkennbarer User-Agent, 3 s Pause zwischen den Abrufen
  - höchstens ein Abruf je Plan und Woche (zwölf Pläne: elf Mannschaften plus die Zusatzquelle D4): liegt ein Abruf jünger als
    6 Tage im Cache, wird er benutzt und nichts abgerufen (eine Option zum Erzwingen gibt es absichtlich nicht); nach einem
    endgültigen Fehlschlag keine weiteren Abrufe im Lauf
  - Wiederholung nur bei einer vorübergehenden Störung: Scheitert ein Abruf an HTTP 408, 429, 500, 502, 503, 504 oder 520 bis 524, an einer
    Zeitüberschreitung, einer abgebrochenen oder verweigerten Verbindung (ConnectionRefusedError, „Verbindung abgelehnt“), einer
    unvollständigen Antwort (IncompleteRead), einem nicht erreichbaren Netz oder Host (ENETUNREACH, EHOSTUNREACH), einem vorübergehenden
    Fehler der Namensauflösung (EAI_AGAIN), einem Verbindungsabbruch beim TLS-Handshake (SSLEOFError) oder an der Proxy-Meldung „Tunnel
    connection failed: 429/500/502/503/504“, wird derselbe Plan nach 60, 120 und 240 s Pause noch bis zu dreimal abgerufen (höchstens vier
    Versuche je Plan); nennt die Antwort ein längeres Retry-After, gilt dieses.
    Ein weiterer Versuch beginnt nur, wenn er spätestens 450 s nach Beginn der Abrufe des Laufs anfängt (--zeitrahmen; der Zeitrahmen
    gilt für den ganzen Lauf, nicht je Plan: wer später im Lauf scheitert, bekommt weniger Versuche). Bei den Standardwerten
    (--zeitrahmen 450) endet die Abrufphase auch im ungünstigsten Fehlerfall nach höchstens 540 s, solange jeder Verbindungsaufbau nach
    60 s aufgibt (Zeitlimit von urlopen); löst der Servername zu mehreren Adressen auf, die alle nicht antworten, kann ein Versuch je
    weiterer Adresse bis zu 60 s länger dauern (urllib probiert jede Adresse mit eigenem Zeitlimit). Ebenfalls bei den Standardwerten
    gibt es höchstens sieben zusätzliche Anfragen je Lauf. Alles andere gilt sofort als endgültig und wird nicht wiederholt: andere
    HTTP-Fehler (403, 404, 501, 505 …), Zertifikats- und andere TLS-Fehler, andere Fehler der Namensauflösung, „Tunnel connection
    failed: 403“ oder „407“ (die Netzfreigabe fehlt) und eine unbrauchbare Antwort. Erst der endgültige Fehlschlag beendet die Abrufe
    des Laufs (Fehlertext unverändert, z. B. „HTTP 503“). Jede Wiederholung steht sofort auf stderr, im Bericht unter „Abrufe dieses
    Laufs“ (mit Diagnose: Kopfzeilen Server, Retry-After und Via, Titel der Fehlerseite) und als Zusatz „n wiederholt“ in den
    Zählzeilen. Ohne Wiederholung bleiben Ausgabe und Bericht unverändert.
  - Wer hole() ohne Wiederholungsobjekt aufruft (tools/nachtrag.py, der Mac-Tageslauf mit hartem Zeitlimit), bekommt wie bisher genau
    einen Versuch je Plan
  - Rohdaten und Bericht landen nie im Repo: Cache im Benutzer-Cache-Ordner, Bericht standardmäßig im temporären Ordner

Aufruf (aus dem Repo-Stamm):
  python3 -I tools/abgleich.py [--csv DATEI] [--bericht-ordner ORDNER] [--cache ORDNER] [--nur-cache]
                               [--heute JJJJ-MM-TT] [--saison JJJJ] [--kein-wochenvergleich] [--zeitrahmen SEKUNDEN]
  --nur-cache              nichts abrufen, den neuesten Cache-Stand je Plan nehmen (zum erneuten Auswerten)
  --kein-wochenvergleich   keinen Vergleich mit dem Bericht der Vorwoche anstellen (z. B. wenn jeder Lauf ohne Vorlauf startet)
  --zeitrahmen SEKUNDEN    Zeitrahmen der Wiederholungen in Sekunden ab Beginn der Abrufe (ganze Zahl, Standard 450); 0 = keine Wiederholung,
                           jeder Abruf nur einmal. Die Pausen (60, 120, 240 s) lassen sich nicht verkürzen. Bei --nur-cache ohne Wirkung.
Reihenfolge und Ausfälle: Zuerst werden die eigenen Pläne aller Mannschaften geholt, danach in einem zweiten Durchgang die Zusatzquellen
(nur für Mannschaften, deren eigener Plan gelesen wurde); der Fehlschlag einer Zusatzquelle hält so keine Mannschaft vom Abruf ab. Fällt
der eigene Plan einer Mannschaft aus, ist sie nicht ausgewertet. Fällt nur eine Zusatzquelle aus (auch mit „nicht abgerufen“ nach einem
früheren Fehlschlag), wird die Mannschaft mit den übrigen Plänen ausgewertet; ihr Abschnitt nennt den Ausfall, und die Spiele der
Zusatzquelle erscheinen als „nur in der CSV“. Ein gültiger Plan ohne Spiele (Rahmen der Seite erkennbar, keine Spielzeilen) ist bei einer
Zusatzquelle kein Ausfall, sondern ein Plan mit 0 Spielen (der Abschnitt vermerkt „ohne Spiele“); bei einem eigenen Plan bleibt er ein Ausfall.
Exit-Code: 0 = Bericht erstellt, 2 = Bericht erstellt, aber mindestens eine Mannschaft nicht ausgewertet oder eine Zusatzquelle
ausgefallen, 1 = Fehler.
"""
import argparse
import calendar
import csv
import difflib
import email.utils
import errno
import glob
import hashlib
import html
import http.client
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

try:
    import ssl
except ImportError:      # Python ohne TLS-Unterstützung: dann gibt es auch keine ssl-Fehler zu unterscheiden
    ssl = None

HOME = os.path.expanduser('~')
CSV_STANDARD = 'spiele.csv'        # relativ zum aktuellen Verzeichnis (Repo-Stamm)
BERICHTE_STANDARD = os.path.join(tempfile.gettempdir(), 'speuzer-spielplan-abgleich', 'berichte')
CACHE_STANDARD = (os.path.join(HOME, 'Library', 'Caches', 'speuzer-spielplan-abgleich') if sys.platform == 'darwin'
                  else os.path.join(HOME, '.cache', 'speuzer-spielplan-abgleich'))
MIN_ABSTAND_TAGE = 6
PAUSE_SEKUNDEN = 3
# Wiederholung bei vorübergehenden Störungen von fussball.de (Klasse Wiederholung, hole): Pausen in Sekunden vor dem 2., 3. und 4. Versuch
# desselben Plans, Zeitrahmen in Sekunden ab Beginn der Abrufe eines Laufs (ein neuer Versuch beginnt nur bis dahin) und die HTTP-Status, die
# als vorübergehend gelten: 408 Request Timeout, 429 Too Many Requests, 500, 502, 503, 504 und die Fehlerseiten 520 bis 524 von Cloudflare
# (unbekannter Fehler, Server down, Verbindung abgelehnt, nicht erreichbar, Zeitüberschreitung). Es gibt bewusst keine Option, die Pausen
# zu verkürzen.
WIEDERHOLUNG_PAUSEN = (60, 120, 240)
ZEITRAHMEN_STANDARD = 450
VORUEBERGEHEND_HTTP = (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524)
DIAGNOSE_SEKUNDEN = 10             # begrenzt das Warten auf jedes einzelne Datenpaket beim Lesen einer HTTP-Fehlerseite (Diagnose), nicht die Gesamtdauer
USER_AGENT = 'Mozilla/5.0 (compatible; SpeuzerSpielplanAbgleich/1.0; rein lesend, ein Abruf je Mannschaft und Woche)'
URL = ('https://www.fussball.de/ajax.team.matchplan/-/datum-von/{von}/datum-bis/{bis}'
       '/max/999/offset/0/mode/PAGE/team-id/{tid}')
EIGEN = 'sportfreunde 04'          # unser Vereinsname in normalisierter Schreibweise
AEHNLICH = 0.88                    # ab hier gilt ein Namensunterschied nur als Schreibweise
# Merkmale einer matchplan-Seite (nur hier definiert, nachtrag.py verwendet dieselben): ein Plan ohne Spiele hat nur den Rahmen
ZEILEN_MARKEN = ('row-headline', 'row-competition')                       # Spielzeilen
RAHMEN_MARKEN = ('id-team-matchplan-table', 'fixtures-matches-table')    # Rahmen der Seite, auch eines Plans ohne Spiele
MIN_SEITENLAENGE = 500                                                    # kürzere Antworten sind keine Spielplanseite

# Kürzel in spiele.csv, Slug, Anzeigename, fussball.de-Team-ID (Mannschaftsliste des Vereins bei fussball.de)
TEAMS = [
    ('HERREN', 'herren', 'Herren', '011MICFB14000000VTVG0001VTR8C1K7'),
    ('A', 'a-jugend', 'A-Jugend', '019ORCB50G000000VV0AG80NVUQ1MD7G'),
    ('D1', 'd1', 'D1', '011MIE88PK000000VTVG0001VTR8C1K7'),
    ('D2', 'd2', 'D2', '011MIF9LDC000000VTVG0001VTR8C1K7'),
    ('D3', 'd3', 'D3', '02USD4SHUG000000VS5489BRVS0D3BPJ'),
    ('E1', 'e1', 'E1', '011MIDBD8C000000VTVG0001VTR8C1K7'),
    ('E2', 'e2', 'E2', '011MIE1KH4000000VTVG0001VTR8C1K7'),
    ('E3', 'e3', 'E3', '02PS0CFJCO000000VS5489B1VVQNIHJA'),
    ('F1', 'f1', 'F1', '011MIF2CMK000000VTVG0001VTR8C1K7'),
    ('F2', 'f2', 'F2', '011MIDEKDC000000VTVG0001VTR8C1K7'),
    ('G1', 'g-jugend', 'G1', '02ILVG354G000000VS5489B1VSO9MMK0'),
]
# Zusatzquellen: fussball.de-Pläne, deren Spiele in spiele.csv unter dem Kürzel einer Mannschaft aus TEAMS stehen (keine eigene
# Mannschaft in der CSV). Aufbau wie TEAMS: Kürzel der CSV-Mannschaft, Slug (Cache-Ordner), Anzeigename, fussball.de-Team-ID.
# D4 = „FFV Sportfreunde 04 (FS) IV“ (D-Junioren, nur Freundschaftsspiele): zweites Spielteam des D3-Kaders. Vereinsentscheidung
# vom 31.10.2026: keine eigene Mannschaft, ihre Spiele stehen als D3 in der CSV und damit in Kalender, Terminseite und App der D3.
ZUSATZQUELLEN = [('D3', 'd4', 'D4 (Freundschaftsspiele der D3)', '0316LKDK8S000000VS5489BRVVV10ESU')]

ART_CODE = {'ME': 'Meisterschaft', 'PO': 'Kreispokal', 'FS': 'Freundschaftsspiel'}
WOCHENTAG = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
DIFF_TITEL = {
    'fehlt': 'Fehlt in der CSV (fussball.de führt das Spiel)',
    'ueberzaehlig': 'Nur in der CSV (bei fussball.de nicht vorhanden)',
    'termin': 'Termin abweichend',
    'heimgast': 'Heim/Auswärts abweichend',
    'gegner': 'Gegner abweichend',
    'csv_aktiv': 'Bei fussball.de abgesetzt oder zurückgezogen, in der CSV noch angesetzt',
    'csv_abgesagt': 'In der CSV „abgesagt“, bei fussball.de wieder angesetzt',
    'wettbewerb': 'Wettbewerbsart abweichend',
    'nummer': 'Spielnummer oder Staffel abweichend',
    'csv_doppelt': 'Schlüssel (Staffel + Spielnummer) doppelt in der CSV',
}


# ---------------------------------------------------------------- Hilfsfunktionen

def sauber(t):
    t = html.unescape(re.sub(r'<[^>]+>', '', t or ''))
    return re.sub(r'\s+', ' ', t.replace('​', '').replace('\xa0', ' ')).strip()


def norm(s):
    """Vereinsnamen vergleichbar machen: Umlaute, Leerzeichen um „/“, Zusätze „zg.“ und „- Kinderfestival“ weg."""
    s = sauber(s).lower()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        s = s.replace(a, b)
    s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    s = re.sub(r'\s*-\s*kinderfestival\s*$', '', s)
    s = re.sub(r'\s+zg\.?\s*$', '', s)
    s = re.sub(r'\s*/\s*', '/', s).replace('.', '')
    return re.sub(r'\s+', ' ', s).strip()


def ohne_zusatz(name):
    """Anzeigename ohne „zg.“ und „- Kinderfestival“."""
    n = re.sub(r'\s*-\s*Kinderfestival\s*$', '', sauber(name))
    return re.sub(r'\s+zg\.?\s*$', '', n)


def fmt(d_iso, zeit=''):
    if not d_iso:
        return 'ohne Datum'
    d = date.fromisoformat(d_iso)
    return '%s %s %s' % (WOCHENTAG[d.weekday()], d.strftime('%d.%m.%Y'), zeit if zeit else '(ohne Uhrzeit)')


def kurz_datum(d_iso):
    return date.fromisoformat(d_iso).strftime('%d.%m.') if d_iso else '–'


def sha256(pfad):
    with open(pfad, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def quellen(team):
    """Alle fussball.de-Pläne einer CSV-Mannschaft: zuerst ihr eigener, dann ihre Zusatzquellen (ZUSATZQUELLEN mit demselben Kürzel)."""
    return [team] + [q for q in ZUSATZQUELLEN if q[0] == team[0]]


# ---------------------------------------------------------------- Wiederholung bei vorübergehenden Störungen

TUNNEL_STOERUNG = re.compile(r'Tunnel connection failed: (?:429|50[0234])\b')     # der Proxy meldet eine Störung des Ziels (403, 407: Netzfreigabe fehlt)
EAI_AGAIN = getattr(socket, 'EAI_AGAIN', None)                                     # „Temporary failure in name resolution“
NETZ_NICHT_ERREICHBAR = (errno.ENETUNREACH, errno.EHOSTUNREACH)                    # kein Weg zum Netz oder zum Host (OSError, kein ConnectionError)


def voruebergehend(e):
    """True, wenn ein Abruf, der mit der Ausnahme `e` scheiterte, an einer vorübergehenden Störung scheiterte und eine Wiederholung lohnt:
    HTTPError mit einem Status aus VORUEBERGEHEND_HTTP; Zeitüberschreitung (TimeoutError, socket.timeout); abgebrochene oder verweigerte
    Verbindung (ConnectionError mit allen Unterklassen, auch http.client.RemoteDisconnected und ConnectionRefusedError); unvollständige
    Antwort (http.client.IncompleteRead); Verbindungsabbruch beim TLS-Handshake (ssl.SSLEOFError); vorübergehender Fehler der
    Namensauflösung (socket.gaierror mit dem Code EAI_AGAIN); OSError ENETUNREACH oder EHOSTUNREACH (Netz oder Host nicht erreichbar);
    jeweils auch als Grund einer URLError; URLError oder OSError mit der Proxy-Meldung „Tunnel connection failed: 429/500/502/503/504 …“.
    Alles andere ist endgültig: andere HTTP-Status (403, 404, 501, 505 …), Zertifikats- und andere ssl-Fehler, andere Fehler der
    Namensauflösung (zum Beispiel EAI_NONAME), „Tunnel connection failed: 403 …“ oder „407 …“ (die Netzfreigabe der Umgebung fehlt) und
    jede sonstige Ausnahme."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in VORUEBERGEHEND_HTTP
    grund = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(grund, (TimeoutError, socket.timeout, ConnectionError, http.client.IncompleteRead)):
        return True
    if ssl is not None and isinstance(grund, ssl.SSLEOFError):
        return True
    if isinstance(grund, socket.gaierror):
        return EAI_AGAIN is not None and grund.errno == EAI_AGAIN
    if isinstance(grund, OSError) and grund.errno in NETZ_NICHT_ERREICHBAR:
        return True
    return isinstance(e, OSError) and TUNNEL_STOERUNG.search(str(grund)) is not None


def sekunden(x):
    """Sekundenzahl für Meldungen (ganze Zahlen ohne Komma)."""
    return str(int(x)) if x == int(x) else '%g' % x


def bereinigt(text, n=80):
    """Text aus einer fremden Antwort für eine Zeile des Berichts: ohne HTML, Entitäten aufgelöst, Leerraum zusammengefasst, nur druckbare
    Zeichen, höchstens `n` Zeichen."""
    t = sauber(re.sub(r'<[^>]*>', '', sauber(text)))
    t = ''.join(c for c in t if c.isprintable())
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def kopfzeilen(kopf, name):
    """Alle Werte der Kopfzeile `name` (Groß- und Kleinschreibung egal) aus einer Mail-Message, einem dict oder None; wirft nie."""
    try:
        return [str(v) for k, v in kopf.items() if str(k).lower() == name.lower()]
    except Exception:
        return []


def retry_after(werte, wanduhr):
    """Retry-After (Sekundenzahl oder HTTP-Datum; bei mehreren Kopfzeilen die längste Angabe) -> Sekunden, aufgerundet, mindestens 1 und
    höchstens 10**7, oder None, wenn nichts Brauchbares dasteht (leer, ungültig, negativ, Datum in der Vergangenheit). `wanduhr`: die
    Zeit in Sekunden seit 1970."""
    beste = None
    for wert in werte:
        try:
            wert = wert.strip()
            if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', wert):
                sek = float(wert)
            else:
                t = email.utils.parsedate_tz(wert)
                if t is None:
                    continue
                sek = calendar.timegm(t[:6]) - (t[9] or 0) - wanduhr()
            sek = 10 ** 7 if sek > 10 ** 7 else math.ceil(sek)
        except Exception:
            continue
        if sek > 0 and (beste is None or sek > beste):
            beste = sek
    return beste


def fehlerseite_titel(e):
    """Titel (<title>) der Fehlerseite einer HTTPError-Antwort, bereinigt auf höchstens 80 Zeichen, sonst ''. Liest höchstens 4096 Bytes;
    DIAGNOSE_SEKUNDEN begrenzt das Warten auf jedes einzelne Datenpaket, nicht die Gesamtdauer (eine Fehlerseite, die Byte für Byte
    tropft, hält das Lesen länger auf). Schließt die Antwort und wirft nie eine Ausnahme."""
    titel = ''
    try:
        try:
            e.fp.fp.raw._sock.settimeout(DIAGNOSE_SEKUNDEN)       # HTTPResponse -> BufferedReader -> SocketIO -> Socket, soweit vorhanden
        except Exception:
            pass
        roh = e.read(4096)
        text = roh.decode('utf-8', 'replace') if isinstance(roh, (bytes, bytearray)) else str(roh)
        m = re.search(r'<title[^>]*>(.*?)(?:</title>|$)', text, re.I | re.S)
        titel = bereinigt(m.group(1)) if m else ''
    except Exception:
        titel = ''
    schliessen(e)
    return titel


def diagnose_von(e, wanduhr):
    """-> (Diagnosetext, Retry-After in Sekunden oder None) zur Antwort eines HTTP-Fehlers: die Kopfzeilen Server, Retry-After und Via (soweit
    vorhanden) und der Titel der Fehlerseite; für jede andere Ausnahme ('', None). Liest höchstens 4096 Bytes der Fehlerseite (siehe
    fehlerseite_titel: DIAGNOSE_SEKUNDEN gilt je Datenpaket, nicht für die Gesamtdauer), schließt die Antwort und wirft nie eine Ausnahme."""
    if not isinstance(e, urllib.error.HTTPError):
        return '', None
    teile, wartezeit = [], None
    try:
        kopf = e.headers
        for name in ('Server', 'Retry-After', 'Via'):
            wert = bereinigt(', '.join(kopfzeilen(kopf, name)))
            if wert:
                teile.append('%s: %s' % (name, wert))
        wartezeit = retry_after(kopfzeilen(kopf, 'Retry-After'), wanduhr)
    except Exception:
        pass
    titel = fehlerseite_titel(e)
    if titel:
        teile.append('Seitentitel: „%s“' % titel)
    return '; '.join(teile), wartezeit


def schliessen(e):
    """Die Antwort eines HTTP-Fehlers schließen (nie eine Ausnahme, mehrfaches Schließen schadet nicht)."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            e.close()
        except Exception:
            pass


class Wiederholung:
    """Die Wiederholungen eines Laufs. main() legt ein Objekt je Lauf an (direkt vor dem ersten Abruf) und reicht es über plan_lesen an hole;
    hole fragt es nach jedem gescheiterten Versuch (nach_fehler), wartet die genannte Pause (warten) und meldet am Ende den Ausgang des Plans
    (abschliessen). Ohne dieses Objekt gibt es in hole genau einen Versuch.

    Regeln: Nach einer vorübergehenden Störung (voruebergehend) gibt es noch einen Versuch, wenn für ihn noch eine Pause in WIEDERHOLUNG_PAUSEN
    übrig ist (der n-te Fehlversuch löst die n-te Pause aus, also höchstens vier Versuche je Plan) und die seit `start` vergangene Zeit plus
    Pause höchstens `zeitrahmen` Sekunden beträgt; die Pause ist die längere aus der Tabelle und dem Retry-After der Antwort. Sonst ist der
    Fehlschlag endgültig. Ein `zeitrahmen` von 0 schaltet alles ab: keine Wiederholung, keine Meldung, kein Protokoll.

    Merkmale: `start` (Stand der Uhr beim Anlegen), `zeitrahmen`, `notizen` (je Plan, bei dem eine vorübergehende Störung auftrat, eine Zeile für
    den Bericht) und `zaehler` (Zahl der Pläne, die mehr als einen Versuch brauchten). Uhr (`uhr`, Standard time.monotonic), Schlaf (`schlaf`,
    time.sleep) und Wanduhr (`wanduhr`, time.time, nur für ein Retry-After als HTTP-Datum) lassen sich für Tests ersetzen; ohne Angabe wird die
    Funktion des time-Moduls erst beim Aufruf nachgeschlagen."""

    def __init__(self, zeitrahmen=None, uhr=None, schlaf=None, wanduhr=None):
        self.zeitrahmen = ZEITRAHMEN_STANDARD if zeitrahmen is None else zeitrahmen
        self._uhr, self._schlaf, self._wanduhr = uhr, schlaf, wanduhr
        self.start = self.jetzt()
        self.beginn = datetime.now()               # Uhrzeit in den Zeilen des Berichts: beginn plus die seit start vergangene Zeit
        self.notizen = []
        self.zaehler = 0

    def jetzt(self):
        return (self._uhr or time.monotonic)()

    def wandzeit(self):
        return (self._wanduhr or time.time)()

    def warten(self, sekunden_):
        (self._schlaf or time.sleep)(sekunden_)

    def uhrzeit(self):
        return (self.beginn + timedelta(seconds=self.jetzt() - self.start)).strftime('%H:%M:%S')

    def beginnen(self, name):
        """Das Protokoll eines Plans (`name` wie in den Zeilen des Berichts), den hole jetzt abruft."""
        return dict(name=name, versuche=[], ausgang=None, aktiv=False)

    def nach_fehler(self, verlauf, versuch, fehler, ausnahme):
        """Der Versuch Nummer `versuch` (ab 1) ist gescheitert: `fehler` ist der Fehlertext, `ausnahme` die Ausnahme dahinter (None bei einer
        unbrauchbaren Antwort) -> Sekunden bis zum nächsten Versuch oder None, wenn der Fehlschlag endgültig ist. Tritt eine vorübergehende
        Störung auf, legt es das Protokoll des Plans an, liest bei einem HTTP-Fehler die Diagnose aus der Antwort und meldet jede Wiederholung
        (und jeden Verzicht darauf) sofort auf stderr. Ein Fehlschlag, der gleich endgültig ist, bleibt ohne Meldung."""
        if self.zeitrahmen <= 0:
            return None
        stoerung = ausnahme is not None and voruebergehend(ausnahme)
        if not stoerung and not verlauf['aktiv']:
            return None
        verlauf['aktiv'] = True
        diagnose, wartezeit = diagnose_von(ausnahme, self.wandzeit)
        eintrag = dict(nr=versuch, um=self.uhrzeit(), text=fehler + (' [%s]' % diagnose if diagnose else ''), pause=None)
        verlauf['versuche'].append(eintrag)
        grund = pause = None
        if not stoerung:
            grund = 'endgültig gescheitert'
        elif versuch > len(WIEDERHOLUNG_PAUSEN):
            grund = 'endgültig gescheitert nach %d Versuchen' % versuch
        else:
            standard = WIEDERHOLUNG_PAUSEN[versuch - 1]
            pause = max(standard, wartezeit or 0)
            verbraucht = self.jetzt() - self.start
            if verbraucht + pause > self.zeitrahmen:
                if wartezeit and wartezeit > standard:
                    grund = 'Retry-After länger als der Zeitrahmen (Retry-After %s s, im Zeitrahmen von %s s noch %d s übrig)' % (
                        sekunden(wartezeit), sekunden(self.zeitrahmen), max(0, self.zeitrahmen - verbraucht))
                else:
                    grund = 'Zeitrahmen erschöpft (%d s von %s s verbraucht, nächste Pause %s s)' % (
                        verbraucht, sekunden(self.zeitrahmen), sekunden(pause))
                pause = None
        name = verlauf['name']
        if pause is None:
            verlauf['ausgang'] = grund
            print('  %s: %s – kein weiterer Versuch: %s' % (name, fehler, grund), file=sys.stderr)
        else:
            eintrag['pause'] = pause
            print('  %s: %s – neuer Versuch in %s s (Versuch %d von höchstens %d)' % (
                name, fehler, sekunden(pause), versuch + 1, len(WIEDERHOLUNG_PAUSEN) + 1), file=sys.stderr)
        if diagnose:
            print('    Diagnose: %s' % diagnose, file=sys.stderr)
        return pause

    def abschliessen(self, verlauf, versuch, fehler):
        """Der Plan ist abgeschlossen (Erfolg, wenn `fehler` None ist, sonst endgültig gescheitert) nach `versuch` Versuchen: zählt ihn als
        wiederholt, wenn es mehr als einen gab, und legt seine Zeile für den Bericht an, falls eine vorübergehende Störung auftrat."""
        if versuch > 1:
            self.zaehler += 1
        if not verlauf['aktiv']:
            return
        if fehler is None:
            verlauf['versuche'].append(dict(nr=versuch, um=self.uhrzeit(), text='Erfolg', pause=None))
            verlauf['ausgang'] = 'Erfolg im %d. Versuch' % versuch
        teile = []
        for v in verlauf['versuche']:
            pause = ', Pause %s s' % sekunden(v['pause']) if v['pause'] is not None else ''
            teile.append('Versuch %d (%s): %s%s' % (v['nr'], v['um'], v['text'], pause))
        self.notizen.append('%s: Wiederholung – %s · Ausgang: %s' % (verlauf['name'], ' · '.join(teile), verlauf['ausgang']))


def ganze_sekunden(text):
    """Wert von --zeitrahmen: eine ganze Zahl von mindestens 0."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError('keine ganze Zahl: %r' % text)
    if n < 0:
        raise argparse.ArgumentTypeError('muss mindestens 0 sein: %d' % n)
    return n


# ---------------------------------------------------------------- Abruf mit Wochensperre

def cache_stand(cache, slug):
    """(Pfad, Zeitstempel) des neuesten Cache-Abrufs oder None."""
    dateien = sorted(glob.glob(os.path.join(cache, slug, '*.html')))
    for pfad in reversed(dateien):
        try:
            return pfad, datetime.strptime(os.path.basename(pfad)[:15], '%Y%m%d-%H%M%S')
        except ValueError:
            continue
    return None


def abruf_versuch(req):
    """Ein einzelner Abruf -> (Text, Fehlertext oder None, Ausnahme oder None). Die Fehlertexte sind die, die hole seit jeher meldet
    („HTTP 503“, „TimeoutError: …“, „unerwartete Antwort (Status …)“)."""
    fehler, text, ausnahme = None, '', None
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode('utf-8', 'replace')
            if r.status != 200 or len(text) < MIN_SEITENLAENGE:
                fehler = 'unerwartete Antwort (Status %s, %d Zeichen)' % (r.status, len(text))
    except urllib.error.HTTPError as e:
        fehler, ausnahme = 'HTTP %s' % e.code, e
    except Exception as e:  # Netz, Zeitüberschreitung, Zertifikat …
        fehler, ausnahme = '%s: %s' % (type(e).__name__, e), e
    return text, fehler, ausnahme


def hole(cache, team, von, bis, nur_cache, jetzt, abbruch, url_vorlage=URL, wiederholung=None):
    """Gibt (html, Quelle, Zeitstempel, Fehler) zurück. Ruft höchstens einmal je MIN_ABSTAND_TAGE ab. `team`: ein Tupel
    wie in TEAMS, auch eine Zusatzquelle (eigener Slug, eigene Wochensperre, eigene Fehlversuch-Marke).
    Wird ein Abruf abgewiesen oder scheitert er endgültig, landet der Grund in `abbruch`; danach wird gar nichts mehr abgerufen.
    Nach einem Fehlversuch gibt es für diese Mannschaft 20 Stunden lang keinen neuen Versuch.
    `wiederholung`: das Objekt der Klasse Wiederholung, das main je Lauf anlegt. Mit ihm wird ein Abruf, der an einer vorübergehenden Störung
    scheitert, nach Pausen wiederholt (Regeln dort); `abbruch` und die Fehlversuch-Marke bekommen nur den endgültigen Fehlschlag, der Fehlertext
    bleibt der des letzten Versuchs. Ohne `wiederholung` (so ruft nachtrag.py auf) gibt es genau einen Versuch."""
    kuerzel, slug, name, tid = team
    stand = cache_stand(cache, slug)
    if stand and (nur_cache or jetzt - stand[1] < timedelta(days=MIN_ABSTAND_TAGE)):
        with open(stand[0], encoding='utf-8') as f:
            return f.read(), 'Cache', stand[1], None
    if nur_cache:
        return None, '', None, 'kein Cache-Stand vorhanden (--nur-cache)'
    if abbruch:
        return None, '', None, 'nicht abgerufen, weil ein vorheriger Abruf scheiterte (%s)' % abbruch[0]
    marke = os.path.join(cache, slug, 'fehlversuch.txt')
    try:
        if jetzt - datetime.strptime(open(marke).read().strip(), '%Y%m%d-%H%M%S') < timedelta(hours=20):
            abbruch.append('%s: letzter Abruf schlug vor weniger als 20 Stunden fehl' % name)
            return None, '', None, 'letzter Abruf schlug vor weniger als 20 Stunden fehl, kein neuer Versuch'
    except (OSError, ValueError):
        pass
    url = url_vorlage.format(von=von, bis=bis, tid=tid)
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Language': 'de-DE,de;q=0.9',
                                                'Accept': 'text/html'})
    verlauf = wiederholung.beginnen(name) if wiederholung is not None else None
    versuch = 1
    while True:
        text, fehler, ausnahme = abruf_versuch(req)
        pause = wiederholung.nach_fehler(verlauf, versuch, fehler, ausnahme) if wiederholung is not None and fehler else None
        schliessen(ausnahme)
        if pause is None:
            break
        wiederholung.warten(pause)
        versuch += 1
    if wiederholung is not None:
        wiederholung.abschliessen(verlauf, versuch, fehler)
    ordner = os.path.join(cache, slug)
    os.makedirs(ordner, exist_ok=True)
    if fehler:
        abbruch.append('%s: %s' % (name, fehler))
        with open(marke, 'w') as f:
            f.write(jetzt.strftime('%Y%m%d-%H%M%S'))
        return None, '', None, fehler
    if os.path.exists(marke):
        os.remove(marke)
    with open(os.path.join(ordner, jetzt.strftime('%Y%m%d-%H%M%S') + '.html'), 'w', encoding='utf-8') as f:
        f.write(text)
    for alt in glob.glob(os.path.join(ordner, '*.html')):  # Cache aufräumen: nichts älter als 21 Tage
        try:
            if jetzt - datetime.strptime(os.path.basename(alt)[:15], '%Y%m%d-%H%M%S') > timedelta(days=21):
                os.remove(alt)
        except (ValueError, OSError):
            pass
    time.sleep(PAUSE_SEKUNDEN)
    return text, 'Abruf', jetzt, None


# ---------------------------------------------------------------- fussball.de-Spielplan lesen

def seite_defekt(text):
    """Grund, warum `text` kein Mannschaftsplan von fussball.de sein kann (zu kurz, Sperr- oder Fehlerseite), sonst None. Ein
    gültiger Plan ohne Spiele (Sommerpause, noch kein Termin) ist kein Defekt."""
    if len(text or '') < MIN_SEITENLAENGE:
        return 'Seite zu kurz (%d Zeichen)' % len(text or '')
    if not any(m in text for m in ZEILEN_MARKEN + RAHMEN_MARKEN):
        return 'kein Spielplan erkannt (Seitenaufbau geändert oder Sperrseite)'
    return None


def leerer_plan(text):
    """True, wenn `text` ein gültiger Mannschaftsplan ohne ein einziges Spiel ist: lang genug, Rahmen der Seite vorhanden, keine
    Spielzeilen (z. B. eine Mannschaft, die nur Freundschaftsspiele hat, solange nichts angesetzt ist). Eine Sperrseite oder eine
    Seite mit geändertem Aufbau (auch Spielzeilen, die sich nicht lesen lassen) ist kein leerer Plan."""
    return seite_defekt(text) is None and not any(m in text for m in ZEILEN_MARKEN)


def parse_spielplan(s):
    """Zerlegt ajax.team.matchplan in Spiele. Zeilen mit Klasse „odd row-competition“ werden mitgezählt.
    Das Datum steht verlässlich nur in der Überschriftszeile („Samstag, 19.09.2026 - 11:00 Uhr | Wettbewerb“):
    folgt ein Spiel am selben Tag, fehlt es in der Datumszelle."""
    spiele, akt, kopf = [], None, None
    for z in re.findall(r'<tr(?: [^>]*)?>.*?</tr>', s, re.S):
        kl = re.match(r'<tr([^>]*)>', z).group(1)
        if 'row-headline' in kl:
            m = re.search(r'(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(?:(\d{1,2}:\d{2})\s*)?Uhr', sauber(z))
            kopf = ('%s-%s-%s' % (m.group(3), m.group(2), m.group(1)), (m.group(4) or '').zfill(5) if m.group(4) else '') if m else None
        elif 'row-competition' in kl:
            d = re.search(r'column-date">(.*?)</td>', z, re.S)
            comp = re.search(r'column-team">\s*<a[^>]*>(.*?)</a>', z, re.S)
            info = re.findall(r'<td colspan="2">\s*<a[^>]*>(.*?)</a>', z, re.S)
            akt = dict(datum_roh=sauber(d.group(1)) if d else '', wettbewerb=sauber(comp.group(1)) if comp else '',
                       info=sauber(info[0]) if info else '', kopf=kopf)
            kopf = None
        elif akt is not None and 'column-club' in z:
            namen = [sauber(n) for n in re.findall(r'<div class="club-name">(.*?)</div>', z, re.S)]
            sc = re.search(r'class="column-score">(.*?)</td>', z, re.S)
            st = re.search(r'class="info-text">(.*?)</span>', sc.group(1), re.S) if sc else None
            m = re.search(r'(\d{2})\.(\d{2})\.(\d{2})\b', akt['datum_roh'])
            zeit = re.search(r'(\d{1,2}:\d{2})\s*$', akt['datum_roh'])
            datum = '20%s-%s-%s' % (m.group(3), m.group(2), m.group(1)) if m else ''
            zeit = zeit.group(1).zfill(5) if zeit else ''
            if akt['kopf']:
                datum, zeit = akt['kopf'][0], akt['kopf'][1] or zeit
            i = re.match(r'([A-Z]{1,4})\s*\|\s*(\d{4,})', akt['info'])
            nrvoll = i.group(2) if i else ''
            spiele.append(dict(
                datum=datum, zeit=zeit, wettbewerb=akt['wettbewerb'], code=i.group(1) if i else '',
                staffel=nrvoll[:-3] if nrvoll else '', nr=int(nrvoll[-3:]) if nrvoll else None,
                heim=namen[0] if namen else '', gast=namen[1] if len(namen) > 1 else '',
                status_txt=sauber(st.group(1)) if st else ''))
            akt = None
    return spiele


def seite_und_gegner(heim, gast, fest):
    """Heim/Auswärts aus Sicht unserer Mannschaft und der Gegner (beim Kinderfestival: der Gastgeber, sonst leer)."""
    if fest:
        host = ohne_zusatz(heim)
        return ('H', '') if EIGEN in norm(host) else ('A', host)
    h, g = EIGEN in norm(heim), EIGEN in norm(gast)
    if h and not g:
        return 'H', ohne_zusatz(gast)
    if g and not h:
        return 'A', ohne_zusatz(heim)
    return '?', ohne_zusatz(gast or heim)


def status_von(g):
    st = g['status_txt'].lower()
    if not g['heim'] or not g['gast']:
        return 'spielfrei'
    if re.search(r'\bzg\.?(\s|$)', g['heim'].lower()) or re.search(r'\bzg\.?(\s|$)', g['gast'].lower()):
        return 'zurueckgezogen'
    if st.startswith('absetzung'):
        return 'abgesetzt'
    if st.startswith('nichtantritt'):
        return 'nichtantritt'
    if re.match(r'\d{2}\.\d{2}\.\d{4}', st):
        return 'verlegt'
    return 'angesetzt'


def fb_normalisieren(g):
    fest = g['heim'].rstrip().endswith('Kinderfestival') or g['wettbewerb'] == 'Kinderfußball'
    ha, gegner = seite_und_gegner(g['heim'], g['gast'], fest)
    return dict(quelle='fussball.de', datum=g['datum'], zeit=g['zeit'], wettbewerb=g['wettbewerb'], code=g['code'],
                art='Kinderfestival' if fest else ART_CODE.get(g['code'], g['code'] or '?'),
                staffel=g['staffel'], nr=g['nr'], heim=g['heim'], gast=g['gast'], ha=ha, gegner=gegner,
                status=status_von(g), status_txt=g['status_txt'], hinweise=[])


def waehle(liste):
    """Mehrere fussball.de-Zeilen mit derselben Spielnummer (Verlegung) auf die gültige reduzieren."""
    if len(liste) == 1:
        return liste[0]
    gueltig = [g for g in liste if g['status'] != 'verlegt']
    verlegt = [g for g in liste if g['status'] == 'verlegt']
    if gueltig:
        aktiv = [g for g in gueltig if g['status'] in ('angesetzt', 'nichtantritt')] or gueltig
        pick = max(aktiv, key=lambda g: (g['datum'], g['zeit']))
        for v in verlegt:
            pick['hinweise'].append('bei fussball.de verlegt von %s auf %s' % (fmt(v['datum'], v['zeit']), v['status_txt']))
        return pick
    pick = dict(liste[-1], hinweise=list(liste[-1]['hinweise']))
    m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})\s*(\d{2}:\d{2})?', pick['status_txt'])
    if m:
        pick['datum'], pick['zeit'], pick['status'] = '%s-%s-%s' % (m.group(3), m.group(2), m.group(1)), m.group(4) or '', 'angesetzt'
        pick['hinweise'].append('Ziel einer Verlegung (bei fussball.de ohne eigene Zeile)')
    return pick


# ---------------------------------------------------------------- spiele.csv lesen

def csv_lesen(pfad):
    zeilen, fehler = [], []
    with open(pfad, encoding='utf-8-sig', newline='') as f:
        for n, r in enumerate(csv.DictReader(f, delimiter=';'), start=2):
            try:
                d = datetime.strptime(r['datum'].strip(), '%d.%m.%Y').date().isoformat()
                fest = r.get('wettbewerb', '').strip() == 'Kinderfestival'
                ha, gegner = seite_und_gegner(r['heim'], r['gast'], fest)
                zeilen.append(dict(
                    quelle='CSV', zeile=n, team=r['team'].strip(), staffel=r['staffel'].strip(), nr=int(r['spielnr']),
                    datum=d, zeit=r['zeit'].strip()[:5], art=r.get('wettbewerb', '').strip() or 'Meisterschaft',
                    heim=r['heim'].strip(), gast=r['gast'].strip(), ha=ha, gegner=gegner,
                    abgesagt=r.get('ergebnis', '').strip().lower() == 'abgesagt', fbid=r.get('fbid', '').strip()))
            except (AttributeError, TypeError):
                fehler.append('Zeile %d: unvollständig' % n)
            except (ValueError, KeyError) as e:
                fehler.append('Zeile %d: %s' % (n, e))
    return zeilen, fehler


def git_info(csv_pfad):
    ordner = os.path.dirname(os.path.abspath(csv_pfad))

    def git(*a):
        try:
            r = subprocess.run(['git', '--no-optional-locks', '-C', ordner] + list(a), capture_output=True, text=True, timeout=20)
            return r.returncode, r.stdout.strip()
        except Exception:
            return 99, ''
    rc, letzter = git('log', '-1', '--format=%h vom %ad: %s', '--date=format:%d.%m.%Y', '--', os.path.basename(csv_pfad))
    _, geaendert = git('status', '--porcelain', '--', os.path.basename(csv_pfad))
    rc2, _ = git('diff', '--quiet', 'origin/main', 'HEAD', '--', os.path.basename(csv_pfad))
    if rc != 0 or not letzter:
        return 'kein Git-Stand lesbar'
    teile = ['letzte Änderung an der Datei: Commit ' + letzter]
    teile.append('lokal ungespeicherte Änderungen vorhanden' if geaendert else 'keine ungespeicherten Änderungen')
    if rc2 == 1:
        teile.append('WEICHT von origin/main ab (nicht gepusht oder Stand nicht aktuell; Stand des letzten fetch)')
    elif rc2 != 0:
        teile.append('Vergleich mit origin/main nicht möglich')
    return '; '.join(teile)


# ---------------------------------------------------------------- Vergleich

def gegner_gleich(a, b):
    na, nb = norm(a), norm(b)
    if na == nb:
        return 'gleich'
    if difflib.SequenceMatcher(None, na, nb).ratio() >= AEHNLICH:
        return 'schreibweise'
    return 'anders'


def vergleiche(team, fb_zeilen, csv_zeilen, heute):
    kuerzel = team[0]
    info = defaultdict(int)
    ohne_nr = []
    gruppen = defaultdict(list)
    for g in fb_zeilen:
        if g['status'] == 'spielfrei':
            info['spielfrei'] += 1
        elif g['nr'] is None:
            ohne_nr.append(g)
        else:
            gruppen[(g['staffel'], g['nr'])].append(g)
    fb = {}
    for k, liste in gruppen.items():
        fb[k] = waehle(liste)
        if len(liste) > 1:
            info['doppelzeilen'] += 1
    csvm, diffs = {}, []
    for r in csv_zeilen:
        k = (r['staffel'], r['nr'])
        if k in csvm:
            diffs.append(dict(art='csv_doppelt', staffel=k[0], nr=k[1], csv=r, fb=None, sort=r['datum'],
                              text='Zeilen %d und %d' % (csvm[k]['zeile'], r['zeile'])))
        csvm[k] = r

    offen_fb, offen_csv, ohne_termin = [], [], []
    for k in sorted(set(fb) | set(csvm)):
        g, r = fb.get(k), csvm.get(k)
        if g and not r:
            if g['status'] in ('angesetzt', 'nichtantritt') and not g['datum']:
                ohne_termin.append(g)
            elif g['status'] in ('angesetzt', 'nichtantritt'):
                offen_fb.append(g)
            else:
                info['fb_entfaellt_ohne_csv'] += 1
        elif r and not g:
            if r['abgesagt']:
                info['csv_abgesagt_ohne_fb'] += 1
            else:
                offen_csv.append(r)
        else:
            paar_vergleichen(k, g, r, diffs, info)
    offen_fb += [g for g in ohne_nr if g['status'] in ('angesetzt', 'nichtantritt')]

    # zweiter Durchgang: gleiche Paarung unter anderer Spielnummer/Staffel
    for g in list(offen_fb):
        kand = [r for r in offen_csv if r['ha'] == g['ha'] and r['art'] == g['art'] and gegner_gleich(g['gegner'], r['gegner']) != 'anders'
                and g['datum'] and abs((date.fromisoformat(r['datum']) - date.fromisoformat(g['datum'])).days) <= 60]
        if kand:
            r = min(kand, key=lambda x: abs((date.fromisoformat(x['datum']) - date.fromisoformat(g['datum'])).days))
            offen_fb.remove(g)
            offen_csv.remove(r)
            diffs.append(dict(art='nummer', staffel=g['staffel'], nr=g['nr'], fb=g, csv=r, sort=min(g['datum'], r['datum']),
                              text='fussball.de %s·%s am %s, CSV %s·%s am %s' % (
                                  g['staffel'] or '?', g['nr'] if g['nr'] is not None else '?', kurz_datum(g['datum']),
                                  r['staffel'], r['nr'], kurz_datum(r['datum']))))
    for g in offen_fb:
        diffs.append(dict(art='fehlt', staffel=g['staffel'], nr=g['nr'], fb=g, csv=None, sort=g['datum'] or '9999', text=''))
    for r in offen_csv:
        diffs.append(dict(art='ueberzaehlig', staffel=r['staffel'], nr=r['nr'], fb=None, csv=r, sort=r['datum'], text=''))

    for d in diffs:
        daten = [x['datum'] for x in (d['fb'], d['csv']) if x and x['datum']]
        d['kommend'] = (max(daten) >= heute) if daten else True
        d['team'] = kuerzel
        d['id'] = '%s|%s|%s-%s' % (kuerzel, d['art'], d['staffel'] or '?', ('%03d' % d['nr']) if d['nr'] is not None else '?')
    diffs.sort(key=lambda d: (d['sort'], d['staffel'] or '', d['nr'] or 0))
    return diffs, staffel_uebersicht(fb, ohne_nr, csv_zeilen, heute), info, ohne_termin


def paar_vergleichen(k, g, r, diffs, info):
    def neu(art, text, kfb=g, kcsv=r):
        diffs.append(dict(art=art, staffel=k[0], nr=k[1], fb=kfb, csv=kcsv, sort=min(x for x in (g['datum'], r['datum']) if x) if (g['datum'] or r['datum']) else '9999', text=text))
    if g['status'] in ('abgesetzt', 'zurueckgezogen'):
        if r['abgesagt']:
            info['abgesagt_passt'] += 1
        else:
            neu('csv_aktiv', 'fussball.de: %s' % ('abgesetzt' if g['status'] == 'abgesetzt' else 'Gegner zurückgezogen (zg.)'))
        return
    if r['abgesagt']:
        neu('csv_abgesagt', 'bei fussball.de weiter angesetzt (nicht abgesetzt)')
        return
    if g['datum'] and g['datum'] != r['datum']:
        neu('termin', 'Datum')
    elif g['zeit'] and g['zeit'] != r['zeit']:
        neu('termin', 'Uhrzeit')
    elif not g['datum'] or not g['zeit']:
        info['fb_ohne_termin'] += 1
    if g['ha'] != r['ha'] and '?' not in (g['ha'], r['ha']):
        neu('heimgast', 'fussball.de: %s, CSV: %s' % ('Heim' if g['ha'] == 'H' else 'Auswärts', 'Heim' if r['ha'] == 'H' else 'Auswärts'))
    elif g['gegner'] or r['gegner']:
        v = gegner_gleich(g['gegner'], r['gegner'])
        if v == 'anders':
            neu('gegner', 'fussball.de: %s · CSV: %s' % (g['gegner'] or '–', r['gegner'] or '–'))
        elif v == 'schreibweise':
            info['schreibweise'] += 1
    if g['art'] != r['art']:
        neu('wettbewerb', 'fussball.de: %s · CSV: %s' % (g['art'], r['art']))


def staffel_uebersicht(fb, ohne_nr, csv_zeilen, heute):
    """Je Staffelkennung: Spiele gesamt und ab heute bei fussball.de und in der CSV, dazu ein Urteil."""
    z = defaultdict(lambda: dict(fb=0, fb_ab=0, csv=0, csv_ab=0, csv_abg=0, wb='', art=''))
    for g in list(fb.values()) + ohne_nr:
        if g['status'] in ('angesetzt', 'nichtantritt'):
            e = z[g['staffel'] or '?']
            e['fb'] += 1
            e['fb_ab'] += 1 if g['datum'] >= heute else 0
            e['wb'], e['art'] = g['wettbewerb'], g['art']
    for r in csv_zeilen:
        e = z[r['staffel']]
        if r['abgesagt']:
            e['csv_abg'] += 1
        else:
            e['csv'] += 1
            e['csv_ab'] += 1 if r['datum'] >= heute else 0
        e['art'] = e['art'] or r['art']
    zeilen = []
    for st in sorted(z):
        e = z[st]
        kennz = 'auffaellig'
        if e['fb_ab'] != e['csv_ab']:
            if e['fb'] and not (e['csv'] or e['csv_abg']):
                urteil = 'NEU bei fussball.de, fehlt komplett in der CSV'
            elif not e['fb'] and e['csv']:
                urteil = 'nur in der CSV (bei fussball.de keine Spiele)'
            else:
                urteil = 'andere Spielanzahl ab heute (Differenz %+d)' % (e['csv_ab'] - e['fb_ab'])
        elif e['fb'] != e['csv']:
            urteil, kennz = 'ab heute gleich, nur vergangene Spiele weichen ab', 'vergangen'
        else:
            urteil, kennz = 'ok', 'ok'
        zeilen.append(dict(staffel=st, wettbewerb=e['wb'], art=e['art'], fb=e['fb'], fb_ab=e['fb_ab'], csv=e['csv'], csv_ab=e['csv_ab'],
                           csv_abg=e['csv_abg'], urteil=urteil, kennz=kennz))
    return zeilen


# ---------------------------------------------------------------- Bericht

def paarung(x):
    if x['art'] == 'Kinderfestival':
        return 'Festival bei uns' if x['ha'] == 'H' else 'Festival bei %s' % x['gegner']
    return '%s · %s' % ({'H': 'Heim', 'A': 'Auswärts'}.get(x['ha'], '?'), x['gegner'] or '–')


def zelle(t):
    return str(t).replace('|', '/').replace('\n', ' ')


def team_abschnitt(team, ergebnis, heute):
    kuerzel, slug, name, tid = team
    out = []
    if ergebnis.get('fehler'):
        return ['### %s – nicht ausgewertet' % name, '', 'Grund: %s. Diese Mannschaft ist in diesem Lauf **nicht** verglichen.' % ergebnis['fehler'], '']
    diffs, staffeln, info = ergebnis['diffs'], ergebnis['staffeln'], ergebnis['info']
    kom = [d for d in diffs if d['kommend']]
    verg = [d for d in diffs if not d['kommend']]
    kopf = '### %s – fussball.de %d Spiele · CSV %d Zeilen' % (name, ergebnis['n_fb'], ergebnis['n_csv'])
    kopf += ' · **keine Unterschiede**' if not diffs else ' · %d kommend, %d vergangen' % (len(kom), len(verg))
    out += [kopf, '']
    if ergebnis.get('quellen'):
        namen = [name] + [q + (', ohne Spiele' if n == 0 else '') for q, n in ergebnis['quellen']]
        out += ['Zusammengeführt aus %d fussball.de-Plänen: %s.' % (len(namen), ' und '.join(namen)), '']
    for qname, grund in ergebnis.get('zusatz_fehler', []):
        out += ['**Zusatzquelle ausgefallen:** %s – %s; ihre Spiele sind in diesem Lauf nicht verglichen und erscheinen deshalb als „nur in der CSV“.' % (qname, grund), '']
    if any(s['kennz'] != 'ok' for s in staffeln):
        out += ['Staffeln (Spiele gesamt / ab heute):', '', '| Staffel | Wettbewerb (fussball.de) | Art | fussball.de | CSV | Urteil |', '|---|---|---|---:|---:|---|']
        for s in staffeln:
            csvtxt = '%d / %d' % (s['csv'], s['csv_ab']) + (' (+%d abgesagt)' % s['csv_abg'] if s['csv_abg'] else '')
            out.append('| %s | %s | %s | %d / %d | %s | %s |' % (s['staffel'], zelle(s['wettbewerb'] or '–'), zelle(s['art'] or '–'), s['fb'], s['fb_ab'], csvtxt, s['urteil']))
        out.append('')
    for art in DIFF_TITEL:
        liste = [d for d in kom if d['art'] == art]
        if not liste:
            continue
        out += ['**%s** (%d)' % (DIFF_TITEL[art], len(liste)), '']
        if art in ('fehlt', 'ueberzaehlig'):
            out += ['| Datum | Art | Paarung | Staffel · Nr. |', '|---|---|---|---|']
            for d in liste:
                x = d['fb'] or d['csv']
                zusatz = ' (%s)' % '; '.join(x['hinweise']) if x.get('hinweise') else ''
                out.append('| %s | %s | %s | %s · %s%s |' % (fmt(x['datum'], x['zeit']), zelle(x['art']), zelle(paarung(x)), d['staffel'] or '?', d['nr'] if d['nr'] is not None else '?', zusatz))
        else:
            out += ['| Staffel · Nr. | Paarung | fussball.de | CSV | Unterschied |', '|---|---|---|---|---|']
            for d in liste:
                g, r = d['fb'], d['csv']
                hin = ('; ' + '; '.join(g['hinweise'])) if g and g.get('hinweise') else ''
                out.append('| %s · %s | %s | %s | %s | %s%s |' % (
                    d['staffel'] or '?', d['nr'] if d['nr'] is not None else '?', zelle(paarung(r or g)),
                    fmt(g['datum'], g['zeit']) if g else '–', fmt(r['datum'], r['zeit']) if r else '–', zelle(d['text']), zelle(hin)))
        out.append('')
    if verg:
        out += ['Vergangene Spiele (nur zur Kenntnis, %d):' % len(verg), '']
        for d in verg:
            x = d['fb'] or d['csv']
            out.append('- %s · %s · %s · %s %s%s' % (fmt(x['datum'], x['zeit']), zelle(paarung(x)), DIFF_TITEL[d['art']], d['staffel'] or '?', d['nr'] if d['nr'] is not None else '?', (' · ' + d['text']) if d['text'] else ''))
        out.append('')
    if ergebnis.get('ohne_termin'):
        out += ['Bei fussball.de noch ohne Termin, daher nicht eintragbar (%d):' % len(ergebnis['ohne_termin']), '']
        for g in ergebnis['ohne_termin']:
            out.append('- %s · %s · %s %s' % (zelle(g['art']), zelle(paarung(g)), g['staffel'] or '?', g['nr'] if g['nr'] is not None else '?'))
        out.append('')
    notiz = []
    for key, text in (('spielfrei', 'spielfreie Termine: %d'), ('fb_entfaellt_ohne_csv', 'bei fussball.de abgesetzt oder zurückgezogen und in der CSV (richtig) nicht angesetzt: %d'),
                      ('abgesagt_passt', 'in der CSV „abgesagt“ und bei fussball.de abgesetzt oder zurückgezogen (stimmt): %d'),
                      ('csv_abgesagt_ohne_fb', 'in der CSV „abgesagt“ und bei fussball.de nicht mehr gelistet (stimmt): %d'),
                      ('doppelzeilen', 'Doppelzeilen bei fussball.de (Verlegung), gültiger Termin ausgewertet: %d'),
                      ('schreibweise', 'Vereinsnamen nur anders geschrieben (nicht als Unterschied gezählt): %d'),
                      ('fb_ohne_termin', 'Spiele bei fussball.de noch ohne vollständigen Termin (Abgleich nicht möglich): %d')):
        if info.get(key):
            notiz.append(text % info[key])
    if ergebnis.get('csv_ausserhalb'):
        notiz.append('%d CSV-Zeilen außerhalb des Saisonfensters nicht verglichen' % ergebnis['csv_ausserhalb'])
    if notiz:
        out += ['Nebenbei: ' + '; '.join(notiz) + '.', '']
    return out


def kurzfassung(ergebnisse, alle, heute_dt, vorher, wochenvergleich=True):
    zeilen = ['| Mannschaft | fussball.de | CSV | fehlt in CSV | nur in CSV | abweichend | Staffeln | Vergangenheit (Info) |', '|---|---:|---:|---:|---:|---:|---|---:|']
    tot = defaultdict(int)
    for team in TEAMS:
        e = ergebnisse[team[0]]
        if e.get('fehler'):
            zeilen.append('| %s | – | – | – | – | – | nicht ausgewertet | – |' % team[2])
            continue
        kom = [d for d in e['diffs'] if d['kommend']]
        fehlt = sum(1 for d in kom if d['art'] == 'fehlt')
        nur = sum(1 for d in kom if d['art'] == 'ueberzaehlig')
        abw = len(kom) - fehlt - nur
        verg = len(e['diffs']) - len(kom)
        auff = [s['staffel'] for s in e['staffeln'] if s['kennz'] == 'auffaellig' and s['art'] != 'Freundschaftsspiel']
        zeilen.append('| %s | %d | %d | %d | %d | %d | %s | %d |' % (team[2], e['n_fb'], e['n_csv'], fehlt, nur, abw, ('auffällig: ' + ', '.join(auff)) if auff else 'ok', verg))
        for k, v in (('fb', e['n_fb']), ('csv', e['n_csv']), ('fehlt', fehlt), ('nur', nur), ('abw', abw), ('verg', verg)):
            tot[k] += v
    zeilen.append('| **Summe** | %d | %d | **%d** | **%d** | **%d** | | %d |' % (tot['fb'], tot['csv'], tot['fehlt'], tot['nur'], tot['abw'], tot['verg']))
    kommend = [d for d in alle if d['kommend']]
    naechste = []
    for d in sorted(kommend, key=lambda d: (d['sort'], d['team']))[:8]:
        x = d['fb'] or d['csv']
        naechste.append('- %s · %s · %s · %s' % (fmt(x['datum'], x['zeit']), next(t[2] for t in TEAMS if t[0] == d['team']), paarung(x), DIFF_TITEL[d['art']]))
    out = zeilen + ['']
    for team in TEAMS:
        for qname, grund in ergebnisse[team[0]].get('zusatz_fehler', []):
            out += ['**Zusatzquelle ausgefallen:** %s – %s; ihre Spiele erscheinen bei %s als „nur in der CSV“.' % (qname, grund, team[2]), '']
    if kommend:
        out.append('Kommend (Spiele ab heute): **%d Unterschiede**, davon %d fehlende Spiele in der CSV, %d überzählige und %d abweichende. In der Vergangenheit (nur zur Kenntnis): %d.' % (
            len(kommend), tot['fehlt'], tot['nur'], tot['abw'], tot['verg']))
    else:
        out.append('Kommend (Spiele ab heute): **keine Unterschiede**. In der Vergangenheit (nur zur Kenntnis): %d.' % tot['verg'])
    out.append('')
    if not wochenvergleich:
        pass
    elif vorher is None:
        out.append('Seit dem letzten Lauf: erster Lauf, kein Vergleich möglich.')
    else:
        ids_jetzt = {d['id'] for d in alle if d['kommend']}
        neu, weg = ids_jetzt - set(vorher['ids']), set(vorher['ids']) - ids_jetzt
        out.append('Seit dem letzten Lauf (%s): %d neue Unterschiede, %d behoben/erledigt, %d unverändert offen.' % (
            vorher['datum'], len(neu), len(weg), len(ids_jetzt & set(vorher['ids']))))
    if naechste:
        out += ['', 'Zeitlich nächste Unterschiede:', ''] + naechste
    return out


def stand_laden(ordner, heute):
    kandidaten = sorted(p for p in glob.glob(os.path.join(ordner, 'stand-*.json')) if os.path.basename(p)[6:16] < heute)
    if not kandidaten:
        return None
    with open(kandidaten[-1], encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- Hauptprogramm

def plan_lesen(cache, quelle, von, bis, nur_cache, jetzt, abbruch, url_vorlage, leer_erlaubt=False, wiederholung=None):
    """Einen fussball.de-Plan holen (Cache oder Abruf, siehe hole) und lesen -> (Spiele, Fehlertext oder None, Zeile für
    „Abrufe dieses Laufs“). `quelle`: ein Tupel wie in TEAMS, also auch eine Zusatzquelle. `leer_erlaubt` (nur für Zusatzquellen):
    eine Seite, die als gültiger Plan ohne Spiele erkennbar ist (leerer_plan), gilt als Plan mit 0 Spielen und ist kein Ausfall; ein
    eigener Plan ohne Spielzeilen bleibt ein Fehler. `wiederholung`: das Objekt des Laufs (Klasse Wiederholung), das als
    Schlüsselwortargument an hole geht (so reicht es auch die Wache in nachtrag_aktion.py durch); ohne es gibt es einen Versuch je Plan."""
    name = quelle[2]
    mehr = {'wiederholung': wiederholung} if wiederholung is not None else {}
    text, herkunft, stempel, fehler = hole(cache, quelle, von, bis, nur_cache, jetzt, abbruch, url_vorlage, **mehr)
    if fehler:
        if not fehler.startswith('nicht abgerufen'):
            print('  %s: Abruf fehlgeschlagen (%s); weitere Abrufe in diesem Lauf entfallen' % (name, fehler), file=sys.stderr)
        return [], fehler, '%s: FEHLER (%s)' % (name, fehler)
    zeile = '%s: %s %s' % (name, herkunft, stempel.strftime('%d.%m.%Y %H:%M'))
    fb = [fb_normalisieren(g) for g in parse_spielplan(text)]
    if not fb:
        if not leer_erlaubt:
            return [], 'fussball.de lieferte keine Spielzeilen (Seitenaufbau geändert oder Plan leer)', zeile
        if not leerer_plan(text):
            return [], 'fussball.de lieferte keine Spielzeilen und keinen erkennbaren Plan (Seitenaufbau geändert oder Sperrseite)', zeile
    return fb, None, zeile


def main():
    ap = argparse.ArgumentParser(description='Spielplan-Abgleich spiele.csv gegen fussball.de (rein lesend)')
    ap.add_argument('--csv', default=CSV_STANDARD)
    ap.add_argument('--bericht-ordner', default=BERICHTE_STANDARD)
    ap.add_argument('--cache', default=CACHE_STANDARD)
    ap.add_argument('--nur-cache', action='store_true')
    ap.add_argument('--heute', help='JJJJ-MM-TT (nur zum Testen)')
    ap.add_argument('--saison', type=int, help='Startjahr der Saison, Standard: aus dem Datum')
    ap.add_argument('--kein-wochenvergleich', action='store_true', help='keinen Vergleich mit dem Vorwochenbericht, keine Standdatei')
    ap.add_argument('--zeitrahmen', type=ganze_sekunden, default=ZEITRAHMEN_STANDARD, metavar='SEKUNDEN',
                    help='Zeitrahmen der Wiederholungen in Sekunden ab Beginn der Abrufe (Standard: %(default)s): scheitert ein Abruf an einer '
                         'vorübergehenden Störung von fussball.de (HTTP 408, 429, 500, 502, 503, 504, 520 bis 524, Zeitüberschreitung, '
                         'abgebrochene oder verweigerte Verbindung, unvollständige Antwort, Netz oder Host nicht erreichbar, vorübergehender '
                         'Fehler der Namensauflösung, Abbruch beim TLS-Handshake, Proxy-Meldung „Tunnel connection failed“ mit 429, 500, 502, '
                         '503 oder 504), wird er nach 60, 120 und 240 s Pause erneut versucht, solange '
                         'der neue Versuch noch in diesem Zeitrahmen beginnt; 0 = keine Wiederholung, jeder Abruf nur einmal. Die Pausen lassen '
                         'sich nicht verkürzen. Bei --nur-cache ohne Wirkung.')
    ap.add_argument('--url-vorlage', default=URL, help=argparse.SUPPRESS)  # nur für Tests gegen einen lokalen Server
    a = ap.parse_args()

    jetzt = datetime.now()
    heute = a.heute or jetzt.date().isoformat()
    h = date.fromisoformat(heute)
    sj = a.saison or (h.year if h.month >= 7 else h.year - 1)
    von, bis = '%d-07-01' % sj, '%d-06-30' % (sj + 1)
    os.makedirs(a.bericht_ordner, exist_ok=True)

    hash_vorher = sha256(a.csv)
    csv_alle, csv_fehler = csv_lesen(a.csv)
    csv_je = defaultdict(list)
    ausserhalb = defaultdict(int)
    for r in csv_alle:
        if von <= r['datum'] <= bis:
            csv_je[r['team']].append(r)
        else:
            ausserhalb[r['team']] += 1
    bekannte = {t[0] for t in TEAMS}
    unbekannt = sorted(set(csv_je) - bekannte)

    abrufe, abbruch = [], []
    wiederholung = Wiederholung(a.zeitrahmen)     # ein Objekt je Lauf, direkt vor dem ersten Abruf: der Zeitrahmen zählt ab hier

    # 1. Durchgang: die eigenen Pläne aller Mannschaften. Fällt einer aus, ist diese Mannschaft nicht ausgewertet und ihre Zusatzquellen
    #    werden in diesem Lauf nicht abgerufen. Ein Fehlschlag setzt `abbruch`: danach wird nichts mehr abgerufen (siehe hole).
    eigene = {}                      # Kürzel -> (Spiele, Fehlertext oder None)
    for team in TEAMS:
        fb, fehler, zeile = plan_lesen(a.cache, team, von, bis, a.nur_cache, jetzt, abbruch, a.url_vorlage, wiederholung=wiederholung)
        abrufe.append(zeile)
        eigene[team[0]] = (fb, fehler)

    # 2. Durchgang: die Zusatzquellen der Mannschaften mit gelesenem eigenen Plan. Sie kommen erst nach allen eigenen Plänen, damit der
    #    Fehlschlag einer Zusatzquelle keine Mannschaft am Abruf hindert; ein früherer Fehlschlag trifft sie wie jede Quelle („nicht
    #    abgerufen …“, also ausgefallen). Ein gültiger Plan ohne Spiele ist bei einer Zusatzquelle kein Ausfall (leer_erlaubt).
    zusatz = {}                      # Kürzel -> dict(fb, gelesen=[(Name, Zahl der Spiele)], ausgefallen=[(Name, Grund)])
    for team in TEAMS:
        if eigene[team[0]][1]:
            continue
        z = zusatz[team[0]] = dict(fb=[], gelesen=[], ausgefallen=[])
        for quelle in quellen(team)[1:]:
            try:
                zfb, zfehler, zzeile = plan_lesen(a.cache, quelle, von, bis, a.nur_cache, jetzt, abbruch, a.url_vorlage, leer_erlaubt=True,
                                                  wiederholung=wiederholung)
            except Exception as ex:  # ein kaputter Zusatzplan darf die Mannschaft nie mitreißen
                zfb, zfehler = [], 'unerwarteter Fehler (%s: %s)' % (type(ex).__name__, ex)
                zzeile = '%s: FEHLER (%s)' % (quelle[2], zfehler)
            abrufe.append(zzeile)
            if zfehler:
                z['ausgefallen'].append((quelle[2], zfehler))
            else:
                z['fb'] += zfb
                z['gelesen'].append((quelle[2], len(zfb)))

    # 3. Je Mannschaft: den Plan aus allen gelesenen Quellen zusammenführen und mit den CSV-Zeilen vergleichen
    ergebnisse = {}
    for team in TEAMS:
        kuerzel = team[0]
        fb, fehler = eigene[kuerzel]
        if fehler:
            ergebnisse[kuerzel] = dict(fehler=fehler)
            continue
        z = zusatz[kuerzel]
        fb = fb + z['fb']
        diffs, staffeln, info, ohne_termin = vergleiche(team, fb, csv_je.get(kuerzel, []), heute)
        n_fb = sum(1 for g in fb if g['status'] in ('angesetzt', 'nichtantritt'))
        ergebnisse[kuerzel] = dict(diffs=diffs, staffeln=staffeln, info=info, ohne_termin=ohne_termin, n_fb=n_fb,
                                   n_csv=sum(1 for r in csv_je.get(kuerzel, []) if not r['abgesagt']), csv_ausserhalb=ausserhalb.get(kuerzel, 0),
                                   zeilen=len(fb), quellen=z['gelesen'], zusatz_fehler=z['ausgefallen'])
    alle = [d for e in ergebnisse.values() if not e.get('fehler') for d in e['diffs']]

    vorher = None if a.kein_wochenvergleich else stand_laden(a.bericht_ordner, heute)
    kf = kurzfassung(ergebnisse, alle, jetzt, vorher, not a.kein_wochenvergleich)
    n_fehler = sum(1 for e in ergebnisse.values() if e.get('fehler')) + sum(len(e.get('zusatz_fehler', ())) for e in ergebnisse.values())
    n_cache = sum(1 for z in abrufe if ': Cache ' in z)
    n_abruf = sum(1 for z in abrufe if ': Abruf ' in z)
    wdh = ', %d wiederholt' % wiederholung.zaehler if wiederholung.zaehler else ''      # Zusatz nur, wenn ein Abruf wiederholt wurde

    md = ['# Spielplan-Abgleich · spiele.csv gegen fussball.de · %s' % h.strftime('%d.%m.%Y'), '',
          'Rein lesender Bericht der Unterschiede. Nichts wurde in `spiele.csv` geschrieben, committet oder gepusht.', '',
          '- **Quelle CSV:** `%s` (%d Zeilen, Saisonfenster %s bis %s); %s' % (a.csv, len(csv_alle), kurz_datum(von) + str(sj), kurz_datum(bis) + str(sj + 1), git_info(a.csv)),
          '- **Quelle fussball.de:** öffentlicher Mannschafts-Spielplan je Mannschaft (`ajax.team.matchplan`), ein Abruf je Plan und Woche. Zusatzquellen: %s. Dieser Lauf: %d Abrufe, %d aus dem Cache, %d fehlgeschlagen%s.' % (
              '; '.join('%s, in der CSV bei %s' % (q[2], q[0]) for q in ZUSATZQUELLEN) or 'keine', n_abruf, n_cache, n_fehler, wdh),
          '- **Schlüssel des Vergleichs:** Staffelkennung + Spielnummer; verglichen werden Datum, Uhrzeit, Heim/Auswärts, Gegner, Wettbewerbsart und Absagen. „Kommend“ heißt: Spieldatum heute (%s) oder später.' % h.strftime('%d.%m.%Y'), '',
          '## Kurzfassung', ''] + kf + ['']
    if unbekannt:
        md += ['**Achtung:** Mannschaften in der CSV ohne fussball.de-Team-ID im Skript, nicht geprüft: %s.' % ', '.join(unbekannt), '']
    if csv_fehler:
        md += ['**Achtung:** %d CSV-Zeilen nicht lesbar (%s).' % (len(csv_fehler), '; '.join(csv_fehler[:5])), '']
    md += ['## Je Mannschaft', '']
    for team in TEAMS:
        md += team_abschnitt(team, ergebnisse[team[0]], heute)
    md += ['## Abrufe dieses Laufs', ''] + ['- ' + z for z in abrufe] + ['- ' + z for z in wiederholung.notizen] + ['']
    hash_nachher = sha256(a.csv)
    md += ['## Kontrolle', '', '- `spiele.csv` vor und nach dem Lauf identisch: %s (SHA-256 %s…).' % ('ja' if hash_vorher == hash_nachher else 'NEIN – SOFORT PRÜFEN', hash_nachher[:16]), '']
    if hash_vorher != hash_nachher:
        print('WARNUNG: spiele.csv hat sich während des Laufs geändert!', file=sys.stderr)

    bericht = os.path.join(a.bericht_ordner, 'bericht-%s.md' % heute)
    with open(bericht, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    if not a.kein_wochenvergleich:
        with open(os.path.join(a.bericht_ordner, 'stand-%s.json' % heute), 'w', encoding='utf-8') as f:
            json.dump(dict(datum=h.strftime('%d.%m.%Y'), ids=sorted(d['id'] for d in alle if d['kommend'])), f, ensure_ascii=False, indent=1)

    print('Spielplan-Abgleich %s – %d Abrufe, %d aus dem Cache, %d fehlgeschlagen%s\n' % (h.strftime('%d.%m.%Y'), n_abruf, n_cache, n_fehler, wdh))
    print('\n'.join(kf))
    print('\nBericht: %s' % bericht)
    return 2 if n_fehler else 0


if __name__ == '__main__':
    sys.exit(main())
