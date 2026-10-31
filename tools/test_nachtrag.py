#!/usr/bin/env python3
"""Tests für tools/nachtrag.py. Kein Netzwerk (jeder Socket-Zugriff wird gesperrt und gezählt), keine echten
fussball.de-Seiten im Repo: Die Seiten sind synthetisch, im Aufbau von ajax.team.matchplan. Git-Tests laufen nur in
Wegwerf-Klonen mit lokalem Bare-Remote unter tempfile; die echte spiele.csv und das echte Repo werden nie angefasst.

Einige Tests starten tools/nachtrag.py als Unterprozess (Lauf-Sperre, Signale) oder schicken dem Testprozess selbst ein Signal (mit
Sicherheitsnetz); auch sie arbeiten nur in Wegwerf-Ordnern und ohne Netzwerk.

Aufruf (aus dem Repo-Stamm):  python3 -I -m unittest discover -s tools -p 'test_nachtrag.py'
Einzelne Klassen:             python3 -I -m unittest discover -s tools -p 'test_nachtrag.py' -k TestZusatzquelleNachtrag
Ein Zusatztest liest den lokalen Cache des Abgleichs (~/Library/Caches/speuzer-spielplan-abgleich) und wird übersprungen,
wenn er fehlt.

Die Zusatzquelle D4 (abgleich.ZUSATZQUELLEN: ihre Spiele stehen in der CSV als D3) prüfen TestZusatzquelleNachtrag (nachtrag.py) und
TestZusatzquelleAbgleich (abgleich.main mit Wegwerf-Cache); in der synthetischen Welt hat die D4 zunächst einen Plan ohne Spiele.
"""
import contextlib
import errno
import glob
import html
import io
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import unittest
import urllib.error
import urllib.request
import warnings
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest import mock

try:
    import fcntl  # die Lauf-Sperre (flock) gibt es nur unter Unix/macOS
except ImportError:  # pragma: no cover
    fcntl = None

sys.dont_write_bytecode = True
HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)
import abgleich as A  # noqa: E402
import nachtrag as N  # noqa: E402

REPO = os.path.dirname(HIER)
HEUTE = '2026-10-26'
JETZT = datetime(2026, 10, 26, 7, 10, 0)
STAND = JETZT - timedelta(hours=2)          # Zeitstempel der synthetischen Cache-Seiten (frisch)
NETZ_VERSUCHE = []


def _gesperrt(*a, **k):
    NETZ_VERSUCHE.append(repr(a)[:80])
    raise OSError('Netzwerk im Test gesperrt')


def setUpModule():
    """Jeder Netzwerkzugriff im Test scheitert und wird gezählt; tearDownModule verlangt null Versuche."""
    global _patches
    # abgleich.hole lässt die Fehlversuch-Marke ungeschlossen (hole bleibt, wie es ist): die Warnung hier nicht zeigen
    warnings.filterwarnings('ignore', category=ResourceWarning, module='abgleich')
    _patches = [mock.patch.object(socket.socket, 'connect', _gesperrt), mock.patch.object(socket, 'getaddrinfo', _gesperrt),
                mock.patch.object(socket, 'create_connection', _gesperrt), mock.patch.object(A, 'PAUSE_SEKUNDEN', 0)]
    for p in _patches:
        p.start()


def tearDownModule():
    for p in _patches:
        p.stop()
    assert not NETZ_VERSUCHE, 'Netzwerkzugriffe im Test: %s' % NETZ_VERSUCHE


# ---------------------------------------------------------------- Synthetische Welt: CSV und fussball.de-Seiten

TEAMS = [t[0] for t in A.TEAMS]
SLUG = {t[0]: t[1] for t in A.TEAMS}
SLUG.update({q[1]: q[1] for q in A.ZUSATZQUELLEN})     # Zusatzquellen (D4) lassen sich unter ihrem Slug ansprechen, z. B. cache_datei(w, 'd4', ...)
D4_SLUG, D4_NAME = A.ZUSATZQUELLEN[0][1], A.ZUSATZQUELLEN[0][2]
EIGEN_D4 = 'FFV Sportfreunde 04 (FS) IV'       # so heißt die D4 bei fussball.de; in der CSV stehen ihre Spiele als D3
EIGEN = {'HERREN': 'FFV Sportfreunde 04 FFM', 'A': 'FFV Sportfreunde 04 1', 'D1': 'FFV Sportfreunde 04 I', 'D2': 'FFV Sportfreunde 04 II',
         'D3': 'FFV Sportfreunde 04 III', 'E1': 'FFV Sportfreunde 04 I', 'E2': 'FFV Sportfreunde 04 II', 'E3': 'FFV Sportfreunde 04 III',
         'F1': 'FFV Sportfreunde 04 I', 'F2': 'FFV Sportfreunde 04 II', 'G1': 'FFV Sportfreunde 04 I'}
HEIMORT = {t: 'Mainzer Landstr. 480, 60326 Frankfurt am Main' for t in TEAMS}
HEIMORT.update(HERREN='Am Römerhof 9, 60486 Frankfurt am Main', A='Am Römerhof 9, 60486 Frankfurt am Main')
GASTORT = 'Beispielweg 1, 60000 Frankfurt am Main'
LIGA = {'HERREN': '341727', 'A': '343410', 'D1': '340782', 'D2': '341874', 'D3': '341873', 'E1': '340651', 'E2': '341538',
        'E3': '341547', 'F1': '340233', 'F2': '340231', 'G1': '340221'}
POKAL = {t: '9400%02d' % i for i, t in enumerate(TEAMS)}
FREUND = {t: '5400%02d' % i for i, t in enumerate(TEAMS)}
WOCHENTAGE = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']
WETTBEWERB_TEXT = {'Meisterschaft': 'Kreisliga A', 'Kreispokal': 'Kreispokal', 'Freundschaftsspiel': 'Kreisfreundschaftsspiele',
                   'Kinderfestival': 'Kinderfußball'}
CODE = {'Meisterschaft': 'ME', 'Kreispokal': 'PO', 'Freundschaftsspiel': 'FS', 'Kinderfestival': 'ME'}
KOPF = '''<div data-ng-controller="AjaxController" id="id-team-matchplan-table">
<div class="table-container fixtures-matches-table club-matchplan-table">
<div class="hint-pre-publish"><span class="inner"><h5>Wichtiger Hinweis zum Spielplan</h5><p>synthetische Testseite</p></span></div>
<table class="table table-striped table-full-width"><thead><tr class="thead hidden-small"><th>Datum | Zeit</th></tr></thead><tbody>
'''
FUSS = '</tbody></table></div></div>\n'
FUELLTEXT = '<!-- ' + 'Filterformular und Legende ' * 20 + '-->'     # ein Plan ohne Spiele muss trotzdem länger als 500 Zeichen sein (wie echte Seiten)


class Spiel:
    """Ein Spiel für CSV und/oder Seite. `status`: Text im Ergebnisfeld der Seite („Absetzung“, „Nichtantritt GAST“, bei
    Verlegung der neue Termin „14.02.2027 11:30“). csv_heim/csv_gast: andere Schreibweise in der CSV als auf der Seite."""

    def __init__(self, team, staffel, nr, datum, zeit, heim, gast='', art='Meisterschaft', ort='', ergebnis='', fbid=None,
                 status='', csv_heim=None, csv_gast=None, wettbewerb_text=None, vorlaeufig=False):
        self.team, self.staffel, self.nr, self.datum, self.zeit = team, staffel, nr, datum, zeit
        self.wettbewerb_text, self.vorlaeufig = wettbewerb_text, vorlaeufig
        self.heim, self.gast, self.art, self.ort, self.ergebnis, self.status = heim, gast, art, ort, ergebnis, status
        self.csv_heim, self.csv_gast = csv_heim, csv_gast
        self.fbid = fbid if fbid is not None else ('' if art == 'Kinderfestival' else ('%s%s%04d' % (team, 'X' * (6 - len(team)), nr)).ljust(32, 'Q'))

    def tt(self):
        d = date.fromisoformat(self.datum)
        return d.strftime('%d.%m.%Y')

    def csv_zeile(self):
        return ';'.join([self.team, self.staffel, str(self.nr), self.tt(), self.zeit, self.csv_heim or self.heim, self.csv_gast if self.csv_gast is not None else self.gast,
                         self.ort, self.ergebnis, self.fbid, self.art])

    def html(self):
        d = date.fromisoformat(self.datum)
        text = self.wettbewerb_text or WETTBEWERB_TEXT[self.art]
        fest = self.art == 'Kinderfestival'
        heim = self.heim + (' - Kinderfestival' if fest else '')
        gast = EIGEN[self.team] if fest else self.gast
        link = ('https://www.fussball.de/spiel/x-y/-/spiel/%s' % self.fbid) if self.fbid else 'https://www.fussball.de/spieltag/x/-/staffel/ABC'
        if self.status:
            score = '<a href="%s"><span class="info-text">%s</span></a>' % (link, self.status)
        else:
            score = '<a href="%s"><span class="score-left">&#xE680;</span><span class="colon">:</span><span class="score-right">&#xE688;</span></a>' % link
        zeit_zelle = self.zeit + ('<span class="icon-pre-publish"></span>' if self.vorlaeufig else '')
        return ('<tr class="row-headline visible-small"><td colspan="6">%s, %s - %s Uhr | %s</td></tr>\n'
                '<tr class="odd row-competition hidden-small"><td class="column-date"><span class="hidden-small inline">%s, %s |&nbsp;</span>%s</td>'
                '<td colspan="3" class="column-team"><a>%s</a></td><td colspan="2"><a>%s | %s%03d</a></td></tr>\n'
                '<tr class="odd"><td class="hidden-small"></td><td class="column-club"><a href="#" class="club-wrapper"><div class="club-name">\n %s\n</div></a></td>'
                '<td class="column-colon">:</td><td class="column-club no-border"><a href="#" class="club-wrapper"><div class="club-name">\n %s\n</div></a></td>'
                '<td class="column-score">%s</td><td class="column-detail"></td></tr>\n') % (
            WOCHENTAGE[d.weekday()], d.strftime('%d.%m.%Y'), self.zeit, text, A.WOCHENTAG[d.weekday()], d.strftime('%d.%m.%y'), zeit_zelle, text,
            CODE[self.art], self.staffel, self.nr, heim, gast, score)


def html_spielfrei(datum, text, staffel, nr, eigen):
    d = date.fromisoformat(datum)
    return ('<tr class="row-headline visible-small"><td colspan="6">%s, %s -  Uhr | %s</td></tr>\n'
            '<tr class="row-competition hidden-small"><td class="column-date"><span class="hidden-small inline">%s, %s |&nbsp;</span></td>'
            '<td colspan="3" class="column-team"><a>%s</a></td><td colspan="2"><a>ME | %s%03d</a></td></tr>\n'
            '<tr><td class="hidden-small"></td><td class="column-club"><a href="#" class="club-wrapper"><div class="club-name">%s</div></a></td>'
            '<td class="column-colon">:</td><td class="column-club no-border"><span class="info-text">spielfrei</span></td>'
            '<td class="column-score"></td><td class="column-detail"></td></tr>\n') % (
        WOCHENTAGE[d.weekday()], d.strftime('%d.%m.%Y'), text, A.WOCHENTAG[d.weekday()], d.strftime('%d.%m.%y'), text, staffel, nr, eigen)


def seite_aus(spiele, zusatz=''):
    return KOPF + ''.join(s.html() if isinstance(s, Spiel) else s for s in spiele) + zusatz + FUSS


def basis_spiele(team):
    """Die Grundausstattung einer Mannschaft: ein vergangenes Spiel, drei Ligaspiele (oder zwei Festivals), Pokal, Freundschaft."""
    e = EIGEN[team]
    if team in ('F1', 'F2', 'G1'):
        return [Spiel(team, LIGA[team], 10, '2026-11-07', '10:00', e, '', 'Kinderfestival', ort=HEIMORT[team]),
                Spiel(team, LIGA[team], 20, '2026-11-14', '09:00', 'VFR Gast 1', e, 'Kinderfestival', ort='Gastweg 2, 60000 Frankfurt am Main'),
                Spiel(team, LIGA[team], 1, '2026-10-11', '10:00', 'Alt FC 1', e, 'Kinderfestival', ort='Altweg 3, 60000 Frankfurt am Main')]
    return [Spiel(team, LIGA[team], 1, '2026-10-11', '10:00', 'Alt FC 1', e, ort='Altweg 3, 60000 Frankfurt am Main'),
            Spiel(team, LIGA[team], 10, '2026-11-01', '11:00', e, 'FC Muster 1', ort=HEIMORT[team]),
            Spiel(team, LIGA[team], 20, '2026-11-08', '12:00', 'SV Beispiel 2', e, ort=GASTORT),
            Spiel(team, LIGA[team], 30, '2026-11-22', '11:30', e, 'SG Bornheim/GW 2', csv_gast='SG Bornheim / GW 2', ort=HEIMORT[team]),
            Spiel(team, POKAL[team], 5, '2026-11-04', '18:30', 'SV Beispiel 2', e, 'Kreispokal', ort=GASTORT),
            Spiel(team, FREUND[team], 50, '2026-11-15', '11:00', e, 'TuS Test 1', 'Freundschaftsspiel', ort=HEIMORT[team])]


class Welt:
    """CSV-Inhalt und Seiteninhalt je Mannschaft; beide starten gleich, Tests ändern gezielt eine Seite."""

    def __init__(self):
        self.csv = {t: basis_spiele(t) for t in TEAMS}
        self.seite = {t: basis_spiele(t) for t in TEAMS}
        self.zusatz = {q[1]: [] for q in A.ZUSATZQUELLEN}      # Plan jeder Zusatzquelle (D4): zunächst ohne Spiele, die CSV-Zeilen stehen bei der Mannschaft

    def beide(self, team, spiel):
        self.csv[team].append(spiel)
        self.seite[team].append(deepcopy(spiel))

    def beide_zusatz(self, slug, spiel):
        """Ein Spiel einer Zusatzquelle: in der CSV unter dem Kürzel der Mannschaft (spiel.team) und im Plan der Zusatzquelle."""
        self.csv[spiel.team].append(spiel)
        self.zusatz[slug].append(deepcopy(spiel))

    def spiel(self, liste, team, nr, staffel=None):
        return next(s for s in liste[team] if s.nr == nr and (staffel is None or s.staffel == staffel))

    def csv_bytes(self, bom=False, eol='\r\n', ende=True, extra=()):
        """Zwei Blöcke wie in der echten Datei: erst Liga/Festivals je Mannschaft, dann Pokal und Freundschaft je Mannschaft."""
        zeilen = [';'.join(N.SPALTEN)]
        for block in (('Meisterschaft', 'Kinderfestival'), ('Kreispokal', 'Freundschaftsspiel')):
            for t in TEAMS:
                zeilen += [s.csv_zeile() for s in self.csv[t] if s.art in block]
        zeilen += list(extra)
        return (b'\xef\xbb\xbf' if bom else b'') + (eol.join(zeilen) + (eol if ende else '')).encode('utf-8')

    def cache_schreiben(self, ordner, stempel=STAND, ersetze=None, ohne=()):
        for t in TEAMS:
            if t in ohne:
                continue
            ziel = os.path.join(ordner, SLUG[t])
            os.makedirs(ziel, exist_ok=True)
            text = (ersetze or {}).get(t)
            if text is None:
                text = seite_aus(self.seite[t])
            with open(os.path.join(ziel, stempel.strftime('%Y%m%d-%H%M%S') + '.html'), 'w', encoding='utf-8') as f:
                f.write(text)
        for slug, spiele in self.zusatz.items():          # Zusatzquellen: in ersetze und ohne zählt ihr Slug (z. B. 'd4')
            if slug in ohne:
                continue
            ziel = os.path.join(ordner, slug)
            os.makedirs(ziel, exist_ok=True)
            text = (ersetze or {}).get(slug)
            if text is None:
                text = seite_aus(spiele, zusatz=FUELLTEXT)
            with open(os.path.join(ziel, stempel.strftime('%Y%m%d-%H%M%S') + '.html'), 'w', encoding='utf-8') as f:
                f.write(text)


def zeilen_von(daten):
    """CSV-Bytes -> Liste der Zeilen (ohne Zeilenende), Kopfzeile eingeschlossen."""
    text = daten.decode('utf-8-sig')
    return text.split('\r\n')[:-1] if text.endswith('\r\n') else text.split('\r\n')


class Werkstatt:
    """Ein Wegwerf-Ordner mit Repo-Inhalt (build_ics.py, vorlage_index.html, spiele.csv), Cache und Berichtsordner."""

    def __init__(self, welt=None, **csv_form):
        self.welt = welt or Welt()
        self.tmp = tempfile.TemporaryDirectory(prefix='nachtrag-test-')
        self.dir = self.tmp.name
        self.repo = os.path.join(self.dir, 'repo')
        os.makedirs(self.repo)
        for n in ('build_ics.py', 'vorlage_index.html'):
            shutil.copy(os.path.join(REPO, n), os.path.join(self.repo, n))
        self.csv = os.path.join(self.repo, 'spiele.csv')
        self.cache = os.path.join(self.dir, 'cache')
        self.berichte = os.path.join(self.dir, 'berichte')
        self.notaus = os.path.join(self.dir, 'AUS')
        self.csv_form = csv_form
        self.fruehere = []                    # ältere Cache-Stände (Mannschaft, Spiele, Stunden), überleben cache_neu()
        with open(self.csv, 'wb') as f:
            f.write(self.welt.csv_bytes(**csv_form))
        self.cache_neu()

    def cache_neu(self, **kw):
        shutil.rmtree(self.cache, ignore_errors=True)
        self.welt.cache_schreiben(self.cache, **kw)
        for team, spiele, stunden in self.fruehere:
            stempel = (kw.get('stempel', STAND) - timedelta(hours=stunden)).strftime('%Y%m%d-%H%M%S')
            with open(os.path.join(self.cache, SLUG[team], stempel + '.html'), 'w', encoding='utf-8') as f:
                f.write(seite_aus(spiele))

    def csv_lesen(self):
        with open(self.csv, 'rb') as f:
            return f.read()

    def frueherer_stand(self, team, spiele, stunden=24):
        """Zusätzlicher, älterer Cache-Stand einer Mannschaft (z. B. mit dem Spiel als „Absetzung“)."""
        self.fruehere.append((team, spiele, stunden))
        self.cache_neu()

    def csv_abgesagt_setzen(self, team, nr, mit_verlauf=True):
        """In der CSV steht das Spiel „abgesagt“; mit_verlauf: ein älterer Stand zeigte es bei fussball.de als abgesetzt."""
        self.welt.spiel(self.welt.csv, team, nr).ergebnis = 'abgesagt'
        with open(self.csv, 'wb') as f:
            f.write(self.welt.csv_bytes())
        if mit_verlauf:
            alt = deepcopy(self.welt.seite[team])
            self.welt.spiel({team: alt}, team, nr).status = 'Absetzung'
            self.frueherer_stand(team, alt)

    def optionen(self, **kw):
        kw.setdefault('heute', HEUTE)
        kw.setdefault('abrufen', False)
        return N.Optionen(csv=self.csv, cache=self.cache, notaus_datei=self.notaus, **kw)

    def lauf(self, **kw):
        return N.lauf(self.optionen(**kw), JETZT)

    def anwenden(self, **kw):
        return self.lauf(anwenden=True, **kw)

    def schluss(self, e):
        return e['schluss']

    def aufraeumen(self):
        self.tmp.cleanup()


class TestBasis(unittest.TestCase):
    def werkstatt(self, welt=None, **csv_form):
        w = Werkstatt(welt, **csv_form)
        self.addCleanup(w.aufraeumen)
        return w

    def pruefe_unveraendert(self, w, vorher):
        self.assertEqual(w.csv_lesen(), vorher)

    def assertNurDiese(self, alt, neu, ersetzt=None, eingefuegt=None):
        """Neue Datei = alte Datei, nur mit diesen Zeilen anders (ersetzt: {Zeilenindex: neue Zeile}, eingefuegt:
        {Zeilenindex: [Zeilen dahinter]}); Zeilenende, BOM und Kopfzeile bleiben Byte für Byte gleich."""
        bom = b'\xef\xbb\xbf' if alt.startswith(b'\xef\xbb\xbf') else b''
        self.assertEqual(neu.startswith(b'\xef\xbb\xbf'), bool(bom))
        eol = '\r\n' if b'\r\n' in alt else '\n'
        text = alt[len(bom):].decode('utf-8')
        ende = text.endswith(eol)
        zeilen = text.split(eol)[:-1] if ende else text.split(eol)
        erwartet = []
        for i, z in enumerate(zeilen):
            erwartet.append((ersetzt or {}).get(i, z))
            erwartet += (eingefuegt or {}).get(i, [])
        soll = bom + (eol.join(erwartet) + (eol if ende else '')).encode('utf-8')
        self.assertEqual(neu, soll)


# ---------------------------------------------------------------- Ergänzen

class TestErgaenzen(TestBasis):
    def lauf_mit(self, team, spiel, **kw):
        w = self.werkstatt()
        w.welt.seite[team].append(spiel)
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden(**kw)
        return w, vorher, e

    def test_meisterschaft_heim_hinter_letzter_zeile_der_staffel(self):
        s = Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1')
        w, vorher, e = self.lauf_mit('D1', s)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        zeilen = zeilen_von(vorher)
        letzte = max(i for i, z in enumerate(zeilen) if z.startswith('D1;' + LIGA['D1'] + ';'))
        neue = 'D1;%s;40;29.11.2026;13:00;%s;FC Neu 1;%s;;%s;Meisterschaft' % (LIGA['D1'], EIGEN['D1'], HEIMORT['D1'], s.fbid)
        self.assertNurDiese(vorher, w.csv_lesen(), eingefuegt={letzte: [neue]})
        self.assertEqual(e['zeilen'], ['ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1'])

    def test_meisterschaft_auswaerts_mit_bekannter_adresse(self):
        s = Spiel('E1', LIGA['E1'], 41, '2026-11-29', '10:00', 'SV Beispiel 2', EIGEN['E1'])
        w, vorher, e = self.lauf_mit('E1', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('E1;%s;41;' % LIGA['E1'])]
        self.assertEqual(neue, ['E1;%s;41;29.11.2026;10:00;SV Beispiel 2;%s;%s;;%s;Meisterschaft' % (LIGA['E1'], EIGEN['E1'], GASTORT, s.fbid)])

    def test_auswaerts_unbekannter_gegner_ohne_adresse(self):
        s = Spiel('E1', LIGA['E1'], 42, '2026-11-29', '10:00', 'Ganz Neu 9', EIGEN['E1'])
        w, vorher, e = self.lauf_mit('E1', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('E1;%s;42;' % LIGA['E1'])][0].split(';')
        self.assertEqual(neue[7], '')

    def test_schreibweise_der_csv_wird_uebernommen(self):
        """„SG Bornheim/GW 2“ (fussball.de) heißt in der CSV schon „SG Bornheim / GW 2“: so wird auch die neue Zeile geschrieben."""
        s = Spiel('D2', LIGA['D2'], 43, '2026-12-06', '11:00', EIGEN['D2'], 'SG Bornheim/GW 2')
        w, vorher, e = self.lauf_mit('D2', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('D2;%s;43;' % LIGA['D2'])][0].split(';')
        self.assertEqual(neue[6], 'SG Bornheim / GW 2')

    def test_zerlegte_umlaute_der_seite_kommen_zusammengesetzt_in_die_csv(self):
        s = Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'SG Oberho\u0308chstadt 1')      # „ö“ als o + Trema
        w, vorher, e = self.lauf_mit('D1', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;%s;40;' % LIGA['D1'])][0].split(';')
        self.assertEqual(neue[6], 'SG Oberh\u00f6chstadt 1')

    def test_pokal(self):
        s = Spiel('D1', POKAL['D1'], 6, '2026-12-02', '18:15', EIGEN['D1'], 'Pokal FC 1', 'Kreispokal')
        w, vorher, e = self.lauf_mit('D1', s)
        zeilen = zeilen_von(vorher)
        letzte = max(i for i, z in enumerate(zeilen) if z.startswith('D1;' + POKAL['D1'] + ';'))
        neue = 'D1;%s;6;02.12.2026;18:15;%s;Pokal FC 1;%s;;%s;Kreispokal' % (POKAL['D1'], EIGEN['D1'], HEIMORT['D1'], s.fbid)
        self.assertNurDiese(vorher, w.csv_lesen(), eingefuegt={letzte: [neue]})
        self.assertEqual(e['zeilen'][0], 'ergänzt: D1 Mi 02.12. 18:15 Pokal, Heim gegen Pokal FC 1')

    def test_anderer_pokalwettbewerb_wird_nur_gemeldet(self):
        """Code PO, aber der Wettbewerb heißt nicht Kreispokal (z. B. Hessenpokal): nicht als „Kreispokal“ eintragen."""
        s = Spiel('D1', POKAL['D1'], 7, '2026-12-02', '18:15', EIGEN['D1'], 'Pokal FC 2', 'Kreispokal', wettbewerb_text='Hessenpokal')
        w, vorher, e = self.lauf_mit('D1', s)
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Hessenpokal' in h for h in e['hinweise']), e['hinweise'])
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Auffällig, nicht übernommen'))

    def test_heimort_bei_gleichstand_bleibt_leer(self):
        """Zwei Heimorte gleich häufig: nichts raten, Spielstätte leer lassen."""
        w = self.werkstatt()
        w.welt.csv['D1'][1].ort = 'Anderer Platz 1, 60000 Frankfurt am Main'         # D1 hat drei Heimzeilen (nr 10, 30, 50) ...
        w.welt.csv['D1'][5].ort = 'Dritter Platz 2, 60000 Frankfurt am Main'         # ... mit drei verschiedenen Orten: kein Standard
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        w.anwenden()
        self.assertEqual([z.split(';')[7] for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;%s;40;' % LIGA['D1'])], [''])

    def test_festival_zuhause_ohne_eigene_heimzeile_bekommt_keine_spielstaette(self):
        w = self.werkstatt()
        w.welt.csv['G1'] = [s for s in w.welt.csv['G1'] if s.nr != 10]
        w.welt.seite['G1'] = [s for s in w.welt.seite['G1'] if s.nr != 10]
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.seite['G1'].append(Spiel('G1', LIGA['G1'], 31, '2026-11-21', '10:00', EIGEN['G1'], '', 'Kinderfestival'))
        w.cache_neu()
        w.anwenden()
        self.assertEqual([z.split(';')[5:8] for z in zeilen_von(w.csv_lesen()) if z.startswith('G1;%s;31;' % LIGA['G1'])], [[EIGEN['G1'], '', '']])

    def test_freundschaftsspiel(self):
        s = Spiel('E2', FREUND['E2'], 51, '2026-12-13', '11:00', EIGEN['E2'], 'SGK Bad Homburg 2', 'Freundschaftsspiel')
        w, vorher, e = self.lauf_mit('E2', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('E2;%s;51;' % FREUND['E2'])]
        self.assertEqual(neue, ['E2;%s;51;13.12.2026;11:00;%s;SGK Bad Homburg 2;%s;;%s;Freundschaftsspiel' % (FREUND['E2'], EIGEN['E2'], HEIMORT['E2'], s.fbid)])
        self.assertEqual(e['zeilen'][0], 'ergänzt: E2 So 13.12. 11:00 Freundschaftsspiel, Heim gegen SGK Bad Homburg 2')

    def test_kinderfestival_heim_gast_leer(self):
        s = Spiel('F1', LIGA['F1'], 30, '2026-11-21', '10:00', EIGEN['F1'], '', 'Kinderfestival')
        w, vorher, e = self.lauf_mit('F1', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('F1;%s;30;' % LIGA['F1'])]
        self.assertEqual(neue, ['F1;%s;30;21.11.2026;10:00;%s;;%s;;;Kinderfestival' % (LIGA['F1'], EIGEN['F1'], HEIMORT['F1'])])
        self.assertEqual(e['zeilen'][0], 'ergänzt: F1 Sa 21.11. 10:00 Kinderfestival bei uns')

    def test_kinderfestival_auswaerts_mit_adresse_des_gastgebers(self):
        s = Spiel('F2', LIGA['F2'], 31, '2026-11-28', '09:30', 'VFR Gast 1', EIGEN['F2'], 'Kinderfestival')
        w, vorher, e = self.lauf_mit('F2', s)
        neue = [z for z in zeilen_von(w.csv_lesen()) if z.startswith('F2;%s;31;' % LIGA['F2'])]
        self.assertEqual(neue, ['F2;%s;31;28.11.2026;09:30;VFR Gast 1;%s;Gastweg 2, 60000 Frankfurt am Main;;;Kinderfestival' % (LIGA['F2'], EIGEN['F2'])])
        self.assertEqual(e['zeilen'][0], 'ergänzt: F2 Sa 28.11. 09:30 Kinderfestival bei VFR Gast 1')

    def test_neue_staffel_hinter_letzter_zeile_der_mannschaft_und_art(self):
        """F1 hat die Kennung 340233; ein Festival in der neuen Staffel 340530 kommt hinter das letzte F1-Festival."""
        s = Spiel('F1', '340530', 3, '2026-11-14', '13:30', EIGEN['F1'], '', 'Kinderfestival')
        w, vorher, e = self.lauf_mit('F1', s)
        zeilen = zeilen_von(vorher)
        letzte = max(i for i, z in enumerate(zeilen) if z.startswith('F1;'))
        neue = 'F1;340530;3;14.11.2026;13:30;%s;;%s;;;Kinderfestival' % (EIGEN['F1'], HEIMORT['F1'])
        self.assertNurDiese(vorher, w.csv_lesen(), eingefuegt={letzte: [neue]})

    def test_neue_meisterschaftsstaffel_wird_ergaenzt_und_einmalig_als_hinweis_gemeldet(self):
        """Die ersten Spiele einer neuen Meisterschaftsstaffel kommen in die CSV; build_ics.py und Widgets brauchen Handarbeit."""
        w = self.werkstatt()
        w.welt.seite['D1'] += [Spiel('D1', '340999', 1, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'),
                               Spiel('D1', '340999', 2, '2026-12-06', '13:00', 'FC Neu 2', EIGEN['D1'])]
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 2 geändert (2 ergänzt), nicht gepusht, 1 Hinweis')
        self.assertEqual(len(e['hinweise']), 1)
        self.assertTrue(e['hinweise'][0].startswith('D1: neue Meisterschaftsstaffel 340999, build_ics.py (TEAMS, INFO_STAFFEL, RUNDE)'))
        self.assertEqual(w.anwenden()['schluss'], 'NACHTRAG: unverändert')       # beim nächsten Lauf kein Hinweis mehr

    def test_mehrere_neue_zeilen_hinter_demselben_anker_nach_datum(self):
        w = self.werkstatt()
        w.welt.seite['D3'] += [Spiel('D3', LIGA['D3'], 61, '2026-12-13', '10:00', EIGEN['D3'], 'FC B 1'),
                               Spiel('D3', LIGA['D3'], 62, '2026-12-06', '10:00', 'FC A 1', EIGEN['D3'])]
        w.cache_neu()
        e = w.anwenden()
        zeilen = zeilen_von(w.csv_lesen())
        i1 = next(i for i, z in enumerate(zeilen) if ';61;' in z and z.startswith('D3;'))
        i2 = next(i for i, z in enumerate(zeilen) if ';62;' in z and z.startswith('D3;'))
        self.assertEqual(i1, i2 + 1, 'früheres Datum zuerst')
        self.assertEqual(e['schluss'], 'NACHTRAG: 2 geändert (2 ergänzt), nicht gepusht')

    def test_spiel_von_heute_wird_ergaenzt_spiel_von_gestern_nicht(self):
        w = self.werkstatt()
        w.welt.seite['E3'] += [Spiel('E3', LIGA['E3'], 70, HEUTE, '14:00', EIGEN['E3'], 'FC Heute 1'),
                               Spiel('E3', LIGA['E3'], 71, '2026-10-25', '14:00', EIGEN['E3'], 'FC Gestern 1')]
        w.cache_neu()
        e = w.anwenden()
        zeilen = zeilen_von(w.csv_lesen())
        self.assertTrue(any(z.startswith('E3;%s;70;' % LIGA['E3']) for z in zeilen))
        self.assertFalse(any(z.startswith('E3;%s;71;' % LIGA['E3']) for z in zeilen))
        self.assertEqual(e['hinweise'], [])

    def test_ohne_uhrzeit_wird_still_uebergangen_und_nicht_gemeldet(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 44, '2026-12-06', '', EIGEN['D1'], 'FC Ohne 1'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['schluss'], e['hinweise']), ('NACHTRAG: unverändert', []))
        self.assertTrue(any('ohne Uhrzeit' in s for s in e['stumm']))

    def test_unplausible_uhrzeit_wird_gemeldet(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 45, '2026-12-06', '00:00', EIGEN['D1'], 'FC Mitternacht 1'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('unplausibel' in h for h in e['hinweise']))

    def test_schluessel_schon_in_anderer_mannschaft_vergeben(self):
        w = self.werkstatt()
        fremd = w.welt.csv['D2'][1]       # D2-Zeile; dieselbe Staffel + Nummer wird bei D1 als „neu“ angeboten
        w.welt.seite['D1'].append(Spiel('D1', fremd.staffel, fremd.nr, '2026-12-06', '11:00', EIGEN['D1'], 'FC Doppel 1'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('schon vergeben' in h for h in e['hinweise']))

    def test_nichtantritt_in_der_zukunft_wird_nicht_ergaenzt(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 46, '2026-12-06', '11:00', EIGEN['D1'], 'FC Weg 1', status='Nichtantritt GAST'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: unverändert')

    def test_abgesetztes_spiel_das_in_der_csv_fehlt_wird_nicht_ergaenzt(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 47, '2026-12-06', '11:00', EIGEN['D1'], 'FC Weg 2', status='Absetzung'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: unverändert')

    def test_gleiche_partie_unter_anderer_nummer_wird_nicht_doppelt_ergaenzt(self):
        """Dieselbe Paarung am selben Tag mit anderer Spielnummer: Hinweis „Spielnummer abweichend“, keine zweite Zeile."""
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D1', 10)
        s.nr = 11
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Spielnummer oder Staffel abweichend' in h for h in e['hinweise']), e['hinweise'])


# ---------------------------------------------------------------- Verlegen, Absagen, Wieder ansetzen

class TestAenderungen(TestBasis):
    def zeile_nr(self, w, team, nr, staffel=None):
        zeilen = zeilen_von(w.csv_lesen())
        return next(i for i, z in enumerate(zeilen) if z.startswith('%s;%s;%d;' % (team, staffel or LIGA[team], nr)))

    def test_verlegen_uhrzeit(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = '13:30'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D1', 10)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i]
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: alt.replace(';01.11.2026;11:00;', ';01.11.2026;13:30;')})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')
        self.assertEqual(e['zeilen'][0], 'verlegt: D1 So 01.11. 11:00 → 13:30 · Heim gegen FC Muster 1')

    def test_verlegen_datum(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'E1', 20).datum = '2026-11-07'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'E1', 20)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i]
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: alt.replace(';08.11.2026;12:00;', ';07.11.2026;12:00;')})
        self.assertEqual(e['zeilen'][0], 'verlegt: E1 So 08.11. 12:00 → Sa 07.11. 12:00 · Auswärts bei SV Beispiel 2')

    def test_verlegen_datum_und_zeit_mit_doppelzeile_bei_fussball_de(self):
        """So zeigt fussball.de eine Verlegung: die alte Zeile mit dem neuen Termin im Ergebnisfeld, dazu die neue Zeile."""
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D2', 30)
        alte_zeile = deepcopy(s)
        alte_zeile.status = '06.12.2026 09:30'
        s.datum, s.zeit = '2026-12-06', '09:30'
        w.welt.seite['D2'].append(alte_zeile)
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D2', 30)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i]
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: alt.replace(';22.11.2026;11:30;', ';06.12.2026;09:30;')})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')

    def test_nachholspiel_vom_vergangenen_zum_kuenftigen_datum(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 1).datum = '2026-11-12'     # CSV: 11.10. (vorbei), fussball.de: 12.11.
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')

    def test_verlegung_in_die_vergangenheit_wird_nur_gemeldet(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).datum = '2026-10-20'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Vergangenheit' in h for h in e['hinweise']))

    def test_verlegung_mit_anderem_gegner_wird_nicht_uebernommen(self):
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D1', 10)
        s.zeit, s.gast = '13:30', 'Ganz Anderer FC 1'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Gegner abweichend' in h for h in e['hinweise']))

    def test_absetzung(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D1', 10)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i].split(';')
        alt[8] = 'abgesagt'
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(alt)})
        self.assertEqual(e['zeilen'][0], 'abgesagt: D1 So 01.11. 11:00 · Heim gegen FC Muster 1 (bei fussball.de abgesetzt)')

    def test_zurueckgezogen_zg(self):
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D3', 20)
        s.heim, s.status = 'SV Beispiel 2 zg.', 'Nichtantritt HEIM'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D3', 20)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i].split(';')
        alt[8] = 'abgesagt'
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(alt)})
        self.assertIn('Gegner zurückgezogen (zg.)', e['zeilen'][0])

    def test_nichtantritt_vor_dem_spieltag(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'E1', 30).status = 'Nichtantritt GAST'
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 abgesagt), nicht gepusht')
        self.assertIn('Nichtantritt', e['zeilen'][0])

    def test_absetzung_mit_eingetragenem_ergebnis_wird_nicht_ueberschrieben(self):
        w = self.werkstatt()
        w.welt.csv['D1'][1].ergebnis = '3:1'
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.spiel(w.welt.seite, 'D1', 10).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Ergebnis' in h for h in e['hinweise']))

    def test_wieder_ansetzen(self):
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D1', 10)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i].split(';')
        alt[8] = ''
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(alt)})
        self.assertEqual(e['zeilen'][0], 'wieder angesetzt: D1 So 01.11. 11:00 · Heim gegen FC Muster 1')

    def test_wieder_ansetzen_ohne_verlauf_nur_hinweis(self):
        """In der CSV „abgesagt“ (von Hand), fussball.de zeigt das Spiel weiter als angesetzt und hat es nie abgesetzt: die
        Absage bleibt, es gibt nur einen Hinweis. Mit WIEDER_ANSETZEN_NUR_MIT_VERLAUF = False (wörtlich nach Plan) wird sie zurückgenommen."""
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10, mit_verlauf=False)
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 01.11. 11:00 · Heim gegen FC Muster 1: in der CSV „abgesagt“, bei fussball.de angesetzt, aber dort früher nie abgesetzt (von Hand abgesagt?): nicht geändert'])
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis'))
        with mock.patch.object(N, 'WIEDER_ANSETZEN_NUR_MIT_VERLAUF', False):
            e2 = w.anwenden()
        self.assertEqual(e2['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')

    def test_wieder_ansetzen_mit_neuem_termin_in_einem_schritt(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).datum = '2026-11-08'
        w.csv_abgesagt_setzen('D1', 10)
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile_nr(w, 'D1', 10)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i].split(';')
        alt[8], alt[3] = '', '08.11.2026'
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(alt)})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')

    def test_abgesagt_bleibt_abgesagt_wenn_fussball_de_nichtantritt_zeigt(self):
        w = self.werkstatt()
        w.welt.csv['D1'][1].ergebnis = 'abgesagt'
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.spiel(w.welt.seite, 'D1', 10).status = 'Nichtantritt GAST'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: unverändert')

    def test_abgesagtes_spiel_ohne_listung_bei_fussball_de_bleibt_still(self):
        """In der CSV „abgesagt“ und bei fussball.de nur noch „spielfrei“ (D2 in der echten Tabelle): stimmt, keine Meldung."""
        w = self.werkstatt()
        s = w.welt.csv['D2'][1]
        s.ergebnis = 'abgesagt'
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.seite['D2'].remove(w.welt.spiel(w.welt.seite, 'D2', 10))
        text = seite_aus(w.welt.seite['D2'], html_spielfrei(s.datum, 'Kreisliga A', s.staffel, s.nr, EIGEN['D2']))
        w.cache_neu(ersetze={'D2': text})
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: unverändert')

    def test_ueberzaehlige_zeile_wird_nur_gemeldet_nie_geloescht(self):
        w = self.werkstatt()
        w.welt.csv['E2'].append(Spiel('E2', LIGA['E2'], 99, '2026-12-06', '11:00', EIGEN['E2'], 'FC Nur CSV 1'))
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['zeilen'][0], 'Hinweis: E2 So 06.12. 11:00 · Heim gegen FC Nur CSV 1: nur in der CSV, bei fussball.de nicht (mehr) vorhanden: nicht gelöscht')
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis'))
        self.assertEqual(e['code'], 2, 'Auffällig = Exit-Code 2, auch wenn es nur Hinweise sind')

    def test_ueberzaehlige_zeile_die_bei_fussball_de_spielfrei_ist(self):
        w = self.werkstatt()
        s = Spiel('E2', LIGA['E2'], 99, '2026-12-06', '11:00', EIGEN['E2'], 'FC Weg 3')
        w.welt.csv['E2'].append(s)
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        text = seite_aus(w.welt.seite['E2'], html_spielfrei(s.datum, 'Kreisliga A', s.staffel, s.nr, EIGEN['E2']))
        w.cache_neu(ersetze={'E2': text})
        e = w.anwenden()
        self.assertIn('jetzt spielfrei (Spiel entfallen?)', e['zeilen'][0])

    def test_abweichungen_werden_nur_gemeldet(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).heim, w.welt.spiel(w.welt.seite, 'D1', 10).gast = 'FC Muster 1', EIGEN['D1']   # Heim/Auswärts vertauscht
        w.welt.spiel(w.welt.seite, 'D2', 10).gast = 'Ganz Anderer FC 1'                                                       # anderer Gegner
        s = w.welt.spiel(w.welt.seite, 'E3', 50, FREUND['E3'])
        s.art, s.staffel = 'Kreispokal', FREUND['E3']                                                                         # andere Wettbewerbsart
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        texte = ' | '.join(e['hinweise'])
        for stichwort in ('Heim/Auswärts abweichend', 'Gegner abweichend', 'Wettbewerbsart abweichend'):
            self.assertIn(stichwort, texte)

    def test_vergangenheit_bleibt_unberuehrt_und_still(self):
        w = self.werkstatt()
        # vergangene Spiele (vor 26.10.): neu bei fussball.de, verlegt, abgesetzt, nur in der CSV, Heim/Auswärts vertauscht
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 80, '2026-10-18', '10:00', EIGEN['D1'], 'FC Vorbei 1'))
        w.welt.spiel(w.welt.seite, 'D2', 1).zeit = '11:15'
        w.welt.spiel(w.welt.seite, 'D3', 1).status = 'Absetzung'
        w.welt.csv['E1'].append(Spiel('E1', LIGA['E1'], 81, '2026-10-17', '10:00', EIGEN['E1'], 'FC Nur CSV 2'))
        s = w.welt.spiel(w.welt.seite, 'E2', 1)
        s.heim, s.gast = EIGEN['E2'], 'Alt FC 1'
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['zeilen'], e['schluss']), ([], 'NACHTRAG: unverändert'))
        self.assertEqual(len([n for n in e['stumm'] if 'aus der Vergangenheit, unberührt' in n]), 5, e['stumm'])

    def test_vorlaeufige_spiele_werden_weder_ergaenzt_noch_geaendert(self):
        """Symbol „vorläufiges Spiel“ (icon-pre-publish): noch nicht vom Staffelleiter freigegeben, also nichts tun."""
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Vorläufig 1', vorlaeufig=True))
        s = w.welt.spiel(w.welt.seite, 'D2', 10)
        s.zeit, s.vorlaeufig = '13:30', True
        w.welt.spiel(w.welt.seite, 'D3', 10).status = 'Absetzung'
        w.welt.spiel(w.welt.seite, 'D3', 10).vorlaeufig = True
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['hinweise'], e['schluss']), ([], 'NACHTRAG: unverändert'))
        self.assertEqual(len([t for t in e['stumm'] if 'vorläufig' in t]), 3)
        w.welt.spiel(w.welt.seite, 'D2', 10).vorlaeufig = False     # nach der Freigabe greift die Änderung
        w.cache_neu()
        self.assertEqual(w.anwenden()['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')

    def test_unklarer_vermerk_bei_fussball_de_wird_nur_gemeldet(self):
        """„Ausfall“, „Annuliert“ & Co. kennt das Skript nicht als Absage: bei künftigen Spielen nur melden."""
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Ausfall 1', status='Ausfall'))
        w.welt.spiel(w.welt.seite, 'D2', 10).status = 'Annuliert'
        w.welt.spiel(w.welt.seite, 'D2', 10).zeit = '13:30'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(sorted(h.split(': ')[0].split(' · ')[0] for h in e['hinweise']), ['D1 So 29.11. 13:00', 'D2 So 01.11. 13:30'])
        self.assertTrue(all('Vermerk' in h for h in e['hinweise']), e['hinweise'])

    def test_schluessel_doppelt_in_der_csv(self):
        w = self.werkstatt()
        dopp = deepcopy(w.welt.spiel(w.welt.csv, 'D1', 10))
        dopp.zeit = '14:00'
        w.welt.csv['D1'].append(dopp)
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = '13:30'      # Verlegung dieses Schlüssels darf nicht angefasst werden
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('doppelt' in h for h in e['hinweise']), e['hinweise'])

    def test_schluessel_doppelt_bei_fussball_de(self):
        w = self.werkstatt()
        dopp = deepcopy(w.welt.spiel(w.welt.seite, 'D1', 10))
        dopp.datum, dopp.zeit = '2026-11-02', '12:00'
        w.welt.seite['D1'].append(dopp)
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('nicht eindeutig' in h for h in e['hinweise']), e['hinweise'])


# ---------------------------------------------------------------- Mengenbremse, Quelle, Idempotenz, Not-Aus, Form

class TestSeite(unittest.TestCase):
    def test_spiel_marken_ids_und_vorlaeufig_im_gleichschritt_mit_dem_parser(self):
        spiele = [Spiel('D1', LIGA['D1'], 1, '2026-11-01', '11:00', EIGEN['D1'], 'FC A 1'),
                  Spiel('D1', LIGA['D1'], 2, '2026-11-08', '11:00', 'FC B 1', EIGEN['D1'], vorlaeufig=True),
                  Spiel('D1', LIGA['D1'], 3, '2026-11-15', '10:00', EIGEN['D1'], '', 'Kinderfestival')]
        text = seite_aus(spiele, html_spielfrei('2026-11-22', 'Kreisliga A', LIGA['D1'], 4, EIGEN['D1']))
        self.assertEqual(len(A.parse_spielplan(text)), 4)
        marken = N.spiel_marken(text)
        self.assertEqual(marken, [(spiele[0].fbid, False), (spiele[1].fbid, True), ('', False), ('', False)])
        fb, fehler = N.seite_lesen(text)
        self.assertIsNone(fehler)
        self.assertEqual([(g['nr'], g['status'], g['vorlaeufig']) for g in fb], [(1, 'angesetzt', False), (2, 'angesetzt', True), (3, 'angesetzt', False), (4, 'spielfrei', False)])

    def test_defekte_seite_wird_als_fehler_gemeldet_nie_als_ausnahme(self):
        with mock.patch.object(A, 'parse_spielplan', side_effect=AttributeError('kaputt')):
            fb, fehler = N.seite_lesen(seite_aus(basis_spiele('D1')))
        self.assertEqual((fb, fehler), ([], 'Seite nicht lesbar (AttributeError)'))

    def test_seite_mit_abweichender_spielzahl_wird_abgelehnt(self):
        with mock.patch.object(N, 'spiel_marken', return_value=[]):
            self.assertIn('Seitenaufbau unerwartet', N.seite_lesen(seite_aus(basis_spiele('D1')))[1])


class TestSchutz(TestBasis):
    def neue(self, w, team, n, ab=40):
        for i in range(n):
            w.welt.seite[team].append(Spiel(team, LIGA[team], ab + i, (date(2026, 11, 29) + timedelta(days=7 * i)).isoformat(), '11:00', EIGEN[team], 'FC Neu %d' % (ab + i)))

    def test_mengenbremse_insgesamt_11_nichts_geschrieben_10_geschrieben(self):
        w = self.werkstatt()
        for t, n in (('D1', 4), ('D2', 4), ('E1', 3)):
            self.neue(w, t, n)
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: Auffällig, nicht übernommen – 11 Änderungen in einem Lauf (Grenze 10)')
        self.assertFalse(e['angewendet'])
        self.assertTrue(e['zeilen'][0].startswith('nicht übernommen: ergänzt: '))
        self.assertEqual(e['code'], 2)
        w2 = self.werkstatt()
        for t, n in (('D1', 4), ('D2', 3), ('E1', 3)):
            self.neue(w2, t, n)
        w2.cache_neu()
        e2 = w2.anwenden()
        self.assertEqual(e2['schluss'], 'NACHTRAG: 10 geändert (10 ergänzt), nicht gepusht')

    def test_mengenbremse_je_mannschaft_7_nichts_6_geschrieben(self):
        w = self.werkstatt()
        self.neue(w, 'D1', 7)
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: Auffällig, nicht übernommen – 7 Änderungen bei D1 in einem Lauf (Grenze 6 je Mannschaft)')
        w2 = self.werkstatt()
        self.neue(w2, 'D1', 6)
        w2.cache_neu()
        self.assertEqual(w2.anwenden()['schluss'], 'NACHTRAG: 6 geändert (6 ergänzt), nicht gepusht')

    def test_mengenbremse_zaehlt_alle_arten(self):
        w = self.werkstatt()
        for nr in (10, 20, 30):
            w.welt.spiel(w.welt.seite, 'D1', nr).zeit = '15:00'            # 3 verlegt
        for nr in (10, 20, 30):
            w.welt.spiel(w.welt.seite, 'D2', nr).status = 'Absetzung'      # 3 abgesagt
        self.neue(w, 'D3', 2)                                              # 2 ergänzt
        self.neue(w, 'E1', 3)                                              # 3 ergänzt -> insgesamt 11
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertIn('11 Änderungen', e['schluss'])

    def quelle_testen(self, ersatz, team='D1', erwartet='Quelle unplausibel'):
        w = self.werkstatt()
        self.neue(w, 'E1', 1)                                              # E1 hat eine Ergänzung: die übrigen Mannschaften laufen
        w.cache_neu(ersetze={team: ersatz})
        e = w.anwenden()
        self.assertTrue(any(erwartet in f for f in e['fehler']), e['fehler'])
        self.assertTrue(any(f.startswith('%s übersprungen' % team) for f in e['fehler']))
        self.assertEqual(len(e['fehler']), 1, 'nur diese Mannschaft fällt aus')
        self.assertTrue(e['angewendet'], 'die übrigen Mannschaften laufen')
        self.assertTrue(any(z.startswith('E1;%s;40;' % LIGA['E1']) for z in zeilen_von(w.csv_lesen())))
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – %s übersprungen' % team), e['schluss'])
        self.assertIn('1 geändert (1 ergänzt)', e['schluss'])
        self.assertEqual(e['code'], 2)

    def test_unerwartete_ausnahme_in_einer_mannschaft_haelt_die_anderen_nicht_auf(self):
        w = self.werkstatt()
        self.neue(w, 'E1', 1)
        w.cache_neu()
        echt = N.plane

        def kaputt(team, *a, **k):
            if team[0] == 'D1':
                raise RuntimeError('Absicht')
            return echt(team, *a, **k)
        with mock.patch.object(N, 'plane', side_effect=kaputt):
            e = w.anwenden()
        self.assertEqual(e['fehler'], ['D1 übersprungen: unerwarteter Fehler (RuntimeError: Absicht)'])
        self.assertTrue(e['angewendet'])
        self.assertEqual(e['code'], 2)

    def test_quelle_leere_seite(self):
        self.quelle_testen('', erwartet='Seite zu kurz')

    def test_quelle_kurze_seite(self):
        self.quelle_testen('<html><body>Zugriff verweigert</body></html>', erwartet='Seite zu kurz')

    def test_quelle_sperrseite_ohne_spielplan(self):
        self.quelle_testen('<html><body>' + '<p>Bitte bestätigen Sie, dass Sie kein Roboter sind.</p>' * 20 + '</body></html>', erwartet='kein Spielplan erkannt')

    def test_quelle_nur_zehn_statt_dreissig_spiele(self):
        w = self.werkstatt()
        for i in range(26):                                                # D1 hat 3+1 Ligaspiele + 26 = 30 Zeilen in der CSV
            w.welt.beide('D1', Spiel('D1', LIGA['D1'], 100 + i, (date(2026, 11, 1) + timedelta(days=i)).isoformat(), '11:00', EIGEN['D1'], 'FC Voll %d' % i))
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        zehn = seite_aus(w.welt.seite['D1'][:10])
        self.assertEqual(len([z for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;')]), 32)
        w.welt.seite['E1'].append(Spiel('E1', LIGA['E1'], 40, '2026-11-29', '11:00', EIGEN['E1'], 'FC Neu 40'))
        w.cache_neu(ersetze={'D1': zehn})
        e = w.anwenden()
        self.assertTrue(any('nur 10 Spiele gelesen' in f and f.startswith('D1 übersprungen') for f in e['fehler']), e['fehler'])
        self.assertTrue(e['angewendet'])

    def test_quelle_falsche_mannschaft(self):
        w = self.werkstatt()
        fremd = [Spiel('D1', LIGA['D1'], s.nr, s.datum, s.zeit, 'FC Fremd %d' % i, 'SV Fremd %d' % i, s.art, ort=s.ort) for i, s in enumerate(basis_spiele('D1'))]
        self.quelle_testen(seite_aus(fremd), erwartet='falsche Mannschaft')

    def test_leerer_aber_gueltiger_spielplan(self):
        """Sommerpause: eine gültige Seite ohne Spiele ist nur dann ein Fehler, wenn die CSV Zeilen der Mannschaft hat."""
        leer = seite_aus([], zusatz='<!-- ' + 'Filterformular und Legende ' * 20 + '-->')   # echte Seiten sind viel länger als 500 Zeichen
        w = self.werkstatt()
        w.cache_neu(ersetze={'D1': leer})
        e = w.anwenden()
        self.assertEqual(len(e['fehler']), 1)
        self.assertRegex(e['fehler'][0], r'^D1 übersprungen: Quelle unplausibel \(keine Spiele gelesen, die CSV hat \d+ Zeilen')
        w2 = self.werkstatt()
        w2.welt.csv['G1'], w2.welt.seite['G1'] = [], []
        with open(w2.csv, 'wb') as f:
            f.write(w2.welt.csv_bytes())
        w2.cache_neu(ersetze={'G1': leer})
        self.assertEqual(w2.anwenden()['schluss'], 'NACHTRAG: unverändert')

    def test_cache_aus_der_zukunft_wird_nicht_genommen(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT + timedelta(days=30))
        e = w.anwenden()
        self.assertEqual(len(e['uebersprungen']), 11)
        self.assertEqual(len(e['fehler']), 1, 'gleiche Ursache bei mehreren Mannschaften: eine Meldung')
        self.assertIn('11 Mannschaften übersprungen, Cache-Stand liegt in der Zukunft (Systemuhr falsch?): Herren, A-Jugend, D1', e['fehler'][0])

    def test_quelle_vertauschte_ablage_seite_einer_anderen_eigenen_mannschaft(self):
        """Die Seite von F2 liegt im Cache von G1: beide nennen unseren Verein, aber G1 findet keine seiner CSV-Zeilen wieder."""
        w = self.werkstatt()
        w.cache_neu(ersetze={'G1': seite_aus(w.welt.seite['F2'])})
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['fehler']), 1)
        self.assertRegex(e['fehler'][0], r'^G1 übersprungen: Quelle unplausibel \(nur 0 von 3 CSV-Zeilen bei fussball.de wiederzufinden')

    def test_quelle_fehlender_cache(self):
        w = self.werkstatt()
        w.cache_neu(ohne=('D1',))
        e = w.anwenden()
        self.assertEqual(e['fehler'], ['D1 übersprungen: kein Cache-Stand vorhanden'])
        self.assertEqual(e['schluss'], 'NACHTRAG: Fehler – D1 übersprungen: kein Cache-Stand vorhanden')

    def test_quelle_veraltet_im_anwendungsmodus_uebersprungen_im_berichtsmodus_nur_vermerkt(self):
        w = self.werkstatt()
        self.neue(w, 'D1', 1)
        w.cache_neu(stempel=JETZT - timedelta(hours=50))
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['uebersprungen']), 11)
        self.assertEqual(e['fehler'], ['11 Mannschaften übersprungen, Cache älter als 30 Stunden (jüngster Stand vom 24.10. 05:10): '
                                       'Herren, A-Jugend, D1, D2, D3, E1, E2, E3, F1, F2, G1'])
        self.assertEqual(e['schluss'], 'NACHTRAG: Fehler – ' + e['fehler'][0])
        b = w.lauf()
        self.assertEqual(len(b['aenderungen']), 1, 'Berichtsmodus wertet trotzdem aus')
        self.assertTrue(any('Quelle älter als 30 Stunden' in h for h in b['hinweise']))
        b2 = w.lauf(nur_cache=True)
        self.assertEqual(b2['hinweise'], [], 'mit --nur-cache keine Altersgrenze im Berichtsmodus')
        b3 = w.anwenden(nur_cache=True)
        self.assertEqual(len(b3['uebersprungen']), 11, 'im Anwendungsmodus gilt die Grenze auch mit --nur-cache')
        b4 = w.anwenden(nur_cache=True, max_alter=0)
        self.assertTrue(b4['angewendet'], '--max-alter 0 hebt die Grenze auf')

    def test_idempotenz_zweiter_lauf_aendert_nichts(self):
        w = self.werkstatt()
        self.neue(w, 'D1', 2)
        w.welt.spiel(w.welt.seite, 'D2', 10).zeit = '13:30'
        w.welt.spiel(w.welt.seite, 'D3', 10).status = 'Absetzung'
        w.csv_abgesagt_setzen('E1', 10)
        w.cache_neu()
        e1 = w.anwenden()
        self.assertEqual(e1['schluss'], 'NACHTRAG: 5 geändert (2 ergänzt, 1 verlegt, 1 abgesagt, 1 wieder angesetzt), nicht gepusht')
        nach_lauf_1 = w.csv_lesen()
        e2 = w.anwenden()
        self.assertEqual(e2['schluss'], 'NACHTRAG: unverändert')
        self.assertEqual(w.csv_lesen(), nach_lauf_1)
        self.assertEqual(w.lauf()['schluss'], 'NACHTRAG: unverändert')

    def test_notaus_datei_nur_bericht(self):
        w = self.werkstatt()
        self.neue(w, 'D1', 2)
        w.cache_neu()
        open(w.notaus, 'w').close()
        vorher = w.csv_lesen()
        e = w.anwenden(push=True)
        self.pruefe_unveraendert(w, vorher)
        self.assertFalse(e['angewendet'])
        self.assertEqual(e['schluss'], 'NACHTRAG: Auffällig, nicht übernommen – Not-Aus aktiv, 2 Änderungen wären fällig')
        self.assertTrue(e['zeilen'][0].startswith('wäre fällig: ergänzt: D1'))
        w2 = self.werkstatt()
        open(w2.notaus, 'w').close()
        self.assertEqual(w2.anwenden()['schluss'], 'NACHTRAG: unverändert')

    def test_berichtsmodus_schreibt_nichts_und_zeigt_csv_zeilen(self):
        w = self.werkstatt()
        self.neue(w, 'D1', 1)
        w.cache_neu()
        vorher = w.csv_lesen()
        dateien = sorted(os.listdir(w.repo))
        e = w.lauf()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(sorted(os.listdir(w.repo)), dateien)
        self.assertEqual(e['schluss'], 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')
        self.assertEqual(e['zeilen'][0], 'Vorschlag ergänzt: D1 So 29.11. 11:00 Heim gegen FC Neu 40')
        self.assertTrue(e['zeilen'][1].startswith('    CSV Zeile ') and ';FC Neu 40;' in e['zeilen'][1])

    def test_push_ohne_anwenden_ist_ein_fehler(self):
        with self.assertRaises(N.AufrufFehler):
            N.optionen_aus(['--push'])


class TestForm(TestBasis):
    def mit_aenderung(self, bom_tolerant=False, **form):
        w = self.werkstatt(**form)
        if bom_tolerant:       # der echte build_ics.py liest ohne utf-8-sig und scheitert an einem BOM: Attrappe, die es verträgt
            pfad = os.path.join(w.repo, 'build_ics.py')
            with open(pfad, encoding='utf-8') as f:
                text = f.read()
            with open(pfad, 'w', encoding='utf-8') as f:
                f.write(text.replace('CSV_FILE.open(encoding="utf-8")', 'CSV_FILE.open(encoding="utf-8-sig")'))
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.welt.spiel(w.welt.seite, 'D2', 10).zeit = '13:30'
        w.welt.spiel(w.welt.seite, 'D3', 10).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        return w, vorher, e

    def pruefe(self, w, vorher, e, eol='\r\n', bom=False):
        nachher = w.csv_lesen()
        self.assertEqual(e['schluss'], 'NACHTRAG: 3 geändert (1 ergänzt, 1 verlegt, 1 abgesagt), nicht gepusht')
        self.assertEqual(nachher.startswith(b'\xef\xbb\xbf'), bom)
        text = nachher[3 if bom else 0:].decode('utf-8')
        zeilen = text.split(eol)
        self.assertEqual(zeilen[0], ';'.join(N.SPALTEN))
        self.assertTrue(all(('\r' not in z and '\n' not in z) for z in zeilen))
        alt = vorher[3 if bom else 0:].decode('utf-8').split(eol)
        self.assertEqual(len(zeilen), len(alt) + 1)
        self.assertEqual(sum(1 for z in alt if z in zeilen), len(alt) - 2, 'bis auf zwei geänderte Zeilen bleibt jede alte Zeile Byte für Byte gleich')
        for z in zeilen[1:]:
            if z:
                f = z.split(';')
                self.assertEqual(len(f), 11, z)
                self.assertRegex(f[3], r'^\d{2}\.\d{2}\.\d{4}$')
                self.assertRegex(f[4], r'^\d{2}:\d{2}$')
        self.assertEqual(text.count(eol), (alt and vorher.decode('utf-8-sig').count(eol)) + 1)

    def test_crlf_ohne_bom_mit_zeilenende_am_ende(self):
        w, vorher, e = self.mit_aenderung()
        self.pruefe(w, vorher, e)
        self.assertTrue(w.csv_lesen().endswith(b'\r\n'))

    def test_bom_bleibt_erhalten(self):
        w, vorher, e = self.mit_aenderung(bom=True, bom_tolerant=True)
        self.pruefe(w, vorher, e, bom=True)
        self.assertEqual(w.csv_lesen()[:3], b'\xef\xbb\xbf')

    def test_bom_datei_scheitert_am_echten_generator_dann_wird_nichts_geschrieben(self):
        """Der echte build_ics.py verträgt kein BOM (die GitHub-Aktion würde scheitern): die Prüfung stoppt das Schreiben."""
        w, vorher, e = self.mit_aenderung(bom=True)
        self.assertEqual(w.csv_lesen(), vorher)
        self.assertIn('build_ics.py scheitert mit dem bisherigen Stand', e['schluss'])

    def test_ohne_zeilenende_am_dateiende_bleibt_so(self):
        w, vorher, e = self.mit_aenderung(ende=False)
        self.assertFalse(w.csv_lesen().endswith(b'\r\n'))
        self.assertEqual(e['schluss'], 'NACHTRAG: 3 geändert (1 ergänzt, 1 verlegt, 1 abgesagt), nicht gepusht')

    def test_lf_datei_bleibt_lf(self):
        w, vorher, e = self.mit_aenderung(eol='\n')
        self.assertNotIn(b'\r', w.csv_lesen())
        self.assertEqual(e['schluss'], 'NACHTRAG: 3 geändert (1 ergänzt, 1 verlegt, 1 abgesagt), nicht gepusht')

    def test_neue_zeile_am_dateiende_wenn_die_mannschaft_keine_zeile_hat(self):
        """Hat die Mannschaft keine Zeile, kommt die neue ans Dateiende (mit richtigem Zeilenende)."""
        w = self.werkstatt()
        w.welt.csv['G1'] = []
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes())
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 2 geändert (2 ergänzt), nicht gepusht')    # das vergangene Spiel (nr 1) kommt nicht dazu
        zeilen = zeilen_von(w.csv_lesen())
        self.assertTrue(all(z.startswith('G1;') for z in zeilen[-2:]))
        self.assertEqual(zeilen[:-2], zeilen_von(vorher))

    def test_unerwartete_form_wird_nie_geschrieben(self):
        for name, aenderung in (('Anführungszeichen', lambda b: b.replace(b'FC Muster 1', b'FC "Muster" 1', 1)),
                                ('Kopfzeile', lambda b: b.replace(b'team;staffel', b'mannschaft;staffel', 1)),
                                ('gemischte Zeilenenden', lambda b: b.replace(b'\r\n', b'\n', 3)),
                                ('Leerzeile', lambda b: b.replace(b'\r\n', b'\r\n\r\n', 1)),
                                ('Spaltenzahl', lambda b: b.replace(b'FC Muster 1', b'FC;Muster 1', 1))):
            with self.subTest(name):
                w = self.werkstatt()
                w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
                w.cache_neu()
                kaputt = aenderung(w.csv_lesen())
                with open(w.csv, 'wb') as f:
                    f.write(kaputt)
                e = w.anwenden()
                self.assertEqual(w.csv_lesen(), kaputt)
                self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – spiele.csv nicht lesbar oder unerwartete Form'), e['schluss'])
                self.assertEqual(e['code'], 1)

    def test_feld_mit_trennzeichen_aus_fussball_de_wird_nicht_ergaenzt(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC; Komisch 1'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.assertEqual(w.csv_lesen(), vorher)
        self.assertTrue(any('Trennzeichen' in h for h in e['hinweise']), e['hinweise'])


# ---------------------------------------------------------------- Prüfung vor dem Schreiben, Wiederherstellung

class TestPruefung(TestBasis):
    def mit_neuem_spiel(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        return w

    def test_gebaute_kalender_haben_genau_die_ergaenzten_termine_mehr(self):
        w = self.mit_neuem_spiel()
        alt, neu = w.csv_lesen(), None
        e = w.anwenden()
        self.assertTrue(e['angewendet'])
        for name, daten in (('alt', alt), ('neu', w.csv_lesen())):
            ziel = os.path.join(w.dir, 'bau-' + name)
            os.makedirs(ziel)
            rc, _ = N.bauen(w.repo, daten, ziel)
            self.assertEqual(rc, 0)
        self.assertEqual(N.ics_lesen(os.path.join(w.dir, 'bau-neu', 'docs', 'alle.ics'))[0],
                         N.ics_lesen(os.path.join(w.dir, 'bau-alt', 'docs', 'alle.ics'))[0] + 1)

    def test_pruefung_scheitert_nichts_geschrieben(self):
        """build_ics.py bricht bei der neuen CSV ab (Attrappe): die Datei bleibt unangetastet, es wird nichts geschrieben."""
        w = self.mit_neuem_spiel()
        with open(os.path.join(w.repo, 'build_ics.py'), 'w') as f:
            f.write('import sys\nsys.exit(1 if "FC Neu 1" in open("spiele.csv", encoding="utf-8").read() else 0)\n')
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertFalse(e['angewendet'])
        self.assertEqual(e['code'], 1)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Prüfung vor dem Schreiben fehlgeschlagen, nichts geschrieben: build_ics.py scheitert'), e['schluss'])

    def test_bisheriger_stand_baut_nicht_nichts_geschrieben(self):
        w = self.mit_neuem_spiel()
        with open(os.path.join(w.repo, 'build_ics.py'), 'w') as f:
            f.write('import sys\nsys.exit(3)\n')
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertIn('bisherigen', e['schluss'])

    def test_vorhandene_maengel_blockieren_nicht_aber_neue_schon(self):
        """Eine Zeile ohne Spielnummer (build_ics.py überspringt sie) und ein doppelter Schlüssel sind schon in der CSV: Änderungen
        an anderen Zeilen laufen trotzdem. Verschlechtert eine Änderung die Lage (neue doppelte Zeile), stoppt die Prüfung."""
        w = self.werkstatt()
        dopp = deepcopy(w.welt.spiel(w.welt.csv, 'D2', 10))
        dopp.zeit = '14:00'
        w.welt.csv['D2'].append(dopp)
        extra = ['HERREN;341727;;01.12.2026;11:00;Ohne Nummer 1;FFV Sportfreunde 04 FFM;;;;Meisterschaft']
        with open(w.csv, 'wb') as f:
            f.write(w.welt.csv_bytes(extra=extra))
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 2 Hinweise')
        self.assertTrue(any(h.startswith('1 Zeile in spiele.csv nicht lesbar (Zeile ') for h in e['hinweise']) and any('doppelt' in h for h in e['hinweise']), e['hinweise'])
        # eine Änderung, die einen zweiten Termin mit derselben UID erzeugt, wird von der Prüfung gestoppt
        alt = N.CsvDatei(w.csv_lesen())
        a = N.Aenderung('ergaenzt', 'D1', 'D1', ('340782', 40), ['D1', '340782', '40', '29.11.2026', '13:00', 'x', 'y', '', '', '', 'Meisterschaft'],
                        datum='2026-11-29', zeit='13:00')
        neu_bytes = alt.bytes_aus(alt.zeilen + [';'.join(a.neu)])
        self.assertTrue(any('nicht mehr eindeutig' in p for p in N.validiere(w.csv_lesen(), neu_bytes, [a], w.repo)))

    def test_fehler_nach_dem_schreiben_stellt_das_original_wieder_her(self):
        w = self.mit_neuem_spiel()
        vorher = w.csv_lesen()
        with mock.patch.object(N, 'gegenlesen', side_effect=[N.LaufFehler('absichtlicher Fehler nach dem Schreiben'), None]):
            e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertFalse(e['angewendet'])
        self.assertEqual(e['code'], 1)
        self.assertIn('absichtlicher Fehler nach dem Schreiben – spiele.csv wiederhergestellt, nichts committet', e['schluss'])
        self.assertEqual([n for n in os.listdir(w.repo) if n.endswith('.tmp')], [])

    def test_ungepruefte_aenderung_wird_nicht_geschrieben_wenn_die_datei_sich_inzwischen_aenderte(self):
        w = self.mit_neuem_spiel()
        echt = N.validiere

        def stoerend(*a, **k):
            with open(w.csv, 'ab') as f:           # jemand ändert die Datei während der Prüfung
                f.write(b'')
            with open(w.csv, 'wb') as f:
                f.write(w.welt.csv_bytes(extra=['HERREN;341727;999;01.12.2026;11:00;X;Y;;;;Meisterschaft']))
            return echt(*a, **k)
        with mock.patch.object(N, 'validiere', side_effect=stoerend):
            e = w.anwenden()
        self.assertFalse(e['angewendet'])
        self.assertIn('hat sich während des Laufs geändert', e['schluss'])


# ---------------------------------------------------------------- G-Jugend selbst abrufen (Attrappe, kein Netzwerk)

class FalscheAntwort:
    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.text.encode('utf-8')


class TestAbruf(TestBasis):
    def test_g1_wird_nach_20_stunden_einmal_abgerufen_und_gelesen(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(hours=26))
        neu = Spiel('G1', LIGA['G1'], 30, '2026-11-21', '10:00', EIGEN['G1'], '', 'Kinderfestival')
        w.welt.seite['G1'].append(neu)
        aufrufe = []

        def antwort(req, timeout=None):
            aufrufe.append(req.full_url)
            return FalscheAntwort(seite_aus(w.welt.seite['G1']))
        for t in TEAMS:                       # die übrigen Mannschaften liefert der Mac-Task: hier frisch (2 Stunden alt)
            if t != 'G1':
                shutil.rmtree(os.path.join(w.cache, SLUG[t]))
        w.welt.cache_schreiben(w.cache, ohne=('G1',))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            e = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 1)
        self.assertIn('ajax.team.matchplan', aufrufe[0])
        self.assertIn('team-id/' + dict((t[0], t[3]) for t in A.TEAMS)['G1'], aufrufe[0])
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertEqual(len(os.listdir(os.path.join(w.cache, 'g-jugend'))), 2, 'der Abruf liegt im Cache')
        # zweiter Lauf kurz danach: der Cache ist jünger als 20 Stunden, kein neuer Abruf
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            e2 = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(e2['schluss'], 'NACHTRAG: unverändert')

    def test_g1_abruf_fehlgeschlagen_alter_cache_unter_30_stunden_wird_genommen(self):
        w = self.werkstatt()
        w.cache_neu()
        os.rename(os.path.join(w.cache, 'g-jugend', STAND.strftime('%Y%m%d-%H%M%S') + '.html'),
                  os.path.join(w.cache, 'g-jugend', (JETZT - timedelta(hours=25)).strftime('%Y%m%d-%H%M%S') + '.html'))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=urllib.error.URLError('Verbindung gesperrt')) as m:
            e = w.anwenden(abrufen=True)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(e['fehler'], [])
        self.assertTrue(any(h.startswith('G1: Abruf nicht möglich') for h in e['hinweise']), e['hinweise'])
        self.assertTrue(os.path.exists(os.path.join(w.cache, 'g-jugend', 'fehlversuch.txt')))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=urllib.error.URLError('nochmal')) as m2:
            w.anwenden(abrufen=True)
        self.assertEqual(m2.call_count, 0, 'nach einem Fehlschlag kein neuer Versuch binnen 20 Stunden')

    def test_g1_abruf_fehlgeschlagen_und_cache_zu_alt_g1_uebersprungen(self):
        w = self.werkstatt()
        w.cache_neu()
        os.rename(os.path.join(w.cache, 'g-jugend', STAND.strftime('%Y%m%d-%H%M%S') + '.html'),
                  os.path.join(w.cache, 'g-jugend', (JETZT - timedelta(hours=40)).strftime('%Y%m%d-%H%M%S') + '.html'))
        fehler403 = urllib.error.HTTPError('u', 403, 'Verboten', {}, io.BytesIO(b''))
        self.addCleanup(fehler403.close)
        with mock.patch.object(urllib.request, 'urlopen', side_effect=fehler403):
            e = w.anwenden(abrufen=True)
        self.assertEqual(len(e['fehler']), 1)
        self.assertTrue(e['fehler'][0].startswith('G1 übersprungen: Cache-Stand vom'), e['fehler'])

    def test_berichtsmodus_und_nur_cache_rufen_nie_ab(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(hours=26))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('Abruf!')) as m:
            w.lauf(abrufen=True)
            w.anwenden(abrufen=True, nur_cache=True)
            w.anwenden(abrufen=False)
        self.assertEqual(m.call_count, 0)

    def test_abruf_setzt_die_abgleich_einstellungen_zurueck(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(hours=26))
        agent, abstand = A.USER_AGENT, A.MIN_ABSTAND_TAGE
        with mock.patch.object(urllib.request, 'urlopen', side_effect=lambda req, timeout=None: (
                self.assertIn('SpeuzerSpielplanNachtrag', req.get_header('User-agent')) or FalscheAntwort(seite_aus(w.welt.seite['G1'])))):
            w.anwenden(abrufen=True, max_alter=0)
        self.assertEqual((A.USER_AGENT, A.MIN_ABSTAND_TAGE), (agent, abstand))


# ---------------------------------------------------------------- Git in Wegwerf-Klonen mit lokalem Bare-Remote

def git_ok(ordner, *args):
    r = subprocess.run(['git', '-C', ordner] + list(args), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, 'git %s: %s' % (' '.join(args), r.stderr)
    return r.stdout.strip()


class TestGit(TestBasis):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nachtrag-git-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        gitconfig = os.path.join(self.dir, 'gitconfig')
        with open(gitconfig, 'w') as f:
            f.write('[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n')
        env = mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': gitconfig, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'})
        env.start()
        self.addCleanup(env.stop)
        self.welt = Welt()
        self.bare = os.path.join(self.dir, 'remote.git')
        subprocess.run(['git', 'init', '--bare', '-q', '-b', 'main', self.bare], check=True)
        saat = os.path.join(self.dir, 'saat')
        os.makedirs(os.path.join(saat, 'docs'))
        for n in ('build_ics.py', 'vorlage_index.html'):
            shutil.copy(os.path.join(REPO, n), os.path.join(saat, n))
        with open(os.path.join(saat, 'spiele.csv'), 'wb') as f:
            f.write(self.welt.csv_bytes())
        with open(os.path.join(saat, 'README.md'), 'w') as f:
            f.write('Test-Repo\n')
        with open(os.path.join(saat, 'docs', 'alt.ics'), 'w') as f:
            f.write('x\n')
        git_ok(self.dir, 'init', '-q', '-b', 'main', saat)
        git_ok(saat, 'add', '-A')
        git_ok(saat, 'commit', '-q', '-m', 'Start')
        git_ok(saat, 'remote', 'add', 'origin', self.bare)
        git_ok(saat, 'push', '-q', 'origin', 'main')
        self.repo = os.path.join(self.dir, 'arbeit')
        self.andere = os.path.join(self.dir, 'andere')
        for ziel in (self.repo, self.andere):
            git_ok(self.dir, 'clone', '-q', self.bare, ziel)
        self.cache = os.path.join(self.dir, 'cache')
        self.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        self.welt.cache_schreiben(self.cache)
        self.csv = os.path.join(self.repo, 'spiele.csv')

    def optionen(self, **kw):
        kw.setdefault('heute', HEUTE)
        kw.setdefault('abrufen', False)
        return N.Optionen(csv=self.csv, cache=self.cache, notaus_datei=os.path.join(self.dir, 'AUS'), **kw)

    def lauf(self, **kw):
        return N.lauf(self.optionen(**kw), JETZT)

    def csv_lesen(self, ordner=None):
        with open(os.path.join(ordner or self.repo, 'spiele.csv'), 'rb') as f:
            return f.read()

    def remote_log(self):
        return git_ok(self.bare, 'log', '--format=%s', 'main').splitlines()

    def test_sauberer_baum_pull_rebase_commit_nur_csv_push(self):
        # origin ist weiter (die GitHub-Aktion hat docs/ neu gebaut): pull --rebase muss das erst holen
        with open(os.path.join(self.andere, 'docs', 'neu.ics'), 'w') as f:
            f.write('y\n')
        git_ok(self.andere, 'add', '-A')
        git_ok(self.andere, 'commit', '-q', '-m', 'Spielplan-Kalender neu gebaut (Lauf 1)')
        git_ok(self.andere, 'push', '-q', 'origin', 'main')
        vorher = self.csv_lesen()
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertTrue(e['gepusht'] and e['commit'])
        self.assertEqual(self.remote_log()[:2], ['Spieltabelle automatisch aktualisiert (1 Änderung)', 'Spielplan-Kalender neu gebaut (Lauf 1)'])
        self.assertEqual(git_ok(self.bare, 'show', '--format=%b', '--name-only', 'main').split('\n\n')[0].strip().splitlines()[:1], ['- ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1'])
        self.assertEqual(git_ok(self.bare, 'show', '--name-only', '--format=', 'main').splitlines(), ['spiele.csv'])
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')
        self.assertNotEqual(self.csv_lesen(), vorher)
        self.assertTrue(os.path.exists(os.path.join(self.repo, 'docs', 'neu.ics')), 'pull --rebase hat docs/ geholt')

    def test_commit_nachricht_mit_liste(self):
        self.welt.seite['D2'].append(Spiel('D2', LIGA['D2'], 41, '2026-11-29', '11:00', EIGEN['D2'], 'FC Neu 2'))
        self.welt.cache_schreiben(self.cache)
        self.lauf(anwenden=True, push=True)
        text = git_ok(self.bare, 'log', '-1', '--format=%B', 'main')
        self.assertEqual(text.splitlines()[0], 'Spieltabelle automatisch aktualisiert (2 Änderungen)')
        self.assertIn('- ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1', text)
        self.assertIn('- ergänzt: D2 So 29.11. 11:00 Heim gegen FC Neu 2', text)

    def anhaengen(self, name, daten):
        with open(os.path.join(self.repo, name), 'ab') as f:
            f.write(daten)

    def test_schmutziger_baum_bricht_ab_ohne_zu_schreiben(self):
        faelle = {'README geändert': lambda: self.anhaengen('README.md', b'x\n'),
                  'spiele.csv von Hand geändert': lambda: self.anhaengen('spiele.csv', b'HERREN;341727;999;01.12.2026;11:00;X;Y;;;;Meisterschaft\r\n'),
                  'nur vorgemerkt': lambda: (self.anhaengen('README.md', b'y\n'), git_ok(self.repo, 'add', 'README.md'))}
        for name, tun in faelle.items():
            with self.subTest(name):
                git_ok(self.repo, 'checkout', '-q', '--', '.')
                git_ok(self.repo, 'restore', '--staged', '.')
                tun()
                vorher = self.csv_lesen()
                kopf = git_ok(self.repo, 'rev-parse', 'HEAD')
                e = self.lauf(anwenden=True, push=True)
                self.assertEqual(self.csv_lesen(), vorher)
                self.assertEqual(git_ok(self.repo, 'rev-parse', 'HEAD'), kopf)
                self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Arbeitsbaum nicht sauber (%s) – nichts geschrieben' % (
                    'spiele.csv' if 'spiele.csv' in name else 'README.md')), e['schluss'])
                self.assertEqual(e['code'], 1)
                self.assertFalse(e['angewendet'])

    def test_unversionierte_dateien_blockieren_nicht(self):
        with open(os.path.join(self.repo, 'notizen.tmp'), 'w') as f:
            f.write('egal\n')
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertEqual(git_ok(self.bare, 'show', '--name-only', '--format=', 'main').splitlines(), ['spiele.csv'])

    def test_push_abgelehnt_commit_bleibt_lokal_naechster_lauf_pusht_nach(self):
        hook = os.path.join(self.bare, 'hooks', 'pre-receive')
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "gesperrt" >&2\nexit 1\n')
        os.chmod(hook, 0o755)
        e = self.lauf(anwenden=True, push=True)
        self.assertTrue(e['commit'] and not e['gepusht'])
        self.assertEqual(e['code'], 1)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Push fehlgeschlagen (remote: gesperrt | '), e['schluss'])
        self.assertNotIn(self.bare, e['schluss'], 'keine Adresse des Remotes in der Meldung')
        self.assertIn('der Commit bleibt lokal', e['schluss'])
        self.assertIn('1 geändert (1 ergänzt)', e['schluss'])
        self.assertEqual(self.remote_log(), ['Start'])
        self.assertEqual(git_ok(self.repo, 'log', '-1', '--format=%s'), 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        os.remove(hook)
        e2 = self.lauf(anwenden=True, push=True)
        self.assertEqual(e2['schluss'], 'NACHTRAG: 1 liegen gebliebener Commit nachgepusht')
        self.assertEqual(self.remote_log()[0], 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')

    def test_push_wegen_neuem_stand_abgelehnt_einmal_pull_rebase_und_erneut(self):
        echt = N.git_commit

        def dazwischen(*a, **k):
            r = echt(*a, **k)                  # nach unserem Commit schiebt die „GitHub-Aktion“ einen Commit nach origin
            with open(os.path.join(self.andere, 'docs', 'zwischen.ics'), 'w') as f:
                f.write('z\n')
            git_ok(self.andere, 'add', '-A')
            git_ok(self.andere, 'commit', '-q', '-m', 'Spielplan-Kalender neu gebaut (Lauf 2)')
            git_ok(self.andere, 'push', '-q', 'origin', 'main')
            return r
        with mock.patch.object(N, 'git_commit', side_effect=dazwischen):
            e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertEqual(self.remote_log()[:2], ['Spieltabelle automatisch aktualisiert (1 Änderung)', 'Spielplan-Kalender neu gebaut (Lauf 2)'])

    def test_fremde_lokale_commits_werden_nicht_gepusht(self):
        with open(os.path.join(self.repo, 'README.md'), 'a') as f:
            f.write('eigene Änderung\n')
        git_ok(self.repo, 'commit', '-q', '-am', 'README von Hand')
        vorher = self.csv_lesen()
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(self.csv_lesen(), vorher)
        self.assertEqual(self.remote_log(), ['Start'])
        self.assertIn('ändern mehr als spiele.csv', e['schluss'])

    def test_nicht_auf_main_bricht_ab(self):
        git_ok(self.repo, 'checkout', '-q', '-b', 'nebenzweig')
        vorher = self.csv_lesen()
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(self.csv_lesen(), vorher)
        self.assertIn('nicht auf main', e['schluss'])

    def test_rebase_konflikt_wird_abgebrochen_und_nichts_geschrieben(self):
        # lokal ein nicht gepushter csv-Commit, auf origin ändert jemand dieselbe Zeile -> Konflikt beim Rebase
        zeilen = zeilen_von(self.csv_lesen())
        i = next(i for i, z in enumerate(zeilen) if z.startswith('D1;') and ';11:00;' in z)
        lokal = list(zeilen)
        lokal[i] = lokal[i].replace(';11:00;', ';12:00;')
        with open(self.csv, 'wb') as f:
            f.write(('\r\n'.join(lokal) + '\r\n').encode('utf-8'))
        git_ok(self.repo, 'commit', '-q', '-am', N.COMMIT_ANFANG + ' (1 Änderung)')     # sieht aus wie ein Commit dieses Werkzeugs
        fern = list(zeilen)
        fern[i] = fern[i].replace(';11:00;', ';13:00;')
        with open(os.path.join(self.andere, 'spiele.csv'), 'wb') as f:
            f.write(('\r\n'.join(fern) + '\r\n').encode('utf-8'))
        git_ok(self.andere, 'commit', '-q', '-am', 'fern')
        git_ok(self.andere, 'push', '-q', 'origin', 'main')
        vorher = self.csv_lesen()
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(self.csv_lesen(), vorher)
        self.assertIn('pull --rebase fehlgeschlagen und abgebrochen', e['schluss'])
        self.assertFalse(os.path.exists(os.path.join(self.repo, '.git', 'rebase-merge')))
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')

    def test_commit_schlaegt_fehl_original_wiederhergestellt_nichts_vorgemerkt(self):
        vorher = self.csv_lesen()
        with mock.patch.object(N, 'git_commit', side_effect=lambda *a, **k: (git_ok(self.repo, 'add', '--', 'spiele.csv'), 'git commit fehlgeschlagen (Test)')[1]):
            e = self.lauf(anwenden=True, push=True)
        self.assertFalse(e['commit'])
        self.assertFalse(e['angewendet'])
        self.assertIn('git commit fehlgeschlagen (Test) – spiele.csv wiederhergestellt, nichts committet', e['schluss'])
        self.assertEqual(self.csv_lesen(), vorher)

    def test_pre_commit_hook_der_abbricht_stellt_original_wieder_her(self):
        hook = os.path.join(self.repo, '.git', 'hooks', 'pre-commit')
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\nexit 1\n')
        os.chmod(hook, 0o755)
        vorher = self.csv_lesen()
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(self.csv_lesen(), vorher)
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')
        self.assertIn('git commit fehlgeschlagen', e['schluss'])
        self.assertEqual(self.remote_log(), ['Start'])

    def test_berichtsmodus_und_anwenden_ohne_push_fassen_git_nicht_an(self):
        kopf = git_ok(self.repo, 'rev-parse', 'HEAD')
        with open(os.path.join(self.andere, 'docs', 'x.ics'), 'w') as f:       # origin ist weiter: ohne --push wird nicht gezogen
            f.write('x\n')
        git_ok(self.andere, 'add', '-A')
        git_ok(self.andere, 'commit', '-q', '-m', 'weiter')
        git_ok(self.andere, 'push', '-q', 'origin', 'main')
        vorher = self.csv_lesen()
        self.lauf()
        self.assertEqual((git_ok(self.repo, 'rev-parse', 'HEAD'), git_ok(self.repo, 'status', '--porcelain'), self.csv_lesen()), (kopf, '', vorher))
        e = self.lauf(anwenden=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertEqual(git_ok(self.repo, 'rev-parse', 'HEAD'), kopf)
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), 'M spiele.csv')
        self.assertEqual(self.remote_log()[:1], ['weiter'])

    def test_ohne_git_identitaet_nimmt_der_commit_die_ersatzidentitaet(self):
        with open(os.path.join(self.dir, 'gitconfig'), 'w') as f:
            f.write('[init]\n\tdefaultBranch = main\n[user]\n\tuseConfigOnly = true\n')
        with mock.patch.dict(os.environ):
            for k in ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'EMAIL'):
                os.environ.pop(k, None)
            e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertEqual(git_ok(self.bare, 'log', '-1', '--format=%an <%ae>', 'main'), '%s <%s>' % N.FALLBACK_AUTOR)


    # ---- Runde 3: liegen gebliebene Commits (B08), Fehlerzweige der Git-Vorbereitung, Commit nur mit spiele.csv, Sperre und Signale

    def datei_aendern(self, aendern):
        """spiele.csv im Arbeitsklon ändern: `aendern` bekommt die Zeilen (ohne Zeilenende) und ändert die Liste an Ort und Stelle."""
        zeilen = zeilen_von(self.csv_lesen())
        aendern(zeilen)
        with open(self.csv, 'wb') as f:
            f.write(('\r\n'.join(zeilen) + '\r\n').encode('utf-8'))

    @staticmethod
    def zeit_aendern(team, alt, neu):
        def tun(zeilen):
            i = next(i for i, z in enumerate(zeilen) if z.startswith(team + ';') and ';%s;' % alt in z)
            zeilen[i] = zeilen[i].replace(';%s;' % alt, ';%s;' % neu, 1)
        return tun

    def abgelehnt(self, betreff, aendern, text, auch_readme=False):
        """Ein lokaler, nicht gepushter Commit, den der Lauf nicht veröffentlichen darf: Abbruch mit Meldung, nichts gepusht, nichts geschrieben."""
        self.datei_aendern(aendern)
        if auch_readme:
            self.anhaengen('README.md', b'noch eine Zeile\n')
        git_ok(self.repo, 'commit', '-q', '-am', betreff)
        vorher, kopf = self.csv_lesen(), git_ok(self.repo, 'rev-parse', 'HEAD')
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(self.csv_lesen(), vorher)
        self.assertEqual(git_ok(self.repo, 'rev-parse', 'HEAD'), kopf)
        self.assertEqual(self.remote_log(), ['Start'])
        self.assertEqual(e['code'], 1)
        self.assertFalse(e['angewendet'] or e['gepusht'])
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Git: ') and text in e['schluss'], e['schluss'])
        return e

    def test_commit_mit_fremdem_betreff_wird_nicht_gepusht(self):                                 # AP-C G05a / B08
        self.abgelehnt('CSV von Hand', self.zeit_aendern('D1', '11:00', '12:00'),
                       'Git: lokale, nicht gepushte Commits stammen nicht von diesem Werkzeug („CSV von Hand“), nichts gepusht')

    def test_ein_fremder_commit_unter_mehreren_genuegt_zur_ablehnung(self):
        self.datei_aendern(self.zeit_aendern('D1', '11:00', '12:00'))
        git_ok(self.repo, 'commit', '-q', '-am', N.COMMIT_ANFANG + ' (1 Änderung)')
        self.datei_aendern(self.zeit_aendern('E1', '12:00', '12:30'))
        git_ok(self.repo, 'commit', '-q', '-am', 'Nebenbei')
        e = self.lauf(anwenden=True, push=True)
        self.assertIn('stammen nicht von diesem Werkzeug („Nebenbei“)', e['schluss'])
        self.assertEqual(self.remote_log(), ['Start'])

    def test_commit_des_werkzeugs_der_eine_zeile_loescht_wird_nicht_gepusht(self):                 # AP-C G05c / B08
        self.abgelehnt(N.COMMIT_ANFANG + ' (1 Änderung)', lambda z: z.pop(5), 'die liegen gebliebenen Commits bestehen die Prüfung nicht (1 Zeile fehlt (dieses Werkzeug löscht nie))')

    def test_commit_des_werkzeugs_mit_zerstoerter_kopfzeile_wird_nicht_gepusht(self):
        self.abgelehnt(N.COMMIT_ANFANG + ' (1 Änderung)', lambda z: z.__setitem__(0, z[0].replace('team;', 'mannschaft;')),
                       'die liegen gebliebenen Commits bestehen die Prüfung nicht (neue CSV hat unerwartete Form: Kopfzeile weicht ab')

    def test_commit_des_werkzeugs_mit_unlesbarem_datum_wird_nicht_gepusht(self):
        def kaputt(zeilen):
            i = next(i for i, z in enumerate(zeilen) if z.startswith('D1;') and ';01.11.2026;' in z)
            zeilen[i] = zeilen[i].replace(';01.11.2026;', ';99.99.2026;')
        self.abgelehnt(N.COMMIT_ANFANG + ' (1 Änderung)', kaputt, 'die liegen gebliebenen Commits bestehen die Prüfung nicht (CSV nicht mehr lesbar: Zeile ')

    def test_commit_des_werkzeugs_der_auch_andere_dateien_aendert_wird_nicht_gepusht(self):
        self.abgelehnt(N.COMMIT_ANFANG + ' (1 Änderung)', self.zeit_aendern('D1', '11:00', '12:00'),
                       'lokale, nicht gepushte Commits ändern mehr als spiele.csv (README.md), nicht angefasst', auch_readme=True)

    def test_commit_des_werkzeugs_mit_gueltigem_zuwachs_wird_nachgepusht_und_der_lauf_geht_weiter(self):
        zeile = 'D1;%s;77;29.11.2026;13:00;%s;FC Hand 1;%s;;;Meisterschaft' % (LIGA['D1'], EIGEN['D1'], HEIMORT['D1'])
        self.datei_aendern(lambda z: z.insert(next(i for i, x in enumerate(z) if x.startswith('D1;')), zeile))
        git_ok(self.repo, 'commit', '-q', '-am', N.COMMIT_ANFANG + ' (1 Änderung)')
        e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), gepusht; 1 liegen gebliebener Commit nachgepusht, 1 Hinweis')
        self.assertEqual(len(self.remote_log()), 3)
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')

    def test_liegen_gebliebener_commit_dessen_csv_git_nicht_liefert(self):
        self.datei_aendern(self.zeit_aendern('D1', '11:00', '12:00'))
        git_ok(self.repo, 'commit', '-q', '-am', N.COMMIT_ANFANG + ' (1 Änderung)')
        with mock.patch.object(N, 'git_bytes', return_value=(128, b'')):
            e = self.lauf(anwenden=True, push=True)
        self.assertEqual(e['schluss'], 'NACHTRAG: Fehler – Git: spiele.csv der liegen gebliebenen Commits nicht lesbar, nichts gepusht')
        self.assertEqual(self.remote_log(), ['Start'])

    def test_nachpushen_scheitert_dann_bricht_der_lauf_ab(self):
        hook = os.path.join(self.bare, 'hooks', 'pre-receive')
        with open(hook, 'w') as f:
            f.write('#!/bin/sh\necho "gesperrt" >&2\nexit 1\n')
        os.chmod(hook, 0o755)
        self.assertTrue(self.lauf(anwenden=True, push=True)['commit'])                 # der Commit bleibt lokal
        e = self.lauf(anwenden=True, push=True)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – liegen gebliebene Commits nicht gepusht: Push fehlgeschlagen (remote: gesperrt'), e['schluss'])
        self.assertEqual((e['code'], e['nachgepusht'], e['angewendet']), (1, 0, False))
        self.assertEqual(self.remote_log(), ['Start'])

    def test_push_wiederholung_mit_rebase_konflikt_wird_abgebrochen(self):                         # AP-C M55
        echt = N.git_commit

        def dazwischen(*a, **k):
            r = echt(*a, **k)                  # nach unserem Commit ändert jemand auf origin genau die Zeilen um unsere neue Zeile herum
            with open(os.path.join(self.andere, 'spiele.csv'), 'rb') as f:
                zeilen = f.read().decode('utf-8').split('\r\n')
            letzte = max(i for i, z in enumerate(zeilen) if z.startswith('D1;%s;' % LIGA['D1']))
            for i in (letzte, letzte + 1):
                f_ = zeilen[i].split(';')
                f_[4] = '23:%02d' % (i % 60)
                zeilen[i] = ';'.join(f_)
            with open(os.path.join(self.andere, 'spiele.csv'), 'wb') as f:
                f.write('\r\n'.join(zeilen).encode('utf-8'))
            git_ok(self.andere, 'commit', '-q', '-am', 'Handänderung auf origin')
            git_ok(self.andere, 'push', '-q', 'origin', 'main')
            return r
        with mock.patch.object(N, 'git_commit', side_effect=dazwischen):
            e = self.lauf(anwenden=True, push=True)
        self.assertTrue(e['commit'] and not e['gepusht'])
        self.assertEqual(e['code'], 1)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Push abgelehnt, pull --rebase scheiterte ('), e['schluss'])
        self.assertIn('der Commit bleibt lokal, der nächste Lauf pusht ihn nach', e['schluss'])
        for n in N.GIT_SPERREN:
            self.assertFalse(os.path.exists(os.path.join(self.repo, '.git', n)), n)
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(git_ok(self.repo, 'log', '-1', '--format=%s'), 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        self.assertEqual(self.remote_log()[0], 'Handänderung auf origin')

    def test_unerwartete_ausnahme_nach_dem_commit_laesst_den_commit_stehen(self):
        with mock.patch.object(N, 'git_push', side_effect=RuntimeError('boom')):
            e = self.lauf(anwenden=True, push=True)
        self.assertTrue(e['commit'] and not e['gepusht'])
        self.assertEqual(e['code'], 1)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – boom'), e['schluss'])
        self.assertEqual(git_ok(self.repo, 'log', '-1', '--format=%s'), 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        self.assertEqual(self.remote_log(), ['Start'])

    def git_mit_fehler(self, befehl):
        echt = N.git

        def falsch(repo, *args, **k):
            if args[0] == befehl:
                return 128, '', 'fatal: absichtlicher Fehler'
            return echt(repo, *args, **k)
        return mock.patch.object(N, 'git', side_effect=falsch)

    def test_git_vorbereiten_fehlerzweige(self):
        with self.subTest('kein Repo-Stamm'):
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: .*docs ist kein Repo-Stamm$'):
                N.git_vorbereiten(os.path.join(self.repo, 'docs'), 'spiele.csv')
        with self.subTest('losgelöster HEAD'):
            git_ok(self.repo, 'checkout', '-q', '--detach')
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: nicht auf main \(losgelöster HEAD\)$'):
                N.git_vorbereiten(self.repo, 'spiele.csv')
            git_ok(self.repo, 'checkout', '-q', 'main')
        with self.subTest('Rebase läuft'):
            sperre = os.path.join(self.repo, '.git', 'rebase-merge')
            os.makedirs(sperre)
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: Rebase oder Merge läuft noch$'):
                N.git_vorbereiten(self.repo, 'spiele.csv')
            os.rmdir(sperre)
        with self.subTest('Status nicht lesbar'), self.git_mit_fehler('status'):
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: Status nicht lesbar$'):
                N.git_vorbereiten(self.repo, 'spiele.csv')
        with self.subTest('origin/main nicht gefunden'), self.git_mit_fehler('rev-list'):
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: origin/main nicht gefunden$'):
                N.git_vorbereiten(self.repo, 'spiele.csv')
        with self.subTest('fetch scheitert'), self.git_mit_fehler('fetch'):
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: fetch fehlgeschlagen \(fatal: absichtlicher Fehler\)$'):
                N.git_vorbereiten(self.repo, 'spiele.csv')
        with self.subTest('kein origin'):
            git_ok(self.repo, 'remote', 'remove', 'origin')
            with self.assertRaisesRegex(N.LaufFehler, r'^Git: fetch fehlgeschlagen \('):
                N.git_vorbereiten(self.repo, 'spiele.csv')

    def test_git_hilfen_bei_zeitueberschreitung_und_fehlern(self):
        with mock.patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired(['git'], 1)):
            self.assertEqual(N.git(self.repo, 'status'), (124, '', 'Zeitüberschreitung bei git status'))
        with mock.patch.object(subprocess, 'run', side_effect=OSError('kaputt')):
            self.assertEqual(N.git(self.repo, 'status'), (99, '', 'OSError: kaputt'))
            self.assertEqual(N.git_bytes(self.repo, 'show', 'HEAD:spiele.csv'), (99, b''))
        self.assertEqual(N.git_bytes(self.repo, 'show', 'HEAD:spiele.csv'), (0, self.csv_lesen()))
        self.assertEqual(N.git_bytes(self.repo, 'show', 'HEAD:gibt-es-nicht')[0], 128)

    def test_der_commit_enthaelt_nur_die_csv_auch_wenn_mehr_vorgemerkt_ist(self):                # AP-C M31
        self.anhaengen('README.md', b'vorgemerkt\n')
        git_ok(self.repo, 'add', 'README.md')
        self.anhaengen('spiele.csv', b'HERREN;341727;999;01.12.2026;11:00;X;Y;;;;Meisterschaft\r\n')
        kopf = git_ok(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(N.git_commit(self.repo, 'spiele.csv', 'Betreff', 'Text'), 'vorgemerkt wäre mehr als spiele.csv (README.md, spiele.csv)')
        self.assertEqual(git_ok(self.repo, 'rev-parse', 'HEAD'), kopf, 'kein Commit')
        self.assertEqual(git_ok(self.repo, 'diff', '--cached', '--name-only'), 'README.md', 'die csv-Vormerkung ist zurückgenommen')
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain').splitlines(), ['M  README.md', ' M spiele.csv'])

    def test_git_commit_ohne_vormerkbare_datei(self):
        self.assertTrue(N.git_commit(self.repo, 'gibt-es-nicht.csv', 'Betreff', 'Text').startswith('git add fehlgeschlagen ('))

    @unittest.skipIf(fcntl is None, 'flock gibt es nur unter Unix/macOS')
    def test_sperrkonflikt_laesst_csv_und_git_unberuehrt(self):                                    # AP-C G11 / B01
        halter = sperre_halten(os.path.join(self.dir, N.SPERRDATEI))
        self.addCleanup(halter.close)
        vorher, kopf = self.csv_lesen(), git_ok(self.repo, 'rev-parse', 'HEAD')
        with mock.patch.object(N, 'git', side_effect=AssertionError('Git angefasst')):
            e = self.lauf(anwenden=True, push=True)
        self.assertEqual((e['schluss'], e['code']), ('NACHTRAG: Fehler – anderer Lauf aktiv', 1))
        self.assertEqual((self.csv_lesen(), git_ok(self.repo, 'rev-parse', 'HEAD'), git_ok(self.repo, 'status', '--porcelain')), (vorher, kopf, ''))
        self.assertEqual(self.remote_log(), ['Start'])

    def argv(self, *extra):
        return ['--csv', self.csv, '--cache', self.cache, '--heute', HEUTE, '--max-alter', '0', '--notaus-datei', os.path.join(self.dir, 'AUS'),
                '--bericht-ordner', os.path.join(self.dir, 'berichte'), '--anwenden', '--push'] + list(extra)

    def test_sigterm_nach_dem_commit_der_commit_bleibt_lokal_der_naechste_lauf_pusht_nach(self):     # AP-C C12 / B10
        fremd = Fremdlauf(self, self.dir, 'git_push', self.argv())
        fremd.signal(signal.SIGTERM)
        code, out, err = fremd.ergebnis()
        self.assertEqual(code, 1, err)
        self.assertEqual(out.splitlines()[-1], 'NACHTRAG: Fehler – abgebrochen (SIGTERM); der Commit bleibt lokal, der nächste Lauf pusht ihn nach')
        self.assertEqual(git_ok(self.repo, 'log', '-1', '--format=%s'), 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        self.assertEqual(self.remote_log(), ['Start'])
        self.assertEqual(git_ok(self.repo, 'status', '--porcelain'), '')
        e2 = self.lauf(anwenden=True, push=True)
        self.assertEqual(e2['schluss'], 'NACHTRAG: 1 liegen gebliebener Commit nachgepusht')
        self.assertEqual(self.remote_log()[0], 'Spieltabelle automatisch aktualisiert (1 Änderung)')

    def test_sigterm_nach_dem_commit_im_selben_prozess(self):
        signal_sicher(self, signal.SIGTERM)

        def mit_signal(repo):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.5)                                                  # der Handler wirft hier den Abbruch
        with mock.patch.object(N, 'git_push', side_effect=mit_signal):
            code, zeilen, err = main_aufrufen(self.argv())
        self.assertEqual((code, zeilen[-1]), (1, 'NACHTRAG: Fehler – abgebrochen (SIGTERM); der Commit bleibt lokal, der nächste Lauf pusht ihn nach'))
        self.assertEqual(git_ok(self.repo, 'log', '-1', '--format=%s'), 'Spieltabelle automatisch aktualisiert (1 Änderung)')
        self.assertEqual(self.remote_log(), ['Start'])

    def test_sigterm_nach_dem_schreiben_vor_dem_commit_stellt_das_original_wieder_her_und_der_baum_bleibt_sauber(self):
        vorher = self.csv_lesen()
        fremd = Fremdlauf(self, self.dir, 'gegenlesen', self.argv())
        fremd.signal(signal.SIGTERM)
        code, out, err = fremd.ergebnis()
        self.assertEqual(code, 1, err)
        self.assertEqual(out.splitlines()[-1], 'NACHTRAG: Fehler – abgebrochen (SIGTERM); spiele.csv wiederhergestellt, nichts committet')
        self.assertEqual((self.csv_lesen(), git_ok(self.repo, 'status', '--porcelain')), (vorher, ''))
        self.assertEqual(self.remote_log(), ['Start'])


# ---------------------------------------------------------------- Berichtsformat, Kommandozeile, Quelltext

class TestFormat(TestBasis):
    def test_unveraendert_genau_diese_zeile(self):
        w = self.werkstatt()
        for kw in ({}, {'anwenden': True}):
            e = w.lauf(**kw)
            self.assertEqual((e['zeilen'], e['schluss'], e['code']), ([], 'NACHTRAG: unverändert', 0))

    def test_kommandozeile_gibt_nur_die_schlusszeile_aus_und_ruft_nichts_ab(self):
        w = self.werkstatt()
        out = io.StringIO()
        argv = ['--csv', w.csv, '--cache', w.cache, '--heute', HEUTE, '--max-alter', '0', '--notaus-datei', w.notaus]
        with contextlib.redirect_stdout(out), mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('Abruf')):
            code = N.main(argv)
        self.assertEqual((out.getvalue(), code), ('NACHTRAG: unverändert\n', 0))

    def test_kommandozeile_unter_python_i_mit_schlusszeile_und_exitcode(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        vorher = w.csv_lesen()
        r = subprocess.run([sys.executable, '-I', '-B', os.path.join(HIER, 'nachtrag.py'), '--csv', w.csv, '--cache', w.cache, '--heute', HEUTE,
                            '--max-alter', '0', '--notaus-datei', w.notaus], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        zeilen = r.stdout.splitlines()
        self.assertEqual(zeilen[0], 'Vorschlag ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1')
        self.assertEqual(zeilen[-1], 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')
        self.assertEqual(w.csv_lesen(), vorher)
        self.assertEqual(r.stderr, '')

    def test_fehler_zeile_bei_unerwarteter_ausnahme(self):
        w = self.werkstatt()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), mock.patch.object(N, 'lauf', side_effect=RuntimeError('kaputt')):
            code = N.main(['--csv', w.csv, '--cache', w.cache])
        self.assertEqual((out.getvalue(), code), ('NACHTRAG: Fehler – unerwartet (RuntimeError: kaputt)\n', 1))

    def test_berichtsdatei_nur_bei_etwas_zu_berichten(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        argv = ['--csv', w.csv, '--cache', w.cache, '--heute', HEUTE, '--max-alter', '0', '--notaus-datei', w.notaus, '--bericht-ordner', w.berichte]
        with contextlib.redirect_stdout(io.StringIO()):
            N.main(argv + ['--anwenden'])
            N.main(argv + ['--anwenden'])     # zweiter Lauf: unverändert, keine zweite Datei
        dateien = os.listdir(w.berichte)
        self.assertEqual(len(dateien), 1)
        with open(os.path.join(w.berichte, dateien[0]), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('`NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht`', text)
        self.assertIn('- ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1', text)

    def test_ausgabe_hat_keine_personennamen_und_keine_telefonnummern(self):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], 'FC Neu 1'))
        w.cache_neu()
        e = w.lauf()
        text = '\n'.join(e['zeilen'] + [e['schluss']])
        import re
        self.assertIsNone(re.search(r'\+?\d[\d /-]{8,}\d', text.replace(HEIMORT['D1'], '')))

    def test_quelltext_ohne_gewalt_befehle(self):
        with open(os.path.join(HIER, 'nachtrag.py'), encoding='utf-8') as f:
            quelle = f.read()
        for verboten in ('--for' + 'ce', 'reset --' + 'hard', "'-" + "f'", 'push -' + 'f', "'--delete'", "'rm'", 'os.remove(opt', 'rmtree'):
            self.assertNotIn(verboten, quelle, verboten)
        self.assertNotIn('requests', quelle)
        self.assertNotIn('urlopen', quelle, 'Abrufe laufen ausschließlich über abgleich.hole')

    def test_abgleich_wird_nicht_dauerhaft_veraendert(self):
        vorher = (A.MIN_ABSTAND_TAGE, A.USER_AGENT, A.URL)
        self.test_ausgabe_hat_keine_personennamen_und_keine_telefonnummern()
        self.assertEqual((A.MIN_ABSTAND_TAGE, A.USER_AGENT, A.URL), vorher)

    @unittest.skipUnless(os.path.exists(os.path.join(REPO, 'README.md')), 'README.md fehlt (Arbeitsordner ohne Repo-Stamm, z. B. Mutationstest)')
    def test_readme_nennt_den_sauberen_arbeitsbaum(self):                                       # AP-C B06
        with open(os.path.join(REPO, 'README.md'), encoding='utf-8') as f:
            text = f.read()
        abschnitt = ' '.join(text.split('## Automatische Aktualisierung', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('Der Arbeitsbaum dieses Repos muss dafür sauber bleiben (keine lokalen Änderungen, z. B. nach einem lokalen `build_ics.py`), '
                      'sonst meldet der Nachtrag „Arbeitsbaum nicht sauber“ und schreibt nichts.', abschnitt)


# ---------------------------------------------------------------- Runde 3: gemeinsame Hilfen

def neues_spiel(w, team='D1', nr=40, datum='2026-11-29', zeit='13:00', heim=None, gast='FC Neu 1', **kw):
    """Ein Spiel, das nur bei fussball.de steht (die CSV kennt es nicht), und den Cache neu schreiben."""
    w.welt.seite[team].append(Spiel(team, kw.pop('staffel', LIGA[team]), nr, datum, zeit, heim or EIGEN[team], gast, **kw))
    w.cache_neu()


def csv_schreiben(w):
    with open(w.csv, 'wb') as f:
        f.write(w.welt.csv_bytes())


def csv_aendern(w, team, nr, **felder):
    """Felder einer CSV-Zeile ändern (die Seite bleibt, wie sie ist)."""
    s = w.welt.spiel(w.welt.csv, team, nr)
    for k, v in felder.items():
        setattr(s, k, v)
    csv_schreiben(w)


def argv_fuer(w, *extra):
    """Kommandozeile für einen Lauf in der Werkstatt: immer mit Berichtsordner im Wegwerf-Ordner (nie ins echte Verzeichnis) und ohne
    Altersgrenze (die Cache-Seiten sind auf die feste Testzeit gestempelt, N.main nimmt aber die echte Uhr)."""
    return ['--csv', w.csv, '--cache', w.cache, '--heute', HEUTE, '--max-alter', '0', '--notaus-datei', w.notaus,
            '--bericht-ordner', w.berichte] + list(extra)


def main_aufrufen(argv):
    """N.main mit abgefangener Ausgabe -> (Exit-Code, Zeilen von stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = N.main(argv)
    return code, out.getvalue().splitlines(), err.getvalue()


def signal_sicher(test, sig):
    """Sicherheitsnetz für Tests, die sich selbst ein Signal schicken: Ohne den Handler von N.main würde SIGTERM den ganzen Testlauf beenden
    (so beim alten Stand und bei einer Mutante ohne Signalbehandlung). N.main ersetzt diesen Handler und stellt ihn am Ende wieder her."""
    if threading.current_thread() is not threading.main_thread():
        test.skipTest('Signalhandler gibt es nur im Hauptthread')
    alt = signal.signal(sig, lambda signum, frame: None)
    test.addCleanup(signal.signal, sig, alt)


def sperre_halten(pfad, pid='424242'):
    """Ein „anderer Lauf“ im Test: hält flock auf der Sperrdatei, bis das zurückgegebene Dateiobjekt geschlossen wird."""
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    f = open(pfad, 'a+')
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.seek(0)
    f.truncate()
    f.write(pid + '\n')
    f.flush()
    return f


def sperre_frei(pfad):
    """True, wenn niemand die Sperrdatei hält (flock ließe sich jetzt nehmen)."""
    with open(pfad, 'a+') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(f, fcntl.LOCK_UN)
        return True


WRAPPER = '''\
import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, __HIER__)
import nachtrag as N

BEREIT, WEITER, ZIEL = __BEREIT__, __WEITER__, __ZIEL__
ECHT = getattr(N, ZIEL)


def halten(*a, **k):
    """Beim ersten Aufruf anhalten (die Datei BEREIT zeigt: der Lauf steht hier), bis WEITER da ist."""
    if not os.path.exists(BEREIT):
        open(BEREIT, 'w').close()
        ende = time.time() + 90
        while not os.path.exists(WEITER) and time.time() < ende:
            time.sleep(0.02)
    return ECHT(*a, **k)


setattr(N, ZIEL, halten)
sys.exit(N.main(__ARGV__))
'''


class Fremdlauf:
    """Ein zweiter Prozess mit dem echten Skript (N.main), der vor einer Funktion von nachtrag stehen bleibt: `ziel` ist
    validiere (vor dem Schreiben), gegenlesen (nach dem Schreiben, vor dem Commit) oder git_push (nach dem Commit)."""

    def __init__(self, test, ordner, ziel, argv):
        self.bereit, self.weiter = os.path.join(ordner, 'bereit-' + ziel), os.path.join(ordner, 'weiter-' + ziel)
        pfad = os.path.join(ordner, 'wrapper-%s.py' % ziel)
        text = WRAPPER
        for marke, wert in (('__HIER__', HIER), ('__BEREIT__', self.bereit), ('__WEITER__', self.weiter), ('__ZIEL__', ziel), ('__ARGV__', argv)):
            text = text.replace(marke, repr(wert))
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(text)
        self._ergebnis = None
        self.p = subprocess.Popen([sys.executable, '-I', '-B', pfad], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        test.addCleanup(self.aufraeumen)
        ende = time.time() + 90
        while not os.path.exists(self.bereit):
            if self.p.poll() is not None or time.time() > ende:
                code, out, err = self.aufraeumen()
                test.fail('der Fremdlauf kam nicht bis %s (Code %s): %s %s' % (ziel, code, out[-300:], err[-300:]))
            time.sleep(0.02)

    def signal(self, sig):
        self.p.send_signal(sig)

    def weitermachen(self):
        open(self.weiter, 'w').close()

    def ergebnis(self, timeout=120):
        """-> (Exit-Code, stdout, stderr) nach dem Ende des Prozesses."""
        if self._ergebnis is None:
            out, err = self.p.communicate(timeout=timeout)
            self._ergebnis = (self.p.returncode, out, err)
        return self._ergebnis

    def aufraeumen(self):
        if self.p.poll() is None:
            self.p.kill()
        return self.ergebnis(30)


# ---------------------------------------------------------------- B01: Sperre gegen gleichzeitige Läufe

@unittest.skipIf(fcntl is None, 'flock gibt es nur unter Unix/macOS')
class TestSperre(TestBasis):
    def mit_neuem_spiel(self):
        w = self.werkstatt()
        neues_spiel(w)
        return w

    def pfad(self, w):
        return os.path.join(w.dir, N.SPERRDATEI)             # neben der Not-Aus-Datei, nicht im Repo

    def halten(self, w, pid='424242'):
        f = sperre_halten(self.pfad(w), pid)
        self.addCleanup(f.close)
        return f

    def test_zweiter_lauf_bricht_sofort_ab_und_beruehrt_weder_csv_noch_git(self):
        w = self.mit_neuem_spiel()
        self.halten(w)
        vorher = w.csv_lesen()
        with mock.patch.object(N, 'git', side_effect=AssertionError('Git angefasst')), \
                mock.patch.object(N, 'validiere', side_effect=AssertionError('nichts geplant, nichts geprüft')):
            e = w.anwenden(push=True)
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: Fehler – anderer Lauf aktiv')
        self.assertEqual(e['code'], 1)
        self.assertFalse(e['angewendet'])
        self.assertEqual(e['aenderungen'], [])
        self.assertEqual(e['zeilen'], ['Fehler: anderer Lauf aktiv (PID 424242, Sperrdatei %s), nichts getan' % self.pfad(w)])

    def test_sperre_liegt_ausserhalb_des_repos_wird_gehalten_und_danach_freigegeben(self):
        w = self.mit_neuem_spiel()
        waehrend = []
        echt = N.validiere

        def spaeher(*a, **k):
            waehrend.append(sperre_frei(self.pfad(w)))
            return echt(*a, **k)
        with mock.patch.object(N, 'validiere', side_effect=spaeher):
            e = w.anwenden()
        self.assertTrue(e['angewendet'])
        self.assertEqual(waehrend, [False], 'während des Laufs ist die Sperre belegt')
        self.assertTrue(sperre_frei(self.pfad(w)), 'nach dem Lauf ist sie frei')
        self.assertNotIn(N.SPERRDATEI, os.listdir(w.repo))
        self.assertFalse(N.DATEN_ORDNER.startswith(REPO + os.sep), 'auch der Standardort liegt nicht im Repo')

    def test_sperre_wird_auch_nach_einer_ausnahme_freigegeben(self):
        w = self.mit_neuem_spiel()
        with mock.patch.object(N, '_lauf', side_effect=RuntimeError('kaputt')), self.assertRaises(RuntimeError):
            w.anwenden()
        self.assertTrue(sperre_frei(self.pfad(w)))

    def test_nur_bericht_und_not_aus_brauchen_keine_sperre(self):
        w = self.mit_neuem_spiel()
        self.halten(w)
        b = w.lauf()
        self.assertEqual(b['schluss'], 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')
        open(w.notaus, 'w').close()
        n = w.anwenden()
        self.assertTrue(n['schluss'].startswith('NACHTRAG: Auffällig, nicht übernommen – Not-Aus aktiv'), n['schluss'])
        w2 = self.mit_neuem_spiel()
        w2.lauf()
        self.assertFalse(os.path.exists(self.pfad(w2)), 'der Berichtsmodus legt keine Sperrdatei an')

    def test_sperrdatei_ueber_die_option_waehlbar(self):
        w = self.mit_neuem_spiel()
        eigene = os.path.join(w.dir, 'unterordner', 'meine.lock')
        e = w.anwenden(sperrdatei=eigene)
        self.assertTrue(e['angewendet'])
        self.assertTrue(os.path.exists(eigene))
        self.assertFalse(os.path.exists(self.pfad(w)))

    def test_sperrdatei_nicht_anlegbar_ist_ein_fehler_ohne_schreiben(self):
        w = self.mit_neuem_spiel()
        vorher = w.csv_lesen()
        e = w.anwenden(sperrdatei=os.path.join(w.csv, 'x.lock'))         # der „Ordner“ ist die CSV-Datei
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Lauf-Sperre nicht möglich ('), e['schluss'])
        self.assertEqual(e['code'], 1)

    def test_flock_fehler_ist_ein_fehler_ohne_schreiben(self):
        w = self.mit_neuem_spiel()
        vorher = w.csv_lesen()
        with mock.patch.object(fcntl, 'flock', side_effect=OSError(errno.ENOLCK, 'keine Sperren verfügbar')):
            e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – Lauf-Sperre nicht möglich (OSError'), e['schluss'])

    def test_sperrdatei_mit_muell_oder_beendeter_pid_blockiert_nicht(self):
        w = self.mit_neuem_spiel()
        with open(self.pfad(w), 'w') as f:
            f.write('kein PID-Text\n')
        e = w.anwenden()
        self.assertTrue(e['angewendet'])
        self.assertEqual([s for s in e['stumm'] if 'Sperrdatei' in s], [])
        w3 = self.mit_neuem_spiel()
        with open(self.pfad(w3), 'w') as f:
            f.write('9' * 30 + '\n')                         # keine mögliche PID
        e3 = w3.anwenden()
        self.assertTrue(e3['angewendet'], e3['schluss'])
        w2 = self.mit_neuem_spiel()
        beendet = subprocess.Popen([sys.executable, '-I', '-c', 'pass'])
        beendet.wait(timeout=60)
        if N.pid_lebt(beendet.pid):
            self.skipTest('die PID des beendeten Prozesses wurde schon neu vergeben')
        with open(self.pfad(w2), 'w') as f:
            f.write('%d\n' % beendet.pid)
        e2 = w2.anwenden()
        self.assertTrue(e2['angewendet'])
        self.assertIn('Sperrdatei eines beendeten Laufs (PID %d) übernommen' % beendet.pid, e2['stumm'])

    def test_haelt_ein_prozess_die_sperre_ist_sie_belegt_nach_kill_9_wieder_frei(self):
        halter = ('import fcntl, os, sys, time\n'
                  'f = open(sys.argv[1], "a+")\n'
                  'fcntl.flock(f, fcntl.LOCK_EX)\n'
                  'f.seek(0); f.truncate(); f.write("%d\\n" % os.getpid()); f.flush()\n'
                  'print("bereit", flush=True)\n'
                  'time.sleep(120)\n')
        w = self.mit_neuem_spiel()
        p = subprocess.Popen([sys.executable, '-I', '-B', '-c', halter, self.pfad(w)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(lambda: (p.kill(), p.wait()) if p.poll() is None else p.wait())
        self.assertEqual(p.stdout.readline().strip(), 'bereit')
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: Fehler – anderer Lauf aktiv')
        self.assertIn('PID %d,' % p.pid, e['zeilen'][0])
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=60)
        e2 = w.anwenden()
        self.assertEqual(e2['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertIn('Sperrdatei eines beendeten Laufs (PID %d) übernommen' % p.pid, e2['stumm'])

    def test_wiederherstellung_nur_durch_den_besitzer_der_sperre(self):
        for wie in ('geloescht', 'ersetzt'):
            with self.subTest(wie):
                w = self.mit_neuem_spiel()
                vorher = w.csv_lesen()
                fremde = []

                def verloren(pfad, erwartet):
                    os.remove(self.pfad(w))                          # ein anderer Lauf hat die Sperrdatei ersetzt ...
                    if wie == 'ersetzt':
                        fremde.append(sperre_halten(self.pfad(w), '777'))
                        self.addCleanup(fremde[-1].close)            # ... und hält jetzt eine neue
                    raise N.LaufFehler('absichtlicher Fehler nach dem Schreiben')
                with mock.patch.object(N, 'gegenlesen', side_effect=verloren):
                    e = w.anwenden()
                self.assertNotEqual(w.csv_lesen(), vorher, 'das Original darf nur der Besitzer der Sperre zurückschreiben')
                self.assertEqual(e['code'], 1)
                self.assertIn('absichtlicher Fehler nach dem Schreiben – Lauf-Sperre verloren, spiele.csv NICHT zurückgeschrieben, bitte prüfen!', e['schluss'])
                self.assertFalse(e['schreibstand']['wiederhergestellt'])

    def test_ohne_gueltige_sperre_wird_nichts_geschrieben(self):
        w = self.mit_neuem_spiel()
        vorher = w.csv_lesen()
        datei = N.CsvDatei(vorher)
        ungeholt = N.Sperre(self.pfad(w))
        for name, sperre in (('keine Sperre', None), ('nie geholt', ungeholt)):
            with self.subTest(name):
                res = {}
                N.schreibe_und_veroeffentliche(w.optionen(), datei, b'ganz anderer Inhalt', [], False, w.repo, 'spiele.csv', sperre, res)
                self.pruefe_unveraendert(w, vorher)
                self.assertFalse(res['angewendet'])
                self.assertIn('Lauf-Sperre gehört diesem Lauf nicht', res['fehler'])

    def test_sperre_einzeln(self):
        with tempfile.TemporaryDirectory(prefix='nachtrag-sperre-') as ordner:
            pfad = os.path.join(ordner, 'tief', 'lauf.lock')
            a = N.Sperre(pfad)
            self.assertFalse(a.gueltig(), 'vor dem Holen gehört sie niemandem')
            self.assertTrue(a.holen())
            self.assertTrue(a.gueltig())
            b = N.Sperre(pfad)
            self.assertFalse(b.holen())
            self.assertEqual(b.besitzer, str(os.getpid()))
            self.assertFalse(b.gueltig())
            a.freigeben()
            self.assertFalse(a.gueltig())
            self.assertTrue(b.holen())
            b.freigeben()
            for wie in ('geloescht', 'ersetzt'):
                c = N.Sperre(pfad)
                self.assertTrue(c.holen())
                if wie == 'geloescht':
                    os.remove(pfad)
                else:
                    os.rename(pfad, pfad + '.alt')
                    with open(pfad, 'w') as f:
                        f.write('neu\n')
                self.assertFalse(c.gueltig(), wie)
                c.freigeben()
            with mock.patch.object(N, 'fcntl', None):                  # Plattform ohne flock: kein Schutz, aber auch kein Fehler
                d = N.Sperre(pfad)
                self.assertTrue(d.holen() and d.gueltig())
                d.freigeben()

    def test_pid_lebt(self):
        self.assertTrue(N.pid_lebt(os.getpid()))
        with mock.patch.object(os, 'kill', side_effect=PermissionError()):
            self.assertTrue(N.pid_lebt(1), 'fremder, aber lebender Prozess')
        with mock.patch.object(os, 'kill', side_effect=ProcessLookupError()):
            self.assertFalse(N.pid_lebt(4194000))
        self.assertFalse(N.pid_lebt(10 ** 30), 'eine unsinnige PID aus einer beschädigten Sperrdatei')
        beendet = subprocess.Popen([sys.executable, '-I', '-c', 'pass'])
        beendet.wait(timeout=60)
        if N.pid_lebt(beendet.pid):
            self.skipTest('die PID des beendeten Prozesses wurde schon neu vergeben')
        self.assertFalse(N.pid_lebt(beendet.pid))

    def test_zwei_echte_prozesse_der_zweite_wird_abgewiesen_der_erste_schreibt_genau_einmal(self):
        w = self.mit_neuem_spiel()
        argv = argv_fuer(w, '--anwenden')
        erster = Fremdlauf(self, w.dir, 'validiere', argv)           # steht in der Prüfung, die Sperre ist gehalten
        vorher = w.csv_lesen()
        zweiter = subprocess.run([sys.executable, '-I', '-B', os.path.join(HIER, 'nachtrag.py')] + argv, capture_output=True, text=True, timeout=120)
        self.assertEqual(zweiter.returncode, 1, zweiter.stderr)
        self.assertEqual(zweiter.stdout.splitlines()[-1], 'NACHTRAG: Fehler – anderer Lauf aktiv')
        self.pruefe_unveraendert(w, vorher)
        erster.weitermachen()
        code, out, err = erster.ergebnis()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[-1], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertEqual(len([z for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;%s;40;' % LIGA['D1'])]), 1)
        self.assertTrue(sperre_frei(self.pfad(w)))


# ---------------------------------------------------------------- B10: ungültiger Aufruf und Signale haben eine Schlusszeile

class TestAbbruch(TestBasis):
    def test_ungueltiger_aufruf_hat_schlusszeile_und_exit_1(self):
        for argv, text in ((['--push'], '--push gibt es nur zusammen mit --anwenden'), (['--heute', 'morgen'], '--heute erwartet JJJJ-MM-TT'),
                           (['--gibt-es-nicht'], '--gibt-es-nicht'), (['--max-alter', 'viel'], 'viel')):
            with self.subTest(argv):
                code, zeilen, err = main_aufrufen(argv)
                self.assertEqual(code, 1)
                self.assertEqual(len(zeilen), 1, zeilen)
                self.assertTrue(zeilen[0].startswith('NACHTRAG: Fehler – ungültiger Aufruf ('), zeilen)
                self.assertIn(text, zeilen[0])
                self.assertIn('usage:', err)

    def test_hilfe_bleibt_ein_normaler_exit_0(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as ctx:
            N.main(['--help'])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn('--anwenden', out.getvalue())

    def test_ungueltiger_aufruf_unter_python_i(self):
        r = subprocess.run([sys.executable, '-I', '-B', os.path.join(HIER, 'nachtrag.py'), '--push'], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.splitlines(), ['NACHTRAG: Fehler – ungültiger Aufruf (--push gibt es nur zusammen mit --anwenden)'])
        self.assertIn('usage:', r.stderr)

    def argv(self, w):
        return argv_fuer(w, '--anwenden')

    def test_sigterm_und_sigint_vor_dem_schreiben(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig.name):
                w = self.werkstatt()
                neues_spiel(w)
                vorher = w.csv_lesen()
                fremd = Fremdlauf(self, w.dir, 'validiere', self.argv(w))
                fremd.signal(sig)
                code, out, err = fremd.ergebnis()
                self.assertEqual(code, 1, err)
                self.assertEqual(out.splitlines()[-1], 'NACHTRAG: Fehler – abgebrochen (%s); nichts geschrieben' % sig.name)
                self.pruefe_unveraendert(w, vorher)
                if fcntl:
                    self.assertTrue(sperre_frei(os.path.join(w.dir, N.SPERRDATEI)))

    def test_sigterm_nach_dem_schreiben_stellt_das_original_wieder_her(self):
        w = self.werkstatt()
        neues_spiel(w)
        vorher = w.csv_lesen()
        fremd = Fremdlauf(self, w.dir, 'gegenlesen', self.argv(w))
        self.assertNotEqual(w.csv_lesen(), vorher, 'die Datei ist schon geschrieben, der Lauf steht vor dem Gegenlesen')
        fremd.signal(signal.SIGTERM)
        code, out, err = fremd.ergebnis()
        self.assertEqual(code, 1, err)
        self.assertEqual(out.splitlines()[-1], 'NACHTRAG: Fehler – abgebrochen (SIGTERM); spiele.csv wiederhergestellt, nichts committet')
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual([n for n in os.listdir(w.repo) if n.endswith('.tmp')], [])

    def signal_im_selben_prozess(self, ziel, sig, nach=None):
        """Wie die Unterprozess-Tests, aber ohne Unterprozess: die ersetzte Funktion `ziel` schickt sich beim ersten Aufruf das Signal
        selbst (der Handler von N.main läuft dann mitten im Lauf). `nach`: Ersatz für die Aufrufe danach (Standard: die echte Funktion)."""
        signal_sicher(self, sig)
        echt, aufrufe = getattr(N, ziel), []

        def mit_signal(*a, **k):
            aufrufe.append(1)
            if len(aufrufe) == 1:
                os.kill(os.getpid(), sig)
                time.sleep(0.5)                    # der Handler wirft hier den Abbruch
            return (nach or echt)(*a, **k)
        return mock.patch.object(N, ziel, side_effect=mit_signal)

    def test_signale_im_selben_prozess_vor_und_nach_dem_schreiben(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest('vor dem Schreiben ' + sig.name):
                w = self.werkstatt()
                neues_spiel(w)
                vorher = w.csv_lesen()
                with self.signal_im_selben_prozess('validiere', sig):
                    code, zeilen, err = main_aufrufen(argv_fuer(w, '--anwenden'))
                self.assertEqual((code, zeilen[-1]), (1, 'NACHTRAG: Fehler – abgebrochen (%s); nichts geschrieben' % sig.name))
                self.pruefe_unveraendert(w, vorher)
            with self.subTest('nach dem Schreiben ' + sig.name):
                w = self.werkstatt()
                neues_spiel(w)
                vorher = w.csv_lesen()
                with self.signal_im_selben_prozess('gegenlesen', sig):
                    code, zeilen, err = main_aufrufen(argv_fuer(w, '--anwenden'))
                self.assertEqual((code, zeilen[-1]), (1, 'NACHTRAG: Fehler – abgebrochen (%s); spiele.csv wiederhergestellt, nichts committet' % sig.name))
                self.pruefe_unveraendert(w, vorher)

    def test_signal_und_dann_scheitert_auch_die_wiederherstellung(self):
        w = self.werkstatt()
        neues_spiel(w)

        def platte_kaputt(*a, **k):
            raise N.LaufFehler('Platte')
        with self.signal_im_selben_prozess('gegenlesen', signal.SIGTERM, nach=platte_kaputt):
            code, zeilen, err = main_aufrufen(argv_fuer(w, '--anwenden'))
        self.assertEqual((code, zeilen[-1]), (1, 'NACHTRAG: Fehler – abgebrochen (SIGTERM); Wiederherstellung fehlgeschlagen (Platte) – spiele.csv sofort prüfen!'))

    def test_signal_nach_dem_lauf_hat_trotzdem_eine_schlusszeile(self):
        w = self.werkstatt()
        with mock.patch.object(N, 'lauf', side_effect=N.Abbruch('SIGTERM')):
            code, zeilen, err = main_aufrufen(argv_fuer(w))
        self.assertEqual((code, zeilen), (1, ['NACHTRAG: Fehler – abgebrochen (SIGTERM)']))

    def test_main_stellt_die_signalbehandlung_wieder_her(self):
        w = self.werkstatt()
        vorher = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        main_aufrufen(argv_fuer(w))
        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)), vorher)

    def test_abbruch_ist_keine_exception(self):
        """Ein „except Exception“ darf den Abbruch nie verschlucken."""
        self.assertTrue(issubclass(N.Abbruch, BaseException) and not issubclass(N.Abbruch, Exception))
        self.assertEqual(str(N.Abbruch('SIGTERM')), 'SIGTERM')


# ---------------------------------------------------------------- B11: Exit-Code und Docstring stimmen überein

class TestExitCodes(TestBasis):
    def lauf_main(self, w, *extra):
        code, zeilen, err = main_aufrufen(argv_fuer(w, *extra))
        return code, zeilen[-1]

    def hessenpokal(self, w, team='D2'):
        w.welt.seite[team].append(Spiel(team, POKAL[team], 7, '2026-12-02', '18:15', EIGEN[team], 'Pokal FC 2', 'Kreispokal', wettbewerb_text='Hessenpokal'))

    def test_in_ordnung_ist_exit_0_auch_mit_hinweisen(self):
        w = self.werkstatt()
        self.assertEqual(self.lauf_main(w), (0, 'NACHTRAG: unverändert'))
        self.assertEqual(self.lauf_main(w, '--anwenden'), (0, 'NACHTRAG: unverändert'))
        neues_spiel(w)
        self.assertEqual(self.lauf_main(w), (0, 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben'))
        self.hessenpokal(w)
        w.cache_neu()
        self.assertEqual(self.lauf_main(w, '--anwenden'), (0, 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis'))

    def test_auffaellig_ist_exit_2(self):
        w = self.werkstatt()
        self.hessenpokal(w)
        w.cache_neu()
        self.assertEqual(self.lauf_main(w, '--anwenden'), (2, 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)'))
        w = self.werkstatt()
        for i in range(11):
            team = 'D1' if i < 6 else 'D2'
            w.welt.seite[team].append(Spiel(team, LIGA[team], 40 + i, (date(2026, 11, 29) + timedelta(days=7 * i)).isoformat(), '11:00', EIGEN[team], 'FC Neu %d' % i))
        w.cache_neu()
        code, schluss = self.lauf_main(w, '--anwenden')
        self.assertEqual((code, schluss), (2, 'NACHTRAG: Auffällig, nicht übernommen – 11 Änderungen in einem Lauf (Grenze 10)'))
        w = self.werkstatt()
        neues_spiel(w)
        open(w.notaus, 'w').close()
        self.assertEqual(self.lauf_main(w, '--anwenden')[0], 2)
        w = self.werkstatt()
        w.cache_neu(ohne=('D1',))
        self.assertEqual(self.lauf_main(w, '--anwenden'), (2, 'NACHTRAG: Fehler – D1 übersprungen: kein Cache-Stand vorhanden'))

    def test_fehler_ist_exit_1(self):
        w = self.werkstatt()
        with open(w.csv, 'wb') as f:
            f.write(b'Das ist keine CSV\r\n')
        code, schluss = self.lauf_main(w)
        self.assertEqual(code, 1)
        self.assertTrue(schluss.startswith('NACHTRAG: Fehler – spiele.csv nicht lesbar oder unerwartete Form'), schluss)
        w = self.werkstatt(bom=True)             # build_ics.py scheitert an einem BOM: Prüfung vor dem Schreiben schlägt fehl
        neues_spiel(w)
        code, schluss = self.lauf_main(w, '--anwenden')
        self.assertEqual(code, 1)
        self.assertTrue(schluss.startswith('NACHTRAG: Fehler – Prüfung vor dem Schreiben fehlgeschlagen'), schluss)

    def test_fehler_schlaegt_auffaellig(self):
        w = self.werkstatt(bom=True)
        neues_spiel(w)
        w.cache_neu(ohne=('D2',))
        code, schluss = self.lauf_main(w, '--anwenden')
        self.assertEqual(code, 1, schluss)
        self.assertIn('D2 übersprungen', schluss)

    def test_docstring_beschreibt_die_drei_exit_codes(self):
        self.assertIn('Exit-Code: 0 = in Ordnung', N.__doc__)
        self.assertIn('2 = auffällig', N.__doc__)
        self.assertIn('1 = Fehler', N.__doc__)
        self.assertIn('nur Hinweise', N.__doc__)

    def test_exit_code_unter_python_i_wie_im_lauf(self):
        w = self.werkstatt()
        self.hessenpokal(w)
        w.cache_neu()
        r = subprocess.run([sys.executable, '-I', '-B', os.path.join(HIER, 'nachtrag.py')] + argv_fuer(w), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)')


# ---------------------------------------------------------------- B02: Zeichen in Namen

class TestNamen(TestBasis):
    """build_ics.py maskiert Namen in der App-Seite nicht (JavaScript-Daten, innerHTML): was nicht in einen Namen gehört, kommt nicht
    in die CSV. Die Namen stehen auf der Seite HTML-maskiert, wie bei fussball.de (abgleich.sauber löst die Entitäten auf)."""

    def lauf_mit_gegner(self, name, **kw):
        w = self.werkstatt()
        w.welt.seite['D1'].append(Spiel('D1', LIGA['D1'], 40, '2026-11-29', '13:00', EIGEN['D1'], html.escape(name), **kw))
        w.cache_neu()
        vorher = w.csv_lesen()
        return w, vorher, w.anwenden()

    def test_gefaehrliche_zeichen_werden_nicht_ergaenzt(self):
        faelle = (('</script><img src=x onerror=alert(1)>', 'ein unzulässiges Zeichen'), ('FC >Test<', 'ein unzulässiges Zeichen'),
                  ('FC\x00Null', 'ein Steuer- oder Formatzeichen'), ('FC\u202eEvil', 'ein Steuer- oder Formatzeichen'),
                  ('FC \ufffd Test', 'ein unzulässiges Zeichen'), ('SV Gr\xc3\xa4fenhain 1', 'zerstörte Umlaute (Mojibake)'),
                  ('FC \xe2\u20ac\u201c 2', 'zerstörte Umlaute (Mojibake)'), ('FC\\Back', 'ein unzulässiges Zeichen'),
                  ('FC\x07Bell', 'ein Steuer- oder Formatzeichen'))
        for name, stichwort in faelle:
            with self.subTest(sichtbar=N.sichtbar(name)):
                w, vorher, e = self.lauf_mit_gegner(name)
                self.pruefe_unveraendert(w, vorher)
                self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
                self.assertIn('Vereinsname „', e['hinweise'][0])
                self.assertIn(stichwort, e['hinweise'][0])
                self.assertTrue(e['hinweise'][0].endswith(': nicht ergänzt'), e['hinweise'][0])
                self.assertEqual((e['code'], e['schluss']), (2, 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)'))
                ausgabe = '\n'.join(e['zeilen'] + e['hinweise'] + [e['schluss']])
                self.assertEqual([c for c in ausgabe if c != '\n' and unicodedata.category(c) in N.NAME_KATEGORIEN], [],
                                 'Steuer- und Formatzeichen stehen in Ausgabe und Bericht nur als Escape')

    def test_steuerzeichen_stehen_in_der_meldung_als_escape(self):
        w, vorher, e = self.lauf_mit_gegner('FC\x00Null\u202e')
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 13:00 · Heim gegen FC\\x00Null\\u202e: Vereinsname „FC\\x00Null\\u202e“ enthält ein Steuer- oder Formatzeichen: nicht ergänzt'])

    def test_anzeige_und_bereinigt(self):
        self.assertEqual(N.anzeige('FC \x1b[31mrot\x00\u202e\n'), 'FC \\x1b[31mrot\\x00\\u202e\\n')
        self.assertEqual(N.anzeige('Müller · Söhne → Nr. 5 – „x“'), 'Müller · Söhne → Nr. 5 – „x“')
        self.assertEqual(N.anzeige(ValueError('a\x00')), 'a\\x00')
        e = N.ergebnis_neu(False)
        e.update(zeilen=['Fehler: \x1b[2J'], hinweise=['a\u202eb'], fehler=['c\x00'], stumm=['d\x07'], schluss='NACHTRAG: e\x1b')
        N.bereinigt(e)
        self.assertEqual((e['zeilen'], e['hinweise'], e['fehler'], e['stumm'], e['schluss']),
                         (['Fehler: \\x1b[2J'], ['a\\u202eb'], ['c\\x00'], ['d\\x07'], 'NACHTRAG: e\\x1b'))

    def test_tab_und_zeilentrenner_macht_schon_der_parser_zum_leerzeichen(self):
        w, vorher, e = self.lauf_mit_gegner('FC\tTab\u2028Zeile')
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertEqual([z.split(';')[6] for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;%s;40;' % LIGA['D1'])], ['FC Tab Zeile'])

    def test_sonderzeichen_die_zu_echten_namen_gehoeren_bleiben_erlaubt(self):
        for name in ('JSG Nidda/Ober-Mörlen 1', 'Türk Gücü Frankfurt', "TSV O'Brien 1", 'FC (Ärger) 2', 'Müller & Söhne 1', 'FC Straße 09'):
            with self.subTest(name):
                w, vorher, e = self.lauf_mit_gegner(name)
                self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
                self.assertEqual([z.split(';')[6] for z in zeilen_von(w.csv_lesen()) if z.startswith('D1;%s;40;' % LIGA['D1'])], [name])

    def test_auch_ein_namensfehler_beim_verlegen_wird_nur_gemeldet(self):
        """Der Gegner in der CSV ist „SV Gräfenhain 1“, bei fussball.de (oder im Cache) steht „SV Gr\xc3\xa4fenhain 1“ und die Uhrzeit ist neu:
        ähnlich genug für „gleicher Gegner“, aber der kaputte Name darf nichts auslösen."""
        w = self.werkstatt()
        csv_aendern(w, 'D2', 10, csv_gast='SV Gräfenhain 1')
        s = w.welt.spiel(w.welt.seite, 'D2', 10)
        s.gast, s.zeit = 'SV Gr\xc3\xa4fenhain 1', '13:30'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
        self.assertTrue(e['hinweise'][0].endswith('(Mojibake): nicht geändert'), e['hinweise'][0])

    def test_auch_beim_absagen_wird_ein_kaputter_name_nur_gemeldet(self):
        w = self.werkstatt()
        csv_aendern(w, 'D2', 10, csv_gast='SV Gräfenhain 1')
        s = w.welt.spiel(w.welt.seite, 'D2', 10)
        s.gast, s.status = 'SV Gr\xc3\xa4fenhain 1', 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])

    def test_zeichenfehler_einzeln(self):
        ok = ('FFV Sportfreunde 04 I', 'SG Bornheim / GW 2', 'JSG Nidda/Ober-Mörlen 1', 'FC Ärger, Zweite', "TSV O'Brien 1", 'SV Gräfenhain 1', 'FC 09 Straße (A)', 'Müller & Söhne')
        for t in ok:
            self.assertIsNone(N.zeichenfehler(t), t)
            self.assertTrue(N.feld_sauber(t))
        faelle = ((' FC', 'Leerzeichen am Rand'), ('FC ', 'Leerzeichen am Rand'), ('\tFC', 'Leerzeichen am Rand'),
                  ('a;b', 'Trennzeichen'), ('a"b', 'Trennzeichen'), ('a\nb', 'Trennzeichen'), ('a\rb', 'Trennzeichen'),
                  ('a<b', 'unzulässiges Zeichen'), ('a>b', 'unzulässiges Zeichen'), ('a\\b', 'unzulässiges Zeichen'), ('a\ufffdb', 'unzulässiges Zeichen'),
                  ('a\x00b', 'Steuer'), ('a\tb', 'Steuer'), ('a\u2028b', 'Steuer'), ('a\u2029b', 'Steuer'), ('a\u200bb', 'Steuer'), ('a\u202eb', 'Steuer'),
                  ('a\ue000b', 'Steuer'), ('a\ud800b', 'Steuer'), ('a\u0378b', 'Steuer'),
                  ('Preu\xc3\u0178en', 'Mojibake'), ('Gr\xc3\xa4fenhain', 'Mojibake'), ('FC \xc2\xab 1', 'Mojibake'), ('a \xe2\u20ac\u201c b', 'Mojibake'), ('\xc3\xa2', 'Mojibake'),
                  (None, 'keinen Text'), (42, 'keinen Text'))
        for t, stichwort in faelle:
            with self.subTest(N.sichtbar(str(t))):
                self.assertIn(stichwort, N.zeichenfehler(t) or '')
                self.assertFalse(N.feld_sauber(t))

    def test_sichtbar_kuerzt_und_maskiert(self):
        self.assertEqual(N.sichtbar('a\x00b\u202e'), 'a\\x00b\\u202e')
        self.assertEqual(len(N.sichtbar('x' * 200)), 60)
        self.assertEqual(N.sichtbar('x' * 200, 5), 'xxxxx')

    def test_namen_problem_einzeln(self):
        g = dict(heim='FFV Sportfreunde 04 I', gast='FC Test 1')
        self.assertIsNone(N.namen_problem(g))
        self.assertIsNone(N.namen_problem(dict(g, gast=' FC Rand ')), 'Randleerzeichen sind hier kein Problem (sauber() schneidet sie ab)')
        self.assertEqual(N.namen_problem(dict(g, gast='FC <b>')), 'Vereinsname „FC <b>“ enthält ein unzulässiges Zeichen (<, >, Backslash oder U+FFFD)')
        self.assertEqual(N.namen_problem(dict(g, heim='a\x00b')), 'Vereinsname „a\\x00b“ enthält ein Steuer- oder Formatzeichen')
        self.assertEqual(N.namen_problem(dict(g, gast='Sieger Spiel 12')), 'Platzhalter als Gegner („Sieger Spiel 12“)')

    def test_feldpruefung_der_csv_zeile_meldet_den_grund(self):
        """Auch wenn ein Name den Parser passiert hat: csv_felder prüft jedes der 11 Felder noch einmal."""
        konv = N.Konventionen([])
        g = dict(art='Meisterschaft', staffel=LIGA['D1'], nr=40, datum='2026-11-29', zeit='13:00', heim=EIGEN['D1'], gast='FC \ufffd 1', ha='H',
                 gegner='FC \ufffd 1', fbid='X' * 32, wettbewerb='Kreisliga A')
        felder, grund = N.csv_felder('D1', g, konv)
        self.assertIsNone(felder)
        self.assertEqual(grund, 'ein Feld („FC \ufffd 1“) enthält ein unzulässiges Zeichen (<, >, Backslash oder U+FFFD)')


# ---------------------------------------------------------------- B03: Erkennung des eigenen Vereins wie in build_ics.py

class TestVereinsname(TestBasis):
    def test_verein_ist_wie_in_build_ics_py(self):
        with open(os.path.join(REPO, 'build_ics.py'), encoding='utf-8') as f:
            quelle = f.read()
        self.assertEqual(re.search(r'^VEREIN = "([^"]*)"', quelle, re.M).group(1), N.VEREIN)
        self.assertIn('row["heim"].startswith(VEREIN)', quelle, 'build_ics.py erkennt ein Heimspiel nur so: Anfang des Namens, Groß-/Kleinschreibung zählt')

    def test_grosse_schreibweise_wuerde_bei_build_ics_zum_auswaertsspiel(self):
        """Die Begründung der Sperre: mit „FFV SPORTFREUNDE 04 I“ als Heim baut build_ics.py ein Auswärtsspiel."""
        w = self.werkstatt()
        zeile = 'D1;%s;77;29.11.2026;13:00;FFV SPORTFREUNDE 04 I;FC Neu 1;%s;;;Meisterschaft' % (LIGA['D1'], HEIMORT['D1'])
        ziel = os.path.join(w.dir, 'bau')
        os.makedirs(ziel)
        rc, _ = N.bauen(w.repo, w.csv_lesen() + (zeile + '\r\n').encode('utf-8'), ziel)
        self.assertEqual(rc, 0)
        with open(os.path.join(ziel, 'docs', 'd1.ics'), encoding='utf-8') as f:
            text = f.read().replace('\r\n ', '')
        self.assertIn('Auswärts bei FFV SPORTFREUNDE 04 I', text)

    def test_eigener_verein_anders_geschrieben_wird_nicht_ergaenzt(self):
        for heim in ('FFV SPORTFREUNDE 04 I', 'Sportfreunde 04 Frankfurt I', 'ffv sportfreunde 04 i'):
            with self.subTest(heim):
                w = self.werkstatt()
                neues_spiel(w, heim=heim)
                vorher = w.csv_lesen()
                e = w.anwenden()
                self.pruefe_unveraendert(w, vorher)
                self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
                self.assertIn('unser Vereinsname („%s“) weicht von „FFV Sportfreunde 04“ ab, build_ics.py würde Heim und Auswärts vertauschen' % heim, e['hinweise'][0])
                self.assertTrue(e['hinweise'][0].endswith(': nicht ergänzt'))
                self.assertEqual(e['code'], 2)

    def test_auswaertsspiel_und_richtige_schreibweise_bleiben_moeglich(self):
        w = self.werkstatt()
        neues_spiel(w, 'D1', 40, heim='FC Neu 1', gast='FFV SPORTFREUNDE 04 I')            # auswärts: der Gast heißt in der CSV, wie er ist
        neues_spiel(w, 'D2', 41, heim='FFV Sportfreunde 04 II', gast='FC Neu 2')
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 2 geändert (2 ergänzt), nicht gepusht')

    def test_kinderfestival_bei_uns_mit_falscher_schreibweise_wird_nicht_ergaenzt(self):
        w = self.werkstatt()
        w.welt.seite['F1'].append(Spiel('F1', LIGA['F1'], 30, '2026-11-21', '10:00', 'FFV SPORTFREUNDE 04 I', '', 'Kinderfestival'))
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('weicht von „FFV Sportfreunde 04“ ab' in h for h in e['hinweise']), e['hinweise'])

    def test_verlegung_bleibt_moeglich_weil_der_csv_name_bleibt(self):
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D1', 10)
        s.heim, s.zeit = 'FFV SPORTFREUNDE 04 I', '13:30'
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')
        self.assertTrue(all(z.split(';')[5] != 'FFV SPORTFREUNDE 04 I' for z in zeilen_von(w.csv_lesen())))


# ---------------------------------------------------------------- B13: Platzhalter als Gegner

class TestPlatzhalter(TestBasis):
    NAMEN = ('Sieger Spiel 12', 'Verlierer Spiel 3', 'Gewinner Halbfinale 1', 'sieger spiel 7', 'N.N.', 'NN', 'N. N.', 'Freilos', 'SV Freilos', 'spielfrei 1')

    def pokalspiel(self, gast, **kw):
        return Spiel('D1', POKAL['D1'], 6, '2026-12-02', '18:15', EIGEN['D1'], gast, 'Kreispokal', ort=HEIMORT['D1'], **kw)

    def test_platzhalter_wird_nicht_ergaenzt(self):
        for name in self.NAMEN:
            with self.subTest(name):
                w = self.werkstatt()
                w.welt.seite['D1'].append(self.pokalspiel(name))
                w.cache_neu()
                vorher = w.csv_lesen()
                e = w.anwenden()
                self.pruefe_unveraendert(w, vorher)
                self.assertEqual(e['hinweise'], ['D1 Mi 02.12. 18:15 · Pokal, Heim gegen %s: Platzhalter als Gegner („%s“): nicht ergänzt' % (name, name)])
                self.assertEqual(e['code'], 2)

    def mit_platzhalter_in_der_csv(self, name='Sieger Spiel 12'):
        w = self.werkstatt()
        w.welt.beide('D1', self.pokalspiel(name))
        csv_schreiben(w)
        return w

    def test_platzhalter_wird_nicht_verlegt(self):
        w = self.mit_platzhalter_in_der_csv()
        w.welt.spiel(w.welt.seite, 'D1', 6, POKAL['D1']).zeit = '19:00'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 Mi 02.12. 19:00 · Pokal, Heim gegen Sieger Spiel 12: Platzhalter als Gegner („Sieger Spiel 12“): nicht geändert'])

    def test_platzhalter_wird_nicht_abgesagt(self):
        w = self.mit_platzhalter_in_der_csv()
        w.welt.spiel(w.welt.seite, 'D1', 6, POKAL['D1']).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
        self.assertIn('Platzhalter als Gegner', e['hinweise'][0])

    def test_echte_namen_sind_kein_platzhalter(self):
        for name in ('SG Bornheim / GW 2', 'JSG Siegerland 1', 'FC Gewinn 04', 'SV Neunkirchen', 'TSV NNW Frankfurt', 'FC Anna Nordend', 'Eintracht Frankfurt 1',
                     'Türk Gücü Frankfurt', 'FSV Frankfurt 1899', 'SKG Frankfurt 1861', 'Rot-Weiss Frankfurt', 'VfR Bockenheim 2', 'NK Croatia Frankfurt'):
            with self.subTest(name):
                self.assertIsNone(N.PLATZHALTER.search(name), name)
                self.assertIsNone(N.namen_problem(dict(heim=EIGEN['D1'], gast=name)))

    def test_wird_der_platzhalter_durch_den_echten_namen_ersetzt_ist_das_nur_eine_meldung(self):
        """Später steht der echte Gegner da: das ist eine Namensabweichung (nur melden, nie ändern)."""
        w = self.mit_platzhalter_in_der_csv()
        w.welt.spiel(w.welt.seite, 'D1', 6, POKAL['D1']).gast = 'FC Pokalgegner 1'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('Gegner abweichend' in h for h in e['hinweise']), e['hinweise'])


# ---------------------------------------------------------------- B04: „abgesagt“ nur zum selben Termin; Vergangenheit bleibt unberührt

class TestAbsageTermin(TestBasis):
    def test_csv_hat_von_hand_den_neuen_termin_fussball_de_setzt_den_alten_ab(self):          # AP-C S04b
        w = self.werkstatt()
        csv_aendern(w, 'D2', 10, datum='2026-11-15')
        w.welt.spiel(w.welt.seite, 'D2', 10).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D2 So 15.11. 11:00 · Heim gegen FC Muster 1: bei fussball.de abgesetzt zum Termin So 01.11. 11:00, die CSV hat So 15.11. 11:00: nicht geändert'])
        self.assertEqual((e['code'], e['aenderungen']), (2, []))

    def test_nur_die_uhrzeit_weicht_ab(self):
        w = self.werkstatt()
        csv_aendern(w, 'D2', 10, zeit='13:00')
        w.welt.spiel(w.welt.seite, 'D2', 10).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D2 So 01.11. 13:00 · Heim gegen FC Muster 1: bei fussball.de abgesetzt zum Termin So 01.11. 11:00, die CSV hat So 01.11. 13:00: nicht geändert'])

    def test_zurueckgezogen_mit_anderer_uhrzeit(self):
        w = self.werkstatt()
        csv_aendern(w, 'D3', 20, zeit='13:00')
        s = w.welt.spiel(w.welt.seite, 'D3', 20)
        s.heim, s.status = 'SV Beispiel 2 zg.', 'Nichtantritt HEIM'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
        self.assertIn('Gegner zurückgezogen (zg.) zum Termin So 08.11. 12:00, die CSV hat So 08.11. 13:00', e['hinweise'][0])

    def test_nichtantritt_zu_einem_anderen_termin(self):
        w = self.werkstatt()
        csv_aendern(w, 'E1', 30, datum='2026-11-29')
        w.welt.spiel(w.welt.seite, 'E1', 30).status = 'Nichtantritt GAST'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
        self.assertIn('Nichtantritt zum Termin So 22.11. 11:30, die CSV hat So 29.11. 11:30', e['hinweise'][0])

    def test_gleicher_termin_wird_abgesagt_auch_wenn_fussball_de_keine_uhrzeit_nennt(self):
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D1', 10)
        s.status, s.zeit = 'Absetzung', ''
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 abgesagt), nicht gepusht')

    def test_vergangenheit_wird_nie_abgesagt(self):
        def spiel_von(datum):
            w = self.werkstatt()
            w.welt.beide('D1', Spiel('D1', LIGA['D1'], 70, datum, '11:00', EIGEN['D1'], 'FC Fruehes 1', ort=HEIMORT['D1']))
            csv_schreiben(w)
            return w
        with self.subTest('Nichtantritt für ein Spiel von gestern'):
            w = spiel_von('2026-10-25')
            w.welt.spiel(w.welt.seite, 'D1', 70).status = 'Nichtantritt GAST'
            w.cache_neu()
            vorher = w.csv_lesen()
            e = w.anwenden()
            self.pruefe_unveraendert(w, vorher)
            self.assertEqual((e['hinweise'], e['schluss']), ([], 'NACHTRAG: unverändert'))
        with self.subTest('CSV-Zeile von gestern, fussball.de setzt sie für morgen ab'):
            w = spiel_von('2026-10-25')
            s = w.welt.spiel(w.welt.seite, 'D1', 70)
            s.status, s.datum = 'Absetzung', '2026-10-27'
            w.cache_neu()
            vorher = w.csv_lesen()
            e = w.anwenden()
            self.pruefe_unveraendert(w, vorher)
            self.assertEqual((e['hinweise'], e['schluss']), ([], 'NACHTRAG: unverändert'))
        with self.subTest('ein Spiel von heute wird noch abgesagt'):
            w = spiel_von(HEUTE)
            w.welt.spiel(w.welt.seite, 'D1', 70).status = 'Nichtantritt GAST'
            w.cache_neu()
            e = w.anwenden()
            self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 abgesagt), nicht gepusht')


# ---------------------------------------------------------------- B07: Termine nach dem Saisonende; Saisonjahr

class TestSaison(TestBasis):
    def test_verlegung_nach_dem_saisonende_wird_nur_gemeldet(self):                         # AP-C S10b
        for ziel in ('2027-07-03', '2028-03-01'):
            with self.subTest(ziel):
                w = self.werkstatt()
                w.welt.spiel(w.welt.seite, 'E1', 20).datum = ziel
                w.cache_neu()
                vorher = w.csv_lesen()
                e = w.anwenden()
                self.pruefe_unveraendert(w, vorher)
                self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
                self.assertTrue(e['hinweise'][0].endswith('das liegt nach dem Saisonende (30.06.2027): nicht geändert'), e['hinweise'][0])
                self.assertEqual(e['code'], 2)

    def test_letzter_tag_der_saison_ist_noch_erlaubt(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'E1', 20).datum = '2027-06-30'
        w.cache_neu()
        self.assertEqual(w.anwenden()['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')

    def test_ergaenzung_nach_dem_saisonende_wird_nur_gemeldet(self):
        w = self.werkstatt()
        neues_spiel(w, datum='2027-07-04')
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 04.07. 13:00 · Heim gegen FC Neu 1: Termin liegt nach dem Saisonende (30.06.2027): nicht ergänzt'])
        w2 = self.werkstatt()
        neues_spiel(w2, datum='2027-06-30')
        self.assertEqual(w2.anwenden()['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')

    def test_wieder_ansetzen_nach_dem_saisonende_wird_nur_gemeldet(self):
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)
        w.welt.spiel(w.welt.seite, 'D1', 10).datum = '2027-08-01'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(e['hinweise']), 1, e['hinweise'])
        self.assertTrue(e['hinweise'][0].endswith('wieder angesetzt, der Termin liegt nach dem Saisonende (30.06.2027): nicht geändert'), e['hinweise'][0])

    def test_zu_weit_voraus(self):
        bis = '2028-06-30'                       # (mit dem echten Saisonende ist die 400-Tage-Grenze nie vor dem Saisonende erreichbar)
        self.assertIsNone(N.zu_weit('2027-11-30', HEUTE, bis))
        self.assertEqual(N.zu_weit('2027-12-01', HEUTE, bis), 'mehr als 400 Tage voraus')
        self.assertIsNone(N.zu_weit('2027-06-30', HEUTE, '2027-06-30'))
        self.assertEqual(N.zu_weit('2027-07-01', HEUTE, '2027-06-30'), 'nach dem Saisonende (30.06.2027)')
        self.assertEqual(N.MAX_TAGE_VORAUS, 400)

    def test_saisonjahr_von_januar_bis_juni_und_ab_juli(self):
        for heute, datum, ergaenzt in (('2027-01-01', '2027-01-09', True), ('2027-02-15', '2027-03-07', True), ('2027-06-30', '2027-06-30', True),
                                       ('2027-02-15', '2027-07-04', False), ('2027-07-01', '2027-08-15', True), ('2026-12-31', '2027-01-16', True)):
            with self.subTest(heute=heute, neu=datum):
                w = self.werkstatt()
                neues_spiel(w, datum=datum)
                e = w.anwenden(heute=heute)
                self.assertEqual(e['angewendet'], ergaenzt, e['schluss'])
                gefunden = any(z.startswith('D1;%s;40;' % LIGA['D1']) for z in zeilen_von(w.csv_lesen()))
                self.assertEqual(gefunden, ergaenzt)
                if heute != '2027-07-01':             # in der neuen Saison steht die erste Zeile einer neuen Staffel (Hinweis); sonst nur diese Ergänzung
                    self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht' if ergaenzt
                                     else 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)')

    def test_csv_zeilen_der_vorsaison_zaehlen_nicht_zur_quelle(self):
        w = self.werkstatt()
        for i in range(8):
            w.welt.csv['D1'].append(Spiel('D1', '330111', 100 + i, '2025-10-%02d' % (4 + i), '10:00', EIGEN['D1'], 'FC Alt %d' % i, ort=HEIMORT['D1']))
        csv_schreiben(w)
        neues_spiel(w)
        e = w.anwenden()
        self.assertEqual((e['fehler'], e['schluss']), ([], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht'))


# ---------------------------------------------------------------- Heim/Auswärts unklar (Derby), Verlegen und Absagen mit Randfällen

class TestDerbyUndRaender(TestBasis):
    def mit_derby(self):
        w = self.werkstatt()
        w.welt.beide('D1', Spiel('D1', LIGA['D1'], 60, '2026-11-29', '11:00', EIGEN['D1'], EIGEN['D2'], ort=HEIMORT['D1']))
        csv_schreiben(w)
        w.cache_neu()
        return w

    def test_derby_neu_bei_fussball_de_wird_nicht_ergaenzt(self):
        w = self.werkstatt()
        neues_spiel(w, gast=EIGEN['D2'])
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 13:00 · Auswärts bei FFV Sportfreunde 04 II: Heim/Auswärts nicht bestimmbar: nicht ergänzt'])

    def test_derby_wird_nicht_verlegt(self):
        w = self.mit_derby()
        w.welt.spiel(w.welt.seite, 'D1', 60).zeit = '13:00'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 11:00 · Auswärts bei FFV Sportfreunde 04 II: bei fussball.de verlegt, die Paarung stimmt aber nicht eindeutig überein: nicht geändert'])

    def test_derby_wird_nicht_abgesagt(self):
        w = self.mit_derby()
        w.welt.spiel(w.welt.seite, 'D1', 60).status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 11:00 · Auswärts bei FFV Sportfreunde 04 II: bei fussball.de abgesetzt, die Paarung stimmt aber nicht eindeutig überein: nicht geändert'])

    def test_derby_wird_nicht_wieder_angesetzt(self):
        w = self.mit_derby()
        w.csv_abgesagt_setzen('D1', 60)
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 11:00 · Auswärts bei FFV Sportfreunde 04 II: bei fussball.de wieder angesetzt, die Paarung stimmt aber nicht eindeutig überein: nicht geändert'])

    def test_gleiche_partie_einzeln(self):
        h = dict(ha='H', gegner='FC Muster 1')
        self.assertTrue(N.gleiche_partie(h, dict(h)))
        self.assertTrue(N.gleiche_partie(h, dict(h, gegner='FC  Muster 1')), 'Schreibweise zählt nicht')
        self.assertFalse(N.gleiche_partie(h, dict(h, ha='A')))
        self.assertFalse(N.gleiche_partie(h, dict(h, gegner='Ganz Anderer FC')))
        self.assertFalse(N.gleiche_partie(dict(ha='?', gegner='x'), dict(ha='?', gegner='x')))
        self.assertTrue(N.gleiche_partie(dict(ha='H', gegner=''), dict(ha='H', gegner='')), 'Kinderfestival bei uns: kein Gegner')

    def test_einzelzeile_mit_neuem_termin_im_ergebnisfeld_wird_nicht_zur_verlegung(self):
        """Die CSV hat den neuen Termin schon von Hand; fussball.de zeigt nur die alte Zeile mit dem neuen Termin im Ergebnisfeld (Status
        „verlegt“, keine zweite Zeile): kein Anlass, etwas zu ändern."""
        w = self.werkstatt()
        csv_aendern(w, 'D1', 10, datum='2026-11-15')
        w.welt.spiel(w.welt.seite, 'D1', 10).status = '15.11.2026 11:00'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['schluss'], e['hinweise']), ('NACHTRAG: unverändert', []))

    def test_verlegung_ohne_vollstaendigen_termin(self):
        w = self.werkstatt()
        s = w.welt.spiel(w.welt.seite, 'D1', 10)
        s.datum, s.zeit = '2026-11-02', ''
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 01.11. 11:00 · Heim gegen FC Muster 1: bei fussball.de verlegt, aber ohne vollständigen Termin: nicht geändert'])

    def test_verlegung_auf_unplausible_uhrzeit(self):
        w = self.werkstatt()
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = '00:00'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 01.11. 11:00 · Heim gegen FC Muster 1: bei fussball.de neue Uhrzeit 00:00 unplausibel: nicht geändert'])

    def test_wieder_angesetzt_ohne_uhrzeit_still_unplausibel_gemeldet_und_in_der_vergangenheit_ignoriert(self):
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = ''
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual((e['schluss'], e['hinweise']), ('NACHTRAG: unverändert', []))
        self.assertIn('D1 So 01.11. · Heim gegen FC Muster 1: wieder angesetzt, aber noch ohne Uhrzeit', e['stumm'])
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = '00:00'
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(e['hinweise'], ['D1 So 01.11. 11:00 · Heim gegen FC Muster 1: bei fussball.de wieder angesetzt, Uhrzeit 00:00 unplausibel: nicht geändert'])
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)
        w.welt.spiel(w.welt.seite, 'D1', 10).datum = '2026-10-20'
        w.cache_neu()
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['schluss'], e['hinweise']), ('NACHTRAG: unverändert', []))

    def test_frueherer_nichtantritt_zaehlt_als_verlauf_fuer_wieder_ansetzen(self):
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10, mit_verlauf=False)
        alt = deepcopy(w.welt.seite['D1'])
        w.welt.spiel({'D1': alt}, 'D1', 10).status = 'Nichtantritt GAST'
        w.frueherer_stand('D1', alt)
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')

    def test_spiel_ohne_spielnummer_wird_nicht_ergaenzt(self):
        ohne = ('<tr class="row-headline visible-small"><td colspan="6">Sonntag, 29.11.2026 - 13:00 Uhr | Kreisliga A</td></tr>\n'
                '<tr class="odd row-competition hidden-small"><td class="column-date"><span class="hidden-small inline">So, 29.11.26 |&nbsp;</span>13:00</td>'
                '<td colspan="3" class="column-team"><a>Kreisliga A</a></td><td colspan="2"><a>ME</a></td></tr>\n'
                '<tr class="odd"><td class="hidden-small"></td><td class="column-club"><a href="#" class="club-wrapper"><div class="club-name">\n %s\n</div></a></td>'
                '<td class="column-colon">:</td><td class="column-club no-border"><a href="#" class="club-wrapper"><div class="club-name">\n FC Ohne Nummer\n</div></a></td>'
                '<td class="column-score"></td><td class="column-detail"></td></tr>\n') % EIGEN['D1']
        w = self.werkstatt()
        w.cache_neu(ersetze={'D1': seite_aus(w.welt.seite['D1'], zusatz=ohne)})
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D1 So 29.11. 13:00 · Heim gegen FC Ohne Nummer: bei fussball.de ohne Staffel oder Spielnummer: nicht ergänzt'])

    def test_neuer_schluessel_gilt_im_selben_lauf_auch_fuer_die_naechste_mannschaft(self):
        """D1 und D2 bieten dieselbe (Staffel, Nr) als neu an: nur eine Zeile darf entstehen, die zweite ist „schon vergeben“."""
        w = self.werkstatt()
        for team in ('D1', 'D2'):
            w.welt.seite[team].append(Spiel(team, '340999', 77, '2026-12-06', '11:00', EIGEN[team], 'FC Doppelt 1'))
        w.cache_neu()
        e = w.anwenden()
        self.assertEqual(len([z for z in zeilen_von(w.csv_lesen()) if ';340999;77;' in z]), 1)
        self.assertTrue(any('340999·77 sind in der CSV schon vergeben: nicht ergänzt' in h for h in e['hinweise']), e['hinweise'])
        self.assertTrue(e['angewendet'], e['schluss'])


# ---------------------------------------------------------------- B12/B15: beschädigte Cache-Dateien, Quarantäne für Sperrseiten

SPERRSEITE = '<html><body>' + '<p>Bitte bestätigen Sie, dass Sie kein Roboter sind.</p>' * 20 + '</body></html>'     # > 500 Zeichen, ohne Spielplan


def cache_datei(w, team, stempel, inhalt):
    """Eine zusätzliche Datei im Cache einer Mannschaft (Text oder Bytes)."""
    pfad = os.path.join(w.cache, SLUG[team], stempel.strftime('%Y%m%d-%H%M%S') + '.html')
    with open(pfad, 'wb') as f:
        f.write(inhalt if isinstance(inhalt, bytes) else inhalt.encode('utf-8'))
    return pfad


class TestCache(TestBasis):
    STUNDE_VORHER = JETZT - timedelta(hours=1)           # neuer als die guten Seiten (STAND = JETZT - 2 Stunden), aber nicht aus der Zukunft

    def test_neueste_datei_unbrauchbar_der_aeltere_gute_stand_wird_genommen(self):
        faelle = (('Sperrseite', SPERRSEITE, 'kein Spielplan erkannt'), ('zu kurz', '<html>x</html>', 'Seite zu kurz (14 Zeichen)'),
                  ('leer', b'', 'Seite zu kurz (0 Zeichen)'), ('kein UTF-8', b'\xff\xfe\x00 kaputt' * 100, 'nicht lesbar (UnicodeDecodeError)'))
        for name, inhalt, grund in faelle:
            with self.subTest(name):
                w = self.werkstatt()
                neues_spiel(w)                                                   # die gute Seite kennt das neue Spiel
                cache_datei(w, 'D1', self.STUNDE_VORHER, inhalt)
                e = w.anwenden()
                self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')
                self.assertEqual(e['fehler'], [])
                self.assertEqual(len(e['hinweise']), 1)
                self.assertTrue(e['hinweise'][0].startswith('D1: neuester Cache-Stand vom 26.10. 06:10 unbrauchbar ('), e['hinweise'])
                self.assertIn(grund, e['hinweise'][0])
                self.assertTrue(e['hinweise'][0].endswith('der Stand vom 26.10. 05:10 wird genommen'), e['hinweise'])
                self.assertEqual(e['stand']['D1'], STAND)

    def test_sind_alle_staende_unbrauchbar_wird_die_mannschaft_uebersprungen(self):
        w = self.werkstatt()
        neues_spiel(w, 'E1', 40)
        for pfad in glob.glob(os.path.join(w.cache, 'd1', '*.html')):
            os.remove(pfad)
        cache_datei(w, 'D1', STAND, SPERRSEITE)
        cache_datei(w, 'D1', self.STUNDE_VORHER, b'\xff\xfe\x00' * 400)
        e = w.anwenden()
        self.assertEqual(e['fehler'], ['D1 übersprungen: kein brauchbarer Cache-Stand (26.10. 06:10: nicht lesbar (UnicodeDecodeError); '
                                       '26.10. 05:10: kein Spielplan erkannt (Seitenaufbau geändert oder Sperrseite))'])
        self.assertTrue(e['angewendet'], 'die übrigen Mannschaften laufen weiter')
        self.assertEqual(e['code'], 2)

    def test_mehrere_mannschaften_ohne_brauchbaren_cache_eine_sammelmeldung(self):
        w = self.werkstatt()
        w.cache_neu(ersetze={'D1': SPERRSEITE, 'D2': SPERRSEITE})
        e = w.anwenden()
        self.assertEqual(e['fehler'], ['2 Mannschaften übersprungen, kein brauchbarer Cache-Stand: D1, D2'])
        w = self.werkstatt()
        w.cache_neu(ohne=('D1', 'D2'))
        self.assertEqual(w.anwenden()['fehler'], ['2 Mannschaften übersprungen, kein Cache-Stand vorhanden: D1, D2'])

    def test_dateien_mit_fremdem_namen_im_cache_werden_ignoriert(self):
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)                                          # braucht den Blick in frühere Stände (frueher_abgesetzt)
        for name in ('notiz.html', 'abruf-20261025.html', 'fehlversuch.txt', '20261025-070000.html.fehler'):
            with open(os.path.join(w.cache, 'd1', name), 'w') as f:
                f.write('egal')
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')
        self.assertEqual(len(N.staende(w.cache, 'd1')), 2)

    def test_beschaedigte_aeltere_datei_stoert_die_verlaufspruefung_nicht(self):          # AP-C S16c / B12
        w = self.werkstatt()
        w.csv_abgesagt_setzen('D1', 10)                                           # ein Stand von vor 24 Stunden zeigt die Absetzung
        cache_datei(w, 'D1', STAND - timedelta(hours=12), b'\xff\xfe\x00 kein UTF-8' * 100)
        cache_datei(w, 'D1', STAND - timedelta(hours=13), SPERRSEITE)
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')
        self.assertEqual(e['fehler'], [])

    def test_g1_abruf_mit_sperrseite_kommt_in_quarantaene_und_verdraengt_den_guten_stand_nicht(self):     # AP-C D01e / B15
        w = self.werkstatt()
        w.welt.seite['G1'].append(Spiel('G1', LIGA['G1'], 30, '2026-11-21', '10:00', EIGEN['G1'], '', 'Kinderfestival'))
        w.cache_neu(stempel=JETZT - timedelta(hours=26))
        aufrufe = []

        def antwort(req, timeout=None):
            aufrufe.append(req.full_url)
            return FalscheAntwort(SPERRSEITE)
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            e = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')
        self.assertEqual(e['fehler'], [])
        self.assertTrue(e['hinweise'][0].startswith('G1: Abruf lieferte keine Spielplanseite (kein Spielplan erkannt'), e['hinweise'])
        self.assertTrue(e['hinweise'][0].endswith('sie liegt als *.html.fehler im Cache; der Cache wird genommen'), e['hinweise'])
        ordner = os.path.join(w.cache, 'g-jugend')
        self.assertEqual(sorted(os.listdir(ordner)), ['%s.html' % (JETZT - timedelta(hours=26)).strftime('%Y%m%d-%H%M%S'),
                                                        '%s.html.fehler' % JETZT.strftime('%Y%m%d-%H%M%S'), 'fehlversuch.txt'])
        self.assertEqual(len(N.staende(w.cache, 'g-jugend')), 1, 'die Sperrseite ist kein Cache-Stand')
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):                  # 20 Stunden Ruhe nach dem Fehlversuch
            e2 = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(e2['fehler'], [])
        self.assertTrue(any(h.startswith('G1: Abruf nicht möglich (letzter Abruf schlug vor weniger als 20 Stunden fehl') for h in e2['hinweise']), e2['hinweise'])

    def test_nur_die_g_jugend_wird_abgerufen(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(hours=26))              # alle Pläne sind älter als 20 Stunden, aber noch nicht zu alt
        aufrufe = []

        def antwort(req, timeout=None):
            aufrufe.append(req.full_url)
            return FalscheAntwort(seite_aus(w.welt.seite['G1']))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            e = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 1)
        self.assertIn('team-id/' + dict((t[0], t[3]) for t in A.TEAMS)['G1'], aufrufe[0])
        self.assertEqual(e['schluss'], 'NACHTRAG: unverändert')


# ---------------------------------------------------------------- B16: Sommerpause

class TestSommerpause(TestBasis):
    SOMMER = '2027-07-15'

    def test_alter_cache_ohne_kommende_spiele_ist_kein_fehler(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(days=60))
        e = w.anwenden(heute=self.SOMMER)
        self.assertEqual((e['schluss'], e['code'], e['fehler'], e['hinweise']), ('NACHTRAG: unverändert', 0, [], []))
        self.assertEqual(len(e['stumm']), 11)
        self.assertTrue(all('keine kommenden Spiele in der CSV und Cache-Stand vom 27.08. 07:10 ist 1440 Stunden alt (Grenze 30), nichts zu tun' in s for s in e['stumm']), e['stumm'])
        b = w.lauf(heute=self.SOMMER)
        self.assertEqual((b['schluss'], b['code'], b['hinweise']), ('NACHTRAG: unverändert', 0, []))

    def test_fehlender_oder_unbrauchbarer_cache_ohne_kommende_spiele(self):
        w = self.werkstatt()
        w.cache_neu(ohne=('D1', 'G1'), ersetze={'D2': SPERRSEITE})
        e = w.anwenden(heute=self.SOMMER)
        self.assertEqual((e['schluss'], e['code'], e['fehler'], e['hinweise']), ('NACHTRAG: unverändert', 0, [], []))
        self.assertIn('D1: keine kommenden Spiele in der CSV und kein Cache-Stand vorhanden, nichts zu tun', e['stumm'])
        self.assertTrue(any(s.startswith('D2: keine kommenden Spiele in der CSV und kein brauchbarer Cache-Stand') for s in e['stumm']), e['stumm'])

    def test_frischer_cache_in_der_sommerpause_ist_unauffaellig(self):
        w = self.werkstatt()
        e = w.anwenden(heute=self.SOMMER)
        self.assertEqual((e['schluss'], e['code'], e['fehler'], e['hinweise']), ('NACHTRAG: unverändert', 0, [], []))

    def test_hat_die_mannschaft_kommende_zeilen_bleibt_der_fehler(self):
        w = self.werkstatt()
        w.welt.csv['D1'].append(Spiel('D1', '340999', 1, '2027-08-20', '11:00', EIGEN['D1'], 'FC Neu 9', ort=HEIMORT['D1']))
        csv_schreiben(w)
        w.cache_neu(stempel=JETZT - timedelta(days=60))
        e = w.anwenden(heute=self.SOMMER)
        self.assertEqual(e['fehler'], ['D1 übersprungen: Cache-Stand vom 27.08. 07:10 ist 1440 Stunden alt (Grenze 30)'])
        self.assertEqual(e['code'], 2)
        self.assertEqual(len(e['stumm']), 10, 'die anderen zehn Mannschaften haben nichts Kommendes')

    def test_vor_der_sommerpause_bleibt_ein_veralteter_cache_ein_fehler(self):
        w = self.werkstatt()
        w.cache_neu(stempel=JETZT - timedelta(days=60))
        e = w.anwenden()
        self.assertEqual(len(e['uebersprungen']), 11)
        self.assertEqual(e['code'], 2)


# ---------------------------------------------------------------- B05: das Sicherheitsnetz der Form und der ICS-Gegenprobe

class TestFormtreue(unittest.TestCase):
    """formtreue_pruefen und loeschfreiheit_pruefen direkt: jede Abweichung außerhalb der geplanten Zeilen muss auffallen."""

    def setUp(self):
        self.basis = Welt().csv_bytes()
        self.alt = N.CsvDatei(self.basis)

    def plan(self):
        """Eine Verlegung der dritten Datenzeile und eine Ergänzung (so, wie plane() sie erzeugt)."""
        f = self.alt.felder(3)
        neu = list(f)
        neu[N.I_ZEIT] = '09:09'
        verlegt = N.Aenderung('verlegt', f[0], f[0], (f[1], int(f[2])), neu, alt=f, idx=3, datum='2026-11-01', zeit='09:09')
        ergaenzt = N.Aenderung('ergaenzt', 'D1', 'D1', (LIGA['D1'], 99),
                               ['D1', LIGA['D1'], '99', '29.11.2026', '13:00', EIGEN['D1'], 'FC Neu 1', '', '', '', 'Meisterschaft'], datum='2026-11-29', zeit='13:00')
        return [verlegt, ergaenzt]

    def neu_aus(self, daten):
        return N.CsvDatei(daten)

    def test_richtig_gebaute_datei_ist_in_ordnung(self):
        plan = self.plan()
        self.assertEqual(N.formtreue_pruefen(self.alt, N.CsvDatei(N.neue_datei(self.alt, plan)), plan), [])
        self.assertEqual(N.formtreue_pruefen(self.alt, self.alt, []), [])

    def test_bom_zustand_geaendert(self):
        self.assertEqual(N.formtreue_pruefen(self.alt, self.neu_aus(b'\xef\xbb\xbf' + self.basis), []), ['BOM-Zustand geändert'])

    def test_zeilenende_geaendert(self):
        self.assertEqual(N.formtreue_pruefen(self.alt, self.neu_aus(self.basis.replace(b'\r\n', b'\n')), []), ['Zeilenende geändert'])
        self.assertEqual(N.formtreue_pruefen(self.alt, self.neu_aus(self.basis[:-2]), []), ['Zeilenende geändert'], 'das letzte Zeilenende fehlt')

    def test_kopfzeile_geaendert(self):
        neu = self.neu_aus(self.basis)
        neu.zeilen[0] = 'a;b;c'
        self.assertEqual(N.formtreue_pruefen(self.alt, neu, []), ['Kopfzeile geändert', 'unveränderte Zeilen weichen ab'])

    def test_zeilenzahl_und_unveraenderte_zeilen(self):
        mehr = self.neu_aus(self.basis + b'HERREN;341727;999;01.12.2026;11:00;X;Y;;;;Meisterschaft\r\n')
        probleme = N.formtreue_pruefen(self.alt, mehr, [])
        self.assertEqual(probleme, ['Zeilenzahl %d statt %d' % (len(self.alt.zeilen) + 1, len(self.alt.zeilen)), 'unveränderte Zeilen weichen ab'])
        weniger = N.CsvDatei(self.basis)
        weniger.zeilen.pop(5)
        self.assertIn('Zeilenzahl %d statt %d' % (len(self.alt.zeilen) - 1, len(self.alt.zeilen)), N.formtreue_pruefen(self.alt, weniger, []))

    def test_eine_nicht_geplante_zeile_wurde_geaendert(self):
        neu = self.neu_aus(self.basis)
        self.assertIn(';11:00;', neu.zeilen[2])
        neu.zeilen[2] = neu.zeilen[2].replace(';11:00;', ';11:01;', 1)
        self.assertEqual(N.formtreue_pruefen(self.alt, neu, []), ['unveränderte Zeilen weichen ab'])

    def test_eine_geplante_zeile_steht_anders_als_geplant_da(self):
        plan = self.plan()
        neu = N.CsvDatei(N.neue_datei(self.alt, plan))
        i = next(i for i, z in enumerate(neu.zeilen) if z.startswith('D1;%s;99;' % LIGA['D1']))
        neu.zeilen[i] = neu.zeilen[i].replace(';13:00;', ';13:05;')
        self.assertEqual(N.formtreue_pruefen(self.alt, neu, plan), ['unveränderte Zeilen weichen ab'])

    def test_loeschfreiheit(self):
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, self.alt), [])
        plus = self.neu_aus(self.basis + b'HERREN;341727;999;01.12.2026;11:00;X;Y;;;;Meisterschaft\r\n')
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, plus), [], 'Zuwachs ist erlaubt')
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, self.neu_aus(b'\xef\xbb\xbf' + self.basis)), ['BOM oder Zeilenende geändert'])
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, self.neu_aus(self.basis.replace(b'\r\n', b'\n'))), ['BOM oder Zeilenende geändert'])
        kopf = self.neu_aus(self.basis)
        kopf.zeilen[0] = 'a;b'
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, kopf), ['Kopfzeile geändert'])
        eine = self.neu_aus(self.basis)
        eine.zeilen.pop(4)
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, eine), ['1 Zeile fehlt (dieses Werkzeug löscht nie)'])
        zwei = self.neu_aus(self.basis)
        del zwei.zeilen[4:6]
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, zwei), ['2 Zeilen fehlen (dieses Werkzeug löscht nie)'])
        verschoben = self.neu_aus(self.basis)
        verschoben.zeilen[4:6] = [verschoben.zeilen[5], verschoben.zeilen[4]]
        self.assertEqual(N.loeschfreiheit_pruefen(self.alt, verschoben), [], 'eine andere Reihenfolge löscht nichts')


# Ein Ersatz für build_ics.py: schreibt je Zeile einen Termin (UID Staffel-Nr, Start, Titel) und lässt sich gezielt falsch einstellen.
FAKE_BAU = '''
import csv, os
MODUS = "__MODUS__"
CRLF = chr(13) + chr(10)
os.makedirs("docs", exist_ok=True)
zeilen = [z for z in csv.DictReader(open("spiele.csv", encoding="utf-8", newline=""), delimiter=";") if z["spielnr"]]


def ereignis(z):
    ab = z["ergebnis"] == "abgesagt"
    if MODUS == "immer_abgesagt":
        ab = True
    if MODUS == "nie_abgesagt":
        ab = False
    d = z["datum"].split(".")
    zeit = "2359" if MODUS == "start_falsch" else z["zeit"].replace(":", "")
    titel = ("abgesagt: " if ab else "") + z["heim"] + " gegen " + z["gast"]
    return CRLF.join(["BEGIN:VEVENT", "UID:%s-%s@speuzer.sportfreunde04.de" % (z["staffel"], z["spielnr"]),
                      "DTSTART;TZID=Europe/Berlin:%s%s%sT%s00" % (d[2], d[1], d[0], zeit), "SUMMARY:" + titel, "END:VEVENT"]) + CRLF


def kalender(pfad, liste):
    with open(pfad, "w", encoding="utf-8", newline="") as f:
        f.write("BEGIN:VCALENDAR" + CRLF + "".join(ereignis(z) for z in liste) + "END:VCALENDAR" + CRLF)


def ohne_40(liste):
    return [z for z in liste if z["spielnr"] != "40"]


alle = ohne_40(zeilen) if MODUS in ("alle_ohne_40", "ohne_40") else zeilen
kalender("docs/alle.ics", alle)
for team in sorted({z["team"] for z in zeilen}):
    liste = [z for z in zeilen if z["team"] == team]
    kalender("docs/%s.ics" % team.lower(), ohne_40(liste) if MODUS in ("team_ohne_40", "ohne_40") else liste)
'''


class TestValidierung(TestBasis):
    """Die ICS-Gegenprobe vor dem Schreiben: jede Art von Fehler im gebauten Kalender muss die Änderung stoppen. Als Generator dient
    ein Ersatz für build_ics.py (FAKE_BAU), der sich gezielt falsch einstellen lässt."""

    def ergaenzt(self, w):
        neues_spiel(w)

    def verlegt(self, w):
        w.welt.spiel(w.welt.seite, 'D1', 10).zeit = '13:30'
        w.cache_neu()

    def abgesagt(self, w):
        w.welt.spiel(w.welt.seite, 'D1', 10).status = 'Absetzung'
        w.cache_neu()

    def wieder_angesetzt(self, w):
        w.csv_abgesagt_setzen('D1', 10)

    def probleme(self, modus, szenario):
        w = self.werkstatt()
        szenario(w)
        with open(os.path.join(w.repo, 'build_ics.py'), 'w', encoding='utf-8') as f:
            f.write(FAKE_BAU.replace('__MODUS__', modus))
        e = w.lauf()                                       # Berichtsmodus: plant nur
        self.assertEqual(len(e['aenderungen']), 1, e['schluss'])
        datei = N.CsvDatei(w.csv_lesen())
        return N.validiere(datei.original, N.neue_datei(datei, e['aenderungen']), e['aenderungen'], w.repo)

    def test_der_ersatz_selbst_ist_in_ordnung(self):
        for szenario in (self.ergaenzt, self.verlegt, self.abgesagt, self.wieder_angesetzt):
            with self.subTest(szenario.__name__):
                self.assertEqual(self.probleme('ok', szenario), [])

    def test_gesamtfeed_hat_zu_wenig_termine(self):
        probleme = self.probleme('alle_ohne_40', self.ergaenzt)
        self.assertEqual(len(probleme), 2, probleme)
        self.assertRegex(probleme[0], r'^Gesamtfeed alle\.ics hat \d+ statt \d+ Termine$')
        self.assertEqual(probleme[1], 'Termin %s·40 fehlt im neuen Feed oder war schon vergeben' % LIGA['D1'], 'der Termin fehlt im Gesamtfeed')

    def test_mannschaftskalender_hat_zu_wenig_termine(self):
        probleme = self.probleme('team_ohne_40', self.ergaenzt)
        self.assertEqual(len(probleme), 1, probleme)
        self.assertRegex(probleme[0], r'^Kalender d1\.ics hat \d+ statt \d+ Termine$')

    def test_ergaenzter_termin_fehlt_im_neuen_feed(self):
        probleme = self.probleme('ohne_40', self.ergaenzt)
        self.assertIn('Termin %s·40 fehlt im neuen Feed oder war schon vergeben' % LIGA['D1'], probleme)
        self.assertEqual(len(probleme), 3, probleme)

    def test_verlegter_termin_hat_nicht_den_neuen_start(self):
        self.assertEqual(self.probleme('start_falsch', self.verlegt), ['Termin %s·10 hat im neuen Feed nicht den neuen Start' % LIGA['D1']])

    def test_wieder_angesetzter_termin_hat_nicht_den_neuen_start(self):
        self.assertEqual(self.probleme('start_falsch', self.wieder_angesetzt), ['Termin %s·10 hat im neuen Feed nicht den neuen Start' % LIGA['D1']])

    def test_abgesagter_termin_steht_nicht_als_abgesagt_im_feed(self):
        self.assertEqual(self.probleme('nie_abgesagt', self.abgesagt), ['Termin %s·10 steht im neuen Feed nicht als abgesagt' % LIGA['D1']])

    def test_wieder_angesetzter_termin_steht_noch_als_abgesagt_im_feed(self):
        self.assertEqual(self.probleme('immer_abgesagt', self.wieder_angesetzt), ['Termin %s·10 steht im neuen Feed noch als abgesagt' % LIGA['D1']])

    def test_fehler_der_gegenprobe_stoppen_den_lauf_ohne_zu_schreiben(self):
        w = self.werkstatt()
        neues_spiel(w)
        with open(os.path.join(w.repo, 'build_ics.py'), 'w', encoding='utf-8') as f:
            f.write(FAKE_BAU.replace('__MODUS__', 'alle_ohne_40'))
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['code'], 1)
        self.assertRegex(e['schluss'], r'^NACHTRAG: Fehler – Prüfung vor dem Schreiben fehlgeschlagen, nichts geschrieben: Gesamtfeed alle\.ics hat \d+ statt \d+ Termine; '
                                       r'Termin %s·40 fehlt im neuen Feed oder war schon vergeben$' % LIGA['D1'])

    def test_unerwartete_form_und_nicht_mehr_lesbare_csv(self):
        w = self.werkstatt()
        alt = w.csv_lesen()
        self.assertEqual(N.validiere(alt, b'kaputt', [], w.repo), ['neue CSV hat unerwartete Form: Kopfzeile weicht ab (erwartet: %s)' % ';'.join(N.SPALTEN)])
        schlecht = alt + b'D1;%s;77;99.99.2026;11:00;A;B;;;;Meisterschaft\r\n' % LIGA['D1'].encode()
        probleme = N.validiere(alt, schlecht, None, w.repo)
        self.assertEqual(len(probleme), 1, probleme)
        self.assertTrue(probleme[0].startswith('CSV nicht mehr lesbar: Zeile '), probleme)

    def test_ohne_plan_gilt_keine_zeile_geloescht_und_der_zuwachs_steht_in_der_csv(self):
        w = self.werkstatt()
        alt = w.csv_lesen()
        mehr = alt + ('D1;%s;77;29.11.2026;13:00;%s;FC Neu 1;;;;Meisterschaft\r\n' % (LIGA['D1'], EIGEN['D1'])).encode()
        self.assertEqual(N.validiere(alt, mehr, None, w.repo), [])
        weniger = N.CsvDatei(alt)
        weniger.zeilen.pop(3)
        self.assertEqual(N.validiere(alt, weniger.bytes_aus(weniger.zeilen), None, w.repo), ['1 Zeile fehlt (dieses Werkzeug löscht nie)'])

    def test_bauen_ohne_generator_oder_mit_abgestuerztem_prozess(self):
        w = self.werkstatt()
        leer = os.path.join(w.dir, 'leer')
        ziel = os.path.join(w.dir, 'ziel')
        os.makedirs(leer)
        os.makedirs(ziel)
        self.assertEqual(N.bauen(leer, w.csv_lesen(), ziel), (1, 'build_ics.py fehlt in %s' % leer))
        with mock.patch.object(subprocess, 'run', side_effect=OSError('kaputt')):
            self.assertEqual(N.bauen(w.repo, w.csv_lesen(), ziel), (1, 'OSError: kaputt'))
        self.assertEqual(N.bauen(w.repo, w.csv_lesen(), ziel)[0], 0)


# ---------------------------------------------------------------- B05: Kleinteile, Fehlerzweige, Schreiben und Gegenlesen

class TestKleinteile(TestBasis):
    def test_unbekannte_option(self):
        with self.assertRaises(TypeError):
            N.Optionen(gibt_es_nicht=1)

    def test_csv_ohne_utf8_und_csv_nur_mit_cr(self):
        kopf = ';'.join(N.SPALTEN)
        faelle = (('Latin-1', (kopf + '\r\nD1;1;1;01.11.2026;11:00;Müller;x;;;;Meisterschaft\r\n').encode('latin-1'), 'keine gültige UTF-8-Datei'),
                  ('nur CR', Welt().csv_bytes().replace(b'\r\n', b'\r'), 'Zeilenenden nur CR'))
        for name, daten, text in faelle:
            with self.subTest(name):
                w = self.werkstatt()
                with open(w.csv, 'wb') as f:
                    f.write(daten)
                e = w.anwenden()
                self.pruefe_unveraendert(w, daten)
                self.assertTrue(e['schluss'].startswith('NACHTRAG: Fehler – spiele.csv nicht lesbar oder unerwartete Form (%s' % text), e['schluss'])
                self.assertEqual(e['code'], 1)

    def test_atomar_schreiben_raeumt_die_hilfsdatei_auf(self):
        w = self.werkstatt()
        vorher = w.csv_lesen()
        with mock.patch.object(os, 'replace', side_effect=OSError('Platte voll')), self.assertRaises(OSError):
            N.atomar_schreiben(w.csv, b'neu')
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual([n for n in os.listdir(w.repo) if n.endswith('.tmp')], [])
        N.atomar_schreiben(w.csv, b'neu')
        self.assertEqual(w.csv_lesen(), b'neu')

    def test_gegenlesen(self):
        w = self.werkstatt()
        N.gegenlesen(w.csv, w.csv_lesen())
        with self.assertRaises(N.LaufFehler) as ctx:
            N.gegenlesen(w.csv, b'etwas anderes')
        self.assertEqual(str(ctx.exception), 'spiele.csv weicht nach dem Schreiben vom geprüften Stand ab')

    def test_abweichende_platte_nach_dem_schreiben_stellt_das_original_wieder_her(self):
        w = self.werkstatt()
        neues_spiel(w)
        vorher = w.csv_lesen()
        echt = N.atomar_schreiben
        aufrufe = []

        def abgeschnitten(pfad, daten):
            aufrufe.append(len(daten))
            echt(pfad, daten[:len(daten) // 2] if len(aufrufe) == 1 else daten)      # das erste Mal landet nur die Hälfte auf der Platte
        with mock.patch.object(N, 'atomar_schreiben', side_effect=abgeschnitten):
            e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(len(aufrufe), 2)
        self.assertEqual(e['code'], 1)
        self.assertIn('spiele.csv weicht nach dem Schreiben vom geprüften Stand ab – spiele.csv wiederhergestellt, nichts committet', e['schluss'])
        self.assertFalse(e['angewendet'])

    def test_wiederherstellung_scheitert_ebenfalls(self):
        w = self.werkstatt()
        neues_spiel(w)
        with mock.patch.object(N, 'gegenlesen', side_effect=N.LaufFehler('Platte kaputt')):
            e = w.anwenden()
        self.assertEqual(e['code'], 1)
        self.assertIn('Wiederherstellung fehlgeschlagen (Platte kaputt) – spiele.csv sofort prüfen!', e['schluss'])

    def test_aenderungen_nicht_anwendbar(self):
        w = self.werkstatt()
        neues_spiel(w)
        vorher = w.csv_lesen()
        with mock.patch.object(N, 'neue_datei', side_effect=ValueError('kaputt')):
            e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual((e['code'], e['schluss']), (1, 'NACHTRAG: Fehler – Änderungen nicht anwendbar, nichts geschrieben (ValueError: kaputt)'))

    def test_berichtsdatei_nicht_schreibbar_ist_nur_ein_hinweis_auf_stderr(self):
        w = self.werkstatt()
        neues_spiel(w)
        code, zeilen, err = main_aufrufen(argv_fuer(w)[:-2] + ['--bericht-ordner', os.path.join(w.csv, 'unterordner')])
        self.assertEqual(code, 0)
        self.assertEqual(zeilen[-1], 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')
        self.assertTrue(err.startswith('Hinweis: Berichtsdatei nicht geschrieben ('), err)

    def test_berichtsdatei_mit_stillen_notizen_und_quellen(self):
        w = self.werkstatt()
        neues_spiel(w)
        main_aufrufen(argv_fuer(w, '--anwenden'))
        dateien = os.listdir(w.berichte)
        self.assertEqual(len(dateien), 1)
        with open(os.path.join(w.berichte, dateien[0]), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('Modus: Anwenden', text)
        self.assertIn('## Quellen', text)
        self.assertIn('- D1: Cache-Stand 26.10.2026 05:10', text)

    def test_seite_lesen_meldet_eine_defekte_seite_als_grund(self):
        self.assertEqual(N.seite_lesen('<html>x</html>'), ([], 'Seite zu kurz (14 Zeichen)'))
        self.assertEqual(N.seite_lesen(SPERRSEITE), ([], 'kein Spielplan erkannt (Seitenaufbau geändert oder Sperrseite)'))
        self.assertEqual(N.seite_lesen(seite_aus([], zusatz='<!-- ' + 'Filterformular und Legende ' * 20 + '-->')), ([], None), 'ein gültiger leerer Plan ist kein Defekt')

    def test_plane_meldet_ein_spiel_ohne_heim_oder_gast_statt_es_zu_ergaenzen(self):
        """Der Parser macht aus so einem Eintrag „spielfrei“; plane prüft trotzdem selbst (Absicherung gegen eine spätere Änderung des Parsers)."""
        ctx = dict(konv=N.Konventionen([]), datei=N.CsvDatei(Welt().csv_bytes()), bis='2027-06-30', neu=set(), neue_staffeln=set(), schluessel=set())
        g = dict(quelle='fussball.de', datum='2026-11-29', zeit='13:00', wettbewerb='Kreisliga A', code='ME', art='Meisterschaft', staffel=LIGA['D1'], nr=40,
                 heim=EIGEN['D1'], gast='', ha='H', gegner='', status='angesetzt', status_txt='', hinweise=[], fbid='X' * 32, vorlaeufig=False)
        p = N.plane(A.TEAMS[2], [g], [], HEUTE, ctx)
        self.assertEqual((p.aenderungen, p.hinweise), ([], ['D1 So 29.11. 13:00 · Heim gegen : Heim oder Gast nicht gelesen: nicht ergänzt']))

    def test_nur_die_zwoelf_neuesten_cache_staende_zaehlen(self):
        w = self.werkstatt()
        for i in range(15):
            cache_datei(w, 'D1', STAND - timedelta(hours=1 + i), seite_aus(w.welt.seite['D1']))
        staende = N.staende(w.cache, 'd1')
        self.assertEqual(len(staende), 12)
        self.assertEqual(staende[0][1], STAND, 'neueste zuerst')
        self.assertEqual(len(N.staende(w.cache, 'd1', grenze=3)), 3)

    def test_quarantaene_ohne_die_datei_ist_kein_fehler(self):
        w = self.werkstatt()
        N.quarantaene(w.cache, 'd1', JETZT)                           # die Datei gibt es nicht: nichts passiert, nichts bricht
        self.assertFalse(os.path.exists(os.path.join(w.cache, 'd1', 'fehlversuch.txt')))

    def test_main_in_einem_nebenthread_laeuft_ohne_signalbehandlung(self):
        w = self.werkstatt()
        ergebnis = []
        faden = threading.Thread(target=lambda: ergebnis.append(main_aufrufen(argv_fuer(w))))
        faden.start()
        faden.join(60)
        self.assertEqual(ergebnis, [(0, ['NACHTRAG: unverändert'], '')])

    def test_git_meldungen_ohne_zugangsdaten_und_nur_mit_den_wichtigen_zeilen(self):
        self.assertEqual(N.schwaerzen('fatal: https://nutzer:geheim@github.com/x/y.git abgelehnt'), 'fatal: https://***@github.com/x/y.git abgelehnt')
        text = 'hint: Updates were rejected\nTo https://github.com/x/y.git\n ! [remote rejected] main -> main (denied)\nerror: failed to push some refs to x'
        self.assertEqual(N.letzte_zeile(text), '! [remote rejected] main -> main (denied)')
        self.assertEqual(N.letzte_zeile('', 'Code 1'), 'Code 1')
        self.assertEqual(N.letzte_zeile('irgendwas Unklares'), 'irgendwas Unklares')
        self.assertEqual(len(N.letzte_zeile('fatal: ' + 'x' * 500)), 200)

    def test_ausgabe_hat_nur_bekannte_zeilenarten_und_keine_adressen_oder_nummern(self):
        """Ein Lauf mit allen Arten von Ausgabe (ergänzt, verlegt, abgesagt, wieder angesetzt, Hinweis, Fehler): jede Zeile ist eine
        bekannte Art, und nichts darin sieht nach Telefonnummer, E-Mail-Adresse oder Link aus."""
        w = self.werkstatt()
        neues_spiel(w)
        w.welt.spiel(w.welt.seite, 'D2', 10).zeit = '13:30'
        w.welt.spiel(w.welt.seite, 'D3', 10).status = 'Absetzung'
        w.csv_abgesagt_setzen('E1', 10)
        w.welt.seite['E2'].append(Spiel('E2', POKAL['E2'], 7, '2026-12-02', '18:15', EIGEN['E2'], 'Pokal FC 2', 'Kreispokal', wettbewerb_text='Hessenpokal'))
        w.cache_neu(ohne=('F1',))
        for modus in ({}, {'anwenden': True}):
            e = w.lauf(**modus)
            text = '\n'.join(e['zeilen'] + [e['schluss']])
            arten = ('Vorschlag ', 'ergänzt: ', 'verlegt: ', 'abgesagt: ', 'wieder angesetzt: ', '    CSV Zeile ', 'Hinweis: ', 'Fehler: ', 'NACHTRAG: ')
            for zeile in e['zeilen'] + [e['schluss']]:
                self.assertTrue(zeile.startswith(arten), zeile)
            self.assertGreaterEqual(len(e['zeilen']), 6)
            ohne_adressen = text.replace(HEIMORT['D1'], '').replace(GASTORT, '')
            self.assertIsNone(re.search(r'\+?\d[\d /-]{8,}\d|@|https?://|www\.', ohne_adressen), ohne_adressen)


# ---------------------------------------------------------------- AP-1: die D4 als Zusatzquelle der D3

def d4_spiel(nr=143, datum='2026-11-29', zeit='11:30', gast='SV 09 Hofheim 2', **kw):
    """Ein Spiel der D4: bei fussball.de heißt unsere Mannschaft „FFV Sportfreunde 04 (FS) IV“, in der CSV steht es als D3
    (Freundschaftsspiel in der Staffel der D3-Freundschaftsspiele, Heimspiel auf dem Vereinsplatz)."""
    return Spiel('D3', FREUND['D3'], nr, datum, zeit, EIGEN_D4, gast, 'Freundschaftsspiel', ort=HEIMORT['D3'], **kw)


def d4_im_plan(w, *spiele):
    """Spiele, die nur im Plan der D4 stehen (die CSV kennt sie nicht); der Cache wird neu geschrieben."""
    w.welt.zusatz[D4_SLUG] += spiele
    w.cache_neu()


def d4_in_csv_und_plan(w, *spiele):
    """Spiele in der CSV (als D3) und im Plan der D4; CSV und Cache werden neu geschrieben."""
    for s in spiele:
        w.welt.beide_zusatz(D4_SLUG, s)
    csv_schreiben(w)
    w.cache_neu()


def d3_neues_spiel(w, nr=40, datum='2026-11-29', zeit='13:00', gast='FC Neu 1'):
    """Ein Meisterschaftsspiel nur im eigenen Plan der D3 (die CSV kennt es nicht); den Cache schreibt der Aufrufer neu."""
    s = Spiel('D3', LIGA['D3'], nr, datum, zeit, EIGEN['D3'], gast)
    w.welt.seite['D3'].append(s)
    return s


D4_ERGAENZT = 'ergänzt: D3 So 29.11. 11:30 Freundschaftsspiel, Heim gegen SV 09 Hofheim 2'
D4_PAARUNG = 'Freundschaftsspiel, Heim gegen SV 09 Hofheim 2'


class TestZusatzquellenKonfiguration(unittest.TestCase):
    def test_teams_bleibt_und_die_d4_ist_die_zusatzquelle_der_d3(self):
        self.assertEqual(len(A.TEAMS), 11)
        self.assertEqual(A.ZUSATZQUELLEN, [('D3', 'd4', 'D4 (Freundschaftsspiele der D3)', '0316LKDK8S000000VS5489BRVVV10ESU')])
        d3 = next(t for t in A.TEAMS if t[0] == 'D3')
        self.assertEqual(A.quellen(d3), [d3, A.ZUSATZQUELLEN[0]], 'der eigene Plan zuerst')
        for t in A.TEAMS:
            self.assertIs(A.quellen(t)[0], t)
            if t[0] != 'D3':
                self.assertEqual(A.quellen(t), [t])

    def test_zusatzquellen_haben_ein_kuerzel_aus_teams_und_eigenen_slug_und_eigene_id(self):
        for q in A.ZUSATZQUELLEN:
            self.assertEqual(len(q), 4)
            self.assertIn(q[0], TEAMS)
            self.assertNotIn(q[1], [t[1] for t in A.TEAMS])
            self.assertNotIn(q[3], [t[3] for t in A.TEAMS])
        self.assertEqual(len({q[1] for q in A.ZUSATZQUELLEN}), len(A.ZUSATZQUELLEN), 'Slugs eindeutig')
        self.assertEqual(len({q[3] for q in A.ZUSATZQUELLEN}), len(A.ZUSATZQUELLEN), 'Team-IDs eindeutig')


class TestZusatzquelleNachtrag(TestBasis):
    def zeile(self, w, nr, staffel=None, team='D3'):
        zeilen = zeilen_von(w.csv_lesen())
        return next(i for i, z in enumerate(zeilen) if z.startswith('%s;%s;%d;' % (team, staffel or FREUND['D3'], nr)))

    # (a) ein neues, kommendes Freundschaftsspiel der D4 wird als D3-Zeile ergänzt
    def test_neues_d4_spiel_wird_als_d3_ergaenzt(self):
        w = self.werkstatt()
        for s in w.welt.csv['D3']:                    # der Heimort kommt aus den Heimzeilen der D3, nicht aus einem festen Wert
            if s.ort == HEIMORT['D3']:
                s.ort = 'Sonderplatz 5, 60000 Frankfurt am Main'
        csv_schreiben(w)
        s = d4_spiel(143, '2026-11-29', '11:30')
        d4_im_plan(w, s)
        vorher = w.csv_lesen()
        e = w.anwenden()
        zeilen = zeilen_von(vorher)
        letzte = max(i for i, z in enumerate(zeilen) if z.startswith('D3;%s;' % FREUND['D3']))
        neue = 'D3;%s;143;29.11.2026;11:30;%s;SV 09 Hofheim 2;Sonderplatz 5, 60000 Frankfurt am Main;;%s;Freundschaftsspiel' % (FREUND['D3'], EIGEN_D4, s.fbid)
        self.assertNurDiese(vorher, w.csv_lesen(), eingefuegt={letzte: [neue]})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht')
        self.assertEqual(e['zeilen'], [D4_ERGAENZT])
        self.assertEqual((e['hinweise'], e['fehler'], e['uebersprungen']), ([], [], []))
        self.assertEqual(N.validiere(vorher, w.csv_lesen(), e['aenderungen'], w.repo), [])
        self.assertEqual(e['stand'][D4_NAME], STAND, 'die D4 steht mit ihrem Stand unter den Quellen')
        self.assertEqual(w.anwenden()['schluss'], 'NACHTRAG: unverändert', 'ein zweiter Lauf ändert nichts')

    def test_neues_d4_spiel_im_berichtsmodus_zeigt_die_csv_zeile_und_schreibt_nichts(self):
        w = self.werkstatt()
        s = d4_spiel(143, '2026-11-29', '11:30')
        d4_im_plan(w, s)
        vorher = w.csv_lesen()
        e = w.lauf()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['zeilen'][0], 'Vorschlag ' + D4_ERGAENZT)
        self.assertTrue(e['zeilen'][1].startswith('    CSV Zeile ') and e['zeilen'][1].endswith(
            ';'.join(['D3', FREUND['D3'], '143', '29.11.2026', '11:30', EIGEN_D4, 'SV 09 Hofheim 2', HEIMORT['D3'], '', s.fbid, 'Freundschaftsspiel'])), e['zeilen'][1])
        self.assertEqual(e['schluss'], 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')

    # (b) D4-Zeilen schon in der CSV und passender D4-Plan: kein Fehlalarm
    def test_d4_zeilen_in_der_csv_mit_passendem_plan_sind_kein_fehlalarm(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'), d4_spiel(299, '2026-12-06', '10:00', 'FC Hellas 1'))
        vorher = w.csv_lesen()
        for modus in ({}, {'anwenden': True}):
            e = w.lauf(**modus)
            self.assertEqual((e['zeilen'], e['hinweise'], e['fehler'], e['schluss'], e['code']), ([], [], [], 'NACHTRAG: unverändert', 0))
            self.assertFalse([t for t in e['hinweise'] if 'nur in der CSV' in t])
        self.pruefe_unveraendert(w, vorher)

    def test_vergangene_d4_zeilen_erzeugen_ebenfalls_keinen_hinweis(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-09-13', '14:30', 'Hellas Rüsselsheim 1'))
        e = w.anwenden()
        self.assertEqual((e['hinweise'], e['stumm'], e['schluss']), ([], [], 'NACHTRAG: unverändert'))

    # (c) kein D4-Cache: die D3 läuft mit ihrem eigenen Plan weiter
    def test_ohne_d4_cache_werden_die_d3_aenderungen_trotzdem_geschrieben(self):
        w = self.werkstatt()
        s = d3_neues_spiel(w)
        w.welt.spiel(w.welt.seite, 'D3', 10).zeit = '13:30'
        w.cache_neu(ohne=(D4_SLUG,))
        vorher = w.csv_lesen()
        zeilen = zeilen_von(vorher)
        i10 = self.zeile(w, 10, LIGA['D3'])
        letzte = max(i for i, z in enumerate(zeilen) if z.startswith('D3;%s;' % LIGA['D3']))
        neue = 'D3;%s;40;29.11.2026;13:00;%s;FC Neu 1;%s;;%s;Meisterschaft' % (LIGA['D3'], EIGEN['D3'], HEIMORT['D3'], s.fbid)
        b = w.lauf()
        self.assertEqual((len(b['aenderungen']), b['hinweise']), (2, [D4_NAME + ': kein Cache-Stand vorhanden; ihre Spiele sind in diesem Lauf nicht geprüft']))
        e = w.anwenden()
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i10: zeilen[i10].replace(';01.11.2026;11:00;', ';01.11.2026;13:30;')}, eingefuegt={letzte: [neue]})
        self.assertEqual(e['schluss'], 'NACHTRAG: 2 geändert (1 ergänzt, 1 verlegt), nicht gepusht, 1 Hinweis')
        self.assertEqual(e['hinweise'], [D4_NAME + ': kein Cache-Stand vorhanden; ihre Spiele sind in diesem Lauf nicht geprüft'])
        self.assertEqual((e['fehler'], e['uebersprungen'], e['code']), ([], [], 0))
        self.assertNotIn(D4_NAME, e['stand'])

    def test_ohne_d4_cache_bleiben_die_d4_zeilen_der_csv_unberuehrt_und_werden_nur_gemeldet(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'), d4_spiel(299, '2026-12-06', '10:00', 'FC Hellas 1'))
        w.cache_neu(ohne=(D4_SLUG,))
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'][0], D4_NAME + ': kein Cache-Stand vorhanden; ihre Spiele sind in diesem Lauf nicht geprüft')
        nur_csv = [h for h in e['hinweise'][1:] if h.endswith('nur in der CSV, bei fussball.de nicht (mehr) vorhanden: nicht gelöscht')]
        self.assertEqual((len(nur_csv), len(e['hinweise'])), (2, 3))
        self.assertEqual((e['uebersprungen'], e['fehler'], e['code']), ([], [], 2))

    def d4_stand_umstempeln(self, w, stunden_alt):
        """Der Cache-Stand der D4 bekommt ein anderes Alter (negativ: liegt in der Zukunft)."""
        os.rename(os.path.join(w.cache, D4_SLUG, STAND.strftime('%Y%m%d-%H%M%S') + '.html'),
                  os.path.join(w.cache, D4_SLUG, (JETZT - timedelta(hours=stunden_alt)).strftime('%Y%m%d-%H%M%S') + '.html'))

    def test_ausfallarten_der_d4_sind_nur_hinweise_nie_ein_ausfall_der_d3(self):
        faelle = (('Sperrseite', {'ersetze': {D4_SLUG: SPERRSEITE}}, None,
                   'kein brauchbarer Cache-Stand (26.10. 05:10: kein Spielplan erkannt (Seitenaufbau geändert oder Sperrseite))'),
                  ('kurze Seite', {'ersetze': {D4_SLUG: '<html>x</html>'}}, None, 'kein brauchbarer Cache-Stand (26.10. 05:10: Seite zu kurz (14 Zeichen))'),
                  ('Zukunft', {}, -3, 'Cache-Stand vom 26.10.2026 10:10 liegt in der Zukunft (Systemuhr falsch?)'),
                  ('veraltet', {}, 50, 'Cache-Stand vom 24.10. 05:10 ist 50 Stunden alt (Grenze 30)'))
        for name, cache_args, stunden, grund in faelle:
            with self.subTest(name):
                w = self.werkstatt()
                d3_neues_spiel(w)
                w.cache_neu(**cache_args)
                if stunden is not None:
                    self.d4_stand_umstempeln(w, stunden)
                e = w.anwenden()
                self.assertEqual(e['hinweise'], ['%s: %s; ihre Spiele sind in diesem Lauf nicht geprüft' % (D4_NAME, grund)])
                self.assertEqual((e['fehler'], e['uebersprungen'], e['angewendet']), ([], [], True))
                self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')

    def test_ist_der_neueste_d4_stand_unbrauchbar_zaehlt_der_aeltere_gute(self):
        w = self.werkstatt()
        d4_im_plan(w, d4_spiel(143, '2026-11-29', '11:30'))
        cache_datei(w, D4_SLUG, JETZT - timedelta(hours=1), SPERRSEITE)
        e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')
        self.assertTrue(e['hinweise'][0].startswith(D4_NAME + ': neuester Cache-Stand vom 26.10. 06:10 unbrauchbar ('), e['hinweise'])
        self.assertTrue(e['hinweise'][0].endswith('der Stand vom 26.10. 05:10 wird genommen'), e['hinweise'])
        self.assertEqual(e['stand'][D4_NAME], STAND)

    # (d) die D4-Seite nennt unseren Verein nicht
    def test_d4_seite_ohne_unseren_verein_wird_verworfen_die_d3_laeuft_weiter(self):
        w = self.werkstatt()
        d3_neues_spiel(w)
        fremd = [Spiel('D3', FREUND['D3'], 143, '2026-11-29', '11:30', 'FC Fremd 1', 'SV Fremd 2', 'Freundschaftsspiel')]
        w.cache_neu(ersetze={D4_SLUG: seite_aus(fremd, zusatz=FUELLTEXT)})
        e = w.anwenden()
        self.assertEqual(e['hinweise'], [D4_NAME + ': Quelle unplausibel (die Seite nennt unseren Verein nur in 0 von 1 Spielen (falsche Mannschaft?)); '
                                         'ihre Spiele sind in diesem Lauf nicht geprüft'])
        self.assertEqual((e['fehler'], e['uebersprungen'], e['angewendet']), ([], [], True))
        zeilen = zeilen_von(w.csv_lesen())
        self.assertTrue(any(z.startswith('D3;%s;40;' % LIGA['D3']) for z in zeilen), 'die eigene Ergänzung der D3 wurde geschrieben')
        self.assertFalse(any('Fremd' in z for z in zeilen), 'das fremde Spiel wurde nicht übernommen')

    def test_d4_seite_mit_defektem_aufbau_wird_verworfen(self):
        w = self.werkstatt()
        d3_neues_spiel(w)
        d4_im_plan(w, d4_spiel(143, '2026-11-29', '11:30'))
        echt = N.spiel_marken
        with mock.patch.object(N, 'spiel_marken', side_effect=lambda text: [] if EIGEN_D4 in text else echt(text)):
            e = w.anwenden()
        self.assertEqual(e['hinweise'], [D4_NAME + ': Quelle unplausibel (Seitenaufbau unerwartet (1 Spiele, 0 Spiel-Markierungen)); '
                                         'ihre Spiele sind in diesem Lauf nicht geprüft'])
        self.assertEqual((e['fehler'], e['uebersprungen']), ([], []))
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')

    # (e) Verlegung und Absetzung eines D4-Spiels
    def test_verlegung_eines_d4_spiels_wird_in_der_d3_zeile_nachgezogen(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.zusatz[D4_SLUG][0].zeit = '13:00'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile(w, 143)
        e = w.anwenden()
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: zeilen_von(vorher)[i].replace(';01.11.2026;11:30;', ';01.11.2026;13:00;')})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 verlegt), nicht gepusht')
        self.assertEqual(e['zeilen'], ['verlegt: D3 So 01.11. 11:30 → 13:00 · ' + D4_PAARUNG])

    def test_verlegung_eines_d4_spiels_auf_ein_anderes_datum(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.zusatz[D4_SLUG][0].datum = '2026-11-08'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile(w, 143)
        e = w.anwenden()
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: zeilen_von(vorher)[i].replace(';01.11.2026;11:30;', ';08.11.2026;11:30;')})
        self.assertEqual(e['zeilen'], ['verlegt: D3 So 01.11. 11:30 → So 08.11. 11:30 · ' + D4_PAARUNG])

    def test_absetzung_eines_d4_spiels_wird_als_abgesagt_in_der_d3_zeile_eingetragen(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.zusatz[D4_SLUG][0].status = 'Absetzung'
        w.cache_neu()
        vorher = w.csv_lesen()
        i = self.zeile(w, 143)
        e = w.anwenden()
        alt = zeilen_von(vorher)[i].split(';')
        alt[8] = 'abgesagt'
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(alt)})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 abgesagt), nicht gepusht')
        self.assertEqual(e['zeilen'], ['abgesagt: D3 So 01.11. 11:30 · ' + D4_PAARUNG + ' (bei fussball.de abgesetzt)'])

    # (f) Wiederansetzen eines D4-Spiels: nur mit früherer Absetzung im Verlauf der D4
    def test_wieder_ansetzen_eines_d4_spiels_mit_absetzung_im_d4_verlauf(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.spiel(w.welt.csv, 'D3', 143).ergebnis = 'abgesagt'
        csv_schreiben(w)
        alt = deepcopy(w.welt.zusatz[D4_SLUG])
        alt[0].status = 'Absetzung'
        w.frueherer_stand(D4_SLUG, alt)           # ein Stand von gestern zeigte das Spiel als abgesetzt, der heutige zeigt es wieder angesetzt
        vorher = w.csv_lesen()
        i = self.zeile(w, 143)
        e = w.anwenden()
        neu = zeilen_von(vorher)[i].split(';')
        neu[8] = ''
        self.assertNurDiese(vorher, w.csv_lesen(), ersetzt={i: ';'.join(neu)})
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')
        self.assertEqual(e['zeilen'], ['wieder angesetzt: D3 So 01.11. 11:30 · ' + D4_PAARUNG])

    def test_wieder_ansetzen_eines_d4_spiels_ohne_verlauf_nur_hinweis(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.spiel(w.welt.csv, 'D3', 143).ergebnis = 'abgesagt'
        csv_schreiben(w)
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['hinweise'], ['D3 So 01.11. 11:30 · ' + D4_PAARUNG + ': in der CSV „abgesagt“, bei fussball.de angesetzt, '
                                         'aber dort früher nie abgesetzt (von Hand abgesagt?): nicht geändert'])
        self.assertTrue(e['schluss'].startswith('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis'))

    def test_der_verlauf_wird_je_plan_mit_dem_zeitstempel_seines_verwendeten_standes_geprueft(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.welt.spiel(w.welt.csv, 'D3', 143).ergebnis = 'abgesagt'
        csv_schreiben(w)
        alt = deepcopy(w.welt.zusatz[D4_SLUG])
        alt[0].status = 'Absetzung'
        w.frueherer_stand(D4_SLUG, alt, stunden=24)
        self.d4_stand_umstempeln(w, 3)               # der verwendete Stand der D4 ist 3 Stunden alt, der der D3 nur 2 (STAND): zwei verschiedene Grenzen
        d4_stand = JETZT - timedelta(hours=3)
        aufrufe = []
        echt = N.frueher_abgesetzt

        def spion(cache, slug, key, vor):
            aufrufe.append((slug, vor))
            return echt(cache, slug, key, vor)
        with mock.patch.object(N, 'frueher_abgesetzt', side_effect=spion):
            e = w.anwenden()
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 wieder angesetzt), nicht gepusht')
        self.assertEqual(aufrufe, [('d3', STAND), (D4_SLUG, d4_stand)], 'erst der eigene Plan, dann die Zusatzquelle, je mit dem Zeitstempel des eigenen Standes')

    # (g) veralteter D4-Cache im Anwendungsmodus
    def test_veralteter_d4_cache_im_anwendungsmodus_ist_ein_hinweis_die_d3_laeuft_weiter(self):
        w = self.werkstatt()
        d3_neues_spiel(w)
        w.cache_neu()
        os.rename(os.path.join(w.cache, D4_SLUG, STAND.strftime('%Y%m%d-%H%M%S') + '.html'),
                  os.path.join(w.cache, D4_SLUG, (JETZT - timedelta(hours=50)).strftime('%Y%m%d-%H%M%S') + '.html'))
        b = w.lauf()               # Berichtsmodus: der alte Stand wird genommen und nur vermerkt
        self.assertEqual(b['hinweise'], [D4_NAME + ': Cache-Stand vom 24.10. 05:10 ist 50 Stunden alt (Grenze 30); im Anwendungsmodus wären ihre Spiele nicht geprüft'])
        e = w.anwenden()
        self.assertEqual(e['hinweise'], [D4_NAME + ': Cache-Stand vom 24.10. 05:10 ist 50 Stunden alt (Grenze 30); ihre Spiele sind in diesem Lauf nicht geprüft'])
        self.assertEqual((e['uebersprungen'], e['fehler'], e['angewendet'], e['code']), ([], [], True, 0))
        self.assertEqual(e['schluss'], 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis')
        b2 = w.lauf(nur_cache=True)
        self.assertEqual(b2['hinweise'], [], 'mit --nur-cache gilt im Berichtsmodus keine Altersgrenze')

    # weitere Schutzregeln
    def test_faellt_der_eigene_plan_aus_bleibt_auch_die_d4_ungenutzt_und_unerwaehnt(self):
        w = self.werkstatt()
        d4_im_plan(w, d4_spiel(143, '2026-11-29', '11:30'))          # ein neues Spiel der D4, das nicht übernommen werden darf
        w.cache_neu(ersetze={'D3': SPERRSEITE})
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['uebersprungen'], ['D3'])
        self.assertEqual(len(e['fehler']), 1)
        self.assertFalse(any('D4' in t for t in e['hinweise'] + e['fehler'] + e['stumm']), (e['hinweise'], e['fehler'], e['stumm']))
        self.assertNotIn(D4_NAME, e['stand'])

    def test_ist_der_gesamtplan_unplausibel_wird_die_d3_uebersprungen_ohne_meldung_zur_d4(self):
        w = self.werkstatt()
        fremd = [Spiel('D3', LIGA['D3'], s.nr, s.datum, s.zeit, 'FC Fremd %d' % i, 'SV Fremd %d' % i, s.art, ort=s.ort) for i, s in enumerate(basis_spiele('D3'))]
        w.cache_neu(ersetze={'D3': seite_aus(fremd)}, ohne=(D4_SLUG,))
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['uebersprungen'], ['D3'])
        self.assertRegex(e['fehler'][0], r'^D3 übersprungen: Quelle unplausibel \(die Seite nennt unseren Verein nur in 0 von \d+ Spielen')
        self.assertFalse(any('D4' in t for t in e['hinweise'] + e['stumm']), (e['hinweise'], e['stumm']))

    def test_ist_der_eigene_plan_unlesbar_wird_die_d3_uebersprungen_auch_wenn_die_d4_passt(self):
        w = self.werkstatt()
        w.welt.beide('D3', Spiel('D3', LIGA['D3'], 41, '2026-12-13', '10:00', EIGEN['D3'], 'Nur Dreier FC 1'))      # nur auf der Seite der D3 zu finden
        csv_schreiben(w)
        d4_im_plan(w, d4_spiel(143, '2026-11-29', '11:30'))                  # ein neues Spiel der D4, das nicht übernommen werden darf
        vorher = w.csv_lesen()
        echt = N.spiel_marken
        with mock.patch.object(N, 'spiel_marken', side_effect=lambda text: [] if 'Nur Dreier FC 1' in text else echt(text)):
            e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['uebersprungen'], ['D3'])
        self.assertRegex(e['fehler'][0], r'^D3 übersprungen: Quelle unplausibel \(Seitenaufbau unerwartet')
        self.assertFalse(any('D4' in t for t in e['hinweise'] + e['stumm']), (e['hinweise'], e['stumm']))
        self.assertNotIn(D4_NAME, e['stand'])

    def test_die_plausibilitaet_gilt_dem_gesamtplan_aus_d3_und_d4(self):
        """Stehen viele D4-Zeilen in der CSV, erklärt erst der Plan der D4 sie: der eigene Plan allein hätte weniger als 60 % der
        Zeilen. Ohne den D4-Plan wird die D3, wie bei jedem unplausiblen Plan, übersprungen (nichts wird geändert oder gelöscht)."""
        w = self.werkstatt()
        d4_in_csv_und_plan(w, *[d4_spiel(200 + i, (date(2026, 11, 1) + timedelta(days=7 * i)).isoformat(), '11:30', 'FC Hellas %d' % i) for i in range(10)])
        e = w.anwenden()
        self.assertEqual((e['schluss'], e['fehler'], e['hinweise'], e['uebersprungen']), ('NACHTRAG: unverändert', [], [], []))
        w.cache_neu(ohne=(D4_SLUG,))
        vorher = w.csv_lesen()
        e2 = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e2['uebersprungen'], ['D3'])
        self.assertIn('D3 übersprungen: Quelle unplausibel (nur 6 Spiele gelesen, die CSV hat 16 Zeilen', e2['fehler'][0])

    def test_die_mengenbremse_zaehlt_d3_und_d4_zusammen(self):
        def welt(n_d3, n_d4):
            w = self.werkstatt()
            for i in range(n_d3):
                d3_neues_spiel(w, 40 + i, (date(2026, 11, 29) + timedelta(days=7 * i)).isoformat(), '11:00', 'FC Neu %d' % (40 + i))
            w.welt.zusatz[D4_SLUG] += [d4_spiel(143 + i, (date(2026, 12, 6) + timedelta(days=7 * i)).isoformat(), '11:30', 'FC Hellas %d' % i) for i in range(n_d4)]
            w.cache_neu()
            return w
        w = welt(3, 4)
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertEqual(e['schluss'], 'NACHTRAG: Auffällig, nicht übernommen – 7 Änderungen bei D3 in einem Lauf (Grenze 6 je Mannschaft)')
        w2 = welt(3, 3)
        e2 = w2.anwenden()
        self.assertEqual(e2['schluss'], 'NACHTRAG: 6 geändert (6 ergänzt), nicht gepusht')
        self.assertEqual(sum(1 for z in zeilen_von(w2.csv_lesen()) if z.startswith('D3;') and ';FC Hellas ' in z), 3)

    def test_gleiche_staffel_und_nummer_in_beiden_plaenen_wird_nicht_geaendert(self):
        """Dieselbe Staffel + Nummer im Plan der D3 und im Plan der D4: mehrdeutig, also blockiert (nur Hinweis, keine Änderung)."""
        w = self.werkstatt()
        d4_im_plan(w, d4_spiel(50, '2026-12-13', '10:00', 'FC Doppelt 1'))        # D3-Freundschaft Nr. 50 steht schon in der CSV
        vorher = w.csv_lesen()
        e = w.anwenden()
        self.pruefe_unveraendert(w, vorher)
        self.assertTrue(any('nicht eindeutig' in h for h in e['hinweise']), e['hinweise'])
        self.assertEqual(e['aenderungen'], [])

    def test_unerwartete_ausnahme_in_der_zusatzquelle_laesst_die_d3_weiterlaufen(self):
        w = self.werkstatt()
        d3_neues_spiel(w)
        w.cache_neu()
        echt = N.quelle_holen

        def kaputt(team, *a, **k):
            if team[1] == D4_SLUG:
                raise OSError('Absicht')
            return echt(team, *a, **k)
        with mock.patch.object(N, 'quelle_holen', side_effect=kaputt):
            e = w.anwenden()
        self.assertEqual(e['hinweise'], [D4_NAME + ': unerwarteter Fehler (OSError: Absicht); ihre Spiele sind in diesem Lauf nicht geprüft'])
        self.assertEqual((e['fehler'], e['uebersprungen'], e['angewendet']), ([], [], True))

    def test_ohne_kommende_csv_zeilen_ist_ein_fehlender_d4_cache_nur_eine_stille_notiz(self):
        w = self.werkstatt()
        w.cache_neu(ohne=(D4_SLUG,))
        e = w.anwenden(heute='2027-07-15')
        self.assertEqual((e['schluss'], e['code'], e['fehler'], e['hinweise']), ('NACHTRAG: unverändert', 0, [], []))
        self.assertIn(D4_NAME + ': keine kommenden Spiele in der CSV und kein Cache-Stand vorhanden, nichts zu tun', e['stumm'])

    def test_die_d4_wird_nie_abgerufen_auch_nicht_wenn_ihre_mannschaft_selbst_abruft(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'))
        w.cache_neu(stempel=JETZT - timedelta(hours=26))          # alle Pläne sind älter als 20 Stunden, aber noch nicht zu alt
        aufrufe = []

        def antwort(req, timeout=None):
            aufrufe.append(req.full_url)
            tid = req.full_url.rsplit('team-id/', 1)[1]
            team = next(t[0] for t in A.TEAMS if t[3] == tid)
            return FalscheAntwort(seite_aus(w.welt.seite[team]))
        with mock.patch.object(N, 'SELBST_ABRUFEN', ('G1', 'D3')), mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            e = w.anwenden(abrufen=True)
        self.assertEqual(len(aufrufe), 2, 'G1 und D3 (so konfiguriert), die D4 nie')
        self.assertFalse(any(A.ZUSATZQUELLEN[0][3] in url for url in aufrufe), aufrufe)
        self.assertEqual(e['hinweise'], [])
        with mock.patch.object(urllib.request, 'urlopen', side_effect=AssertionError('Abruf!')) as m:      # Standardbetrieb: nur die G-Jugend
            w.anwenden(abrufen=True, nur_cache=True)
            w.lauf(abrufen=True)
        self.assertEqual(m.call_count, 0)

    def test_die_d4_steht_unter_quellen_im_bericht(self):
        w = self.werkstatt()
        d4_im_plan(w, d4_spiel(143, '2026-11-29', '11:30'))
        main_aufrufen(argv_fuer(w, '--anwenden'))
        dateien = os.listdir(w.berichte)
        self.assertEqual(len(dateien), 1)
        with open(os.path.join(w.berichte, dateien[0]), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('- D3: Cache-Stand 26.10.2026 05:10', text)
        self.assertIn('- %s: Cache-Stand 26.10.2026 05:10' % D4_NAME, text)
        self.assertIn('- ' + D4_ERGAENZT, text)


class TestZusatzquelleAbgleich(TestBasis):
    """tools/abgleich.py: je Mannschaft alle Quellen (eigener Plan zuerst), Plan = alle gelesenen Quellen zusammen."""

    def abgleich(self, w, *extra, heute=HEUTE):
        out, err = io.StringIO(), io.StringIO()
        argv = ['abgleich.py', '--csv', w.csv, '--cache', w.cache, '--bericht-ordner', w.berichte, '--heute', heute, '--kein-wochenvergleich'] + list(extra)
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = A.main()
        with open(os.path.join(w.berichte, 'bericht-%s.md' % heute), encoding='utf-8') as f:
            bericht = f.read()
        return code, out.getvalue(), err.getvalue(), bericht

    @staticmethod
    def abschnitt(bericht, name):
        """Der Abschnitt „### <name> – …“ des Berichts bis zum nächsten Abschnitt."""
        kopf = '### %s – ' % name
        anfang = bericht.index(kopf)
        ende = min([i for i in (bericht.find('\n### ', anfang + 1), bericht.find('\n## ', anfang + 1)) if i >= 0] or [len(bericht)])
        return bericht[anfang:ende]

    @staticmethod
    def abrufe(bericht):
        return bericht.split('## Abrufe dieses Laufs', 1)[1].split('## Kontrolle', 1)[0]

    @staticmethod
    def tabellenzeile(text, name):
        """Die Zellen der Zeile „| <name> | …“ der Kurzfassung."""
        zeile = next(z for z in text.splitlines() if z.startswith('| %s |' % name))
        return [c.strip() for c in zeile.strip('|').split('|')]

    def d4_welt(self):
        w = self.werkstatt()
        d4_in_csv_und_plan(w, d4_spiel(143, '2026-11-01', '11:30'), d4_spiel(299, '2026-12-06', '10:00', 'FC Hellas 1'))
        return w

    # (h) Abgleich mit Wegwerf-Cache und --nur-cache
    def test_d4_zeilen_erscheinen_nicht_als_nur_in_der_csv_und_die_abrufe_nennen_die_d4(self):
        w = self.d4_welt()
        code, out, err, bericht = self.abgleich(w, '--nur-cache')
        self.assertEqual(code, 0, err)
        d3 = self.abschnitt(bericht, 'D3')
        self.assertNotIn('nur in der csv', d3.lower())
        self.assertIn('**keine Unterschiede**', d3.splitlines()[0])
        self.assertIn('Zusammengeführt aus 2 fussball.de-Plänen: D3 und %s.' % D4_NAME, d3)
        self.assertNotIn('Zusatzquelle ausgefallen', bericht)
        zeilen = self.tabellenzeile(out, 'D3')
        self.assertEqual(zeilen[3:6], ['0', '0', '0'], 'fehlt in CSV, nur in CSV, abweichend')
        self.assertIn('- %s: Cache 26.10.2026 05:10' % D4_NAME, self.abrufe(bericht).splitlines())
        self.assertIn('Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 12 aus dem Cache, 0 fehlgeschlagen', out)
        self.assertEqual(len([z for z in self.abrufe(bericht).splitlines() if z.startswith('- ')]), 12, 'elf Mannschaften und die D4')

    def test_ohne_d4_cache_nennt_der_d3_abschnitt_den_ausfall_und_der_exitcode_ist_2(self):
        w = self.d4_welt()
        w.cache_neu(ohne=(D4_SLUG,))
        code, out, err, bericht = self.abgleich(w, '--nur-cache')
        self.assertEqual(code, 2)
        d3 = self.abschnitt(bericht, 'D3')
        satz = ('**Zusatzquelle ausgefallen:** %s – kein Cache-Stand vorhanden (--nur-cache); ihre Spiele sind in diesem Lauf nicht verglichen '
                'und erscheinen deshalb als „nur in der CSV“.' % D4_NAME)
        self.assertIn(satz, d3.splitlines())
        self.assertNotIn('Zusammengeführt', d3)
        self.assertIn('Nur in der CSV (bei fussball.de nicht vorhanden)** (2)', d3)
        self.assertTrue('SV 09 Hofheim 2' in d3 and 'FC Hellas 1' in d3)
        self.assertIn('- %s: FEHLER (kein Cache-Stand vorhanden (--nur-cache))' % D4_NAME, self.abrufe(bericht).splitlines())
        self.assertIn('0 Abrufe, 11 aus dem Cache, 1 fehlgeschlagen', out)
        self.assertIn('**Zusatzquelle ausgefallen:** %s – kein Cache-Stand vorhanden (--nur-cache); ihre Spiele erscheinen bei D3 als „nur in der CSV“.' % D4_NAME, out)
        self.assertEqual(self.tabellenzeile(out, 'D3')[4], '2', 'nur in CSV')
        self.assertTrue(all('nicht ausgewertet' not in z for z in out.splitlines()), 'die D3 wird ausgewertet')

    def test_eine_d4_ohne_spielzeilen_ist_ein_ausfall_der_zusatzquelle(self):
        w = self.d4_welt()
        w.welt.zusatz[D4_SLUG] = []
        w.cache_neu()
        code, out, err, bericht = self.abgleich(w, '--nur-cache')
        self.assertEqual(code, 2)
        self.assertIn('**Zusatzquelle ausgefallen:** %s – fussball.de lieferte keine Spielzeilen' % D4_NAME, self.abschnitt(bericht, 'D3'))
        self.assertIn('1 fehlgeschlagen', out)

    def test_faellt_der_eigene_plan_aus_wird_die_d4_nicht_gelesen_und_nicht_abgerufen(self):
        w = self.d4_welt()
        w.cache_neu(ohne=('D3',))
        code, out, err, bericht = self.abgleich(w, '--nur-cache')
        self.assertEqual(code, 2)
        self.assertTrue(self.abschnitt(bericht, 'D3').startswith('### D3 – nicht ausgewertet'))
        abrufe = self.abrufe(bericht)
        self.assertIn('- D3: FEHLER (kein Cache-Stand vorhanden (--nur-cache))', abrufe.splitlines())
        self.assertNotIn(D4_NAME, abrufe)
        self.assertIn('1 fehlgeschlagen', out)

    def test_andere_mannschaften_bleiben_unveraendert(self):
        w = self.d4_welt()
        code, out, err, bericht = self.abgleich(w, '--nur-cache')
        for t in A.TEAMS:
            if t[0] != 'D3':
                self.assertNotIn('Zusammengeführt', self.abschnitt(bericht, t[2]))
                self.assertIn('**keine Unterschiede**', self.abschnitt(bericht, t[2]).splitlines()[0])

    def test_der_abgleich_ruft_die_d4_mit_ab_ein_abruf_mehr_mit_eigener_wochensperre(self):
        w = self.d4_welt()
        shutil.rmtree(w.cache)                          # leerer Cache: jeder Plan wird abgerufen
        aufrufe = []

        def antwort(req, timeout=None):
            aufrufe.append(req.full_url)
            tid = req.full_url.rsplit('team-id/', 1)[1]
            if tid == A.ZUSATZQUELLEN[0][3]:
                return FalscheAntwort(seite_aus(w.welt.zusatz[D4_SLUG], zusatz=FUELLTEXT))
            return FalscheAntwort(seite_aus(w.welt.seite[next(t[0] for t in A.TEAMS if t[3] == tid)]))
        with mock.patch.object(urllib.request, 'urlopen', side_effect=antwort):
            code, out, err, bericht = self.abgleich(w)
            self.assertEqual((code, len(aufrufe)), (0, 12), err)
            self.assertEqual(len([u for u in aufrufe if A.ZUSATZQUELLEN[0][3] in u]), 1)
            self.assertIn('12 Abrufe, 0 aus dem Cache, 0 fehlgeschlagen', out)
            self.assertEqual(len(os.listdir(os.path.join(w.cache, D4_SLUG))), 1, 'die D4 hat ihren eigenen Cache-Ordner')
            self.abgleich(w)                            # zweiter Lauf: alles jünger als 6 Tage, nichts wird abgerufen
            self.assertEqual(len(aufrufe), 12)
            shutil.rmtree(os.path.join(w.cache, D4_SLUG))     # nur die D4 fehlt: genau ein Abruf, und zwar ihrer
            self.abgleich(w)
            self.assertEqual(len(aufrufe), 13)
            self.assertIn(A.ZUSATZQUELLEN[0][3], aufrufe[-1])

    def test_scheitert_der_abruf_der_d4_wird_die_d3_trotzdem_ausgewertet(self):
        w = self.d4_welt()
        w.cache_neu(stempel=datetime.now() - timedelta(hours=1))      # main() rechnet mit der echten Uhr: die Pläne sind frisch (unter 6 Tage)
        shutil.rmtree(os.path.join(w.cache, D4_SLUG))                 # nur die D4 muss abgerufen werden
        fehler403 = urllib.error.HTTPError('u', 403, 'Verboten', {}, io.BytesIO(b''))
        self.addCleanup(fehler403.close)
        with mock.patch.object(urllib.request, 'urlopen', side_effect=fehler403) as m:
            code, out, err, bericht = self.abgleich(w)
        self.assertEqual((code, m.call_count), (2, 1))
        self.assertIn('- %s: FEHLER (HTTP 403)' % D4_NAME, self.abrufe(bericht).splitlines())
        self.assertIn('**Zusatzquelle ausgefallen:** %s – HTTP 403;' % D4_NAME, self.abschnitt(bericht, 'D3'))
        self.assertIn('Abruf fehlgeschlagen (HTTP 403)', err)
        self.assertIn('1 fehlgeschlagen', out)
        self.assertTrue(os.path.exists(os.path.join(w.cache, D4_SLUG, 'fehlversuch.txt')), 'eigene Fehlversuch-Marke der D4')
        self.assertFalse(os.path.exists(os.path.join(w.cache, 'd3', 'fehlversuch.txt')))


# ---------------------------------------------------------------- Rekonstruktion an den echten Daten (übersprungen ohne Cache)

def rekonstruktion(csv_pfad, cache, mit_zusatz=True):
    """Rechnet alle Spiele, die in der CSV und im Cache vorkommen, in CSV-Zeilen um (Konventionen jeweils ohne die Zeile
    selbst abgeleitet) und vergleicht -> dict mit Zählern und einer Liste der Abweichungen. Der Plan einer Mannschaft besteht aus
    ihrem eigenen Plan und den Zusatzquellen, deren Cache vorhanden ist (mit_zusatz=False: nur der eigene Plan)."""
    with open(csv_pfad, 'rb') as f:
        datei = N.CsvDatei(f.read())
    rows, _ = A.csv_lesen(csv_pfad)
    tabelle = N.tabelle_aus(rows, datei)
    z = dict(csv=len(rows), gepaart=0, nicht_angesetzt=0, umgerechnet=0, identisch=0, ohne_spiel=0, ort_gleich=0, ort_leer_abgeleitet=0,
             ort_anders=0, fbid_gleich=0, fbid_nur_fussball_de=0, fbid_anders=0, abweichungen=[])
    for team in A.TEAMS:
        fb = []
        for i, quelle in enumerate(A.quellen(team) if mit_zusatz else [team]):
            stand = A.cache_stand(cache, quelle[1])
            if stand is None and i:             # eine Zusatzquelle ohne Cache: ihre CSV-Zeilen zählen dann als „ohne_spiel“
                continue
            with open(stand[0], encoding='utf-8') as f:
                fb_quelle, fehler = N.seite_lesen(f.read())
            assert not fehler, (team[0], quelle[2], fehler)
            fb += fb_quelle
        gruppen = {}
        for g in fb:
            if g['nr'] is not None and g['status'] != 'spielfrei':
                gruppen.setdefault((g['staffel'], g['nr']), []).append(g)
        spiele = {k: A.waehle(v) for k, v in gruppen.items()}
        for r in rows:
            if r['team'] != team[0]:
                continue
            roh = datei.felder(r['zeile'] - 1)
            g = spiele.get((r['staffel'], r['nr']))
            if g is None:
                z['ohne_spiel'] += 1
                z['abweichungen'].append('%s %s·%s %s: bei fussball.de kein Spiel (spielfrei/entfallen), in der CSV „%s“' % (team[0], r['staffel'], r['nr'], roh[3], roh[8]))
                continue
            z['gepaart'] += 1
            if g['status'] != 'angesetzt':
                z['nicht_angesetzt'] += 1
                z['abweichungen'].append('%s %s·%s %s: bei fussball.de %s (Fall für „abgesagt“, nie für „ergänzt“), in der CSV „%s“' % (team[0], r['staffel'], r['nr'], roh[3], g['status'], roh[8]))
                continue
            z['umgerechnet'] += 1
            felder, grund = N.csv_felder(team[0], g, N.Konventionen(tabelle, ausser=r['zeile'] - 1))
            assert felder, grund
            verschieden = [(SPALTE, felder[i], roh[i]) for i, SPALTE in ((0, 'team'), (1, 'staffel'), (2, 'spielnr'), (3, 'datum'), (4, 'zeit'), (5, 'heim'), (6, 'gast'), (10, 'wettbewerb')) if felder[i] != roh[i]]
            if verschieden:
                for s, a, b in verschieden:
                    z['abweichungen'].append('%s %s·%s %s: %s fussball.de „%s“, CSV „%s“ (%s)' % (
                        team[0], r['staffel'], r['nr'], roh[3], s, a, b, 'nur Schreibweise, Name kommt in keiner anderen Zeile vor' if A.norm(a) == A.norm(b) else 'INHALTLICH'))
            else:
                z['identisch'] += 1
            z['ort_gleich' if felder[7] == roh[7] else ('ort_leer_abgeleitet' if not felder[7] else 'ort_anders')] += 1
            if felder[7] and felder[7] != roh[7]:
                z['abweichungen'].append('%s %s·%s %s: Spielstätte abgeleitet „%s“, CSV „%s“ (Ausnahme vom Standard)' % (team[0], r['staffel'], r['nr'], roh[3], felder[7], roh[7]))
            z['fbid_gleich' if felder[9] == roh[9] else ('fbid_nur_fussball_de' if not roh[9] else 'fbid_anders')] += 1
    return z


CACHE_ECHT = A.CACHE_STANDARD
CSV_ECHT = os.path.join(REPO, 'spiele.csv')


@unittest.skipUnless(all(os.path.isdir(os.path.join(CACHE_ECHT, t[1])) and A.cache_stand(CACHE_ECHT, t[1]) for t in A.TEAMS) and os.path.exists(CSV_ECHT),
                     'lokaler Cache des Abgleichs oder spiele.csv fehlt')
class TestRekonstruktionEchteDaten(unittest.TestCase):
    def test_konverter_stimmt_mit_der_vorhandenen_csv_ueberein(self):
        z = rekonstruktion(CSV_ECHT, CACHE_ECHT)
        # Lebende Daten: bewusst großzügige Schwellen, damit der Test nicht an späteren Namens- oder Adressänderungen scheitert
        anteil = z['identisch'] / z['umgerechnet']
        self.assertGreaterEqual(anteil, 0.95, z['abweichungen'])
        self.assertLessEqual(z['fbid_anders'], 2)
        self.assertEqual(z['ort_leer_abgeleitet'] + z['ort_gleich'] + z['ort_anders'], z['umgerechnet'])
        self.assertLessEqual(z['ort_anders'], 3)

    def test_berichtsmodus_gegen_echte_csv_aendert_nichts_an_der_datei(self):
        with open(CSV_ECHT, 'rb') as f:
            vorher = f.read()
        e = N.lauf(N.Optionen(csv=CSV_ECHT, cache=CACHE_ECHT, heute=HEUTE, nur_cache=True, notaus_datei=os.path.join(tempfile.gettempdir(), 'gibt-es-nicht')), JETZT)
        with open(CSV_ECHT, 'rb') as f:
            self.assertEqual(f.read(), vorher)
        self.assertFalse(e['angewendet'] or e['commit'] or e['gepusht'], 'der Berichtsmodus schreibt nie')
        self.assertRegex(e['schluss'], r'^NACHTRAG: (unverändert|Nur-Bericht – \d+ Änderungen? vorgeschlagen, nichts geschrieben|Auffällig, nicht übernommen – .+|Fehler – .+)')
        self.assertIn(e['code'], (0, 1, 2))
        self.assertTrue(all(a.art in ('ergaenzt', 'verlegt', 'abgesagt', 'wieder_angesetzt') and a.datum >= HEUTE for a in e['aenderungen']),
                        'nur erlaubte Arten, nie die Vergangenheit')
        self.assertEqual([c for z in e['zeilen'] + [e['schluss']] for c in z if unicodedata.category(c) in N.NAME_KATEGORIEN], [])

    def test_alle_namen_in_csv_und_cache_bestehen_die_zeichenpruefung(self):
        with open(CSV_ECHT, 'rb') as f:
            datei = N.CsvDatei(f.read())
        for zeile in datei.zeilen[1:]:
            for feld in zeile.split(';'):
                self.assertIsNone(N.zeichenfehler(feld), zeile)
        for team in A.TEAMS:
            for i, quelle in enumerate(A.quellen(team)):
                stand = A.cache_stand(CACHE_ECHT, quelle[1])
                if stand is None and i:         # eine Zusatzquelle (D4) ohne Cache wird übersprungen
                    continue
                with open(stand[0], encoding='utf-8') as f:
                    fb, fehler = N.seite_lesen(f.read())
                self.assertIsNone(fehler, quelle[2])
                for g in fb:
                    self.assertIsNone(N.namen_problem(g), (quelle[2], g['heim'], g['gast']))

    @unittest.skipUnless(A.cache_stand(CACHE_ECHT, D4_SLUG) is not None, 'kein D4-Cache im lokalen Cache des Abgleichs')
    def test_zeilen_der_zusatzquelle_zaehlen_als_gepaart_und_nicht_als_ohne_spiel(self):
        with open(A.cache_stand(CACHE_ECHT, D4_SLUG)[0], encoding='utf-8') as f:
            fb, fehler = N.seite_lesen(f.read())
        self.assertIsNone(fehler)
        schluessel = {(g['staffel'], g['nr']) for g in fb if g['nr'] is not None and g['status'] != 'spielfrei'}
        zeilen, _ = A.csv_lesen(CSV_ECHT)
        d4_zeilen = [r for r in zeilen if r['team'] == A.ZUSATZQUELLEN[0][0] and (r['staffel'], r['nr']) in schluessel]
        if not d4_zeilen:
            self.skipTest('die CSV enthält keine Zeile mehr, die zum Plan der D4 gehört')
        mit, ohne = rekonstruktion(CSV_ECHT, CACHE_ECHT), rekonstruktion(CSV_ECHT, CACHE_ECHT, mit_zusatz=False)
        n = len(d4_zeilen)
        self.assertEqual((mit['ohne_spiel'], mit['gepaart']), (ohne['ohne_spiel'] - n, ohne['gepaart'] + n), 'die D4-Zeilen sind gepaart, nicht ohne Spiel')
        for r in d4_zeilen:
            kopf = '%s %s·%s ' % (r['team'], r['staffel'], r['nr'])
            self.assertFalse([a for a in mit['abweichungen'] if a.startswith(kopf) and 'kein Spiel' in a], r)
        self.assertGreaterEqual(mit['identisch'], ohne['identisch'], 'die Zusatzquelle verschlechtert keine Zeile')     # lebende Daten: nur die Paarung wird verlangt


if __name__ == '__main__':
    unittest.main()
