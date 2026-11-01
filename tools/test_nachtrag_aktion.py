#!/usr/bin/env python3
"""Tests für tools/nachtrag_aktion.py (Cloud-Sicherung des Nachtrags: Wrapper, Workflow-Vorlage, README-Abschnitt).

Isolation: Kein Netzwerk außer 127.0.0.1 (jeder andere Verbindungsversuch scheitert und wird gezählt, am Ende wird null verlangt;
fussball.de und GitHub werden nie erreicht). Die Ende-zu-Ende-Läufe rufen den Wrapper im selben Prozess auf, gegen einen lokalen
HTTP-Server mit synthetischen Spielplanseiten (--url-vorlage) und gegen ein Wegwerf-Repo mit lokalem Bare-Remote als origin. HOME und
die Git-Konfiguration zeigen auf Wegwerf-Ordner; die Standardpfade von abgleich und nachtrag (Cache, Berichte, Not-Aus-Datei) zeigen auf
einen Wächter-Ordner, der leer bleiben muss (am Ende geprüft): alle Läufe bekommen ausdrückliche Pfade. Die wenigen Unterprozess-Tests
(TestAufruf) laufen mit Wegwerf-HOME und einem Proxy auf einen toten lokalen Port. Das echte Repo, die echte spiele.csv und die
Benutzerordner des Macs werden nie gelesen oder geschrieben.

Zeitstabil: Die Uhr von nachtrag UND abgleich ist für die Läufe fest (JETZT, 26.10.2026 07:10, wie in der Testwelt von test_nachtrag):
die Cache-Zeitstempel, die der Abgleich beim Abruf schreibt, und das jetzt des Nachtrags passen so zusammen, und die Spiele im Oktober und
November 2026 bleiben „kommend“, wann immer die Tests laufen. Die echte Uhr liest hier nichts ergebnisrelevant (TestUhr prüft es).

Hilfen (Welt, Spiel, seite_aus, git_ok …) kommen aus test_nachtrag (als T); dessen TestCase-Klassen werden nicht in diesen Namensraum geholt,
sonst liefen die bestehenden Tests hier ein zweites Mal und ohne ihre Netzsperre.

Die Vorlagentests (TestVorlage) finden die Workflow-Datei unter tools/nachtrag-aktion.yml oder, nach dem Verschieben, unter
.github/workflows/nachtrag.yml und werden nur übersprungen, wenn es keine von beiden gibt; die README-Tests hängen nicht an der Vorlage.
Die Vorlage wird ohne YAML-Leser geprüft (Textauswertung, der run-Text läuft unter bash -e mit einem Stub-python3 für jedes Ereignis); dazu
kommt eine Syntaxprüfung mit Ruby, die „übersprungen, Ruby fehlt“ meldet, wenn es kein Ruby gibt.

Aufruf (aus dem Repo-Stamm):  python3 -I -B -m unittest discover -s tools -p 'test_nachtrag_aktion.py'
"""
import ast
import contextlib
import http.server
import inspect
import io
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import warnings
from datetime import date, datetime, timedelta
from unittest import mock

sys.dont_write_bytecode = True
HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)
import abgleich as A  # noqa: E402
import nachtrag as N  # noqa: E402
import nachtrag_aktion as NA  # noqa: E402
import test_nachtrag as T  # noqa: E402  (Hilfen: nur über T., die Testklassen bleiben dort)

REPO = T.REPO
JETZT = T.JETZT                 # 26.10.2026 07:10: die feste Uhr der synthetischen Welt (Spiele im Oktober und November 2026)


def finde_vorlage(repo, tools):
    """Pfad der Workflow-Datei der Cloud-Sicherung: nach dem Verschieben (Inbetriebnahme, Schritt 1) liegt sie unter .github/workflows/nachtrag.yml,
    davor als Vorlage unter tools/nachtrag-aktion.yml; liegen beide da, zählt die aktive. Keine von beiden: None (dann werden die Vorlagentests übersprungen)."""
    for pfad in (os.path.join(repo, '.github', 'workflows', 'nachtrag.yml'), os.path.join(tools, 'nachtrag-aktion.yml')):
        if os.path.exists(pfad):
            return pfad
    return None


VORLAGE = finde_vorlage(REPO, HIER)
README = os.path.join(REPO, 'README.md')
UA_ERWARTET = 'Mozilla/5.0 (compatible; SpeuzerSpielplanNachtrag/1.0; GitHub Actions; rein lesend, ein Abruf je Mannschaft und Tag)'
UA_ABGLEICH = A.USER_AGENT      # der Stand beim Import: so muss abgleich nach jedem Lauf des Wrappers wieder aussehen (ein Leck früherer Tests
ARGV_START = list(sys.argv)     # stünde sonst schon im Vergleichswert, wenn man ihn erst vor dem Lauf liest)
HOLE_ORIGINAL = A.hole          # abgleich.hole beim Import: der Wrapper umhüllt es nur für die Dauer des Abgleichs

NETZ_VERSUCHE = []
LOOPBACK = ('127.0.0.1', '::1', 'localhost')
_ECHT_GETADDRINFO, _ECHT_CREATE_CONNECTION, _ECHT_CONNECT = socket.getaddrinfo, socket.create_connection, socket.socket.connect
_stuetzen = []
_waechter = _home = _standard = _warnungen = None


def _host(adresse):
    return adresse[0] if isinstance(adresse, (tuple, list)) else adresse


def _gesperrt(art, ziel):
    NETZ_VERSUCHE.append('%s %s' % (art, repr(ziel)[:80]))
    raise OSError('Netzwerk im Test gesperrt (nur 127.0.0.1): %r' % (ziel,))


def _addrinfo(host, *a, **k):
    if host not in LOOPBACK:
        _gesperrt('getaddrinfo', host)
    return _ECHT_GETADDRINFO(host, *a, **k)


def _create_connection(adresse, *a, **k):
    if _host(adresse) not in LOOPBACK:
        _gesperrt('create_connection', adresse)
    return _ECHT_CREATE_CONNECTION(adresse, *a, **k)


def _connect(self, adresse):
    if self.family != getattr(socket, 'AF_UNIX', None) and _host(adresse) not in LOOPBACK:
        _gesperrt('connect', adresse)
    return _ECHT_CONNECT(self, adresse)


def setUpModule():
    """Netz nur nach 127.0.0.1, HOME und Git-Konfiguration im Wegwerf-Ordner, Standardpfade von abgleich und nachtrag auf einen Wächter-Ordner,
    keine Pause zwischen den Abrufen (ein eigener Test prüft, dass der Wrapper sie nicht verändert)."""
    global _waechter, _home, _standard, _warnungen
    _warnungen = warnings.catch_warnings()
    _warnungen.__enter__()
    # abgleich.hole lässt die Fehlversuch-Marke ungeschlossen, HTTPError-Antworten bleiben offen (hole bleibt, wie es ist)
    warnings.simplefilter('ignore', ResourceWarning)
    _waechter = tempfile.mkdtemp(prefix='nachtrag-aktion-waechter-')
    _home = os.path.join(_waechter, 'home')
    _standard = os.path.join(_waechter, 'standard')
    os.makedirs(_home)
    gitconfig = os.path.join(_waechter, 'gitconfig')
    with open(gitconfig, 'w') as f:
        f.write('[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n')
    umgebung = {'HOME': _home, 'GIT_CONFIG_GLOBAL': gitconfig, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    _stuetzen[:] = [
        mock.patch.object(socket, 'getaddrinfo', _addrinfo), mock.patch.object(socket, 'create_connection', _create_connection),
        mock.patch.object(socket.socket, 'connect', _connect), mock.patch.object(A, 'PAUSE_SEKUNDEN', 0),
        mock.patch.dict(os.environ, umgebung),
        mock.patch.object(A, 'CACHE_STANDARD', os.path.join(_standard, 'abgleich-cache')),
        mock.patch.object(A, 'BERICHTE_STANDARD', os.path.join(_standard, 'abgleich-berichte')),
        mock.patch.object(N, 'DATEN_ORDNER', os.path.join(_standard, 'daten')),
        mock.patch.object(N, 'NOTAUS_STANDARD', os.path.join(_standard, 'daten', 'AUS')),
        mock.patch.object(N, 'BERICHTE_STANDARD', os.path.join(_standard, 'daten', 'berichte'))]
    for p in _stuetzen:
        p.start()
    # Keine Proxys, auch nicht die Systemeinstellung des Macs: der lokale Server wird immer direkt angesprochen (no_proxy=* schaltet urllib auf
    # „kein Proxy“; patch.dict stellt die Umgebung am Ende wieder her)
    for name in [n for n in os.environ if n.lower().endswith('_proxy')]:
        del os.environ[name]
    os.environ['no_proxy'] = os.environ['NO_PROXY'] = '*'


def tearDownModule():
    netz, home, standard = list(NETZ_VERSUCHE), os.listdir(_home), os.path.exists(_standard)
    for p in reversed(_stuetzen):
        p.stop()
    _warnungen.__exit__(None, None, None)
    shutil.rmtree(_waechter, ignore_errors=True)
    assert not netz, 'Netzwerkzugriffe im Test (außer 127.0.0.1): %s' % netz
    assert not home, 'etwas im Wegwerf-HOME angelegt: %s' % home
    assert not standard, 'ein Standardpfad von abgleich oder nachtrag wurde benutzt (der Wächter-Ordner ist nicht leer)'


# ---------------------------------------------------------------- Feste Uhr

class Fest(datetime):
    """Die Uhr der Tests: now() liefert immer JETZT (nachtrag und abgleich fragen die Uhr nur über die now()-Methode von datetime ab)."""

    @classmethod
    def now(cls, tz=None):
        return cls(JETZT.year, JETZT.month, JETZT.day, JETZT.hour, JETZT.minute, JETZT.second, tzinfo=tz)


@contextlib.contextmanager
def feste_uhr():
    """Die Uhr von nachtrag UND abgleich festlegen: die Cache-Zeitstempel, die der Abgleich beim Abruf schreibt, und das jetzt des Nachtrags
    passen so zusammen (sonst meldete quelle_holen „Cache-Stand liegt in der Zukunft“ oder „veraltet“ und übersprünge die Mannschaft)."""
    with mock.patch.object(N, 'datetime', Fest), mock.patch.object(A, 'datetime', Fest):
        yield


# ---------------------------------------------------------------- Läufe des Wrappers

class Ergebnis:
    """Ein Lauf von NA.main: Exit-Code, stdout und stderr, Inhalt von GITHUB_OUTPUT und GITHUB_STEP_SUMMARY (None, wenn nicht gesetzt)."""

    def __init__(self, code, out, err, ausgabe, zusammenfassung):
        self.code, self.out, self.err, self.ausgabe, self.zusammenfassung = code, out, err, ausgabe, zusammenfassung
        self.zeilen = out.split('\n')
        if self.zeilen[-1] == '':
            self.zeilen.pop()

    @property
    def letzte(self):
        return self.zeilen[-1]

    @property
    def ausgabe_zeilen(self):
        return None if self.ausgabe is None else self.ausgabe.splitlines()

    @property
    def alles(self):
        return '\n'.join([self.out, self.err, self.ausgabe or '', self.zusammenfassung or ''])


def _gelesen(pfad):
    if not pfad or not os.path.exists(pfad):
        return None
    with open(pfad, encoding='utf-8') as f:
        return f.read()


def lauf_ausfuehren(argv, github=None, umgebung=None, uhr=False):
    """NA.main(argv) mit abgefangener Ausgabe. `github`: Ordner, in dem GITHUB_OUTPUT und GITHUB_STEP_SUMMARY (frische Dateien) liegen; ohne
    ihn sind beide Variablen nicht gesetzt (ebenso RUNNER_TEMP, außer `umgebung` setzt sie). `uhr`: feste Uhr für nachtrag und abgleich
    (nur nötig, wenn beide wirklich laufen)."""
    out, err = io.StringIO(), io.StringIO()
    env = dict(umgebung or {})
    ausgabe = zusammenfassung = None
    if github:
        ausgabe, zusammenfassung = os.path.join(github, 'github-output'), os.path.join(github, 'github-summary')
        for pfad in (ausgabe, zusammenfassung):
            if os.path.exists(pfad):
                os.remove(pfad)
        env.update(GITHUB_OUTPUT=ausgabe, GITHUB_STEP_SUMMARY=zusammenfassung)
    with contextlib.ExitStack() as stapel:
        stapel.enter_context(mock.patch.dict(os.environ))
        for name in ('RUNNER_TEMP', 'GITHUB_OUTPUT', 'GITHUB_STEP_SUMMARY'):
            os.environ.pop(name, None)
        os.environ.update(env)
        if uhr:
            stapel.enter_context(feste_uhr())
        stapel.enter_context(contextlib.redirect_stdout(out))
        stapel.enter_context(contextlib.redirect_stderr(err))
        code = NA.main(argv)
    return Ergebnis(code, out.getvalue(), err.getvalue(), _gelesen(ausgabe or env.get('GITHUB_OUTPUT')),
                    _gelesen(zusammenfassung or env.get('GITHUB_STEP_SUMMARY')))


def abgleich_unveraendert(test):
    """Nach jedem Lauf des Wrappers steht abgleich wieder, wie es war: User-Agent, Wochensperre, Pause (im Test 0), hole (ohne die Wache) und sys.argv."""
    test.assertIn('SpeuzerSpielplanAbgleich', UA_ABGLEICH)
    test.assertEqual((A.USER_AGENT, A.MIN_ABSTAND_TAGE, A.PAUSE_SEKUNDEN), (UA_ABGLEICH, 6, 0), 'abgleich bleibt unverändert')
    test.assertIs(A.hole, HOLE_ORIGINAL, 'abgleich.hole ist wieder die ursprüngliche Funktion (ohne Wache)')
    test.assertEqual(sys.argv, ARGV_START, 'sys.argv bleibt unverändert')


def abgleich_attrappe(code=0, vorher=None):
    """Ersatz für abgleich.main: tut nichts, ruft `vorher` auf (zum Beobachten) und liefert `code`."""
    def main():
        if vorher:
            vorher()
        print('Spielplan-Abgleich (Attrappe)')
        return code
    return main


def nachtrag_attrappe(*zeilen, code=0, aufrufe=None):
    """Ersatz für nachtrag.main: merkt sich argv, druckt die Zeilen und liefert `code`."""
    def main(argv=None):
        if aufrufe is not None:
            aufrufe.append(list(argv))
        for z in zeilen:
            print(z)
        return code
    return main


class TestAktion(unittest.TestCase):
    """Wegwerf-Ordner für Läufe mit Attrappen: <tmp>/repo/spiele.csv (der Repo-Stamm) und daneben cache, berichte und zustand."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nachtrag-aktion-test-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.repo = os.path.join(self.dir, 'repo')
        os.makedirs(self.repo)
        self.csv = os.path.join(self.repo, 'spiele.csv')
        with open(self.csv, 'wb') as f:
            f.write(T.Welt().csv_bytes())
        self.cache, self.berichte, self.zustand = (os.path.join(self.dir, n) for n in ('cache', 'berichte', 'zustand'))

    def tearDown(self):
        abgleich_unveraendert(self)

    def argv(self, *extra, **ordner):
        """Kommandozeile mit ausdrücklichen Ordnern (`ordner`: cache, berichte oder zustand ersetzen)."""
        ordner = dict(dict(cache=self.cache, berichte=self.berichte, zustand=self.zustand), **ordner)
        return ['--csv', self.csv, '--cache', ordner['cache'], '--bericht-ordner', ordner['berichte'],
                '--zustand-ordner', ordner['zustand']] + list(extra)

    def lauf_mit(self, abgleich=None, nachtrag=None, argv=None, github=True, umgebung=None):
        """NA.main mit Attrappen für abgleich.main und nachtrag.main (Mock-Objekte: Aufrufe lassen sich zählen)."""
        abgleich = abgleich if abgleich is not None else mock.Mock(side_effect=abgleich_attrappe())
        nachtrag = nachtrag if nachtrag is not None else mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert'))
        with mock.patch.object(A, 'main', abgleich), mock.patch.object(N, 'main', nachtrag):
            return lauf_ausfuehren(argv if argv is not None else self.argv(), github=self.dir if github else None, umgebung=umgebung)


# ---------------------------------------------------------------- Bewertung der Schlusszeile

def echte_schlusszeile(arten=(), angewendet=False, gepusht=False, pushen=True, schreiben=True, hinweise=0, fehler=(), bremse=None,
                       notaus=False, nachgepusht=0):
    """Eine echte Schlusszeile des Nachtrags (N.schluss_bauen, nicht von Hand getippt) -> (Zeile, Exit-Code)."""
    e = N.ergebnis_neu(notaus)
    e.update(angewendet=angewendet, gepusht=gepusht, bremse=bremse, nachgepusht=nachgepusht)
    e['hinweise'] = ['Hinweis %d' % i for i in range(hinweise)]
    e['fehler'] = list(fehler)
    aenderungen = [N.Aenderung(art, 'D1', 'D1', ('340782', 40 + i), None) for i, art in enumerate(arten)]
    ergebnis = N.schluss_bauen(e, aenderungen, schreiben, pushen, None)
    return ergebnis['schluss'], ergebnis['code']


class TestBewerten(unittest.TestCase):
    def pruefe(self, erwartet, schluss, code, nur_bericht=False):
        self.assertEqual(NA.bewerten(schluss, code, nur_bericht), erwartet, '%r (Exit %s, nur_bericht=%s)' % (schluss, code, nur_bericht))

    def test_unveraendert_ist_ok(self):
        self.pruefe('ok', 'NACHTRAG: unverändert', 0)

    def test_geaendert_und_gepusht_ist_ok_auch_mit_hinweisen_und_nachgepushten_commits(self):
        self.pruefe('ok', 'NACHTRAG: 1 geändert (1 ergänzt), gepusht', 0)
        self.pruefe('ok', 'NACHTRAG: 3 geändert (2 ergänzt, 1 verlegt), gepusht, 2 Hinweise', 0)
        self.pruefe('ok', 'NACHTRAG: 2 geändert (1 abgesagt, 1 wieder angesetzt), gepusht; 1 liegen gebliebener Commit nachgepusht', 0)

    def test_geaendert_ohne_gepusht_ist_im_anwendungsmodus_ein_fehler(self):
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt)', 0)
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt), 2 Hinweise', 0)

    def test_nicht_gepusht_und_nachgepusht_zaehlen_nicht_als_gepusht(self):
        """Beide Wörter enthalten „gepusht“: ein einfacher Textvergleich hielte sie für einen Erfolg."""
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht', 0)
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht, 1 Hinweis', 0)
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt); 1 liegen gebliebener Commit nachgepusht', 0)

    def test_nur_bericht_ist_ok(self):
        self.pruefe('ok', 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben', 0, nur_bericht=True)
        self.pruefe('ok', 'NACHTRAG: Nur-Bericht – 2 Änderungen vorgeschlagen, nichts geschrieben, 1 Hinweis', 0, nur_bericht=True)

    def test_geaendert_im_probelauf_ist_ein_fehler(self):
        """Mit --nur-bericht darf nichts geschrieben worden sein."""
        self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt), gepusht', 0, nur_bericht=True)

    def test_liegen_gebliebene_commits_nachgepusht_ist_ok(self):
        self.pruefe('ok', 'NACHTRAG: 1 liegen gebliebener Commit nachgepusht', 0)
        self.pruefe('ok', 'NACHTRAG: 2 liegen gebliebene Commits nachgepusht', 0)

    def test_nur_hinweise_sind_eine_warnung(self):
        self.pruefe('warnung', 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)', 2)
        self.pruefe('warnung', 'NACHTRAG: Auffällig, nicht übernommen – 3 Hinweise (siehe oben)', 2)

    def test_jede_andere_auffaelligkeit_ist_ein_fehler(self):
        self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – 11 Änderungen in einem Lauf (Grenze 10)', 2)
        self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – 7 Änderungen bei D1 in einem Lauf (Grenze 6 je Mannschaft)', 2)
        self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – Not-Aus aktiv, eine Änderung wäre fällig', 2)
        self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – Not-Aus aktiv, 3 Änderungen wären fällig', 2)
        self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – Mengenbremse', 2)

    def test_fehler_ist_ein_fehler_auch_wenn_ein_teil_geschrieben_wurde(self):
        self.pruefe('fehler', 'NACHTRAG: Fehler – spiele.csv nicht lesbar oder unerwartete Form (x)', 1)
        self.pruefe('fehler', 'NACHTRAG: Fehler – 11 Mannschaften übersprungen, kein Cache-Stand vorhanden: Herren, A-Jugend', 2)
        self.pruefe('fehler', 'NACHTRAG: Fehler – 7 Mannschaften übersprungen, kein Cache-Stand vorhanden: D1; 2 geändert (2 ergänzt), gepusht', 2)
        self.pruefe('fehler', 'NACHTRAG: Fehler – Push fehlgeschlagen (remote: gesperrt); 1 geändert (1 ergänzt)', 1)

    def test_fehlende_oder_unbekannte_schlusszeile_ist_ein_fehler(self):
        for schluss in (None, '', 'irgendeine Zeile', 'NACHTRAG:', 'NACHTRAG: etwas ganz anderes', 'nachtrag: unverändert', ' NACHTRAG: unverändert'):
            with self.subTest(schluss):
                self.pruefe('fehler', schluss, 0)

    def test_gegenprobe_mit_dem_exit_code(self):
        """Maßgeblich ist die Schlusszeile; ein Exit-Code, der nicht dazu passt, ist ein Fehler (der Nachtrag hat sich geändert)."""
        for code in (1, 2, None):
            with self.subTest('unverändert', code=code):
                self.pruefe('fehler', 'NACHTRAG: unverändert', code)
            with self.subTest('gepusht', code=code):
                self.pruefe('fehler', 'NACHTRAG: 1 geändert (1 ergänzt), gepusht', code)
            with self.subTest('Nur-Bericht', code=code):
                self.pruefe('fehler', 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben', code, nur_bericht=True)
        for code in (0, 1, None):
            with self.subTest('Hinweise', code=code):
                self.pruefe('fehler', 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)', code)

    def test_der_grund_nennt_widerspruch_und_unbekannte_zeilen(self):
        self.assertEqual(NA.beurteilen('NACHTRAG: unverändert', 1, False),
                         ('fehler', 'Exit-Code 1 des Nachtrags widerspricht der Schlusszeile (erwartet: 0)'))
        self.assertEqual(NA.beurteilen('NACHTRAG: Auffällig, nicht übernommen – 2 Hinweise (siehe oben)', 0, False),
                         ('fehler', 'Exit-Code 0 des Nachtrags widerspricht der Schlusszeile (erwartet: 2)'))
        self.assertEqual(NA.beurteilen('NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht', 0, False), ('fehler', 'geändert, aber nicht gepusht'))
        self.assertEqual(NA.beurteilen('NACHTRAG: etwas anderes', 0, False), ('fehler', 'unbekannte Schlusszeile'))
        self.assertEqual(NA.beurteilen(None, 0, False), ('fehler', 'keine Schlusszeile des Nachtrags'))
        self.assertEqual(NA.beurteilen('NACHTRAG: unverändert', 0, False), ('ok', ''))
        self.assertEqual(NA.beurteilen('NACHTRAG: Fehler – x', 1, False), ('fehler', ''), 'die Zeile erklärt sich selbst')

    def test_bewertung_versteht_die_echten_schlusszeilen_des_nachtrags(self):
        """Die Zeilen kommen aus N.schluss_bauen: ändert der Nachtrag ihre Form, fällt das hier auf, nicht erst in der Cloud."""
        faelle = [
            ('ok', dict(), False),
            ('ok', dict(arten=['ergaenzt'], angewendet=True, gepusht=True), False),
            ('ok', dict(arten=['ergaenzt', 'verlegt'], angewendet=True, gepusht=True, hinweise=2), False),
            ('ok', dict(arten=['abgesagt'], angewendet=True, gepusht=True, nachgepusht=1), False),
            ('ok', dict(nachgepusht=2), False),
            ('ok', dict(arten=['ergaenzt'], schreiben=False), True),
            ('warnung', dict(hinweise=1), False),
            ('warnung', dict(hinweise=4), False),
            ('fehler', dict(arten=['ergaenzt'], angewendet=True, pushen=False), False),                  # „… nicht gepusht“
            ('fehler', dict(bremse='11 Änderungen in einem Lauf (Grenze 10)'), False),
            ('fehler', dict(notaus=True, arten=['ergaenzt']), False),
            ('fehler', dict(notaus=True, arten=['ergaenzt', 'verlegt']), False),
            ('fehler', dict(fehler=['Arbeitsbaum nicht sauber (README.md) – nichts geschrieben']), False),
            ('fehler', dict(fehler=['D1 übersprungen: kein Cache-Stand vorhanden']), False),
            ('fehler', dict(fehler=['x'], arten=['ergaenzt'], angewendet=True, gepusht=True), False),
            ('fehler', dict(bremse='11 Änderungen in einem Lauf (Grenze 10)', hinweise=2), False),       # Mengenbremse zusammen mit Hinweisen
            ('fehler', dict(bremse='7 Änderungen bei D1 in einem Lauf (Grenze 6 je Mannschaft)', hinweise=1), False),
            ('fehler', dict(notaus=True, arten=['ergaenzt'], hinweise=2), False),                        # Not-Aus zusammen mit Hinweisen
            ('fehler', dict(notaus=True, arten=['ergaenzt', 'verlegt'], hinweise=1), False),
        ]
        for erwartet, kw, nur_bericht in faelle:
            schluss, code = echte_schlusszeile(**kw)
            with self.subTest(schluss):
                self.pruefe(erwartet, schluss, code, nur_bericht)

    def test_mengenbremse_und_not_aus_zusammen_mit_hinweisen_sind_nie_eine_gruene_warnung(self):
        """Eine neue Staffel erzeugt Hinweise UND viele Änderungen: die Zeile enthält dann „Hinweis“, ist aber keine bloße Warnung (nichts wurde übernommen)."""
        for kw in (dict(bremse='11 Änderungen in einem Lauf (Grenze 10)', hinweise=2), dict(notaus=True, arten=['ergaenzt'], hinweise=3)):
            schluss, code = echte_schlusszeile(**kw)
            self.assertIn('Hinweis', schluss)
            self.assertEqual(code, 2, schluss)
            self.assertIsNone(NA.NUR_HINWEISE.fullmatch(schluss[len(NA.PRAEFIX):]), schluss)
            self.assertEqual(NA.bewerten(schluss, code, False), 'fehler', schluss)

    def test_bewerten_kennt_nur_drei_ergebnisse(self):
        for schluss, code in (('NACHTRAG: unverändert', 0), ('NACHTRAG: Fehler – x', 1), ('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)', 2),
                              (None, 0)):
            self.assertIn(NA.bewerten(schluss, code, False), ('ok', 'warnung', 'fehler'))


# ---------------------------------------------------------------- Ausgabeformate

class TestFormate(unittest.TestCase):
    def test_maskierung_nach_der_github_regel(self):
        self.assertEqual(NA.maskiert('50% sicher'), '50%25 sicher')
        self.assertEqual(NA.maskiert('a\rb\nc'), 'a%0Db%0Ac')
        self.assertEqual(NA.maskiert('%0A bleibt Text\r\n'), '%250A bleibt Text%0D%0A', 'das Prozentzeichen wird zuerst maskiert')
        self.assertEqual(NA.maskiert('nichts zu tun'), 'nichts zu tun')

    def test_einzeilig_laesst_keine_weitere_zeile_zu(self):
        self.assertEqual(NA.einzeilig('a\r\nb\nc\rd e\x0bf'), 'a b c d e f')
        self.assertEqual(NA.einzeilig('  vorn und   hinten  '), 'vorn und hinten')
        self.assertEqual(NA.einzeilig('geaendert=true'), 'geaendert=true')

    def test_codezaun_ist_laenger_als_jede_gravis_folge(self):
        self.assertEqual(NA.codeblock(['a', 'b']), '```text\na\nb\n```')
        self.assertEqual(NA.codeblock(['x ``` y']), '````text\nx ``` y\n````')
        self.assertEqual(NA.codeblock(['x ```` y', 'z']), '`````text\nx ```` y\nz\n`````')
        self.assertEqual(NA.inline_code('NACHTRAG: unverändert'), '`NACHTRAG: unverändert`')
        self.assertEqual(NA.inline_code('a `b` c'), '`` a `b` c ``')

    def test_zusammenfassung_hat_ueberschrift_modus_schlusszeile_und_ausgabe_im_codeblock(self):
        text = NA.zusammenfassung('ok', 'NACHTRAG: 1 geändert (1 ergänzt), gepusht', ['- ergänzt: D1', 'NACHTRAG: 1 geändert (1 ergänzt), gepusht', ''],
                                  'schreiben und pushen')
        self.assertEqual(text, '## Spielplan-Nachtrag (Cloud-Sicherung)\n\nModus: schreiben und pushen\n\n'
                               '**ok**: `NACHTRAG: 1 geändert (1 ergänzt), gepusht`\n\n'
                               '```text\n- ergänzt: D1\nNACHTRAG: 1 geändert (1 ergänzt), gepusht\n```\n\n')

    def test_zusammenfassung_ohne_zeilen_hat_keinen_leeren_codeblock(self):
        text = NA.zusammenfassung('fehler', 'ungültiger Aufruf (x)', [], '')
        self.assertEqual(text, '## Spielplan-Nachtrag (Cloud-Sicherung)\n\n**fehler**: `ungültiger Aufruf (x)`\n\n')


# ---------------------------------------------------------------- Ablauf mit Attrappen

class TestAblauf(TestAktion):
    def csv_zeilen(self):
        with open(self.csv, 'rb') as f:
            return T.zeilen_von(f.read())

    def grundlage(self):
        """Die Kommandozeile des Nachtrags ohne --anwenden und --push."""
        return ['--csv', self.csv, '--cache', self.cache, '--bericht-ordner', self.berichte,
                '--notaus-datei', os.path.join(self.zustand, 'AUS'), '--nur-cache']

    # (c) User-Agent und sys.argv
    def test_user_agent_und_argv_gelten_nur_fuer_den_aufruf_des_abgleichs(self):
        gesehen = {}

        def abgleich():
            gesehen.update(agent=A.USER_AGENT, argv=list(sys.argv))
            return 0
        r = self.lauf_mit(abgleich=mock.Mock(side_effect=abgleich))
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(NA.USER_AGENT, UA_ERWARTET)
        self.assertEqual(gesehen['agent'], UA_ERWARTET)
        self.assertEqual(gesehen['argv'], ['abgleich.py', '--csv', self.csv, '--cache', self.cache, '--bericht-ordner', self.berichte,
                                           '--kein-wochenvergleich'])
        self.assertEqual((A.USER_AGENT, sys.argv), (UA_ABGLEICH, ARGV_START), 'danach wiederhergestellt')

    def test_url_vorlage_wird_an_den_abgleich_durchgereicht(self):
        gesehen = []
        vorlage = 'http://127.0.0.1:9/plan/{tid}/{von}/{bis}'
        self.lauf_mit(abgleich=mock.Mock(side_effect=lambda: gesehen.append(list(sys.argv)) or 0), argv=self.argv('--url-vorlage', vorlage))
        self.assertEqual(gesehen[0][-3:], ['--kein-wochenvergleich', '--url-vorlage', vorlage])

    def test_user_agent_und_argv_werden_auch_nach_fehlern_zurueckgesetzt(self):
        for name, abgleich in (('Ausnahme', mock.Mock(side_effect=RuntimeError('kaputt'))), ('SystemExit', mock.Mock(side_effect=SystemExit(2))),
                               ('Exit 1', mock.Mock(return_value=1))):
            with self.subTest(name):
                r = self.lauf_mit(abgleich=abgleich)
                self.assertEqual((A.USER_AGENT, sys.argv), (UA_ABGLEICH, ARGV_START))
                self.assertEqual(r.code, 1)

    def test_pause_und_wochensperre_des_abgleichs_bleiben_unveraendert(self):
        gesehen = []
        with mock.patch.object(A, 'PAUSE_SEKUNDEN', 7), mock.patch.object(A, 'MIN_ABSTAND_TAGE', 6):
            self.lauf_mit(abgleich=mock.Mock(side_effect=lambda: gesehen.append((A.PAUSE_SEKUNDEN, A.MIN_ABSTAND_TAGE)) or 0))
            self.assertEqual((A.PAUSE_SEKUNDEN, A.MIN_ABSTAND_TAGE), (7, 6))
        self.assertEqual(gesehen, [(7, 6)])

    # Wache um abgleich.hole: eine Antwort mit HTTP 200, die kein Plan ist, beendet die Abrufe wie ein Fehlschlag
    def test_wache_umhuellt_hole_nur_fuer_den_aufruf_des_abgleichs(self):
        gesehen = []
        faelle = (('ok', mock.Mock(side_effect=lambda: gesehen.append(A.hole) or 0)), ('Ausnahme', mock.Mock(side_effect=RuntimeError('kaputt'))),
                  ('SystemExit', mock.Mock(side_effect=SystemExit(2))), ('Exit 1', mock.Mock(return_value=1)))
        for name, abgleich in faelle:
            with self.subTest(name):
                self.lauf_mit(abgleich=abgleich)
                self.assertIs(A.hole, HOLE_ORIGINAL, 'danach wieder die ursprüngliche Funktion')
        self.assertEqual(len(gesehen), 1)
        self.assertIsNot(gesehen[0], HOLE_ORIGINAL, 'während des Aufrufs ist hole umhüllt')

    def test_antwort_unbrauchbar_unterscheidet_sperrseite_aufbau_und_leeren_plan(self):
        spiele = T.seite_aus(T.basis_spiele('D1'))
        leer = T.seite_aus([], zusatz=T.FUELLTEXT)
        self.assertIn('kein Spielplan erkannt', NA.antwort_unbrauchbar(T.SPERRSEITE))
        self.assertIn('Seite zu kurz', NA.antwort_unbrauchbar('<html>x</html>'))
        self.assertIn('Seitenaufbau geändert', NA.antwort_unbrauchbar(spiele.replace('column-club', 'column-klub')))
        self.assertIsNone(NA.antwort_unbrauchbar(spiele))
        self.assertTrue(A.leerer_plan(leer))
        self.assertIsNone(NA.antwort_unbrauchbar(leer), 'ein gültiger Plan ohne Spiele ist keine unbrauchbare Antwort')

    def test_wache_traegt_unbrauchbare_antworten_in_die_abbruchliste_ein(self):
        team = A.TEAMS[0]
        spiele = T.seite_aus(T.basis_spiele('D1'))
        faelle = [('Sperrseite', T.SPERRSEITE, True), ('zu kurz', '<html>x</html>', True),
                  ('geänderter Seitenaufbau', spiele.replace('column-club', 'column-klub'), True),
                  ('normaler Plan', spiele, False), ('gültiger Plan ohne Spiele', T.seite_aus([], zusatz=T.FUELLTEXT), False)]
        for name, text, abbruch_erwartet in faelle:
            with self.subTest(name):
                aufrufe, abbruch, err = [], [], io.StringIO()

                def echt(*args, **kw):
                    aufrufe.append((args, kw))
                    return text, 'Abruf', JETZT, None
                with contextlib.redirect_stderr(err):
                    ergebnis = NA.hole_mit_wache(echt)('cache', team, 'von', 'bis', False, JETZT, abbruch, 'vorlage')
                self.assertEqual(ergebnis, (text, 'Abruf', JETZT, None), 'das Ergebnis geht unverändert durch')
                self.assertEqual(aufrufe, [(('cache', team, 'von', 'bis', False, JETZT, abbruch, 'vorlage'), {})], 'alle Argumente gehen unverändert durch')
                self.assertEqual(len(abbruch), 1 if abbruch_erwartet else 0)
                if abbruch_erwartet:
                    self.assertTrue(abbruch[0].startswith('Herren: unbrauchbare Antwort ('), abbruch)
                    self.assertIn('Herren: unbrauchbare Antwort (', err.getvalue())
                    self.assertIn('weitere Abrufe in diesem Lauf entfallen', err.getvalue())
                else:
                    self.assertEqual(err.getvalue(), '')

    def test_wache_laesst_cache_staende_und_fehlschlaege_in_ruhe(self):
        team = A.TEAMS[0]
        for name, ergebnis in (('Cache-Stand', (T.SPERRSEITE, 'Cache', JETZT, None)), ('Fehlschlag', (None, '', None, 'HTTP 403')),
                               ('nicht abgerufen', (None, '', None, 'nicht abgerufen, weil ein vorheriger Abruf scheiterte (x)'))):
            with self.subTest(name):
                abbruch = []
                self.assertEqual(NA.hole_mit_wache(lambda *a, **k: ergebnis)('c', team, 'v', 'b', False, JETZT, abbruch), ergebnis)
                self.assertEqual(abbruch, [])

    # (d) Cache
    def test_cache_wird_zu_beginn_geleert(self):
        os.makedirs(os.path.join(self.cache, 'd1'))
        os.makedirs(os.path.join(self.cache, 'g-jugend', 'unterordner'))
        for name in (os.path.join('d1', '20200101-000000.html'), os.path.join('g-jugend', 'fehlversuch.txt')):
            with open(os.path.join(self.cache, name), 'w') as f:
                f.write('alt')
        gesehen = []
        self.lauf_mit(abgleich=mock.Mock(side_effect=abgleich_attrappe(vorher=lambda: gesehen.append(os.listdir(self.cache)))))
        self.assertEqual(gesehen, [[]], 'der Abgleich sieht einen leeren Cache')
        self.assertTrue(os.path.isdir(self.cache))

    def test_cache_mit_allen_zwoelf_plan_ordnern_wird_ganz_geleert(self):
        plaene = sorted([t[1] for t in A.TEAMS] + [q[1] for q in A.ZUSATZQUELLEN])
        self.assertEqual(sorted(NA.slugs()), plaene)
        self.assertEqual(len(plaene), 12)
        for slug in plaene:
            for datei in ('20200101-000000.html', '20200102-000000.html.fehler', 'fehlversuch.txt'):
                self.schreibe(os.path.join(self.cache, slug, datei), 'alt')
        gesehen = []
        r = self.lauf_mit(abgleich=mock.Mock(side_effect=abgleich_attrappe(vorher=lambda: gesehen.append(os.listdir(self.cache)))))
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(gesehen, [[]])

    def test_leerer_und_fehlender_cache_ordner_sind_erlaubt(self):
        os.makedirs(self.cache)
        self.assertEqual(self.lauf_mit().code, 0)
        shutil.rmtree(self.cache)
        r = self.lauf_mit()
        self.assertEqual(r.code, 0, r.alles)
        self.assertTrue(os.path.isdir(self.cache))

    @staticmethod
    def baum(ordner):
        """Der ganze Inhalt eines Ordners (Pfade, Dateiinhalte, Verknüpfungsziele), um vorher und nachher zu vergleichen."""
        ergebnis = []
        for wurzel, ordnernamen, dateien in os.walk(ordner):
            for n in ordnernamen + dateien:
                pfad = os.path.join(wurzel, n)
                rel = os.path.relpath(pfad, ordner)
                if os.path.islink(pfad):
                    ergebnis.append((rel, 'link', os.readlink(pfad)))
                elif os.path.isdir(pfad):
                    ergebnis.append((rel, 'dir', ''))
                else:
                    with open(pfad, encoding='utf-8') as f:
                        ergebnis.append((rel, 'datei', f.read()))
        return sorted(ergebnis)

    @staticmethod
    def schreibe(pfad, text='wichtig'):
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_cache_mit_fremdem_inhalt_wird_abgewiesen_und_nichts_geloescht(self):
        verknuepft = os.path.join(self.dir, 'verknuepft')
        faelle = {
            'fremde Datei': lambda: self.schreibe(os.path.join(self.cache, 'alt.txt')),
            'fremder Ordner': lambda: self.schreibe(os.path.join(self.cache, 'fremd', 'tief', 'wichtig.txt')),
            'Datei mit dem Namen eines Plans': lambda: self.schreibe(os.path.join(self.cache, 'd2')),
            'Verknüpfung mit dem Namen eines Plans': lambda: (self.schreibe(os.path.join(verknuepft, 'wichtig.txt')),
                                                              os.symlink(verknuepft, os.path.join(self.cache, 'e1'))),
        }
        for name, anlegen in faelle.items():
            with self.subTest(name):
                shutil.rmtree(self.cache, ignore_errors=True)
                shutil.rmtree(verknuepft, ignore_errors=True)
                self.schreibe(os.path.join(self.cache, 'd1', '20200101-000000.html'), 'Stand eines Plans')     # ein echter Plan-Ordner daneben
                anlegen()
                vorher = self.baum(self.cache)
                vorher_ziel = self.baum(verknuepft) if os.path.exists(verknuepft) else None
                abgleich = mock.Mock(side_effect=abgleich_attrappe())
                r = self.lauf_mit(abgleich=abgleich)
                self.assertEqual(r.code, 1, r.alles)
                self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – Cache-Ordner enthält Fremdes, nichts gelöscht')
                self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=Cache-Ordner enthält Fremdes, nichts gelöscht'])
                self.assertIn('::error::Cache-Ordner enthält Fremdes, nichts gelöscht', r.zeilen)
                self.assertEqual(abgleich.call_count, 0)
                self.assertEqual(self.baum(self.cache), vorher, 'nichts gelöscht, auch nicht der Plan-Ordner daneben')
                if vorher_ziel is not None:
                    self.assertEqual(self.baum(verknuepft), vorher_ziel, 'auch das Ziel der Verknüpfung ist unberührt')

    def test_cache_des_mac_tasks_und_ordner_darueber_und_darin_werden_abgewiesen(self):
        """abgleich.CACHE_STANDARD ist der Cache des Mac-Tasks (seine älteren Stände speisen die Rücknahme von Absagen und die Aufstellungs-Apps):
        der Wrapper löscht dort nichts. Der Test setzt den Wert auf Wegwerf-Ordner, jeden so gebaut, dass er ohne diese Regel erlaubt wäre (nur
        Plan-Ordner oder leer), und berührt den echten Wert nie."""
        def pruefe(name, standard, cache, wurzel):
            with self.subTest(name):
                vorher = self.baum(wurzel)
                abgleich = mock.Mock(side_effect=abgleich_attrappe())
                with mock.patch.object(A, 'CACHE_STANDARD', standard):
                    r = self.lauf_mit(abgleich=abgleich, argv=self.argv(cache=cache))
                self.assertEqual(r.code, 1, r.alles)
                self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – Cache-Ordner gehört dem Mac-Task, nichts gelöscht')
                self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=Cache-Ordner gehört dem Mac-Task, nichts gelöscht'])
                self.assertEqual(abgleich.call_count, 0)
                self.assertEqual(self.baum(wurzel), vorher, 'nichts gelöscht, nichts angelegt')
        m1 = os.path.join(self.dir, 'm1')                    # der Cache des Mac-Tasks selbst: nur Plan-Ordner mit Ständen
        self.schreibe(os.path.join(m1, 'herren', '20260101-000000.html'), 'Stand des Mac-Tasks')
        pruefe('der Cache des Mac-Tasks selbst', m1, m1, m1)
        m2 = os.path.join(self.dir, 'm2')                    # ein Ordner darüber, der nur einen Plan-Ordner enthält (diesen als Mac-Task-Cache)
        self.schreibe(os.path.join(m2, 'd1', '20260101-000000.html'), 'Stand des Mac-Tasks')
        pruefe('ein Ordner darüber', os.path.join(m2, 'd1'), m2, m2)
        m3 = os.path.join(self.dir, 'm3')                    # ein leerer und ein nicht vorhandener Ordner darin
        self.schreibe(os.path.join(m3, 'herren', '20260101-000000.html'), 'Stand des Mac-Tasks')
        os.makedirs(os.path.join(m3, 'leer'))
        pruefe('ein leerer Ordner darin', m3, os.path.join(m3, 'leer'), m3)
        pruefe('ein nicht vorhandener Ordner darin', m3, os.path.join(m3, 'neu', 'cache'), m3)
        self.assertFalse(os.path.exists(os.path.join(m3, 'neu')), 'auch nicht angelegt')

    def test_cache_leeren_loescht_selbst_nur_die_plan_ordner(self):
        """Auch ohne die Vorprüfung (ordner_problem) fasst cache_leeren nur die zwölf Plan-Ordner an."""
        self.schreibe(os.path.join(self.cache, 'd1', 'x.html'))
        self.schreibe(os.path.join(self.cache, 'fremd', 'wichtig.txt'))
        self.schreibe(os.path.join(self.cache, 'alt.txt'))
        NA.cache_leeren(self.cache)
        self.assertEqual(sorted(os.listdir(self.cache)), ['alt.txt', 'fremd'])
        self.assertTrue(os.path.exists(os.path.join(self.cache, 'fremd', 'wichtig.txt')))

    def test_cache_fremdes_erkennt_andere_namen_dateien_und_verknuepfungen(self):
        self.assertEqual(NA.cache_fremdes(self.cache), [], 'den Ordner gibt es nicht')
        os.makedirs(self.cache)
        self.assertEqual(NA.cache_fremdes(self.cache), [], 'leer')
        os.makedirs(os.path.join(self.cache, 'd1'))                                   # ein Plan-Ordner ist nicht fremd
        os.makedirs(os.path.join(self.cache, 'fremd'))
        self.schreibe(os.path.join(self.cache, 'd2'))                                 # Datei mit dem Namen eines Plans
        os.symlink(self.repo, os.path.join(self.cache, 'e1'))                         # Verknüpfung mit dem Namen eines Plans
        self.assertEqual(NA.cache_fremdes(self.cache), ['d2', 'e1', 'fremd'])

    def test_innerhalb_vergleicht_aufgeloeste_pfade(self):
        a = os.path.join(self.dir, 'Eins')
        os.makedirs(os.path.join(a, 'zwei', 'drei'))
        self.assertTrue(NA.innerhalb(a, a))
        self.assertTrue(NA.innerhalb(os.path.join(a, 'zwei', 'drei'), a))
        self.assertTrue(NA.innerhalb(os.path.join(a, 'gibt-es-nicht', 'tief'), a), 'auch ein Pfad, den es noch nicht gibt')
        self.assertFalse(NA.innerhalb(a, os.path.join(a, 'zwei')))
        self.assertFalse(NA.innerhalb(os.path.join(self.dir, 'Eins2'), a), 'gleicher Anfang heißt nicht „darin“')
        verweis = os.path.join(self.dir, 'verweis')
        os.symlink(a, verweis)
        self.assertTrue(NA.innerhalb(os.path.join(verweis, 'zwei'), a), 'Verknüpfungen werden aufgelöst')
        self.assertFalse(NA.innerhalb(os.path.join(self.dir, 'woanders'), a))

    def test_cache_des_mac_tasks_wird_auch_bei_anderer_schreibweise_erkannt(self):
        """Auf dem Mac (Dateisystem, das Groß- und Kleinschreibung nicht unterscheidet) ist ~/library derselbe Ordner wie ~/Library: die
        Schreibweise allein darf den Schutz nicht umgehen. Auf einem Dateisystem mit Unterscheidung entfällt der Test."""
        standard = os.path.join(self.dir, 'Mac-Cache')
        self.schreibe(os.path.join(standard, 'herren', '20260101-000000.html'), 'Stand des Mac-Tasks')
        anders = os.path.join(self.dir, 'mac-cache')
        if not os.path.exists(anders):
            self.skipTest('dieses Dateisystem unterscheidet Groß- und Kleinschreibung')
        self.assertTrue(NA.innerhalb(anders, standard))
        self.assertTrue(NA.innerhalb(standard, anders))
        vorher = self.baum(standard)
        abgleich = mock.Mock(side_effect=abgleich_attrappe())
        with mock.patch.object(A, 'CACHE_STANDARD', standard):
            r = self.lauf_mit(abgleich=abgleich, argv=self.argv(cache=anders))
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – Cache-Ordner gehört dem Mac-Task, nichts gelöscht')
        self.assertEqual(abgleich.call_count, 0)
        self.assertEqual(self.baum(standard), vorher, 'nichts gelöscht')

    def test_fehlender_cache_wird_angelegt_ebenso_berichte_und_zustand(self):
        gesehen = []
        nachtrag = mock.Mock(side_effect=lambda argv: gesehen.append([os.path.isdir(p) for p in (self.cache, self.berichte, self.zustand)])
                             or print('NACHTRAG: unverändert') or 0)
        self.lauf_mit(nachtrag=nachtrag)
        self.assertEqual(gesehen, [[True, True, True]])

    # (e) --nur-bericht
    def test_nur_bericht_ruft_den_nachtrag_ohne_anwenden_und_push(self):
        aufrufe = []
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben',
                                                                           aufrufe=aufrufe)), argv=self.argv('--nur-bericht'))
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(aufrufe, [self.grundlage()])
        for verboten in ('--anwenden', '--push', '--force'):
            self.assertNotIn(verboten, aufrufe[0])

    def test_anwendungsmodus_ruft_den_nachtrag_mit_anwenden_und_push_nie_mit_force(self):
        aufrufe = []
        self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert', aufrufe=aufrufe)))
        self.assertEqual(aufrufe, [self.grundlage() + ['--anwenden', '--push']])
        self.assertNotIn('--force', aufrufe[0])
        self.assertIn('--nur-cache', aufrufe[0], 'der Nachtrag ruft nie selbst ab')
        self.assertEqual(aufrufe[0][aufrufe[0].index('--notaus-datei') + 1], os.path.join(self.zustand, 'AUS'),
                         'die Sperre lauf.lock liegt damit im Zustandsordner')

    def test_die_kommandozeile_des_wrappers_kennt_weder_force_noch_push_noch_anwenden(self):
        for option in ('--force', '--push', '--anwenden', '--nur-cache', '--heute', '--max-alter'):
            with self.subTest(option):
                abgleich = mock.Mock(side_effect=abgleich_attrappe())
                r = self.lauf_mit(abgleich=abgleich, argv=self.argv(option))
                self.assertEqual(r.code, 1)
                self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: fehler – ungültiger Aufruf ('), r.letzte)
                self.assertEqual(abgleich.call_count, 0)

    # (f) Abgleich fehlgeschlagen
    def test_exit_1_des_abgleichs_startet_den_nachtrag_nicht(self):
        faelle = (('Exit 1', mock.Mock(return_value=1)), ('Ausnahme', mock.Mock(side_effect=OSError('Platte voll'))),
                  ('SystemExit', mock.Mock(side_effect=SystemExit(2))), ('unbekannter Code', mock.Mock(return_value=3)))
        for name, abgleich in faelle:
            with self.subTest(name):
                nachtrag = mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert'))
                r = self.lauf_mit(abgleich=abgleich, nachtrag=nachtrag)
                self.assertEqual(nachtrag.call_count, 0, 'der Nachtrag wird nicht gestartet')
                self.assertEqual(abgleich.call_count, 1, 'keine Wiederholung')
                self.assertEqual(r.code, 1)
                erwartet = 'Abruf/Abgleich fehlgeschlagen (Exit %s)' % (3 if name == 'unbekannter Code' else 1)
                self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – ' + erwartet)
                self.assertIn('::error::' + erwartet, r.zeilen)
                self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=' + erwartet])
                self.assertNotIn('NACHTRAG: unverändert', r.out)

    def test_exit_2_des_abgleichs_laeuft_weiter_der_nachtrag_meldet_fehlende_plaene_selbst(self):
        nachtrag = mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert'))
        r = self.lauf_mit(abgleich=mock.Mock(side_effect=abgleich_attrappe(code=2)), nachtrag=nachtrag)
        self.assertEqual(nachtrag.call_count, 1)
        self.assertEqual(r.code, 0)

    def test_der_abgleich_laeuft_in_jedem_fall_hoechstens_einmal(self):
        for name, abgleich, nachtrag in (('ok', mock.Mock(side_effect=abgleich_attrappe()), None),
                                         ('Fehler', mock.Mock(return_value=1), None),
                                         ('Nachtrag-Fehler', mock.Mock(side_effect=abgleich_attrappe(code=2)),
                                          mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: Fehler – x', code=1)))):
            with self.subTest(name):
                self.lauf_mit(abgleich=abgleich, nachtrag=nachtrag)
                self.assertEqual(abgleich.call_count, 1)

    # Ordner
    def test_standardordner_liegen_unter_runner_temp(self):
        runner = os.path.join(self.dir, 'runner')
        os.makedirs(runner)
        abgleich_argv, aufrufe = [], []
        abgleich = mock.Mock(side_effect=lambda: abgleich_argv.append(list(sys.argv)) or 0)
        nachtrag = mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert', aufrufe=aufrufe))
        r = self.lauf_mit(abgleich=abgleich, nachtrag=nachtrag, argv=['--csv', self.csv], umgebung={'RUNNER_TEMP': runner})
        basis = os.path.join(runner, 'speuzer-nachtrag-aktion')
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(abgleich_argv[0][abgleich_argv[0].index('--cache') + 1], os.path.join(basis, 'cache'))
        self.assertEqual(abgleich_argv[0][abgleich_argv[0].index('--bericht-ordner') + 1], os.path.join(basis, 'berichte'))
        self.assertEqual(aufrufe[0][aufrufe[0].index('--notaus-datei') + 1], os.path.join(basis, 'zustand', 'AUS'))
        self.assertEqual(sorted(os.listdir(basis)), ['berichte', 'cache', 'zustand'])
        self.assertNotIn(runner, r.out + r.err, 'der Wert von RUNNER_TEMP wird nicht ausgegeben')

    def test_ohne_runner_temp_dient_der_temp_ordner(self):
        temp = os.path.join(self.dir, 'temp')
        os.makedirs(temp)
        gesehen = []
        with mock.patch.object(tempfile, 'gettempdir', return_value=temp):
            self.lauf_mit(abgleich=mock.Mock(side_effect=lambda: gesehen.append(list(sys.argv)) or 0), argv=['--csv', self.csv])
        self.assertEqual(gesehen[0][gesehen[0].index('--cache') + 1], os.path.join(temp, 'speuzer-nachtrag-aktion', 'cache'))

    def test_standard_csv_ist_die_spiele_csv_im_repo_stamm(self):
        self.assertEqual(NA.optionen_aus([]).csv, os.path.join(REPO, 'spiele.csv'))
        self.assertEqual(os.path.dirname(HIER), REPO)

    def test_ordner_im_repo_und_ein_cache_ueber_dem_repo_werden_abgewiesen(self):
        faelle = {'Cache im Repo': dict(cache=os.path.join(self.repo, 'cache')), 'Cache ist das Repo': dict(cache=self.repo),
                  'Cache über dem Repo': dict(cache=self.dir), 'Berichte im Repo': dict(berichte=os.path.join(self.repo, 'berichte')),
                  'Zustand im Repo': dict(zustand=os.path.join(self.repo, 'zustand'))}
        marke = os.path.join(self.repo, 'wichtig.txt')
        with open(marke, 'w') as f:
            f.write('bleibt')
        vorher = self.csv_zeilen()
        for name, ordner in faelle.items():
            with self.subTest(name):
                abgleich = mock.Mock(side_effect=abgleich_attrappe())
                r = self.lauf_mit(abgleich=abgleich, argv=self.argv(**ordner))
                self.assertEqual(r.code, 1)
                self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: fehler – '), r.letzte)
                self.assertIn('Repo', r.letzte)
                self.assertEqual(abgleich.call_count, 0)
                self.assertTrue(os.path.exists(marke), 'nichts gelöscht')
                self.assertEqual(self.csv_zeilen(), vorher)
        self.assertFalse(os.path.exists(os.path.join(self.repo, 'cache')), 'im Repo wird nichts angelegt')

    # Bewertung im Ablauf
    def test_geaendert_braucht_gepusht_und_neuen_head_aber_nicht_die_bewertung_ok(self):
        zeile = 'NACHTRAG: 1 geändert (1 ergänzt), gepusht'
        teilausfall = ('NACHTRAG: Fehler – 6 Mannschaften übersprungen, kein Cache-Stand vorhanden: E1, E2, E3, F1, F2, G1; '
                       '1 geändert (1 ergänzt), gepusht, 1 Hinweis')
        faelle = [
            (zeile, 0, 'a', 'b', 'true', 'ok'),
            (zeile, 0, 'a', 'a', 'false', 'ok'),                                      # ohne Commit bliebe HEAD, wo er ist
            ('NACHTRAG: unverändert', 0, 'a', 'b', 'false', 'ok'),                    # nur pull --rebase hat HEAD bewegt
            ('NACHTRAG: 1 geändert (1 ergänzt), nicht gepusht', 0, 'a', 'b', 'false', 'fehler'),
            ('NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben', 0, 'a', 'b', 'false', 'ok'),
            (teilausfall, 2, 'a', 'b', 'true', 'fehler'),                             # teilweiser Ausfall: rot, aber es liegt Neues auf main
            (teilausfall, 2, 'a', 'a', 'false', 'fehler'),                            # … ohne neuen HEAD wurde nichts gepusht
            ('NACHTRAG: Fehler – x; 1 geändert (1 ergänzt), gepusht', 1, 'a', 'b', 'true', 'fehler'),
            ('NACHTRAG: Fehler – Push fehlgeschlagen (remote: gesperrt); der Commit bleibt lokal, der nächste Lauf pusht ihn nach; '
             '1 geändert (1 ergänzt)', 1, 'a', 'b', 'false', 'fehler'),               # nur lokal committet: auf main liegt nichts Neues
            ('NACHTRAG: Fehler – seltsam), gepusht', 1, 'a', 'b', 'false', 'fehler'),  # kein „n geändert (…)“ davor
            (zeile, 1, 'a', 'b', 'true', 'fehler'),                                   # Widerspruch zum Exit-Code: gepusht bleibt gepusht
            ('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)', 2, 'a', 'b', 'false', 'warnung'),
            (zeile, 0, None, None, 'false', 'ok'),                                    # HEAD nicht zu ermitteln: nichts zu vergleichen
        ]
        for schluss, code, vorher, nachher, geaendert, bewertung in faelle:
            with self.subTest(schluss, code=code, vorher=vorher, nachher=nachher):
                with mock.patch.object(NA, 'kopf_von', side_effect=[vorher, nachher]):
                    r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe(schluss, code=code)))
                self.assertEqual(r.ausgabe_zeilen, ['geaendert=' + geaendert, 'status=' + schluss])
                self.assertEqual(r.letzte, 'NACHTRAG-AKTION: %s – %s' % (bewertung, schluss))
                self.assertEqual(r.code, 1 if bewertung == 'fehler' else 0)

    def test_kopf_wird_vor_und_nach_dem_nachtrag_gelesen(self):
        reihenfolge = []
        with mock.patch.object(NA, 'kopf_von', side_effect=lambda repo: reihenfolge.append(('kopf', repo)) or 'a'):
            self.lauf_mit(nachtrag=mock.Mock(side_effect=lambda argv: reihenfolge.append('nachtrag') or print('NACHTRAG: unverändert') or 0))
        self.assertEqual(reihenfolge, [('kopf', self.repo), 'nachtrag', ('kopf', self.repo)])

    def test_ohne_schlusszeile_ist_ein_fehler(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('irgendeine Zeile', 'Hinweis: nichts', code=0)))
        self.assertEqual(r.code, 1)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – keine Schlusszeile des Nachtrags (Exit 0)')
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=keine Schlusszeile des Nachtrags (Exit 0)'])
        self.assertIn('Auswertung: keine Schlusszeile des Nachtrags', r.zeilen)

    def test_unerwartete_ausnahme_oder_systemexit_des_nachtrags_ist_ein_fehler(self):
        for name, fehler in (('Ausnahme', RuntimeError('kaputt')), ('SystemExit', SystemExit(0))):
            with self.subTest(name):
                r = self.lauf_mit(nachtrag=mock.Mock(side_effect=fehler))
                self.assertEqual(r.code, 1)
                self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – Nachtrag unerwartet beendet (Ausnahme oder SystemExit)')
                self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=false')

    def test_schlusszeile_ist_die_letzte_zeile_die_mit_dem_praefix_beginnt(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: Fehler – alt', 'Hinweis: x', 'NACHTRAG: unverändert', '', code=0)))
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: unverändert')

    def test_widerspruch_zwischen_schlusszeile_und_exit_code_ist_ein_fehler_mit_erklaerung(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: unverändert', code=1)))
        self.assertEqual(r.code, 1)
        self.assertIn('Auswertung: Exit-Code 1 des Nachtrags widerspricht der Schlusszeile (erwartet: 0)', r.zeilen)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – NACHTRAG: unverändert')

    def test_warnung_ist_exit_0_mit_anmerkung_und_gruener_letzter_zeile(self):
        schluss = 'NACHTRAG: Auffällig, nicht übernommen – 2 Hinweise (siehe oben)'
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('Hinweis: a', 'Hinweis: b', schluss, code=2)))
        self.assertEqual(r.code, 0)
        self.assertEqual(r.zeilen[-2:], ['::warning::' + schluss, 'NACHTRAG-AKTION: warnung – ' + schluss])
        self.assertFalse([z for z in r.zeilen if z.startswith('::error::')])
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=' + schluss])

    def test_fehler_ist_exit_1_mit_anmerkung_und_maskierung(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('NACHTRAG: Fehler – 100% kaputt\rzweite Zeile', code=1)))
        self.assertEqual(r.code, 1)
        self.assertEqual(r.zeilen[-2], '::error::NACHTRAG: Fehler – 100%25 kaputt%0Dzweite Zeile')
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – NACHTRAG: Fehler – 100% kaputt zweite Zeile')
        self.assertEqual(len(r.ausgabe_zeilen), 2, 'status bleibt in einer Zeile')
        self.assertEqual(r.ausgabe_zeilen[1], 'status=NACHTRAG: Fehler – 100% kaputt zweite Zeile')

    def test_ok_hat_keine_anmerkung(self):
        r = self.lauf_mit()
        self.assertFalse([z for z in r.zeilen if z.startswith('::')])

    # GITHUB_OUTPUT und GITHUB_STEP_SUMMARY
    def test_github_output_und_zusammenfassung(self):
        schluss = 'NACHTRAG: 1 geändert (1 ergänzt), gepusht'
        with mock.patch.object(NA, 'kopf_von', side_effect=['a', 'b']):
            r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('ergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1', schluss)))
        self.assertEqual(r.ausgabe, 'geaendert=true\nstatus=' + schluss + '\n')
        self.assertEqual(r.zusammenfassung, '## Spielplan-Nachtrag (Cloud-Sicherung)\n\nModus: schreiben und pushen\n\n'
                                            '**ok**: `' + schluss + '`\n\n'
                                            '```text\nergänzt: D1 So 29.11. 13:00 Heim gegen FC Neu 1\n' + schluss + '\n```\n\n')

    def test_zusammenfassung_im_probelauf_nennt_den_modus_und_hat_einen_sicheren_zaun(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('Vorschlag ``` ausbruch', 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben')),
                          argv=self.argv('--nur-bericht'))
        self.assertIn('Modus: nur Bericht (nichts geschrieben, nichts gepusht)', r.zusammenfassung)
        self.assertIn('````text\nVorschlag ``` ausbruch\n', r.zusammenfassung)
        self.assertTrue(r.zusammenfassung.endswith('````\n\n'))

    def test_ausgabedateien_werden_angehaengt_nicht_ersetzt(self):
        ausgabe, zusammenfassung = os.path.join(self.dir, 'out'), os.path.join(self.dir, 'summary')
        for pfad, inhalt in ((ausgabe, 'vorher=1\n'), (zusammenfassung, '# Vorheriger Schritt\n')):
            with open(pfad, 'w') as f:
                f.write(inhalt)
        umgebung = {'GITHUB_OUTPUT': ausgabe, 'GITHUB_STEP_SUMMARY': zusammenfassung}
        self.lauf_mit(github=False, umgebung=umgebung)
        self.lauf_mit(github=False, umgebung=umgebung)
        with open(ausgabe, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'vorher=1\n' + 'geaendert=false\nstatus=NACHTRAG: unverändert\n' * 2)
        with open(zusammenfassung, encoding='utf-8') as f:
            inhalt = f.read()
        self.assertTrue(inhalt.startswith('# Vorheriger Schritt\n## Spielplan-Nachtrag (Cloud-Sicherung)\n'))
        self.assertEqual(inhalt.count('## Spielplan-Nachtrag (Cloud-Sicherung)'), 2)

    def test_ohne_github_variablen_entstehen_keine_dateien_und_kein_fehler(self):
        vorher = set(os.listdir(self.dir))
        r = self.lauf_mit(github=False)
        self.assertEqual(r.code, 0, r.alles)
        self.assertIsNone(r.ausgabe)
        self.assertEqual(set(os.listdir(self.dir)) - vorher, {'cache', 'berichte', 'zustand'})

    def test_nicht_beschreibbares_github_output_ist_ein_fehler(self):
        """Ohne GITHUB_OUTPUT erführe die Aktion nichts vom Ergebnis (auch nicht, dass der Kalender neu zu bauen ist)."""
        kaputt = os.path.join(self.dir, 'gibt-es-nicht', 'github-output')
        r = self.lauf_mit(github=False, umgebung={'GITHUB_OUTPUT': kaputt})
        self.assertEqual(r.code, 1)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – NACHTRAG: unverändert (GITHUB_OUTPUT nicht beschreibbar (FileNotFoundError))')
        self.assertIn('::error::NACHTRAG: unverändert (GITHUB_OUTPUT nicht beschreibbar (FileNotFoundError))', r.zeilen)
        self.assertNotIn(kaputt, r.alles, 'der Pfad (Wert der Variablen) wird nicht ausgegeben')

    def test_nicht_beschreibbare_zusammenfassung_bleibt_ohne_folgen(self):
        r = self.lauf_mit(github=False, umgebung={'GITHUB_STEP_SUMMARY': os.path.join(self.dir, 'gibt-es-nicht', 'summary')})
        self.assertEqual(r.code, 0)
        self.assertIn('Hinweis: GITHUB_STEP_SUMMARY nicht beschreibbar (FileNotFoundError)', r.err)

    # Durchreichen und Geheimhaltung
    def test_ausgabe_des_nachtrags_wird_unveraendert_und_in_ordnung_ausgegeben(self):
        r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe('Vorschlag eins', 'Hinweis: zwei', 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)',
                                                                           code=2)))
        i = r.zeilen.index('Vorschlag eins')
        self.assertEqual(r.zeilen[i:i + 3], ['Vorschlag eins', 'Hinweis: zwei', 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)'])
        self.assertIn('Spielplan-Abgleich (Attrappe)', r.zeilen[:i], 'die Ausgabe des Abgleichs kommt davor')
        self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: '))

    def test_umgebungsvariablen_und_zugangsdaten_werden_nie_ausgegeben(self):
        geheim = 'ghs_GEHEIMER_TOKEN_0123456789'
        umgebung = {'GITHUB_TOKEN': geheim, 'GH_TOKEN': geheim, 'RUNNER_NAME': 'geheimer-runner-name'}
        for schluss, code in (('NACHTRAG: unverändert', 0), ('NACHTRAG: Fehler – x', 1), ('NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)', 2)):
            r = self.lauf_mit(nachtrag=mock.Mock(side_effect=nachtrag_attrappe(schluss, code=code)), umgebung=umgebung)
            for wert in (geheim, 'geheimer-runner-name'):
                self.assertNotIn(wert, r.alles)
        r = lauf_ausfuehren(['--bogus'], github=self.dir, umgebung=umgebung)
        self.assertNotIn(geheim, r.alles)

    def test_ungueltiger_aufruf_hat_letzte_zeile_und_ausgabe(self):
        abgleich = mock.Mock(side_effect=abgleich_attrappe())
        with mock.patch.object(A, 'main', abgleich):
            r = lauf_ausfuehren(['--bogus'], github=self.dir)
        self.assertEqual(r.code, 1)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – ungültiger Aufruf (unrecognized arguments: --bogus)')
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=ungültiger Aufruf (unrecognized arguments: --bogus)'])
        self.assertIn('usage:', r.err)
        self.assertEqual(abgleich.call_count, 0)

    def test_arbeitsordner_nicht_anlegbar_ist_ein_fehler(self):
        sperre = os.path.join(self.dir, 'datei')
        with open(sperre, 'w') as f:
            f.write('x')
        abgleich = mock.Mock(side_effect=abgleich_attrappe())
        r = self.lauf_mit(abgleich=abgleich, argv=self.argv(zustand=os.path.join(sperre, 'zustand')))
        self.assertEqual(r.code, 1)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – Arbeitsordner nicht vorbereitet (NotADirectoryError)')
        self.assertEqual(abgleich.call_count, 0)

    def test_der_docstring_beschreibt_aufruf_bewertung_und_exit_codes(self):
        for text in ('python3 -I tools/nachtrag_aktion.py', '--nur-bericht', 'NACHTRAG-AKTION: <ok|warnung|fehler>', 'Exit-Code: 0 = ok oder warnung, 1 = fehler',
                     'GITHUB_OUTPUT', 'GITHUB_STEP_SUMMARY', 'geaendert=true', 'Rücksicht auf fussball.de', 'build.yml', '--nur-cache',
                     'abgleich.CACHE_STANDARD', 'ohne etwas zu löschen', 'unabhängig von der Bewertung', 'Kalenderbau wird trotzdem angestoßen',
                     'unbrauchbaren Antwort', 'Wache um abgleich.hole'):
            self.assertIn(text, ' '.join(NA.__doc__.split()))


# ---------------------------------------------------------------- Ende zu Ende: lokaler Server, Wegwerf-Remote, echter Abgleich und Nachtrag

class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):          # ohne socket.getfqdn: keine Namensauflösung im Test
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


class Planserver:
    """Lokaler HTTP-Server auf 127.0.0.1: liefert je fussball.de-Team-ID den synthetischen Plan der Welt (die D4 aus welt.zusatz) und zählt
    jede Anfrage. `status`: {Team-ID: HTTP-Status} für Pläne, die der Server abweist. `ersatz`: {Team-ID: Text} statt des Plans, mit HTTP 200
    (z. B. eine Sperrseite)."""

    def __init__(self, welt):
        self.welt, self.anfragen, self.status, self.ersatz = welt, [], {}, {}
        planserver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                teile = self.path.split('?')[0].strip('/').split('/')
                tid = teile[1] if len(teile) > 1 and teile[0] == 'plan' else ''
                planserver.anfragen.append((tid, self.headers.get('User-Agent', '')))
                status, text = planserver.status.get(tid, 200), planserver.seite(tid)
                if status != 200 or text is None:
                    self.send_error(status if status != 200 else 404)
                    return
                daten = text.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(daten)))
                self.end_headers()
                self.wfile.write(daten)

            def log_message(self, *args):       # kein Protokoll auf stderr
                pass

        self.httpd = _Server(('127.0.0.1', 0), Handler)
        self.vorlage = 'http://127.0.0.1:%d/plan/{tid}/{von}/{bis}' % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    def seite(self, tid):
        if tid in self.ersatz:
            return self.ersatz[tid]
        for kuerzel, slug, name, team_id in A.TEAMS:
            if team_id == tid:
                return T.seite_aus(self.welt.seite[kuerzel])
        for kuerzel, slug, name, team_id in A.ZUSATZQUELLEN:
            if team_id == tid:
                return T.seite_aus(self.welt.zusatz[slug], zusatz=T.FUELLTEXT)
        return None

    def beenden(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(10)


def neues_spiel(team='D1', nr=40, datum='2026-11-29', zeit='13:00', gast='FC Neu 1', **kw):
    """Ein Spiel, das nur bei fussball.de steht (die CSV kennt es nicht)."""
    return T.Spiel(team, T.LIGA[team], nr, datum, zeit, T.EIGEN[team], gast, **kw)


class Aktion:
    """Wegwerf-Welt für Läufe des Wrappers: Bare-Remote als origin, Arbeitsklon (der Repo-Stamm der CSV), ein zweiter Klon für fremde Commits,
    der lokale Planserver und Ordner für Cache, Berichte und Zustand daneben (nie im Repo)."""

    def __init__(self, test):
        self.tmp = tempfile.TemporaryDirectory(prefix='nachtrag-aktion-')
        test.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.welt = T.Welt()
        self.bare = os.path.join(self.dir, 'remote.git')
        subprocess.run(['git', 'init', '--bare', '-q', '-b', 'main', self.bare], check=True)
        saat = os.path.join(self.dir, 'saat')
        os.makedirs(os.path.join(saat, 'docs'))
        for name in ('build_ics.py', 'vorlage_index.html'):
            shutil.copy(os.path.join(REPO, name), os.path.join(saat, name))
        with open(os.path.join(saat, 'spiele.csv'), 'wb') as f:
            f.write(self.welt.csv_bytes())
        with open(os.path.join(saat, 'README.md'), 'w') as f:
            f.write('Test-Repo\n')
        with open(os.path.join(saat, 'docs', 'alt.ics'), 'w') as f:
            f.write('x\n')
        T.git_ok(self.dir, 'init', '-q', '-b', 'main', saat)
        T.git_ok(saat, 'add', '-A')
        T.git_ok(saat, 'commit', '-q', '-m', 'Start')
        T.git_ok(saat, 'remote', 'add', 'origin', self.bare)
        T.git_ok(saat, 'push', '-q', 'origin', 'main')
        self.repo, self.andere = os.path.join(self.dir, 'arbeit'), os.path.join(self.dir, 'andere')
        for ziel in (self.repo, self.andere):
            T.git_ok(self.dir, 'clone', '-q', self.bare, ziel)
        self.csv = os.path.join(self.repo, 'spiele.csv')
        self.cache, self.berichte, self.zustand = (os.path.join(self.dir, n) for n in ('cache', 'berichte', 'zustand'))
        self.server = Planserver(self.welt)
        test.addCleanup(self.server.beenden)

    def lauf(self, *extra):
        argv = ['--csv', self.csv, '--cache', self.cache, '--bericht-ordner', self.berichte, '--zustand-ordner', self.zustand,
                '--url-vorlage', self.server.vorlage] + list(extra)
        return lauf_ausfuehren(argv, github=self.dir, uhr=True)

    def csv_lesen(self):
        with open(self.csv, 'rb') as f:
            return f.read()

    def kopf(self):
        return T.git_ok(self.repo, 'rev-parse', 'HEAD')

    def remote_log(self):
        return T.git_ok(self.bare, 'log', '--format=%s', 'main').splitlines()

    def stand(self):
        """Alles, was ein Lauf ohne Änderung nicht anfasst: CSV, HEAD, origin (Spitze und Verlauf), Arbeitsbaum."""
        return (self.csv_lesen(), self.kopf(), T.git_ok(self.bare, 'rev-parse', 'main'), self.remote_log(), T.git_ok(self.repo, 'status', '--porcelain'))

    def fremder_commit(self, name='neu.ics', text='y\n', betreff='Spielplan-Kalender neu gebaut (Lauf 1)'):
        """Ein Commit nur in docs/ von einem anderen Klon aus (so arbeitet build.yml): origin läuft dem Arbeitsklon voraus."""
        with open(os.path.join(self.andere, 'docs', name), 'w') as f:
            f.write(text)
        T.git_ok(self.andere, 'add', '-A')
        T.git_ok(self.andere, 'commit', '-q', '-m', betreff)
        T.git_ok(self.andere, 'push', '-q', 'origin', 'main')


class TestEndeZuEnde(unittest.TestCase):
    def setUp(self):
        self.a = Aktion(self)
        self.netz_vorher = len(NETZ_VERSUCHE)

    def tearDown(self):
        self.assertEqual(NETZ_VERSUCHE[self.netz_vorher:], [], 'Zugriff auf etwas anderes als 127.0.0.1')
        abgleich_unveraendert(self)

    def pruefe_nichts_geschrieben(self, vorher):
        self.assertEqual(self.a.stand(), vorher, 'CSV, HEAD, origin und Arbeitsbaum unverändert')

    # (1) nichts zu tun
    def test_nichts_zu_tun(self):
        a = self.a
        vorher = a.stand()
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: unverändert')
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=NACHTRAG: unverändert'])
        self.assertFalse([z for z in r.zeilen if z.startswith('::')], 'keine Anmerkung')
        self.pruefe_nichts_geschrieben(vorher)
        self.assertIn('**ok**: `NACHTRAG: unverändert`', r.zusammenfassung)

    # (5) Rücksicht
    def test_hoechstens_zwoelf_abrufe_mit_erkennbarem_user_agent_und_unveraenderter_pause(self):
        a = self.a
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        anfragen = a.server.anfragen
        self.assertEqual(len(anfragen), 12, 'elf Mannschaften und die D4, jede genau einmal')
        self.assertEqual([t for t, _ in anfragen], [t[3] for t in A.TEAMS] + [q[3] for q in A.ZUSATZQUELLEN], 'eigene Pläne zuerst, dann die D4')
        for tid, ua in anfragen:
            self.assertEqual(ua, UA_ERWARTET)
            self.assertIn('SpeuzerSpielplanNachtrag', ua)
            self.assertIn('GitHub Actions', ua)
            self.assertNotIn('ein Abruf je Mannschaft und Woche', ua)
            self.assertNotIn('SpeuzerSpielplanAbgleich', ua)
        self.assertEqual((A.PAUSE_SEKUNDEN, A.MIN_ABSTAND_TAGE, A.USER_AGENT), (0, 6, UA_ABGLEICH), 'der Wrapper verändert abgleich nicht')

    def test_cache_stempel_und_uhr_des_nachtrags_passen_zusammen(self):
        a = self.a
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        for slug in [t[1] for t in A.TEAMS] + [q[1] for q in A.ZUSATZQUELLEN]:
            self.assertEqual(os.listdir(os.path.join(a.cache, slug)), [JETZT.strftime('%Y%m%d-%H%M%S') + '.html'], slug)

    # (2) neues Spiel
    def test_neues_spiel_wird_eingetragen_committet_gepusht_und_gemeldet_der_zweite_lauf_aendert_nichts(self):
        a = self.a
        a.welt.seite['D1'].append(neues_spiel())
        csv_vorher, log_vorher = a.csv_lesen(), a.remote_log()
        r = a.lauf()
        schluss = 'NACHTRAG: 1 geändert (1 ergänzt), gepusht'
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – ' + schluss)
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=true', 'status=' + schluss])
        self.assertFalse([z for z in r.zeilen if z.startswith('::')])
        csv_nachher = a.csv_lesen()
        self.assertNotEqual(csv_nachher, csv_vorher)
        zeilen_neu = [z for z in T.zeilen_von(csv_nachher) if z not in T.zeilen_von(csv_vorher)]
        self.assertEqual(len(zeilen_neu), 1)
        self.assertTrue(zeilen_neu[0].startswith('D1;') and ';29.11.2026;13:00;' in zeilen_neu[0] and 'FC Neu 1' in zeilen_neu[0], zeilen_neu[0])
        self.assertEqual(a.remote_log(), ['Spieltabelle automatisch aktualisiert (1 Änderung)'] + log_vorher)
        self.assertEqual(T.git_ok(a.bare, 'show', '--name-only', '--format=', 'main').splitlines(), ['spiele.csv'], 'nur die CSV im Commit')
        self.assertEqual(T.git_ok(a.bare, 'rev-parse', 'main'), a.kopf(), 'origin und Arbeitsklon gleich')
        self.assertEqual(T.git_ok(a.repo, 'status', '--porcelain'), '')
        # zweiter Lauf: die CSV kennt das Spiel jetzt
        nachher = a.stand()
        r2 = a.lauf()
        self.assertEqual(r2.code, 0, r2.alles)
        self.assertEqual(r2.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: unverändert')
        self.assertEqual(r2.ausgabe_zeilen, ['geaendert=false', 'status=NACHTRAG: unverändert'])
        self.pruefe_nichts_geschrieben(nachher)

    # (3) Mengenbremse
    def test_mengenbremse_ist_ein_fehler_und_nichts_wird_geschrieben_oder_gepusht(self):
        a = self.a
        for i in range(11):
            team = 'D1' if i < 6 else 'D2'
            a.welt.seite[team].append(neues_spiel(team, 40 + i, (date(2026, 11, 29) + timedelta(days=7 * i)).isoformat(), '11:00', 'FC Neu %d' % i))
        vorher = a.stand()
        r = a.lauf()
        schluss = 'NACHTRAG: Auffällig, nicht übernommen – 11 Änderungen in einem Lauf (Grenze 10)'
        self.assertEqual(r.code, 1, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: fehler – ' + schluss)
        self.assertIn('::error::' + schluss, r.zeilen)
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=' + schluss])
        self.pruefe_nichts_geschrieben(vorher)

    # (4) Abruf abgewiesen
    def test_403_beim_ersten_plan_genau_eine_anfrage_exit_1_nichts_gepusht(self):
        a = self.a
        a.server.status[A.TEAMS[0][3]] = 403
        vorher = a.stand()
        r = a.lauf()
        self.assertEqual(len(a.server.anfragen), 1, 'nach dem Fehlschlag kein weiterer Abruf, auch nicht durch den Nachtrag')
        self.assertEqual(a.server.anfragen[0][0], A.TEAMS[0][3])
        self.assertEqual(r.code, 1, r.alles)
        self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: fehler – NACHTRAG: Fehler – '), r.letzte)
        self.assertIn('übersprungen', r.letzte)
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=false')
        self.assertTrue([z for z in r.zeilen if z.startswith('::error::NACHTRAG: Fehler – ')])
        self.assertIn('HTTP 403', r.err, 'der Abgleich nennt den Grund auf stderr')
        self.pruefe_nichts_geschrieben(vorher)

    def test_ein_fehlschlag_mitten_im_lauf_ruft_danach_nichts_mehr_ab(self):
        a = self.a
        a.server.status[A.TEAMS[4][3]] = 500
        r = a.lauf()
        self.assertEqual([t for t, _ in a.server.anfragen], [t[3] for t in A.TEAMS[:5]])
        self.assertEqual(r.code, 1, r.alles)
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=false')

    def test_ein_spaeterer_plan_faellt_aus_gepushtes_wird_gemeldet_der_lauf_bleibt_rot(self):
        """Der Server liefert für die erste bis fünfte Mannschaft Pläne (bei der D1 mit einem neuen Spiel) und für die sechste (E1) 403: der Abgleich
        ruft danach nichts mehr ab, der Nachtrag trägt das neue D1-Spiel ein und pusht es und meldet die übrigen als übersprungen. Der Lauf ist rot
        (Exit 1, ::error::), aber geaendert ist true: auf main liegt Neues, der Kalender muss gebaut werden (der Dispatch-Schritt der Vorlage läuft
        dafür mit !cancelled())."""
        a = self.a
        a.welt.seite['D1'].append(neues_spiel())
        a.server.status[A.TEAMS[5][3]] = 403
        log_vorher = a.remote_log()
        r = a.lauf()
        anfang = ('NACHTRAG: Fehler – 6 Mannschaften übersprungen, kein Cache-Stand vorhanden: E1, E2, E3, F1, F2, G1; '
                  '1 geändert (1 ergänzt), gepusht')
        self.assertEqual([t for t, _ in a.server.anfragen], [t[3] for t in A.TEAMS[:6]],
                         'sechs Abrufe: der sechste (403) genau einmal, danach kein weiterer und keine Wiederholung')
        self.assertEqual(r.code, 1, r.alles)
        self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: fehler – ' + anfang), r.letzte)
        self.assertTrue([z for z in r.zeilen if z.startswith('::error::' + anfang)], r.zeilen)
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=true')
        self.assertTrue(r.ausgabe_zeilen[1].startswith('status=' + anfang))
        self.assertEqual(a.remote_log(), ['Spieltabelle automatisch aktualisiert (1 Änderung)'] + log_vorher, 'der Commit liegt auf origin/main')
        self.assertEqual(T.git_ok(a.bare, 'rev-parse', 'main'), a.kopf())
        self.assertIn('FC Neu 1', T.git_ok(a.bare, 'show', 'main:spiele.csv'))
        self.assertIn('HTTP 403', r.err, 'der Abgleich nennt den Grund auf stderr')

    # HTTP 200 mit einer Antwort, die kein Plan ist (Sperr- oder Einwilligungsseite, geänderter Seitenaufbau)
    def test_sperrseite_mit_200_beim_ersten_plan_stoppt_den_abruf(self):
        a = self.a
        a.server.ersatz[A.TEAMS[0][3]] = T.SPERRSEITE
        vorher = a.stand()
        r = a.lauf()
        self.assertEqual(len(a.server.anfragen), 1, 'nach der ersten unbrauchbaren Antwort wird nichts mehr abgerufen')
        self.assertEqual(a.server.anfragen[0][0], A.TEAMS[0][3])
        self.assertEqual(r.code, 1, r.alles)
        self.assertTrue(r.letzte.startswith('NACHTRAG-AKTION: fehler – NACHTRAG: Fehler – '), r.letzte)
        self.assertTrue([z for z in r.zeilen if z.startswith('::error::NACHTRAG: Fehler – ')])
        self.assertIn('Herren: unbrauchbare Antwort (kein Spielplan erkannt', r.err)
        self.assertIn('weitere Abrufe in diesem Lauf entfallen', r.err)
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=false')
        self.pruefe_nichts_geschrieben(vorher)

    def test_sperrseite_mit_200_beim_sechsten_plan_stoppt_den_abruf(self):
        a = self.a
        a.welt.seite['D1'].append(neues_spiel())
        a.server.ersatz[A.TEAMS[5][3]] = T.SPERRSEITE
        r = a.lauf()
        self.assertEqual([t for t, _ in a.server.anfragen], [t[3] for t in A.TEAMS[:6]],
                         'sechs Abrufe: der sechste (Sperrseite) genau einmal, danach keiner')
        self.assertEqual(r.code, 1, r.alles)
        self.assertIn('E1: unbrauchbare Antwort (kein Spielplan erkannt', r.err)
        self.assertTrue([z for z in r.zeilen if z.startswith('::error::')])
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=true', 'die ersten fünf Pläne sind angekommen: das neue D1-Spiel ist gepusht')
        self.assertEqual(a.remote_log()[0], 'Spieltabelle automatisch aktualisiert (1 Änderung)')

    def test_geaenderter_seitenaufbau_beim_ersten_plan_stoppt_den_abruf(self):
        a = self.a
        a.server.ersatz[A.TEAMS[0][3]] = T.seite_aus(a.welt.seite['HERREN']).replace('column-club', 'column-klub')
        vorher = a.stand()
        r = a.lauf()
        self.assertEqual(len(a.server.anfragen), 1)
        self.assertEqual(r.code, 1, r.alles)
        self.assertIn('Herren: unbrauchbare Antwort (Spielzeilen vorhanden, aber keine lesbar (Seitenaufbau geändert))', r.err)
        self.pruefe_nichts_geschrieben(vorher)

    def test_gueltiger_leerer_plan_der_d4_als_letzter_abruf_ist_kein_abbruch(self):
        a = self.a
        d4 = A.ZUSATZQUELLEN[0][3]
        self.assertTrue(A.leerer_plan(a.server.seite(d4)), 'die D4 der Testwelt hat einen gültigen Plan ohne Spiele')
        r = a.lauf()
        self.assertEqual(len(a.server.anfragen), 12)
        self.assertEqual(a.server.anfragen[-1][0], d4, 'die D4 wird als letzte abgerufen')
        self.assertEqual(r.code, 0, r.alles)
        self.assertNotIn('unbrauchbare Antwort', r.err)

    def test_gueltiger_leerer_plan_beim_ersten_plan_ist_kein_abbruch(self):
        """Ein Plan ohne Spiele (Rahmen erkennbar, keine Spielzeilen) ist keine Sperrseite: auch wenn die Herren so antworten, werden alle zwölf Pläne
        abgerufen. Der Lauf ist rot, weil für die Herren kein Plan zu verwerten ist (die CSV hat Spiele)."""
        a = self.a
        a.server.ersatz[A.TEAMS[0][3]] = T.seite_aus([], zusatz=T.FUELLTEXT)
        r = a.lauf()
        self.assertEqual(len(a.server.anfragen), 12)
        self.assertNotIn('unbrauchbare Antwort', r.err)
        self.assertEqual(r.code, 1, r.alles)
        self.assertIn('Herren übersprungen', r.letzte)

    # (5) bloße Hinweise
    def test_bloss_hinweise_sind_eine_warnung_exit_0_nichts_geaendert(self):
        a = self.a
        a.welt.seite['D2'].append(T.Spiel('D2', T.POKAL['D2'], 7, '2026-12-02', '18:15', T.EIGEN['D2'], 'Pokal FC 2', 'Kreispokal',
                                          wettbewerb_text='Hessenpokal'))
        vorher = a.stand()
        r = a.lauf()
        schluss = 'NACHTRAG: Auffällig, nicht übernommen – 1 Hinweis (siehe oben)'
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.zeilen[-2:], ['::warning::' + schluss, 'NACHTRAG-AKTION: warnung – ' + schluss])
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=' + schluss])
        self.assertTrue([z for z in r.zeilen if z.startswith('Hinweis: ')], 'der Hinweis steht im Protokoll')
        self.pruefe_nichts_geschrieben(vorher)

    # (6) origin ist vorausgelaufen
    def test_origin_vorausgelaufen_pull_rebase_dann_push_ohne_datenverlust(self):
        a = self.a
        a.fremder_commit()
        a.welt.seite['D1'].append(neues_spiel())
        kopf_vorher = a.kopf()
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertEqual(r.ausgabe_zeilen[0], 'geaendert=true')
        self.assertEqual(a.remote_log(), ['Spieltabelle automatisch aktualisiert (1 Änderung)', 'Spielplan-Kalender neu gebaut (Lauf 1)', 'Start'])
        self.assertEqual(T.git_ok(a.bare, 'show', '--name-only', '--format=', 'main').splitlines(), ['spiele.csv'])
        self.assertEqual(T.git_ok(a.bare, 'show', 'main:docs/neu.ics'), 'y', 'der fremde Commit ist unversehrt')
        self.assertIn('FC Neu 1', T.git_ok(a.bare, 'show', 'main:spiele.csv'))
        self.assertTrue(os.path.exists(os.path.join(a.repo, 'docs', 'neu.ics')), 'pull --rebase hat docs/ geholt')
        self.assertNotEqual(a.kopf(), kopf_vorher)
        self.assertEqual(T.git_ok(a.repo, 'status', '--porcelain'), '')

    def test_origin_vorausgelaufen_ohne_aenderung_head_bewegt_sich_aber_geaendert_bleibt_false(self):
        a = self.a
        a.fremder_commit()
        kopf_vorher, csv_vorher = a.kopf(), a.csv_lesen()
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: unverändert')
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=NACHTRAG: unverändert'])
        self.assertNotEqual(a.kopf(), kopf_vorher, 'pull --rebase hat HEAD bewegt: nur die Schlusszeile verhindert den überflüssigen Kalenderbau')
        self.assertEqual(a.csv_lesen(), csv_vorher)

    # (7) Probelauf
    def test_nur_bericht_schreibt_nichts_committet_nichts_pusht_nichts(self):
        a = self.a
        a.welt.seite['D1'].append(neues_spiel())
        vorher = a.stand()
        r = a.lauf('--nur-bericht')
        schluss = 'NACHTRAG: Nur-Bericht – 1 Änderung vorgeschlagen, nichts geschrieben'
        self.assertEqual(r.code, 0, r.alles)
        self.assertEqual(r.letzte, 'NACHTRAG-AKTION: ok – ' + schluss)
        self.assertEqual(r.ausgabe_zeilen, ['geaendert=false', 'status=' + schluss])
        self.pruefe_nichts_geschrieben(vorher)
        self.assertIn('Modus: nur Bericht (nichts geschrieben, nichts gepusht)', r.zusammenfassung)
        self.assertEqual(len(a.server.anfragen), 12, 'der Probelauf holt die Pläne wie der echte Lauf')

    # (d) zustandslos
    def test_wiederholter_lauf_im_selben_cache_ordner_ruft_wieder_ab_und_sieht_den_neuen_stand(self):
        a = self.a
        r1 = a.lauf()
        self.assertEqual(r1.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: unverändert')
        self.assertEqual(len(a.server.anfragen), 12)
        a.welt.seite['D1'].append(neues_spiel())
        r2 = a.lauf()
        self.assertEqual(len(a.server.anfragen), 24, 'der Cache des ersten Laufs ist weg: es wird wieder abgerufen')
        self.assertEqual(r2.letzte, 'NACHTRAG-AKTION: ok – NACHTRAG: 1 geändert (1 ergänzt), gepusht')
        self.assertEqual(r2.ausgabe_zeilen[0], 'geaendert=true')

    def test_berichte_und_sperre_liegen_ausserhalb_des_repos(self):
        a = self.a
        a.welt.seite['D1'].append(neues_spiel())
        r = a.lauf()
        self.assertEqual(r.code, 0, r.alles)
        self.assertTrue(os.path.exists(os.path.join(a.zustand, 'lauf.lock')), 'die Sperre des Nachtrags liegt im Zustandsordner')
        self.assertTrue([n for n in os.listdir(a.berichte) if n.startswith('nachtrag-')], 'Bericht des Nachtrags')
        self.assertTrue([n for n in os.listdir(a.berichte) if n.startswith('bericht-')], 'Bericht des Abgleichs')
        self.assertEqual(sorted(os.listdir(a.repo)), ['.git', 'README.md', 'build_ics.py', 'docs', 'spiele.csv', 'vorlage_index.html'], 'nichts Neues im Repo')


# ---------------------------------------------------------------- Aufruf unter python3 -I (Unterprozess)

class TestAufruf(unittest.TestCase):
    """Der Wrapper als Programm: python3 -I (unter -I fehlt das Skriptverzeichnis in sys.path), Wegwerf-HOME und ein Proxy auf einen toten
    lokalen Port (käme je ein Abruf zustande, erreichte er nie fussball.de). Die Fälle brauchen weder Netz noch die Uhr."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nachtrag-aktion-aufruf-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        os.makedirs(os.path.join(self.dir, 'home'))
        os.makedirs(os.path.join(self.dir, 'repo'))
        self.proxy = 'http://127.0.0.1:9'

    def aufruf(self, *args, github=True):
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': os.path.join(self.dir, 'home'), 'TMPDIR': self.dir,
               'http_proxy': self.proxy, 'https_proxy': self.proxy, 'HTTP_PROXY': self.proxy, 'HTTPS_PROXY': self.proxy, 'no_proxy': ''}
        if github:
            env.update(GITHUB_OUTPUT=os.path.join(self.dir, 'github-output'), GITHUB_STEP_SUMMARY=os.path.join(self.dir, 'github-summary'))
        return subprocess.run([sys.executable, '-I', '-B', os.path.join(HIER, 'nachtrag_aktion.py')] + list(args), capture_output=True, text=True,
                              timeout=120, env=env, cwd=self.dir)

    def test_ungueltiger_aufruf(self):
        r = self.aufruf('--bogus')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertTrue(r.stdout.splitlines()[-1].startswith('NACHTRAG-AKTION: fehler – ungültiger Aufruf ('), r.stdout)
        self.assertIn('usage:', r.stderr)
        with open(os.path.join(self.dir, 'github-output'), encoding='utf-8') as f:
            self.assertTrue(f.read().startswith('geaendert=false\nstatus=ungültiger Aufruf ('))

    def test_hilfe_beendet_mit_exit_0(self):
        r = self.aufruf('--help')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('--nur-bericht', r.stdout)
        self.assertNotIn('--url-vorlage', r.stdout, 'versteckt, nur für Tests')

    def test_fehlende_csv_ist_ein_fehler_vor_dem_ersten_abruf(self):
        r = self.aufruf('--csv', os.path.join(self.dir, 'repo', 'gibt-es-nicht.csv'), '--cache', os.path.join(self.dir, 'cache'),
                        '--bericht-ordner', os.path.join(self.dir, 'berichte'), '--zustand-ordner', os.path.join(self.dir, 'zustand'))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], 'NACHTRAG-AKTION: fehler – Abruf/Abgleich fehlgeschlagen (Exit 1)')
        self.assertIn('::error::Abruf/Abgleich fehlgeschlagen (Exit 1)', r.stdout.splitlines())
        self.assertIn('FileNotFoundError', r.stderr)
        self.assertNotIn('Abruf fehlgeschlagen (', r.stderr, 'es kam nie zu einem Abruf')
        with open(os.path.join(self.dir, 'github-output'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'geaendert=false\nstatus=Abruf/Abgleich fehlgeschlagen (Exit 1)\n')
        with open(os.path.join(self.dir, 'github-summary'), encoding='utf-8') as f:
            self.assertIn('**fehler**: `Abruf/Abgleich fehlgeschlagen (Exit 1)`', f.read())

    def test_cache_des_mac_tasks_wird_unter_python_i_abgewiesen(self):
        """Der Standardort des Mac-Task-Caches (abgleich.CACHE_STANDARD) liegt hier im Wegwerf-HOME: weder er noch ein Ordner darüber wird geleert."""
        home = os.path.join(self.dir, 'home')
        standard = (os.path.join(home, 'Library', 'Caches', 'speuzer-spielplan-abgleich') if sys.platform == 'darwin'
                    else os.path.join(home, '.cache', 'speuzer-spielplan-abgleich'))
        marke = os.path.join(standard, 'd1', '20260101-000000.html')
        os.makedirs(os.path.dirname(marke))
        with open(marke, 'w') as f:
            f.write('Stand des Mac-Tasks')
        csv = os.path.join(self.dir, 'repo', 'spiele.csv')
        with open(csv, 'wb') as f:
            f.write(T.Welt().csv_bytes())
        for cache in (standard, os.path.dirname(standard), home):
            with self.subTest(cache=cache[len(self.dir):]):
                r = self.aufruf('--csv', csv, '--cache', cache)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertEqual(r.stdout.splitlines()[-1], 'NACHTRAG-AKTION: fehler – Cache-Ordner gehört dem Mac-Task, nichts gelöscht')
                self.assertTrue(os.path.exists(marke), 'nichts gelöscht')

    def test_cache_im_repo_wird_unter_python_i_abgewiesen(self):
        csv = os.path.join(self.dir, 'repo', 'spiele.csv')
        with open(csv, 'wb') as f:
            f.write(T.Welt().csv_bytes())
        r = self.aufruf('--csv', csv, '--cache', os.path.join(self.dir, 'repo', 'cache'))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.splitlines()[-1], 'NACHTRAG-AKTION: fehler – Cache liegt im Repo, nichts getan')
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'repo', 'cache')))


# ---------------------------------------------------------------- Quelltext des Wrappers

class TestQuelltext(unittest.TestCase):
    def setUp(self):
        with open(NA.__file__, encoding='utf-8') as f:
            self.quelle = f.read()

    def test_ohne_gewalt_befehle_ohne_eigene_abrufe_ohne_eigene_unterprozesse(self):
        for verboten in ('--for' + 'ce', 'reset --' + 'hard', 'push -' + 'f', "'-" + "f'", 'urlopen', 'requests', 'os.system', 'subprocess', 'socket',
                         'Popen', 'eval(', 'exec('):
            self.assertNotIn(verboten, self.quelle, verboten)

    def test_geloescht_wird_nur_in_den_plan_ordnern_des_cache(self):
        self.assertEqual(self.quelle.count('rmtree('), 1)
        for verboten in ('os.unlink(', 'os.remove(', 'os.rmdir(', 'shutil.move('):
            self.assertNotIn(verboten, self.quelle, verboten)
        funktion = inspect.getsource(NA.cache_leeren)
        self.assertIn('rmtree(', funktion)
        self.assertIn('slugs()', funktion)
        self.assertNotIn('os.listdir', funktion, 'der Ordner wird nicht durchsucht: es gibt nur die Namen der zwölf Pläne')

    def test_nur_standardbibliothek_und_die_beiden_schwesterskripte(self):
        module = set()
        for knoten in ast.walk(ast.parse(self.quelle)):
            if isinstance(knoten, ast.Import):
                module |= {a.name.split('.')[0] for a in knoten.names}
            elif isinstance(knoten, ast.ImportFrom):
                module.add(knoten.module.split('.')[0])
        self.assertEqual(module - set(sys.stdlib_module_names), {'abgleich', 'nachtrag'})

    def test_syntax_hoechstens_python_3_12(self):
        """Die Aktion läuft auf Python 3.12 (ubuntu-latest): keine Syntax, die erst später dazukam (Wrapper und Tests)."""
        for pfad in (NA.__file__, __file__):
            with open(pfad, encoding='utf-8') as f:
                ast.parse(f.read(), filename=pfad, feature_version=(3, 12))

    def test_umgebungsvariablen_werden_nur_gelesen_und_nie_gedruckt_oder_weitergegeben(self):
        baum = ast.parse(self.quelle)
        aufrufe = [k for k in ast.walk(baum) if isinstance(k, ast.Call)]
        direkt = {k.args[0].value for k in aufrufe if isinstance(k.func, ast.Attribute) and k.func.attr == 'get' and 'environ' in ast.dump(k.func.value)
                  and k.args and isinstance(k.args[0], ast.Constant)}
        ueber_anhaengen = {k.args[0].value for k in aufrufe if isinstance(k.func, ast.Name) and k.func.id == 'anhaengen'
                           and k.args and isinstance(k.args[0], ast.Constant)}
        self.assertEqual(direkt, {'RUNNER_TEMP'})
        self.assertEqual(ueber_anhaengen, {'GITHUB_OUTPUT', 'GITHUB_STEP_SUMMARY'})
        for k in aufrufe:
            if isinstance(k.func, ast.Name) and k.func.id == 'print':
                self.assertNotIn('environ', ast.dump(k), 'print mit einer Umgebungsvariablen')
        for verboten in ('os.environ)', 'environ.items', 'environ.copy', 'environ.keys', 'environ.values', 'getenv'):
            self.assertNotIn(verboten, self.quelle, verboten)


# ---------------------------------------------------------------- Uhr

class TestUhr(unittest.TestCase):
    """Kein Testergebnis hängt von der echten Uhr ab: Wrapper und Tests lesen sie nirgends; nachtrag und abgleich bekommen die feste Uhr."""

    def quelltext(self, pfad):
        with open(pfad, encoding='utf-8') as f:
            return f.read()

    UHREN = ('date' + 'time.now', 'date' + '.today', 'time' + '.time', 'utc' + 'now', 'mono' + 'tonic', 'perf_' + 'counter', 'import ' + 'time')

    def test_wrapper_liest_die_uhr_nirgends(self):
        text = self.quelltext(NA.__file__)
        for name in self.UHREN + ('datetime',):
            self.assertNotIn(name, text)

    def test_testmodul_fragt_die_echte_uhr_nirgends_ab(self):
        """Die einzige Uhr des Testmoduls ist Fest.now(); die Namen der echten Uhr kommen im Quelltext nur in dieser Prüfung vor (zerlegt)."""
        text = self.quelltext(__file__)
        for name in self.UHREN:
            self.assertNotIn(name, text)

    def test_feste_uhr_gilt_fuer_nachtrag_und_abgleich_und_endet_danach(self):
        with feste_uhr():
            for modul in (N, A):
                jetzt = getattr(modul, 'datetime').now()
                self.assertEqual(jetzt, JETZT)
                self.assertEqual(jetzt.date().isoformat(), T.HEUTE)
                self.assertEqual(getattr(modul, 'datetime').strptime('20261026-071000', '%Y%m%d-%H%M%S'), JETZT)
        self.assertIs(N.datetime, datetime)
        self.assertIs(A.datetime, datetime)


# ---------------------------------------------------------------- Isolation

class TestIsolation(unittest.TestCase):
    def test_netzsperre_laesst_nur_loopback_durch_und_zaehlt_alles_andere(self):
        """Selbsttest der Sperre mit einem Namen, den es nicht gibt (.invalid, RFC 6761): scheitert die Sperre, scheitert schon die Namensauflösung."""
        vorher = len(NETZ_VERSUCHE)
        with self.assertRaises(OSError):
            socket.create_connection(('nicht-erreichbar.invalid', 80), timeout=1)
        with self.assertRaises(OSError):
            socket.getaddrinfo('nicht-erreichbar.invalid', 80)
        with self.assertRaises(OSError):
            urllib.request.urlopen('http://nicht-erreichbar.invalid/', timeout=1)
        with self.assertRaises(OSError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(('192.0.2.1', 80))      # TEST-NET-1 (RFC 5737), nie geroutet
        self.assertEqual(len(NETZ_VERSUCHE) - vorher, 4)
        del NETZ_VERSUCHE[vorher:]           # absichtlich ausgelöst: tearDownModule soll sie nicht als Fehler zählen

    def test_standardpfade_und_home_zeigen_auf_den_waechter(self):
        self.assertTrue(A.CACHE_STANDARD.startswith(_standard))
        self.assertTrue(A.BERICHTE_STANDARD.startswith(_standard))
        self.assertTrue(N.NOTAUS_STANDARD.startswith(_standard))
        self.assertTrue(N.BERICHTE_STANDARD.startswith(_standard))
        self.assertEqual(os.environ['HOME'], _home)
        self.assertEqual(os.environ['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertTrue(os.environ['GIT_CONFIG_GLOBAL'].startswith(_waechter))
        self.assertEqual({n: v for n, v in os.environ.items() if n.lower().endswith('_proxy')}, {'no_proxy': '*', 'NO_PROXY': '*'})

    def test_im_namensraum_liegen_nur_eigene_testklassen(self):
        """Wer die TestCase-Klassen von test_nachtrag hierher importierte, liefe die 299 bestehenden Tests ein zweites Mal und ohne deren Netzsperre."""
        modul = sys.modules[__name__]
        fremd = [n for n, o in vars(modul).items() if isinstance(o, type) and issubclass(o, unittest.TestCase) and o.__module__ != __name__]
        self.assertEqual(fremd, [])
        geladen = unittest.defaultTestLoader.loadTestsFromModule(modul)
        namen = {t.__class__.__module__ for gruppe in geladen for t in (gruppe if hasattr(gruppe, '__iter__') else [gruppe])}
        self.assertEqual(namen, {__name__})

    def test_pause_des_abgleichs_steht_im_test_auf_null_und_im_modul_auf_drei(self):
        """Für die Geschwindigkeit steht A.PAUSE_SEKUNDEN im Test auf 0; im Quelltext von abgleich.py bleibt es bei 3 Sekunden."""
        self.assertEqual(A.PAUSE_SEKUNDEN, 0)
        with open(A.__file__, encoding='utf-8') as f:
            self.assertIn('\nPAUSE_SEKUNDEN = 3\n', f.read())


# ---------------------------------------------------------------- Workflow-Vorlage und README

def run_bloecke(zeilen):
    """Die Texte aller run:-Schritte (einzeilig oder als Block) einer Workflow-Datei."""
    bloecke = []
    i = 0
    while i < len(zeilen):
        m = re.match(r'^(\s*)(?:- )?run:\s*(.*)$', zeilen[i])
        if not m:
            i += 1
            continue
        einzug, rest = len(m.group(1)), m.group(2).strip()
        if rest in ('|', '>', '|-', '>-'):
            block = []
            i += 1
            while i < len(zeilen) and (not zeilen[i].strip() or len(zeilen[i]) - len(zeilen[i].lstrip()) > einzug):
                block.append(zeilen[i])
                i += 1
            bloecke.append('\n'.join(block))
        else:
            bloecke.append(rest)
            i += 1
    return bloecke


class TestVorlageOrt(unittest.TestCase):
    """Die Vorlagentests finden die Workflow-Datei vor und nach dem Verschieben (Inbetriebnahme, Schritt 1) und werden nur übersprungen, wenn es keine
    von beiden gibt: sonst blieben die aktive Datei und der README-Abschnitt nach dem Verschieben ungeschützt."""

    def test_vorlage_wird_an_beiden_orten_gefunden_und_nur_ohne_beide_uebersprungen(self):
        with tempfile.TemporaryDirectory(prefix='nachtrag-aktion-ort-') as d:
            repo = os.path.join(d, 'repo')
            tools = os.path.join(repo, 'tools')
            os.makedirs(tools)
            self.assertIsNone(finde_vorlage(repo, tools), 'keine der beiden Dateien: nur dann werden die Vorlagentests übersprungen')
            vorlage = os.path.join(tools, 'nachtrag-aktion.yml')
            aktiv = os.path.join(repo, '.github', 'workflows', 'nachtrag.yml')
            open(vorlage, 'w').close()
            self.assertEqual(finde_vorlage(repo, tools), vorlage, 'vor dem Verschieben')
            os.makedirs(os.path.dirname(aktiv))
            open(aktiv, 'w').close()
            self.assertEqual(finde_vorlage(repo, tools), aktiv, 'liegen beide da, zählt die aktive')
            os.remove(vorlage)
            self.assertEqual(finde_vorlage(repo, tools), aktiv, 'nach dem Verschieben')

    def test_liegen_im_klon_beide_dateien_da_sind_sie_gleich(self):
        vorlage, aktiv = os.path.join(HIER, 'nachtrag-aktion.yml'), os.path.join(REPO, '.github', 'workflows', 'nachtrag.yml')
        if os.path.exists(vorlage) and os.path.exists(aktiv):
            with open(vorlage, encoding='utf-8') as f1, open(aktiv, encoding='utf-8') as f2:
                self.assertEqual(f1.read(), f2.read(), 'kopiert statt verschoben und dann nur eine Datei geändert?')

    def test_die_gefundene_datei_ist_die_aktive_oder_die_vorlage(self):
        if VORLAGE is not None:
            self.assertIn(VORLAGE, (os.path.join(REPO, '.github', 'workflows', 'nachtrag.yml'), os.path.join(HIER, 'nachtrag-aktion.yml')))


@unittest.skipUnless(VORLAGE, 'weder .github/workflows/nachtrag.yml noch tools/nachtrag-aktion.yml vorhanden (Arbeitsordner ohne die Vorlage)')
class TestVorlage(unittest.TestCase):
    # (Ereignis, SCHREIBEN, ZEITPLAN, schreibt?, Hinweis „Zeitplan läuft nur als Bericht“?)
    FAELLE = (('schedule', '', '', False, True), ('schedule', '', '1', True, False), ('schedule', '', '0', False, True), ('schedule', '', 'true', False, True),
              ('schedule', '', '1 ', False, True), ('schedule', 'true', '', False, True), ('schedule', 'true', '1', True, False),
              ('workflow_dispatch', '', '', False, False), ('workflow_dispatch', 'false', '', False, False), ('workflow_dispatch', 'true', '', True, False),
              ('workflow_dispatch', 'True', '', False, False), ('workflow_dispatch', '1', '', False, False), ('workflow_dispatch', 'true', '1', True, False),
              ('workflow_dispatch', '', '1', False, False), ('push', 'true', '1', False, False), ('workflow_run', 'true', '1', False, False),
              ('repository_dispatch', 'true', '1', False, False), ('', 'true', '1', False, False), ('SCHEDULE', '', '1', False, False))

    @classmethod
    def setUpClass(cls):
        with open(VORLAGE, encoding='utf-8') as f:
            cls.text = f.read()
        cls.zeilen = cls.text.split('\n')
        cls.code = [z for z in cls.zeilen if not z.lstrip().startswith('#')]

    def block(self, kopf):
        """Die eingerückten Zeilen (ohne Kommentare) unter einer Zeile ohne Einzug (z. B. permissions:)."""
        i = self.zeilen.index(kopf)
        ergebnis = []
        for z in self.zeilen[i + 1:]:
            if z.lstrip().startswith('#'):
                continue
            if z.strip() and not z.startswith(' '):
                break
            if z.strip():
                ergebnis.append(z.strip())
        return ergebnis

    def schluessel(self, kopf, einzug):
        """Die Schlüssel (Zeilen „name:“) genau auf dieser Einrückung unter einer Zeile ohne Einzug."""
        i = self.zeilen.index(kopf)
        ergebnis = []
        for z in self.zeilen[i + 1:]:
            if z.lstrip().startswith('#'):
                continue
            if z.strip() and not z.startswith(' '):
                break
            if z.strip() and len(z) - len(z.lstrip()) == einzug:
                ergebnis.append(z.strip().split(':', 1)[0])
        return ergebnis

    def schritt(self, name):
        """Die Zeilen (ohne Einzug) des Schritts „- name: <name>“ bis zum nächsten Schritt."""
        i = next(n for n, z in enumerate(self.zeilen) if z.strip() == '- name: ' + name)
        einzug = len(self.zeilen[i]) - len(self.zeilen[i].lstrip())
        ergebnis = [self.zeilen[i].strip()]
        for z in self.zeilen[i + 1:]:
            if z.strip() and len(z) - len(z.lstrip()) <= einzug:
                break
            if z.strip():
                ergebnis.append(z.strip())
        return ergebnis

    def run_block(self, name):
        """Der run:-Text des Schritts „- name: <name>“ in Blockschreibweise (ohne Einzug): reine Textauswertung, ohne YAML-Leser."""
        i = next(n for n, z in enumerate(self.zeilen) if z.strip() == '- name: ' + name)
        einzug_schritt = len(self.zeilen[i]) - len(self.zeilen[i].lstrip())
        for n in range(i + 1, len(self.zeilen)):
            z = self.zeilen[n]
            if z.strip() and len(z) - len(z.lstrip()) <= einzug_schritt:
                break
            m = re.match(r'^(\s*)run:\s*\|\s*$', z)
            if m:
                block = []
                for z2 in self.zeilen[n + 1:]:
                    if z2.strip() and len(z2) - len(z2.lstrip()) <= len(m.group(1)):
                        break
                    block.append(z2)
                basis = min(len(z2) - len(z2.lstrip()) for z2 in block if z2.strip())
                return '\n'.join(z2[basis:] if z2.strip() else '' for z2 in block).strip('\n') + '\n'
        self.fail('kein run-Block im Schritt ' + name)

    def test_kopfkommentar_nennt_zweck_voreinstellung_und_aktivierung(self):
        kopf_zeilen = []
        for z in self.zeilen:
            if not z.startswith('#'):
                break
            kopf_zeilen.append(z)
        kopf = '\n'.join(kopf_zeilen)
        flach = ' '.join(kopf.replace('# ', '').split())
        self.assertIn('Zweck', kopf)
        self.assertIn('Sie liegt unter tools/, weil das Zugangs-Token des Betreuers Workflow-Dateien nicht pushen darf; nach dem Verschieben liegt die Datei '
                      'unter .github/workflows/nachtrag.yml.', flach)
        self.assertIn('.github/workflows/nachtrag.yml', kopf)
        self.assertIn('Stift-Symbol', kopf)
        self.assertIn('Sicher voreingestellt', kopf)
        self.assertIn('NACHTRAG_SCHREIBEN', kopf)
        self.assertIn('ruby -ryaml', kopf, 'der Hinweis, dass nach jeder Änderung die Syntax zu prüfen ist')

    def test_name_zeitplan_und_manueller_start(self):
        self.assertIn('name: Spielplan-Nachtrag (Cloud-Sicherung)', self.code)
        self.assertEqual([z for z in self.code if 'cron:' in z], ['    - cron: "17 6 * * *"'], 'genau ein Zeitplan')
        self.assertEqual(self.block('on:')[:1], ['schedule:'])
        eingabe = self.block('on:')
        self.assertIn('workflow_dispatch:', eingabe)
        self.assertIn('schreiben:', eingabe)
        self.assertIn('type: boolean', eingabe)
        self.assertIn('default: false', eingabe)

    def test_beschriftung_des_kaestchens_nennt_schreiben_und_den_leeren_zustand(self):
        """Die Oberfläche zeigt bei einer boolean-Eingabe die description als Beschriftung des Kästchens."""
        self.assertIn('description: "Schreiben und pushen (leer = nur Bericht, nichts wird geschrieben)"', self.block('on:'))

    def test_on_hat_genau_schedule_und_workflow_dispatch(self):
        """Jeder Zusatzauslöser wäre ein Risiko: push schriebe bei jedem Push, workflow_run auf build.yml wäre eine Endlosschleife."""
        self.assertEqual(self.schluessel('on:', 2), ['schedule', 'workflow_dispatch'])

    def test_rechte_genau_contents_und_actions_write(self):
        self.assertEqual(self.block('permissions:'), ['contents: write', 'actions: write'])

    def test_gleichzeitige_laeufe_warten_statt_abzubrechen(self):
        self.assertEqual(self.block('concurrency:'), ['group: spielplan-nachtrag', 'cancel-in-progress: false'])

    def test_job_zeitgrenze_not_aus_zweigbedingung_und_checkout(self):
        job = self.block('jobs:')
        self.assertIn('nachtrag:', job)
        self.assertIn('runs-on: ubuntu-latest', job)
        self.assertIn('timeout-minutes: 15', job)
        self.assertIn("if: ${{ vars.NACHTRAG_AUS != '1' && github.ref == 'refs/heads/main' }}", job, 'Not-Aus und nur auf main')
        self.assertIn('- uses: actions/checkout@v4', job)
        self.assertIn('fetch-depth: 0', job)

    def test_checkout_behaelt_die_zugangsdaten_fuer_den_push(self):
        """Mit persist-credentials: false scheiterte jeder Push (git push braucht die Zugangsdaten des Checkouts)."""
        self.assertNotIn('persist-credentials', self.text)

    def test_schritte_in_der_vorgesehenen_reihenfolge(self):
        text = '\n'.join(self.code)
        reihenfolge = [text.index(s) for s in ('actions/checkout@v4', 'git config user.name "github-actions"', 'id: nachtrag',
                                              'python3 -I tools/nachtrag_aktion.py', 'gh workflow run build.yml --ref main')]
        self.assertEqual(reihenfolge, sorted(reihenfolge))
        self.assertIn('git config user.email "actions@github.com"', text)

    def test_id_des_nachtrag_schritts_ist_der_name_in_den_ausgaben_des_dispatch_schritts(self):
        """Mit einer anderen id liefe der Verweis steps.<id>.outputs ins Leere: der Kalender würde nie angestoßen."""
        ids = [z[len('id: '):] for z in self.schritt('Nachtrag ausfuehren') if z.startswith('id: ')]
        self.assertEqual(len(ids), 1)
        bedingung = [z for z in self.schritt('Kalender-Workflow anstossen') if z.startswith('if:')]
        self.assertEqual(len(bedingung), 1)
        self.assertEqual(re.findall(r'steps\.([A-Za-z0-9_-]+)\.outputs\.geaendert', bedingung[0]), ids)
        self.assertIn('python3 -I tools/nachtrag_aktion.py', self.run_block('Nachtrag ausfuehren'), 'der Schritt mit der id ruft den Wrapper auf')

    def test_schreibfreigabe_nur_ueber_ausdrueckliche_wege_und_werte_nur_ueber_env(self):
        schritt = self.schritt('Nachtrag ausfuehren')
        for zeile in ('EVENT: ${{ github.event_name }}', 'SCHREIBEN: ${{ inputs.schreiben }}', 'ZEITPLAN: ${{ vars.NACHTRAG_SCHREIBEN }}'):
            self.assertIn(zeile, schritt)
        run = self.run_block('Nachtrag ausfuehren')
        self.assertEqual(run.count('python3 -I tools/nachtrag_aktion.py'), 2, 'Schreiblauf und Bericht')
        self.assertEqual(run.count('python3 -I tools/nachtrag_aktion.py --nur-bericht'), 1)
        self.assertIn('"$EVENT" = "workflow_dispatch"', run)
        self.assertIn('"$SCHREIBEN" = "true"', run)
        self.assertIn('"$EVENT" = "schedule"', run)
        self.assertIn('"$ZEITPLAN" = "1"', run)
        self.assertIn('::notice::Zeitplan läuft nur als Bericht: die Repository-Variable NACHTRAG_SCHREIBEN steht nicht auf 1', run)

    @unittest.skipUnless(shutil.which('bash'), 'übersprungen, bash fehlt')
    def test_ereignisse_ergeben_bericht_oder_schreiblauf(self):
        """Der run-Text des Schritts „Nachtrag ausfuehren“ (Textauswertung der Vorlage, ohne Ruby) läuft unter bash -e mit einem Stub-python3 für jedes
        Ereignis: geschrieben wird nur bei (workflow_dispatch und SCHREIBEN=true) oder (schedule und ZEITPLAN=1); alles andere ist der Bericht, und der
        Zeitplan ohne Freigabe sagt es in einer ::notice::-Zeile."""
        notiz = '::notice::Zeitplan läuft nur als Bericht: die Repository-Variable NACHTRAG_SCHREIBEN steht nicht auf 1'
        with tempfile.TemporaryDirectory(prefix='nachtrag-aktion-sim-') as d:
            stub = os.path.join(d, 'bin')
            os.makedirs(stub)
            with open(os.path.join(stub, 'python3'), 'w') as f:
                f.write('#!/bin/bash\necho "STUB python3 $*"\n')
            os.chmod(os.path.join(stub, 'python3'), 0o755)
            skript = os.path.join(d, 'schritt.sh')
            with open(skript, 'w', encoding='utf-8') as f:
                f.write(self.run_block('Nachtrag ausfuehren'))
            for ereignis, schreiben, zeitplan, schreibt, hinweis in self.FAELLE:
                with self.subTest(ereignis=ereignis, schreiben=schreiben, zeitplan=zeitplan):
                    r = subprocess.run([shutil.which('bash'), '-e', skript], capture_output=True, text=True, encoding='utf-8', timeout=60,
                                       env={'PATH': stub + os.pathsep + '/usr/bin' + os.pathsep + '/bin', 'EVENT': ereignis, 'SCHREIBEN': schreiben,
                                            'ZEITPLAN': zeitplan})
                    erwartet = ([notiz] if hinweis else []) + ['STUB python3 -I tools/nachtrag_aktion.py' + ('' if schreibt else ' --nur-bericht')]
                    self.assertEqual((r.returncode, r.stdout.splitlines()), (0, erwartet), r.stderr)

    def test_kalender_workflow_wird_nur_bei_geaendert_angestossen(self):
        schritt = self.schritt('Kalender-Workflow anstossen')
        self.assertIn("if: ${{ !cancelled() && steps.nachtrag.outputs.geaendert == 'true' }}", schritt)
        self.assertIn('GH_TOKEN: ${{ github.token }}', schritt)
        self.assertIn('run: gh workflow run build.yml --ref main', schritt)
        self.assertEqual(len([z for z in self.code if 'GH_TOKEN' in z]), 1)
        self.assertEqual(len([z for z in self.code if 'gh workflow run' in z]), 1)

    def test_kalender_workflow_laeuft_auch_nach_einem_roten_nachtrag_schritt(self):
        """Nach einem fehlgeschlagenen Schritt überspränge GitHub jeden weiteren Schritt ohne Statusfunktion (implizites success()): bei einem
        teilweisen Ausfall (Lauf rot, aber etwas gepusht) bliebe der Kalender liegen. Der Nachtrag-Schritt selbst muss den Lauf rot färben dürfen."""
        schritt = self.schritt('Kalender-Workflow anstossen')
        bedingung = [z for z in schritt if z.startswith('if:')]
        self.assertEqual(len(bedingung), 1)
        self.assertIn('!cancelled()', bedingung[0])
        self.assertIn("steps.nachtrag.outputs.geaendert == 'true'", bedingung[0])
        self.assertNotIn('always()', bedingung[0], 'nicht auch nach einem Abbruch von Hand')
        self.assertNotIn('continue-on-error', self.text, 'der Lauf bleibt rot, wenn der Nachtrag-Schritt fehlschlägt')
        namen = [z.strip() for z in self.zeilen if z.strip().startswith('- name:')]
        self.assertEqual(namen[-1], '- name: Kalender-Workflow anstossen', 'der letzte Schritt, nach dem Nachtrag')

    def test_in_keinem_run_text_steht_eine_ausdrucksklammer(self):
        bloecke = run_bloecke(self.zeilen)
        self.assertGreaterEqual(len(bloecke), 3)
        for block in bloecke:
            self.assertNotIn('${{', block)

    def test_nichts_was_nicht_hineingehoert(self):
        for verboten in ('secrets.', 'pull_request', 'actions/cache', 'pip ', 'pip3', 'setup-python', 'upload-artifact', 'GITHUB_TOKEN:', 'always()'):
            self.assertNotIn(verboten, self.text)

    def test_kein_kalenderbau_in_der_vorlage(self):
        """Kalender baut nur build.yml: SEQUENCE kommt aus dessen github.run_number, ein eigener Zähler würde sie senken."""
        code = '\n'.join(self.code)
        self.assertNotIn('build_ics', code)
        self.assertNotIn('SEQ', code)
        self.assertNotIn('docs', code)

    def test_grobe_struktur_ohne_tabulatoren_mit_zweierschritten_und_ohne_doppelpunkt_in_werten(self):
        """Die Standardbibliothek hat keinen YAML-Leser: eine grobe Prüfung der Struktur (die Syntax prüft test_yaml_syntax_mit_ruby, wenn Ruby da ist).
        Kein Tabulator, keine Leerzeichen am Zeilenende, Einrückung in Zweierschritten und je Zeile höchstens eine Stufe tiefer als davor, die obersten
        Schlüssel bekannt, kein „: “ und kein „ #“ in einem unquotierten Wert (außerhalb der Blocktexte von run:)."""
        oberste = []
        block_einzug = None
        vorher = 0
        for nr, z in enumerate(self.zeilen, start=1):
            self.assertNotIn('\t', z, 'Zeile %d: Tabulator' % nr)
            self.assertEqual(z, z.rstrip(), 'Zeile %d: Leerzeichen am Ende' % nr)
            if not z.strip() or z.lstrip().startswith('#'):
                continue
            einzug = len(z) - len(z.lstrip())
            if block_einzug is not None:
                if einzug > block_einzug:
                    continue                              # Zeile eines Blocktexts (run: |): beliebiger Inhalt
                block_einzug = None
            self.assertEqual(einzug % 2, 0, 'Zeile %d: Einrückung nicht in Zweierschritten' % nr)
            self.assertLessEqual(einzug - vorher, 2, 'Zeile %d: mehr als eine Stufe tiefer als die Zeile davor' % nr)
            vorher = einzug
            if einzug == 0:
                oberste.append(z.split(':', 1)[0])
            m = re.match(r'^\s*(?:- )?[A-Za-z0-9_-]+:(?:\s+(.*))?$', z)
            if m and m.group(1):
                wert = m.group(1)
                if wert in ('|', '>', '|-', '>-', '|+', '>+'):
                    block_einzug = einzug
                    continue
                if wert[0] in '"\'':
                    self.assertTrue(wert.endswith(wert[0]), 'Zeile %d: Anführungszeichen nicht geschlossen' % nr)
                    continue
                self.assertNotIn(': ', wert, 'Zeile %d: „: “ in einem unquotierten Wert' % nr)
                self.assertNotIn(' #', wert, 'Zeile %d: „ #“ in einem unquotierten Wert' % nr)
        self.assertEqual(oberste, ['name', 'on', 'permissions', 'concurrency', 'jobs'])

    def test_yaml_syntax_mit_ruby(self):
        """Echte Syntaxprüfung mit Ruby (Psych); ohne Ruby wird ausdrücklich gemeldet, dass sie übersprungen wurde."""
        if shutil.which('ruby') is None:
            print('übersprungen, Ruby fehlt (YAML-Syntax der Vorlage nicht geprüft)', file=sys.stderr)
            self.skipTest('übersprungen, Ruby fehlt')
        r = subprocess.run([shutil.which('ruby'), '-ryaml', '-e', 'YAML.load_file(ARGV[0]); puts "YAML ok"', VORLAGE], capture_output=True, text=True,
                           encoding='utf-8', timeout=120)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'YAML ok'), r.stderr)


@unittest.skipUnless(os.path.exists(README), 'README.md fehlt (Arbeitsordner ohne Repo-Stamm, z. B. Mutationstest)')
class TestReadme(unittest.TestCase):
    ABSCHNITT = '## Cloud-Sicherung des Nachtrags (GitHub-Aktion, vorbereitet)'

    @classmethod
    def setUpClass(cls):
        with open(README, encoding='utf-8') as f:
            cls.readme = f.read()
        anfang = cls.readme.index(cls.ABSCHNITT)
        ende = cls.readme.find('\n## ', anfang + 1)
        cls.abschnitt = cls.readme[anfang:ende if ende >= 0 else len(cls.readme)]
        cls.flach = ' '.join(cls.abschnitt.split())

    def test_abschnitt_steht_hinter_der_automatischen_aktualisierung(self):
        self.assertLess(self.readme.index('## Automatische Aktualisierung (`tools/nachtrag.py`)'), self.readme.index(self.ABSCHNITT))
        self.assertEqual(self.readme.count(self.ABSCHNITT), 1)

    def test_stand_vorbereitet_und_nicht_aktiv(self):
        self.assertIn('vorbereitet 31.10.2026, noch nicht aktiv', self.abschnitt)
        for aktiv in ('ist aktiv', 'läuft seit', 'ist eingerichtet', 'aktiv seit', 'ist live'):
            self.assertNotIn(aktiv, self.abschnitt)

    def test_abschnitt_nennt_dateien_optionen_variablen_und_zeiten_wie_im_code(self):
        for text in ('tools/nachtrag-aktion.yml', '.github/workflows/nachtrag.yml', 'tools/nachtrag_aktion.py', '--nur-bericht', '--nur-cache',
                     'NACHTRAG_AUS', 'NACHTRAG_SCHREIBEN', 'schreiben', '17 6 * * *', 'build.yml', 'Run workflow', 'Settings', 'Variables', 'SEQUENCE',
                     'zwölf', 'zustandslos', 'Mac', 'öffentlich', 'NACHTRAG-AKTION', '!cancelled()', 'ohne etwas zu löschen', 'Cache des Mac-Tasks'):
            self.assertIn(text, self.abschnitt)
        self.assertNotIn('startet dann erst mit dem nächsten Push', self.abschnitt, 'bei einem teilweisen Ausfall wird build.yml trotzdem angestoßen')
        self.assertNotIn('springt nur ein', self.abschnitt, 'die Aktion läuft täglich unabhängig vom Mac')
        for datei in ('nachtrag_aktion.py', 'test_nachtrag_aktion.py'):
            self.assertTrue(os.path.exists(os.path.join(HIER, datei)), datei)

    def test_abschnitt_stimmt_mit_der_vorlage_ueberein_wenn_eine_da_ist(self):
        """Zeitplan und Beschriftung des Kästchens stehen in README und Workflow-Datei gleich; ohne Datei (Arbeitsordner ohne Vorlage) entfällt der Abgleich."""
        if VORLAGE is None:
            self.skipTest('keine Workflow-Datei vorhanden')
        with open(VORLAGE, encoding='utf-8') as f:
            vorlage = f.read()
        self.assertIn(re.search(r'cron: "([^"]+)"', vorlage).group(1), self.abschnitt)
        self.assertIn(re.search(r'description: "([^"]+)"', vorlage).group(1), self.flach)

    def test_inbetriebnahme_sicher_voreingestellt_mit_probelauf_freigabe_und_schreibtest(self):
        self.assertIn('Sicher voreingestellt', self.abschnitt)
        for text in ('Der Zeitplan läuft ab jetzt, aber nur als Bericht', 'Use workflow from: main', 'Schreiben und pushen (leer = nur Bericht, nichts wird geschrieben)',
                     'erprobt aber weder Push noch Kalender-Anstoß', 'Erst bei Erfolg die Repository-Variable `NACHTRAG_SCHREIBEN` = `1` anlegen',
                     'Schreibtest', 'die Uhrzeit um eine Stunde ändern', '1 geändert (1 verlegt), gepusht', 'github-actions', 'Spielplan-Kalender bauen',
                     'workflow_dispatch', 'spiele.csv` ist wieder wie vorher'):
            self.assertIn(text, self.flach)
        schritte = re.findall(r'^(\d)\. ', self.abschnitt[self.abschnitt.index('**Inbetriebnahme**'):self.abschnitt.index('**Not-Aus und Schreibfreigabe:**')], re.M)
        self.assertEqual(schritte, ['1', '2', '3', '4'], 'Inbetriebnahme in vier nummerierten Schritten')

    def test_not_aus_schreibfreigabe_ruecknahme_und_zweig(self):
        for text in ('`NACHTRAG_AUS` = `1` hält alles an', '`NACHTRAG_SCHREIBEN` ungleich `1`', 'nur den Bericht', 'Der Job läuft nur auf `main`',
                     'andere Zweige werden übersprungen', 'Rücknahme', 'löschen oder in *Actions* den Workflow deaktivieren'):
            self.assertIn(text, self.flach)

    def test_grenzen_und_fehlersuche_praezise(self):
        for text in ('Eine Absage, die zuerst die Cloud einträgt, geht nur zurück, wenn der Cache des Mac-Tasks die Absetzung selbst gezeigt hat (Verlauf)',
                     'von Hand abgesagt?', 'als Zeile „Hinweis: …“', 'Zusammenfassung des Laufs', 'Spalte `ergebnis` leeren',
                     'Saisonwechsel', 'Quelle unplausibel', 'bis fussball.de die Pläne füllt', 'Sommerpause ohne neue CSV-Zeilen bleibt grün',
                     'unbrauchbaren Antwort (Sperrseite, geänderter Seitenaufbau) kein weiterer Abruf', 'unbrauchbare Antwort (…)',
                     'Warnungen (grün) lösen keine Mail aus', 'Actions-Benachrichtigungen', 'nichts ist verloren, der nächste Lauf rechnet neu und wiederholt es',
                     'der nächste Lauf pusht ihn nach'):
            self.assertIn(text, self.flach)

    def test_verweise_in_pflege_und_dateitabelle(self):
        tabelle = self.readme[self.readme.index('## Dateien'):self.readme.index('## Pflege im Alltag')]
        self.assertIn('`tools/nachtrag-aktion.yml`', tabelle)
        self.assertIn('nach dem Verschieben liegt die Datei unter `.github/workflows/nachtrag.yml`', tabelle)
        pflege = self.readme[self.readme.index('## Pflege im Alltag'):self.readme.index('## Bewusst nicht automatisiert')]
        self.assertIn('Cloud-Sicherung des Nachtrags', pflege)
        self.assertIn('nicht aktiv', pflege)

    def test_keine_personennamen_im_oeffentlichen_repo(self):
        for name in ('Olgay', 'Özkan', 'Oezkan', 'Melli', 'Klima', 'Lippert', 'Jenny', 'Sören'):
            self.assertNotIn(name, self.abschnitt)


if __name__ == '__main__':
    unittest.main()
