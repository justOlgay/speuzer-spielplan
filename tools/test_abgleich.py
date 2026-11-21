#!/usr/bin/env python3
"""Tests für die Wiederholung bei vorübergehenden fussball.de-Störungen in tools/abgleich.py: Einstufung (voruebergehend), Retry-After,
Diagnose der Fehlerseite, die Klasse Wiederholung, hole(..., wiederholung=...) und main() mit --zeitrahmen.

Isolation: Netzwerk nur nach 127.0.0.1 (jeder andere Verbindungsversuch scheitert und wird gezählt, am Ende wird null verlangt); fussball.de und
jeder andere Host werden nie erreicht. Die Läufe von main() gehen gegen einen lokalen HTTP-Server mit synthetischen Spielplanseiten
(--url-vorlage), damit die echten Fehlerklassen von urllib entstehen (HTTPError mit echten Kopfzeilen, RemoteDisconnected, IncompleteRead,
Zeitüberschreitung, Proxy-Meldung „Tunnel connection failed“, TLS-Fehler); was ein Server nicht auslösen kann (Zertifikat, Namensauflösung),
kommt aus einer urlopen-Attrappe. HOME und die Standardpfade von abgleich zeigen auf Wegwerf-Ordner (am Ende geprüft: leer).

Keine echten Pausen: Für die Dauer eines Tests bekommt abgleich ein Ersatz für sein time-Modul (Zeitraffer), in dem sleep() nur simulierte Zeit
verbraucht und monotonic() diese zählt; ein echtes time.sleep ab einer Sekunde lässt den Test scheitern. Die feste Uhr (Fest) macht die
Uhrzeiten in den Berichtzeilen und die Cache-Namen unabhängig von der echten Zeit.

Die Hilfen für die synthetische Welt (Welt, Spiel, seite_aus …) kommen aus test_nachtrag (als T); dessen TestCase-Klassen werden nicht in diesen
Namensraum geholt, sonst liefen die bestehenden Tests hier ein zweites Mal.

Aufruf (aus dem Repo-Stamm):  python3 -I -B -m unittest discover -s tools -p 'test_abgleich.py'
"""
import ast
import calendar
import contextlib
import email.utils
import errno
import http.client
import http.server
import inspect
import io
import os
import re
import shutil
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.dont_write_bytecode = True
HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)
import abgleich as A  # noqa: E402
import test_nachtrag as T  # noqa: E402  (Hilfen: nur über T., die Testklassen bleiben dort)

JETZT, HEUTE = T.JETZT, T.HEUTE          # 26.10.2026 07:10:00 und 2026-10-26: die feste Uhr der synthetischen Welt
GIT_TEXT = 'kein Git-Stand lesbar'       # git_info ist hier ersetzt: der Bericht hängt nicht an Git und der Lauf nicht an dessen Tempo
LOOPBACK = ('127.0.0.1', '::1', 'localhost')
NETZ_VERSUCHE = []
SCHLAF_VERSUCHE = []
_ECHT_GETADDRINFO, _ECHT_CREATE_CONNECTION, _ECHT_CONNECT, _ECHT_SLEEP = socket.getaddrinfo, socket.create_connection, socket.socket.connect, time.sleep
_stuetzen = []
_waechter = _standard = _warnungen = None


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


def _schlaf(sekunden):
    """Das echte time.sleep, solange es nur Sekundenbruchteile dauert (Hilfsthreads, Unterprozesse); längeres Schlafen wäre eine echte Pause."""
    if sekunden >= 1:
        SCHLAF_VERSUCHE.append(sekunden)
        raise AssertionError('echtes Schlafen im Test: %s s' % sekunden)
    return _ECHT_SLEEP(sekunden)


def setUpModule():
    """Netz nur nach 127.0.0.1, kein echtes Warten, HOME und Standardpfade von abgleich in einem Wächter-Ordner, der leer bleiben muss."""
    global _waechter, _standard, _warnungen
    _warnungen = warnings.catch_warnings()
    _warnungen.__enter__()
    warnings.simplefilter('ignore', ResourceWarning)      # hole lässt die Fehlversuch-Marke beim Lesen ungeschlossen (bestehendes Verhalten)
    _waechter = tempfile.mkdtemp(prefix='abgleich-test-waechter-')
    home = os.path.join(_waechter, 'home')
    _standard = os.path.join(_waechter, 'standard')
    os.makedirs(home)
    _stuetzen[:] = [
        mock.patch.object(socket, 'getaddrinfo', _addrinfo), mock.patch.object(socket, 'create_connection', _create_connection),
        mock.patch.object(socket.socket, 'connect', _connect), mock.patch.object(time, 'sleep', _schlaf),
        mock.patch.dict(os.environ, {'HOME': home}),
        mock.patch.object(A, 'CACHE_STANDARD', os.path.join(_standard, 'cache')),
        mock.patch.object(A, 'BERICHTE_STANDARD', os.path.join(_standard, 'berichte'))]
    for p in _stuetzen:
        p.start()
    # Keine Proxys, auch nicht die Systemeinstellung des Macs: der lokale Server wird immer direkt angesprochen (der Proxy-Test setzt sie selbst)
    for name in [n for n in os.environ if n.lower().endswith('_proxy')]:
        del os.environ[name]
    os.environ['no_proxy'] = os.environ['NO_PROXY'] = '*'


def tearDownModule():
    netz, schlaf, standard = list(NETZ_VERSUCHE), list(SCHLAF_VERSUCHE), os.path.exists(_standard)
    for p in reversed(_stuetzen):
        p.stop()
    _warnungen.__exit__(None, None, None)
    shutil.rmtree(_waechter, ignore_errors=True)
    assert not netz, 'Netzwerkzugriffe im Test (außer 127.0.0.1): %s' % netz
    assert not schlaf, 'echte Pausen im Test: %s' % schlaf
    assert not standard, 'ein Standardpfad von abgleich wurde benutzt (der Wächter-Ordner ist nicht leer)'


# ---------------------------------------------------------------- Uhr, Antworten, Server

class Fest(datetime):
    """Die Uhr von abgleich in den Tests: now() liefert immer JETZT."""

    @classmethod
    def now(cls, tz=None):
        return cls(JETZT.year, JETZT.month, JETZT.day, JETZT.hour, JETZT.minute, JETZT.second, tzinfo=tz)


class Zeitraffer:
    """Ersatz für das time-Modul in abgleich: sleep() kostet keine echte Zeit, monotonic() zählt nur die simulierten Sekunden, time() ist die Wanduhr
    (fester Anfang plus simulierte Sekunden). Alles andere kommt aus dem echten Modul."""

    def __init__(self, wand=1792000000.0):
        self.t, self.wand, self.pausen = 0.0, wand, []

    def monotonic(self):
        return self.t

    def sleep(self, sekunden):
        self.pausen.append(sekunden)
        self.t += sekunden

    def time(self):
        return self.wand + self.t

    def vergehen(self, sekunden):
        """Zeit, die ein Abruf selbst verbraucht (zum Beispiel einer, der in die Zeitüberschreitung läuft)."""
        self.t += sekunden

    def wartezeiten(self):
        """Die Pausen der Wiederholungen (ohne die 3 Sekunden zwischen den Abrufen)."""
        return [p for p in self.pausen if p != A.PAUSE_SEKUNDEN]

    def __getattr__(self, name):
        return getattr(time, name)


PLAN_TEXT = T.seite_aus(T.basis_spiele('HERREN'))                      # eine gültige Plan-Seite (länger als 500 Zeichen)
FEHLERSEITE = '<html><head><title>Wartungsarbeiten &amp; Störung</title></head><body>Bitte später wieder.</body></html>'
FEHLERTITEL = 'Wartungsarbeiten & Störung'


def kopf(werte=None):
    """Echte Mail-Kopfzeilen (wie bei einer HTTPError-Antwort) aus {Name: Wert}."""
    m = http.client.HTTPMessage()
    for name, wert in (werte or {}).items():
        m[name] = wert
    return m


def http_fehler(code, kopfzeilen=None, koerper=b''):
    """Eine frische urllib.error.HTTPError, wie urlopen sie wirft: echte Kopfzeilen, lesbarer Körper."""
    return urllib.error.HTTPError('http://127.0.0.1/plan', code, 'Meldung', kopf(kopfzeilen), io.BytesIO(koerper))


class Seite:
    """Eine Antwort mit Status 200 (oder dem angegebenen), wie urlopen sie liefert: Kontextmanager mit status und read()."""

    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.text.encode('utf-8')


class Dauer:
    """Ein Eintrag für Urlopen, der erst `sekunden` simulierte Zeit verbraucht (ein Abruf, der in die Zeitüberschreitung läuft)."""

    def __init__(self, sekunden, eintrag):
        self.sekunden, self.eintrag = sekunden, eintrag


class Urlopen:
    """Ersatz für urllib.request.urlopen: der n-te Aufruf bekommt den n-ten Eintrag, der letzte gilt weiter. Ein Eintrag ist eine Ausnahme (wird
    geworfen), ein Text (Antwort mit Status 200), ein Antwort-Objekt oder eine Funktion ohne Argumente, die einen dieser Einträge frisch liefert
    (nötig bei HTTPError, dessen Körper nach dem Lesen leer ist). `zeit`: der Zeitraffer für Dauer-Einträge."""

    def __init__(self, *folge, zeit=None):
        self.folge, self.zeit, self.aufrufe, self.timeouts = folge, zeit, [], []

    def __call__(self, req, timeout=None):
        eintrag = self.folge[min(len(self.aufrufe), len(self.folge) - 1)]
        self.aufrufe.append(req.full_url)
        self.timeouts.append(timeout)
        if isinstance(eintrag, Dauer):
            self.zeit.vergehen(eintrag.sekunden)
            eintrag = eintrag.eintrag
        if callable(eintrag) and not isinstance(eintrag, BaseException):
            eintrag = eintrag()
        if isinstance(eintrag, BaseException):
            raise eintrag
        return Seite(eintrag) if isinstance(eintrag, str) else eintrag


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):          # ohne socket.getfqdn: keine Namensauflösung im Test
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


def plan(h, seite, server):
    """HTTP 200 mit dem Plan der synthetischen Welt."""
    daten = seite.encode('utf-8')
    h.send_response(200)
    h.send_header('Content-Type', 'text/html; charset=utf-8')
    h.send_header('Content-Length', str(len(daten)))
    h.end_headers()
    h.wfile.write(daten)


def fehler(status, kopfzeilen=(), koerper=FEHLERSEITE):
    """Ein HTTP-Fehler mit Kopfzeilen (dict oder Liste von Paaren, auch doppelte Namen) und Körper."""
    def antwort(h, seite, server):
        daten = koerper.encode('utf-8')
        h.send_response(status)
        for name, wert in (kopfzeilen.items() if hasattr(kopfzeilen, 'items') else kopfzeilen):
            h.send_header(name, wert)
        h.send_header('Content-Length', str(len(daten)))
        h.end_headers()
        h.wfile.write(daten)
    return antwort


def trennen(h, seite, server):
    """Die Verbindung ohne jede Antwort schließen (der Client sieht RemoteDisconnected)."""
    h.close_connection = True


def abgeschnitten(h, seite, server):
    """HTTP 200 mit zu großem Content-Length und nur 50 Bytes Körper (der Client sieht IncompleteRead)."""
    h.send_response(200)
    h.send_header('Content-Length', '100000')
    h.end_headers()
    h.wfile.write(b'x' * 50)
    h.close_connection = True


def haengt(h, seite, server):
    """Gar nicht antworten, bis der Test den Server freigibt (der Client läuft in die Zeitüberschreitung)."""
    server.freigabe.wait(10)
    h.close_connection = True


def stockt(h, seite, server):
    """Die Kopfzeilen eines HTTP 503 senden, den Körper aber nie (das Lesen der Fehlerseite hängt)."""
    h.send_response(503)
    h.send_header('Content-Length', '5000')
    h.end_headers()
    h.wfile.flush()
    server.freigabe.wait(10)
    h.close_connection = True


class Skriptserver:
    """Lokaler HTTP-Server auf 127.0.0.1: je fussball.de-Team-ID eine Folge von Antworten (plan, fehler(…), trennen …); die n-te Anfrage an
    eine Team-ID bekommt den n-ten Eintrag, der letzte gilt weiter, ohne Eintrag liefert er den Plan der Welt. Zählt jede Anfrage."""

    def __init__(self, welt):
        self.welt, self.folge, self.anfragen, self.freigabe = welt, {}, [], threading.Event()
        self.sperre = threading.Lock()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def version_string(self):
                return 'Testserver/1.0'

            def do_GET(self):
                tid = self.path.split('?')[0].strip('/').split('/')[1]
                with server.sperre:
                    n = server.anfragen.count(tid)
                    server.anfragen.append(tid)
                folge = server.folge.get(tid) or [plan]
                folge[min(n, len(folge) - 1)](self, server.seite(tid), server)

            def log_message(self, *args):
                pass

        self.httpd = _Server(('127.0.0.1', 0), Handler)
        self.vorlage = 'http://127.0.0.1:%d/plan/{tid}/{von}/{bis}' % self.httpd.server_address[1]
        self.basis = 'http://127.0.0.1:%d/plan/' % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    def seite(self, tid):
        for kuerzel, slug, name, team_id in A.TEAMS:
            if team_id == tid:
                return T.seite_aus(self.welt.seite[kuerzel])
        for kuerzel, slug, name, team_id in A.ZUSATZQUELLEN:
            if team_id == tid:
                return T.seite_aus(self.welt.zusatz[slug], zusatz=T.FUELLTEXT)
        return PLAN_TEXT

    def anfragen_an(self, tid):
        return self.anfragen.count(tid)

    def beenden(self):
        self.freigabe.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(10)


class RohServer:
    """Ein winziger TCP-Server auf 127.0.0.1, der auf jede Verbindung (nach `bis` Bytes der Anfrage) dieselben Bytes antwortet und schließt: ein
    Proxy, der auf CONNECT mit einer Statuszeile antwortet, oder ein „TLS-Server“, der Klartext spricht."""

    def __init__(self, antwort, bis=b'\r\n\r\n'):
        self.antwort, self.bis, self.verbindungen, self.ende = antwort, bis, 0, False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(5)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._lauf, daemon=True)
        self.thread.start()

    def _lauf(self):
        while not self.ende:
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.verbindungen += 1
            with c:
                c.settimeout(5)
                try:
                    daten = b''
                    while self.bis not in daten:
                        teil = c.recv(4096)
                        if not teil:
                            break
                        daten += teil
                    c.sendall(self.antwort)
                except OSError:
                    pass

    def beenden(self):
        self.ende = True
        self.thread.join(5)
        self.sock.close()


def freier_port():
    """Ein Port auf 127.0.0.1, an dem nichts lauscht (Verbindung verweigert)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- Die Einstufung: vorübergehend oder endgültig

def _tunnel(code, text, als_url_error=True):
    ursache = OSError('Tunnel connection failed: %d %s' % (code, text))
    return urllib.error.URLError(ursache) if als_url_error else ursache


# (Name, Funktion, die die Ausnahme frisch erzeugt): vorübergehende Störungen, die wiederholt werden
HTTP_VORUEBERGEHEND = (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524)
VORUEBERGEHEND = [('HTTP %d' % code, (lambda code=code: http_fehler(code))) for code in HTTP_VORUEBERGEHEND] + [
    ('TimeoutError', lambda: TimeoutError('timed out')),
    ('socket.timeout', lambda: socket.timeout('timed out')),
    ('URLError(TimeoutError)', lambda: urllib.error.URLError(TimeoutError('timed out'))),
    ('URLError(socket.timeout)', lambda: urllib.error.URLError(socket.timeout('timed out'))),
    ('ConnectionResetError', lambda: ConnectionResetError(54, 'Connection reset by peer')),
    ('ConnectionRefusedError', lambda: ConnectionRefusedError(61, 'Connection refused')),
    ('ConnectionAbortedError', lambda: ConnectionAbortedError(53, 'Software caused connection abort')),
    ('BrokenPipeError', lambda: BrokenPipeError(32, 'Broken pipe')),
    ('RemoteDisconnected', lambda: http.client.RemoteDisconnected('Remote end closed connection without response')),
    ('URLError(ConnectionResetError)', lambda: urllib.error.URLError(ConnectionResetError(54, 'Connection reset by peer'))),
    ('URLError(ConnectionRefusedError)', lambda: urllib.error.URLError(ConnectionRefusedError(61, 'Connection refused'))),
    ('IncompleteRead', lambda: http.client.IncompleteRead(b'abc', 10)),
    ('URLError(IncompleteRead)', lambda: urllib.error.URLError(http.client.IncompleteRead(b'abc', 10))),
    ('ssl.SSLEOFError', lambda: ssl.SSLEOFError(8, 'EOF occurred in violation of protocol')),
    ('URLError(SSLEOFError)', lambda: urllib.error.URLError(ssl.SSLEOFError(8, 'EOF occurred in violation of protocol'))),
    ('socket.gaierror EAI_AGAIN', lambda: socket.gaierror(socket.EAI_AGAIN, 'Temporary failure in name resolution')),
    ('URLError(gaierror EAI_AGAIN)', lambda: urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, 'Temporary failure in name resolution'))),
    ('OSError ENETUNREACH', lambda: OSError(errno.ENETUNREACH, 'Network is unreachable')),
    ('URLError(OSError ENETUNREACH)', lambda: urllib.error.URLError(OSError(errno.ENETUNREACH, 'Network is unreachable'))),
    ('OSError EHOSTUNREACH', lambda: OSError(errno.EHOSTUNREACH, 'No route to host')),
    ('URLError(OSError EHOSTUNREACH)', lambda: urllib.error.URLError(OSError(errno.EHOSTUNREACH, 'No route to host'))),
    ('Tunnel 429', lambda: _tunnel(429, 'Too Many Requests')),
    ('Tunnel 500', lambda: _tunnel(500, 'Internal Server Error')),
    ('Tunnel 502', lambda: _tunnel(502, 'Bad Gateway')),
    ('Tunnel 503', lambda: _tunnel(503, 'Service Unavailable')),
    ('Tunnel 504', lambda: _tunnel(504, 'Gateway Timeout')),
    ('Tunnel 429 als OSError', lambda: _tunnel(429, 'Too Many Requests', als_url_error=False)),
    ('Tunnel 503 als OSError', lambda: _tunnel(503, 'Service Unavailable', als_url_error=False)),
    ('Tunnel 500 als Text', lambda: urllib.error.URLError('Tunnel connection failed: 500 Internal Server Error')),
    ('Tunnel 503 als Text', lambda: urllib.error.URLError('Tunnel connection failed: 503 Service Unavailable')),
]

# endgültige Fehler: ein Versuch, wie bisher (HTTP 425, 519, 525 und 526 sind bewusst keine vorübergehende Störung: nicht in der Liste)
ENDGUELTIG = [('HTTP %d' % code, (lambda code=code: http_fehler(code))) for code in (400, 401, 403, 404, 410, 418, 425, 451, 501, 505, 519, 525, 526)] + [
    ('Tunnel 403', lambda: _tunnel(403, 'Forbidden')),
    ('Tunnel 403 als Text', lambda: urllib.error.URLError('Tunnel connection failed: 403 Forbidden')),
    ('Tunnel 403 als OSError', lambda: _tunnel(403, 'Forbidden', als_url_error=False)),
    ('Tunnel 407', lambda: _tunnel(407, 'Proxy Authentication Required')),
    ('Tunnel 404', lambda: _tunnel(404, 'Not Found')),
    ('Tunnel 501', lambda: _tunnel(501, 'Not Implemented')),
    ('Tunnel 505', lambda: _tunnel(505, 'HTTP Version Not Supported')),
    ('Tunnel 5030', lambda: _tunnel(5030, 'x')),
    ('ssl.SSLCertVerificationError', lambda: ssl.SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')),
    ('URLError(SSLCertVerificationError)', lambda: urllib.error.URLError(ssl.SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]'))),
    ('ssl.SSLError', lambda: ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number')),
    ('URLError(SSLError)', lambda: urllib.error.URLError(ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number'))),
    ('ssl.SSLZeroReturnError', lambda: ssl.SSLZeroReturnError(6, 'TLS/SSL connection has been closed (EOF)')),
    ('socket.gaierror EAI_NONAME', lambda: socket.gaierror(socket.EAI_NONAME, 'Name or service not known')),
    ('URLError(gaierror EAI_NONAME)', lambda: urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'Name or service not known'))),
    ('socket.gaierror EAI_FAIL', lambda: socket.gaierror(socket.EAI_FAIL, 'Non-recoverable failure in name resolution')),
    ('socket.gaierror ohne Code', lambda: socket.gaierror('kaputt')),
    ('OSError EACCES', lambda: OSError(errno.EACCES, 'Permission denied')),
    ('URLError(OSError EACCES)', lambda: urllib.error.URLError(OSError(errno.EACCES, 'Permission denied'))),
    ('URLError(Text)', lambda: urllib.error.URLError('Verbindung gesperrt')),
    ('OSError', lambda: OSError('Netzwerk gesperrt')),
    ('BadStatusLine', lambda: http.client.BadStatusLine('kaputt')),
    ('ValueError', lambda: ValueError('kaputt')),
    ('RuntimeError', lambda: RuntimeError('kaputt')),
]


class TestEinstufung(unittest.TestCase):
    def test_vorubergehende_stoerungen_werden_als_solche_erkannt(self):
        for name, erzeuge in VORUEBERGEHEND:
            with self.subTest(name):
                e = erzeuge()
                self.assertTrue(A.voruebergehend(e), repr(e))

    def test_endgueltige_fehler_werden_nie_wiederholt(self):
        for name, erzeuge in ENDGUELTIG:
            with self.subTest(name):
                e = erzeuge()
                self.assertFalse(A.voruebergehend(e), repr(e))

    def test_http_status_liste_ist_genau_diese(self):
        self.assertEqual(A.VORUEBERGEHEND_HTTP, (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524))
        self.assertEqual(A.VORUEBERGEHEND_HTTP, HTTP_VORUEBERGEHEND)
        for code in range(100, 600):
            with self.subTest(code=code):
                self.assertEqual(A.voruebergehend(http_fehler(code)), code in HTTP_VORUEBERGEHEND)

    def test_501_und_505_sowie_die_uebrigen_5xx_bleiben_endgueltig(self):
        for code in (501, 505, 506, 507, 508, 510, 511, 519, 525, 526, 527, 530):
            with self.subTest(code):
                self.assertFalse(A.voruebergehend(http_fehler(code)))

    def test_namensaufloesung_nur_eai_again_ist_vorubergehend_jede_andere_eai_konstante_nicht(self):
        konstanten = sorted(n for n in dir(socket) if n.startswith('EAI_') and isinstance(getattr(socket, n), int))
        self.assertIn('EAI_AGAIN', konstanten)
        self.assertIn('EAI_NONAME', konstanten)
        for name in konstanten:
            for verpackt in (False, True):
                with self.subTest(name, als_url_error=verpackt):
                    e = socket.gaierror(getattr(socket, name), 'Fehler der Namensauflösung')
                    self.assertEqual(A.voruebergehend(urllib.error.URLError(e) if verpackt else e), name == 'EAI_AGAIN')

    def test_netz_oder_host_nicht_erreichbar_nur_enetunreach_und_ehostunreach(self):
        for nummer, erwartet in ((errno.ENETUNREACH, True), (errno.EHOSTUNREACH, True), (errno.EACCES, False), (errno.EPERM, False),
                                 (errno.EINVAL, False), (errno.EADDRNOTAVAIL, False), (errno.ENOENT, False)):
            for verpackt in (False, True):
                with self.subTest(errno.errorcode[nummer], als_url_error=verpackt):
                    e = OSError(nummer, 'Fehler')
                    self.assertEqual(A.voruebergehend(urllib.error.URLError(e) if verpackt else e), erwartet)

    def test_ssl_nur_der_verbindungsabbruch_beim_handshake_ist_vorubergehend(self):
        fehlerklassen = (ssl.SSLError, ssl.SSLZeroReturnError, ssl.SSLWantReadError, ssl.SSLWantWriteError, ssl.SSLSyscallError, ssl.SSLEOFError,
                         ssl.SSLCertVerificationError)
        for klasse in fehlerklassen:
            for verpackt in (False, True):
                with self.subTest(klasse.__name__, als_url_error=verpackt):
                    e = klasse(1, 'Fehler')
                    self.assertEqual(A.voruebergehend(urllib.error.URLError(e) if verpackt else e), klasse is ssl.SSLEOFError)

    def test_proxy_meldung_nur_bei_429_500_502_503_504_vorubergehend(self):
        for code in range(100, 600):
            for verpackt in (False, True):
                with self.subTest(code, als_url_error=verpackt):
                    self.assertEqual(A.voruebergehend(_tunnel(code, 'Meldung', als_url_error=verpackt)), code in (429, 500, 502, 503, 504))

    def test_ohne_ausnahme_text_oder_none_ist_nichts_vorubergehend(self):
        for e in (None, 'HTTP 503', 503, object()):
            with self.subTest(repr(e)):
                self.assertFalse(A.voruebergehend(e))


# ---------------------------------------------------------------- Retry-After

class TestRetryAfter(unittest.TestCase):
    WAND = 1792000000.0

    def sek(self, *werte):
        return A.retry_after(list(werte), lambda: self.WAND)

    def datum(self, abstand):
        return email.utils.formatdate(self.WAND + abstand, usegmt=True)

    def test_sekundenzahl(self):
        self.assertEqual(self.sek('30'), 30)
        self.assertEqual(self.sek(' 120 '), 120)
        self.assertEqual(self.sek('1.2'), 2, 'aufgerundet')
        self.assertEqual(self.sek('0.4'), 1)

    def test_http_datum_in_der_zukunft(self):
        self.assertEqual(self.sek(self.datum(90)), 90)
        self.assertEqual(self.sek(self.datum(1)), 1)
        self.assertEqual(self.sek('Sunday, 06-Nov-94 08:49:37 GMT'), None, 'Datum in der Vergangenheit (RFC 850)')
        self.assertEqual(self.sek('Sun Nov  6 08:49:37 1994'), None, 'Datum in der Vergangenheit (asctime)')

    def test_http_datum_mit_zeitzonenversatz_wird_nach_utc_gerechnet(self):
        """RFC 9110 schreibt GMT vor, ältere Formate und manche Server senden einen Versatz: 09:30 +0200, 02:30 -0500 und 13:00 +0530 sind alle 07:30 UTC,
        also 120 Sekunden nach 07:28:00 UTC; ein falsches Vorzeichen des Versatzes ergäbe Stunden."""
        jetzt = float(calendar.timegm((2026, 10, 21, 7, 28, 0)))
        for text in ('Wed, 21 Oct 2026 09:30:00 +0200', 'Wed, 21 Oct 2026 02:30:00 -0500', 'Wed, 21 Oct 2026 13:00:00 +0530',
                     'Wed, 21 Oct 2026 07:30:00 GMT', 'Wed, 21 Oct 2026 07:30:00 +0000', 'Wed, 21 Oct 2026 07:30:00 -0000',
                     'Wed, 21 Oct 2026 21:30:00 +1400', 'Tue, 20 Oct 2026 19:30:00 -1200'):
            with self.subTest(text):
                self.assertEqual(A.retry_after([text], lambda: jetzt), 120)

    def test_http_datum_mit_jedem_ganzstundenversatz_gleiche_sekunden(self):
        for stunden in range(-12, 15):
            with self.subTest(stunden):
                moment = datetime.fromtimestamp(self.WAND + 90, timezone(timedelta(hours=stunden)))
                self.assertEqual(self.sek(email.utils.format_datetime(moment)), 90)

    def test_datum_in_der_vergangenheit_und_jetzt_zaehlt_nicht(self):
        self.assertIsNone(self.sek(self.datum(-100)))
        self.assertIsNone(self.sek(self.datum(0)))

    def test_ungueltige_werte_zaehlen_nicht(self):
        for wert in ('', '   ', 'abc', '-5', '-1.5', '+5', '1e3', '0x10', '12abc', 'ü', '٣٣', '5 Sekunden', '0', '0.0', '1,5', 'Wed, 99 Foo 2026'):
            with self.subTest(wert):
                self.assertIsNone(self.sek(wert))

    def test_riesige_werte_werden_gedeckelt_statt_zu_scheitern(self):
        self.assertEqual(self.sek('99999999999999999999'), 10 ** 7)
        self.assertEqual(self.sek('9' * 5000), 10 ** 7)
        self.assertEqual(self.sek('Fri, 31 Dec 9999 23:59:59 GMT'), 10 ** 7)

    def test_mehrere_kopfzeilen_die_laengste_gueltige_zaehlt(self):
        self.assertEqual(self.sek('10', '100', '50'), 100)
        self.assertEqual(self.sek('abc', '10'), 10)
        self.assertEqual(self.sek('10', self.datum(300)), 300)
        self.assertIsNone(self.sek('abc', '-3'))
        self.assertIsNone(A.retry_after([], lambda: self.WAND))

    def test_ein_kaputter_wert_bringt_die_uebrigen_nicht_um(self):
        self.assertEqual(A.retry_after([None, '20'], lambda: self.WAND), 20)


# ---------------------------------------------------------------- Diagnose der Fehlerseite

class Koerper:
    """Körper einer HTTPError-Antwort: merkt sich, wie viel gelesen und ob geschlossen wurde; `daten` oder eine Ausnahme beim Lesen."""

    def __init__(self, daten=b'', ausnahme=None):
        self.daten, self.ausnahme, self.gelesen, self.zu = daten, ausnahme, [], False

    def read(self, n=-1):
        self.gelesen.append(n)
        if self.ausnahme:
            raise self.ausnahme
        return self.daten if n < 0 else self.daten[:n]

    def close(self):
        self.zu = True


def fehler_mit(koerper, kopfzeilen=None, code=503):
    return urllib.error.HTTPError('http://127.0.0.1/plan', code, 'Meldung', kopf(kopfzeilen), koerper)


class TestDiagnose(unittest.TestCase):
    WAND = 1792000000.0

    def titel(self, text, **kw):
        k = Koerper(text if isinstance(text, bytes) else text.encode('utf-8'))
        return A.fehlerseite_titel(fehler_mit(k)), k

    def test_titel_ohne_html_mit_aufgeloesten_entitaeten_und_zusammengefasstem_leerraum(self):
        t, k = self.titel('<html><head><TITLE lang="de">\n  Wartung &amp;   Störung\t&lt;b&gt; <b>jetzt</b>\n</TITLE></head></html>')
        self.assertEqual(t, 'Wartung & Störung jetzt')
        self.assertTrue(k.zu, 'die Antwort wird geschlossen')

    def test_titel_hoechstens_80_zeichen(self):
        t, _ = self.titel('<title>%s</title>' % ('ä' * 200))
        self.assertEqual(len(t), 80)
        self.assertTrue(t.endswith('…'))
        t, _ = self.titel('<title>%s</title>' % ('x' * 80))
        self.assertEqual(t, 'x' * 80, 'genau 80 Zeichen bleiben ganz')

    def test_hoechstens_4096_bytes_werden_gelesen(self):
        t, k = self.titel('<html><head>' + ' ' * 5000 + '<title>zu spät</title>')
        self.assertEqual(k.gelesen, [4096])
        self.assertEqual(t, '', 'ein Titel hinter dem 4096. Byte wird nicht gesehen')
        t, k = self.titel('<title>vorn</title>' + 'x' * 9000)
        self.assertEqual((t, k.gelesen), ('vorn', [4096]))

    def test_abgeschnittener_titel_am_ende_der_4096_bytes(self):
        t, _ = self.titel('<title>' + 'a' * 5000)
        self.assertEqual(len(t), 80)
        self.assertTrue(t.startswith('aaa'))

    def test_ohne_titel_oder_mit_leerem_titel(self):
        for text in ('<html><body>ohne Titel</body></html>', '<title></title>', '<title>   </title>', '<title>\x00\x01</title>', '', 'nur Text'):
            with self.subTest(text):
                self.assertEqual(self.titel(text)[0], '')

    def test_steuerzeichen_im_titel_werden_entfernt(self):
        self.assertEqual(self.titel('<title>a\x00b\x07c​d</title>')[0], 'abcd')

    def test_kein_utf8_ist_kein_absturz(self):
        t, _ = self.titel(b'<title>St\xf6rung</title>')
        self.assertIn('St', t)

    def test_lesen_das_wirft_bringt_nie_eine_ausnahme_nach_aussen_und_die_antwort_wird_geschlossen(self):
        for ausnahme in (OSError('Verbindung getrennt'), ValueError('I/O operation on closed file'), TimeoutError('timed out'),
                         http.client.IncompleteRead(b'', 5), RuntimeError('kaputt')):
            with self.subTest(repr(ausnahme)):
                k = Koerper(ausnahme=ausnahme)
                e = fehler_mit(k, {'Server': 'Testserver/1.0'})
                text, wartezeit = A.diagnose_von(e, lambda: self.WAND)
                self.assertEqual((text, wartezeit), ('Server: Testserver/1.0', None))
                self.assertTrue(k.zu)

    def test_antwort_ohne_koerper_und_ohne_kopfzeilen(self):
        e = urllib.error.HTTPError('u', 503, 'x', None, None)
        self.assertEqual(A.diagnose_von(e, lambda: self.WAND), ('', None))

    def test_kopfzeilen_server_retry_after_via_in_fester_reihenfolge_ohne_andere(self):
        e = fehler_mit(Koerper(b'<title>T</title>'), {'Via': '1.1 cdn', 'X-Cache': 'MISS', 'Retry-After': '30', 'Server': 'nginx'})
        self.assertEqual(A.diagnose_von(e, lambda: self.WAND), ('Server: nginx; Retry-After: 30; Via: 1.1 cdn; Seitentitel: „T“', 30))

    def test_kopfzeilen_ohne_beachtung_der_schreibweise_auch_aus_einem_dict(self):
        e = fehler_mit(Koerper(), {'server': 'nginx'})
        e.hdrs = {'SERVER': 'apache', 'retry-after': '7'}          # ein einfaches dict statt einer Message
        self.assertEqual(A.diagnose_von(e, lambda: self.WAND), ('Server: apache; Retry-After: 7', 7))

    def test_langer_oder_fremder_kopfzeilenwert_wird_bereinigt_und_gekuerzt(self):
        e = fehler_mit(Koerper(), {'Server': '<b>x</b>' + 'y' * 200})
        text, _ = A.diagnose_von(e, lambda: self.WAND)
        self.assertEqual(len(text), len('Server: ') + 80)
        self.assertNotIn('<', text)

    def test_mehrere_retry_after_kopfzeilen(self):
        m = http.client.HTTPMessage()
        m['Retry-After'] = '10'
        m['Retry-After'] = '100'
        e = fehler_mit(Koerper(), None)
        e.hdrs = m
        text, wartezeit = A.diagnose_von(e, lambda: self.WAND)
        self.assertEqual((text, wartezeit), ('Retry-After: 10, 100', 100))

    def test_alles_andere_als_ein_http_fehler_hat_keine_diagnose(self):
        for e in (TimeoutError('timed out'), urllib.error.URLError('x'), None):
            self.assertEqual(A.diagnose_von(e, lambda: self.WAND), ('', None))

    def test_schliessen_ist_wiederholbar_und_wirft_nie(self):
        k = Koerper()
        e = fehler_mit(k)
        A.schliessen(e)
        A.schliessen(e)
        self.assertTrue(k.zu)
        A.schliessen(TimeoutError())
        A.schliessen(None)

        class Boese(Koerper):
            def close(self):
                raise OSError('kaputt')
        A.schliessen(fehler_mit(Boese()))


# ---------------------------------------------------------------- Basis: feste Uhr, Zeitraffer

class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='abgleich-test-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.zeit = Zeitraffer()
        for p in (mock.patch.object(A, 'time', self.zeit), mock.patch.object(A, 'datetime', Fest), mock.patch.object(A, 'git_info', return_value=GIT_TEXT)):
            p.start()
            self.addCleanup(p.stop)

    def wdh(self, zeitrahmen=A.ZEITRAHMEN_STANDARD, **kw):
        """Ein Wiederholungsobjekt, das den Zeitraffer ausdrücklich bekommt (statt ihn über das time-Modul zu finden)."""
        return A.Wiederholung(zeitrahmen, uhr=self.zeit.monotonic, schlaf=self.zeit.sleep, wanduhr=self.zeit.time, **kw)


# ---------------------------------------------------------------- hole() mit der Wiederholung (urlopen-Attrappe)

class Holen:
    """Das Ergebnis eines Aufrufs von hole(): `ergebnis` (Text, Quelle, Stempel, Fehler), die Attrappe, das Wiederholungsobjekt, die Abbruchliste."""

    def __init__(self, ergebnis, urlopen, wdh, abbruch, err=''):
        self.ergebnis, self.urlopen, self.wdh, self.abbruch, self.err = ergebnis, urlopen, wdh, abbruch, err
        self.text, self.quelle, self.stempel, self.fehler = ergebnis

    @property
    def versuche(self):
        return len(self.urlopen.aufrufe)


class TestHole(Basis):
    TEAM = A.TEAMS[0]

    def setUp(self):
        super().setUp()
        self.cache = os.path.join(self.dir, 'cache')

    def neu(self):
        """Ein frischer Anfang: leerer Cache, keine Pausen, Uhr auf 0 (für Schleifen mit subTest)."""
        shutil.rmtree(self.cache, ignore_errors=True)
        self.zeit.pausen.clear()
        self.zeit.t = 0.0

    def hole(self, *folge, zeitrahmen=A.ZEITRAHMEN_STANDARD, mit_wiederholung=True, abbruch=None, team=None, nur_cache=False, wdh=None):
        urlopen = Urlopen(*folge, zeit=self.zeit)
        wdh = wdh or (self.wdh(zeitrahmen) if mit_wiederholung else None)
        abbruch = [] if abbruch is None else abbruch
        mehr = {'wiederholung': wdh} if mit_wiederholung else {}
        err = io.StringIO()
        with mock.patch.object(urllib.request, 'urlopen', urlopen), contextlib.redirect_stderr(err):
            ergebnis = A.hole(self.cache, team or self.TEAM, '2026-07-01', '2027-06-30', nur_cache, JETZT, abbruch,
                              'http://127.0.0.1:9/{tid}/{von}/{bis}', **mehr)
        return Holen(ergebnis, urlopen, wdh, abbruch, err.getvalue())

    def marke(self, slug='herren'):
        return os.path.join(self.cache, slug, 'fehlversuch.txt')

    # (i) ohne Wiederholungsobjekt: ein Versuch (so ruft nachtrag.py auf)
    def test_ohne_wiederholungsobjekt_genau_ein_versuch_bei_jeder_art_von_fehler(self):
        for name, erzeuge in VORUEBERGEHEND + ENDGUELTIG:
            with self.subTest(name):
                self.neu()
                h = self.hole(erzeuge, PLAN_TEXT, mit_wiederholung=False)
                self.assertEqual(h.versuche, 1)
                self.assertIsNone(h.text)
                self.assertTrue(h.fehler)
                self.assertEqual(h.abbruch, ['Herren: %s' % h.fehler])
                self.assertEqual(self.zeit.pausen, [], 'kein Schlafen')
                self.assertTrue(os.path.exists(self.marke()))

    def test_der_parameter_wiederholung_hat_den_standard_none(self):
        self.assertIsNone(inspect.signature(A.hole).parameters['wiederholung'].default)
        self.assertEqual(list(inspect.signature(A.hole).parameters)[:8], ['cache', 'team', 'von', 'bis', 'nur_cache', 'jetzt', 'abbruch', 'url_vorlage'])

    def test_direktaufruf_wie_in_nachtrag_py_ein_versuch_ohne_wartezeit(self):
        """So ruft tools/nachtrag.py auf (positional, ohne Schlüsselwort): HTTP 503 wird nicht wiederholt, auch nicht über Modul-Zustand."""
        urlopen = Urlopen(lambda: http_fehler(503), PLAN_TEXT)
        with mock.patch.object(urllib.request, 'urlopen', urlopen):
            ergebnis = A.hole(self.cache, self.TEAM, 'v', 'b', False, JETZT, [], 'http://127.0.0.1:9/{tid}/{von}/{bis}')
        self.assertEqual((len(urlopen.aufrufe), ergebnis[3]), (1, 'HTTP 503'))
        self.assertEqual(self.zeit.pausen, [])

    # Fehlertexte bleiben, wie sie waren
    def test_fehlertexte_sind_die_bisherigen(self):
        faelle = ((lambda: http_fehler(503), 'HTTP 503'), (lambda: http_fehler(404), 'HTTP 404'),
                  (lambda: TimeoutError('timed out'), 'TimeoutError: timed out'),
                  (lambda: urllib.error.URLError(TimeoutError('timed out')), 'URLError: <urlopen error timed out>'),
                  (lambda: _tunnel(403, 'Forbidden'), 'URLError: <urlopen error Tunnel connection failed: 403 Forbidden>'),
                  ('x' * 499, 'unerwartete Antwort (Status 200, 499 Zeichen)'),
                  (Seite(PLAN_TEXT, status=203), 'unerwartete Antwort (Status 203, %d Zeichen)' % len(PLAN_TEXT)))
        for eintrag, text in faelle:
            for mit in (False, True):
                with self.subTest(text=text, wiederholung=mit):
                    self.neu()
                    self.assertEqual(self.hole(eintrag, mit_wiederholung=mit, zeitrahmen=0).fehler, text)
        self.neu()                                   # nach vier Versuchen bleibt HTTP 503 genau HTTP 503
        self.assertEqual(self.hole(lambda: http_fehler(503)).fehler, 'HTTP 503')

    # (a)/(b) Verlauf, Pausen, Abbruchliste, Marke
    def test_503_einmal_dann_plan_gelesen_ohne_abbruch_und_ohne_marke(self):
        h = self.hole(lambda: http_fehler(503), PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler, h.quelle, h.stempel), (2, None, 'Abruf', JETZT))
        self.assertEqual(h.text, PLAN_TEXT)
        self.assertEqual(h.abbruch, [], 'ein wiederholter, dann erfolgreicher Abruf steht nicht in der Abbruchliste')
        self.assertFalse(os.path.exists(self.marke()), 'keine Fehlversuch-Marke')
        self.assertEqual(os.listdir(os.path.join(self.cache, 'herren')), ['20261026-071000.html'])
        with open(os.path.join(self.cache, 'herren', '20261026-071000.html'), encoding='utf-8') as f:
            self.assertEqual(f.read(), PLAN_TEXT)
        self.assertEqual(self.zeit.pausen, [60, A.PAUSE_SEKUNDEN], 'erst die Wiederholungspause, nach dem Erfolg wie sonst die Pause zwischen den Abrufen')
        self.assertEqual(h.wdh.zaehler, 1)

    def test_erfolg_nach_wiederholung_loescht_eine_alte_marke(self):
        os.makedirs(os.path.dirname(self.marke()))
        with open(self.marke(), 'w') as f:
            f.write('20250101-000000')           # vor mehr als 20 Stunden
        h = self.hole(lambda: http_fehler(503), PLAN_TEXT)
        self.assertEqual(h.fehler, None)
        self.assertFalse(os.path.exists(self.marke()))

    def test_503_dauerhaft_vier_versuche_pausen_60_120_240_dann_endgueltig(self):
        h = self.hole(lambda: http_fehler(503))
        self.assertEqual((h.versuche, h.fehler, h.text, h.quelle, h.stempel), (4, 'HTTP 503', None, '', None))
        self.assertEqual(self.zeit.pausen, [60, 120, 240])
        self.assertEqual(h.abbruch, ['Herren: HTTP 503'], 'erst der endgültige Fehlschlag kommt in die Abbruchliste, genau einmal')
        with open(self.marke()) as f:
            self.assertEqual(f.read(), '20261026-071000')
        self.assertFalse(glob_html(self.cache), 'kein Cache-Stand')
        self.assertEqual(self.zeit.t, 420.0)
        self.assertEqual(h.wdh.zaehler, 1)
        self.assertEqual(len(h.wdh.notizen), 1)
        self.assertTrue(h.wdh.notizen[0].endswith('Ausgang: endgültig gescheitert nach 4 Versuchen'), h.wdh.notizen)

    def test_nach_einem_endgueltigen_fehlschlag_ruft_der_naechste_aufruf_nichts_mehr_ab(self):
        abbruch = []
        self.hole(lambda: http_fehler(503), abbruch=abbruch)
        n = self.zeit.t
        h = self.hole(PLAN_TEXT, abbruch=abbruch, team=A.TEAMS[1])
        self.assertEqual((h.versuche, h.fehler), (0, 'nicht abgerufen, weil ein vorheriger Abruf scheiterte (Herren: HTTP 503)'))
        self.assertEqual(self.zeit.t, n)

    def test_je_plan_beginnt_die_zaehlung_der_versuche_und_pausen_von_vorn_der_zeitrahmen_aber_laeuft_weiter(self):
        w = self.wdh()
        h1 = self.hole(lambda: http_fehler(503), PLAN_TEXT, wdh=w)
        self.assertEqual((h1.versuche, self.zeit.wartezeiten()), (2, [60]))
        self.zeit.pausen.clear()
        h2 = self.hole(lambda: http_fehler(500), lambda: http_fehler(504), PLAN_TEXT, wdh=w, team=A.TEAMS[1])
        self.assertEqual((h2.versuche, h2.fehler), (3, None))
        self.assertEqual(self.zeit.wartezeiten(), [60, 120], 'der zweite Plan beginnt wieder bei 60 s, nicht bei der Pause des ersten')
        self.assertEqual((w.zaehler, len(w.notizen)), (2, 2))
        self.assertTrue(w.notizen[0].startswith('Herren: ') and w.notizen[1].startswith('A-Jugend: '), w.notizen)

    def test_zeitrahmen_begrenzt_die_versuche_genau_an_der_grenze(self):
        """Versuch n+1 beginnt nur, wenn vergangene Zeit plus Pause höchstens den Zeitrahmen ausmacht (kleiner oder gleich)."""
        for zeitrahmen, versuche, pausen in ((0, 1, []), (1, 1, []), (59, 1, []), (60, 2, [60]), (100, 2, [60]), (179, 2, [60]), (180, 3, [60, 120]),
                                            (419, 3, [60, 120]), (420, 4, [60, 120, 240]), (450, 4, [60, 120, 240]), (10 ** 6, 4, [60, 120, 240])):
            with self.subTest(zeitrahmen=zeitrahmen):
                self.neu()
                h = self.hole(lambda: http_fehler(503), zeitrahmen=zeitrahmen)
                self.assertEqual((h.versuche, h.fehler), (versuche, 'HTTP 503'))
                self.assertEqual(self.zeit.wartezeiten(), pausen)
                self.assertEqual(h.abbruch, ['Herren: HTTP 503'])

    def test_zeitrahmen_null_schaltet_die_wiederholung_ab_ohne_jede_meldung(self):
        h = self.hole(lambda: http_fehler(503), PLAN_TEXT, zeitrahmen=0)
        self.assertEqual((h.versuche, h.fehler), (1, 'HTTP 503'))
        self.assertEqual((h.wdh.notizen, h.wdh.zaehler, h.err), ([], 0, ''))

    def test_zeitrahmen_zaehlt_ab_dem_start_des_objekts_nicht_ab_dem_nullpunkt_der_uhr(self):
        """Der Zeitrahmen gilt für den ganzen Lauf: Wer erst spät scheitert, bekommt weniger Versuche. 30 + 60 + 120 + 240 = 450 passt gerade noch."""
        for uhr_vorher in (0.0, 1000.0):
            for vorlauf, versuche in ((30, 4), (31, 3)):
                with self.subTest(uhr_vorher=uhr_vorher, vorlauf=vorlauf):
                    self.neu()
                    self.zeit.t = uhr_vorher
                    w = self.wdh(450)                      # start = Stand der Uhr jetzt
                    self.zeit.vergehen(vorlauf)            # so viel vom Lauf ist vorbei, als dieser Plan scheitert
                    h = self.hole(lambda: http_fehler(503), wdh=w)
                    self.assertEqual((h.versuche, h.fehler), (versuche, 'HTTP 503'))

    # (d) Retry-After
    def test_retry_after_laenger_als_die_pause_bestimmt_die_pause(self):
        h = self.hole(lambda: http_fehler(503, {'Retry-After': '100'}), PLAN_TEXT)
        self.assertEqual((h.versuche, self.zeit.wartezeiten()), (2, [100]))

    def test_retry_after_kuerzer_oder_gleich_der_pause_aendert_nichts(self):
        for wert in ('1', '30', '60'):
            with self.subTest(wert):
                self.neu()
                self.hole(lambda: http_fehler(503, {'Retry-After': wert}), PLAN_TEXT)
                self.assertEqual(self.zeit.wartezeiten(), [60])

    def test_retry_after_als_http_datum(self):
        for abstand, pause in ((90, 90), (200, 200), (30, 60), (-500, 60)):
            with self.subTest(abstand=abstand):
                self.neu()
                datum = email.utils.formatdate(self.zeit.wand + abstand, usegmt=True)
                self.hole(lambda: http_fehler(503, {'Retry-After': datum}), PLAN_TEXT)
                self.assertEqual(self.zeit.wartezeiten(), [pause])

    def test_retry_after_gilt_je_antwort_nicht_fuer_alle_folgenden_pausen(self):
        h = self.hole(lambda: http_fehler(503, {'Retry-After': '100'}), lambda: http_fehler(503), PLAN_TEXT)
        self.assertEqual((h.versuche, self.zeit.wartezeiten()), (3, [100, 120]))

    def test_unbrauchbares_retry_after_wird_ignoriert(self):
        for wert in ('abc', '-5', '', '1e3'):
            with self.subTest(wert):
                self.neu()
                self.hole(lambda: http_fehler(503, {'Retry-After': wert}), PLAN_TEXT)
                self.assertEqual(self.zeit.wartezeiten(), [60])

    def test_retry_after_groesser_als_der_zeitrahmen_kein_weiterer_versuch_und_der_grund_steht_in_der_zeile(self):
        for wert in ('900', '451', '99999999999999999999', email.utils.formatdate(self.zeit.wand + 900, usegmt=True)):
            with self.subTest(wert=wert[:12]):
                self.neu()
                h = self.hole(lambda: http_fehler(503, {'Retry-After': wert}), PLAN_TEXT)
                self.assertEqual((h.versuche, h.fehler), (1, 'HTTP 503'))
                self.assertEqual(self.zeit.pausen, [])
                self.assertEqual(h.abbruch, ['Herren: HTTP 503'])
                self.assertEqual(len(h.wdh.notizen), 1)
                self.assertIn('Ausgang: Retry-After länger als der Zeitrahmen', h.wdh.notizen[0])
                self.assertEqual(h.wdh.zaehler, 0, 'es wurde nichts wiederholt')

    def test_retry_after_genau_im_zeitrahmen_wird_noch_abgewartet(self):
        h = self.hole(lambda: http_fehler(503, {'Retry-After': '450'}), PLAN_TEXT)
        self.assertEqual((h.versuche, self.zeit.wartezeiten()), (2, [450]))
        self.neu()
        h = self.hole(lambda: http_fehler(503, {'Retry-After': '451'}), PLAN_TEXT, zeitrahmen=450)
        self.assertEqual((h.versuche, self.zeit.wartezeiten()), (1, []))

    def test_retry_after_das_den_rest_des_zeitrahmens_sprengt(self):
        h = self.hole(lambda: http_fehler(503, {'Retry-After': '190'}), zeitrahmen=200)
        self.assertEqual((h.versuche, self.zeit.wartezeiten()), (2, [190]))
        self.assertIn('Ausgang: Retry-After länger als der Zeitrahmen', h.wdh.notizen[0])

    def test_zeitrahmen_erschoepft_hat_einen_eigenen_grund(self):
        h = self.hole(lambda: http_fehler(503), zeitrahmen=100)
        self.assertEqual(h.versuche, 2)
        self.assertIn('Ausgang: Zeitrahmen erschöpft (60 s von 100 s verbraucht, nächste Pause 120 s)', h.wdh.notizen[0])

    def test_http_429_wird_wiederholt_und_beachtet_retry_after(self):
        h = self.hole(lambda: http_fehler(429, {'Retry-After': '120'}), PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler, self.zeit.wartezeiten()), (2, None, [120]))

    # (e) endgültig: genau ein Versuch
    def test_endgueltige_fehler_ein_versuch_auch_mit_wiederholungsobjekt(self):
        for name, erzeuge in ENDGUELTIG:
            with self.subTest(name):
                self.neu()
                h = self.hole(erzeuge, PLAN_TEXT)
                self.assertEqual(h.versuche, 1)
                self.assertIsNone(h.text)
                self.assertEqual(h.abbruch, ['Herren: %s' % h.fehler])
                self.assertEqual((self.zeit.pausen, h.wdh.notizen, h.wdh.zaehler, h.err), ([], [], 0, ''), 'nichts gemeldet, nichts gewartet')

    def test_endgueltiger_fehler_nach_einer_vorubergehenden_stoerung_beendet_die_wiederholung(self):
        h = self.hole(lambda: http_fehler(503), lambda: http_fehler(403), PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler, self.zeit.wartezeiten()), (2, 'HTTP 403', [60]))
        self.assertTrue(h.wdh.notizen[0].endswith('Ausgang: endgültig gescheitert'), h.wdh.notizen)
        self.assertEqual(h.abbruch, ['Herren: HTTP 403'])

    def test_unbrauchbare_antwort_nach_einer_stoerung_ist_endgueltig(self):
        h = self.hole(lambda: http_fehler(503), 'x' * 100, PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler), (2, 'unerwartete Antwort (Status 200, 100 Zeichen)'))
        self.assertTrue(h.wdh.notizen[0].endswith('Ausgang: endgültig gescheitert'))

    # (f) vorübergehend: wiederholt, dann Erfolg
    def test_jede_vorubergehende_art_wird_wiederholt_und_im_bericht_genannt(self):
        for name, erzeuge in VORUEBERGEHEND:
            with self.subTest(name):
                self.neu()
                h = self.hole(erzeuge, PLAN_TEXT)
                self.assertEqual((h.versuche, h.fehler, h.text), (2, None, PLAN_TEXT))
                self.assertEqual(self.zeit.wartezeiten(), [60])
                self.assertEqual(h.abbruch, [])
                self.assertEqual(len(h.wdh.notizen), 1)
                self.assertIn('Ausgang: Erfolg im 2. Versuch', h.wdh.notizen[0])
                self.assertIn('neuer Versuch in 60 s (Versuch 2 von höchstens 4)', h.err)

    def test_gescheiterter_versuch_im_lesen_des_koerpers_wird_wiederholt(self):
        class KoerperKaputt(Seite):
            def read(self):
                raise http.client.IncompleteRead(b'abc', 100)
        h = self.hole(KoerperKaputt(PLAN_TEXT), PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler), (2, None))

    # Ressourcen
    def test_http_fehler_antworten_werden_geschlossen_mit_und_ohne_wiederholung(self):
        for mit in (False, True):
            with self.subTest(wiederholung=mit):
                self.neu()
                erzeugt = []

                def frisch(code=503):
                    k = Koerper(b'<title>x</title>')
                    erzeugt.append(k)
                    return fehler_mit(k, code=code)
                self.hole(frisch, lambda: frisch(403), PLAN_TEXT, mit_wiederholung=mit)
                self.assertTrue(erzeugt and all(k.zu for k in erzeugt), 'alle %d Fehlerantworten geschlossen' % len(erzeugt))

    def test_cache_treffer_und_nur_cache_brauchen_weder_urlopen_noch_wiederholung(self):
        h = self.hole(PLAN_TEXT)
        self.assertEqual(h.quelle, 'Abruf')
        h2 = self.hole(lambda: http_fehler(503))
        self.assertEqual((h2.versuche, h2.quelle, h2.text), (0, 'Cache', PLAN_TEXT))
        h3 = self.hole(lambda: http_fehler(503), nur_cache=True, team=A.TEAMS[1])
        self.assertEqual((h3.versuche, h3.fehler), (0, 'kein Cache-Stand vorhanden (--nur-cache)'))
        self.assertEqual(h3.wdh.notizen, [])

    def test_fehlversuch_marke_vor_weniger_als_20_stunden_verhindert_jeden_versuch(self):
        os.makedirs(os.path.dirname(self.marke()))
        with open(self.marke(), 'w') as f:
            f.write('20261026-020000')
        h = self.hole(PLAN_TEXT)
        self.assertEqual((h.versuche, h.fehler), (0, 'letzter Abruf schlug vor weniger als 20 Stunden fehl, kein neuer Versuch'))

    def test_der_schlafaufruf_nach_erfolg_ist_die_pause_zwischen_den_abrufen(self):
        self.hole(PLAN_TEXT)
        self.assertEqual(self.zeit.pausen, [A.PAUSE_SEKUNDEN])
        self.assertEqual(A.PAUSE_SEKUNDEN, 3)


def glob_html(cache):
    return [os.path.join(d, n) for d, _, ns in os.walk(cache) for n in ns if n.endswith('.html')]


# ---------------------------------------------------------------- Das Wiederholungsobjekt

class TestWiederholungsobjekt(Basis):
    def test_standardwerte_und_merkmale(self):
        w = A.Wiederholung()
        self.assertEqual((w.zeitrahmen, w.notizen, w.zaehler, w.start), (450, [], 0, 0.0))
        self.assertEqual(A.Wiederholung(7).zeitrahmen, 7)
        self.assertEqual(A.Wiederholung(0).zeitrahmen, 0)

    def test_ohne_angabe_werden_uhr_und_schlaf_des_time_moduls_beim_aufruf_gelesen(self):
        """abgleich.time ist hier der Zeitraffer: das Objekt findet monotonic() und sleep() über das Modul, nicht über beim Import gebundene Funktionen."""
        w = A.Wiederholung()
        self.zeit.vergehen(5)
        self.assertEqual(w.jetzt(), 5.0)
        w.warten(7)
        self.assertEqual((self.zeit.pausen, w.jetzt(), w.wandzeit()), ([7], 12.0, self.zeit.wand + 12.0))

    def test_uhr_schlaf_und_wanduhr_sind_einspritzbar(self):
        gesehen = []
        with mock.patch.object(A, 'time', mock.Mock(side_effect=AssertionError('das time-Modul darf nicht gebraucht werden'))):
            w = A.Wiederholung(450, uhr=lambda: 42.0, schlaf=gesehen.append, wanduhr=lambda: 1000.0)
            w.warten(3)
            self.assertEqual((w.start, w.jetzt(), w.wandzeit(), gesehen), (42.0, 42.0, 1000.0, [3]))

    def test_zeitrahmen_null_ist_stumm(self):
        w = A.Wiederholung(0, uhr=lambda: 0.0, schlaf=lambda s: None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            verlauf = w.beginnen('Herren')
            self.assertIsNone(w.nach_fehler(verlauf, 1, 'HTTP 503', http_fehler(503)))
            w.abschliessen(verlauf, 1, 'HTTP 503')
        self.assertEqual((err.getvalue(), w.notizen, w.zaehler), ('', [], 0))

    def test_erste_meldung_auf_stderr_und_die_berichtzeile_im_festen_format(self):
        w = self.wdh()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            verlauf = w.beginnen('Herren')
            pause = w.nach_fehler(verlauf, 1, 'HTTP 503', http_fehler(503, {'Server': 'nginx', 'Retry-After': '90', 'Via': '1.1 x'}, FEHLERSEITE.encode()))
            w.warten(pause)
            pause2 = w.nach_fehler(verlauf, 2, 'TimeoutError: timed out', TimeoutError('timed out'))
            w.warten(pause2)
            w.abschliessen(verlauf, 3, None)
        self.assertEqual((pause, pause2), (90, 120))
        self.assertEqual(err.getvalue(), '  Herren: HTTP 503 – neuer Versuch in 90 s (Versuch 2 von höchstens 4)\n'
                                         '    Diagnose: Server: nginx; Retry-After: 90; Via: 1.1 x; Seitentitel: „Wartungsarbeiten & Störung“\n'
                                         '  Herren: TimeoutError: timed out – neuer Versuch in 120 s (Versuch 3 von höchstens 4)\n')
        self.assertEqual(w.notizen, ['Herren: Wiederholung – Versuch 1 (07:10:00): HTTP 503 [Server: nginx; Retry-After: 90; Via: 1.1 x; '
                                     'Seitentitel: „Wartungsarbeiten & Störung“], Pause 90 s · Versuch 2 (07:11:30): TimeoutError: timed out, Pause 120 s · '
                                     'Versuch 3 (07:13:30): Erfolg · Ausgang: Erfolg im 3. Versuch'])
        self.assertEqual(w.zaehler, 1)

    def test_verzicht_auf_weitere_versuche_wird_auf_stderr_begruendet(self):
        w = self.wdh(50)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(w.nach_fehler(w.beginnen('Herren'), 1, 'HTTP 503', http_fehler(503)))
        self.assertEqual(err.getvalue(), '  Herren: HTTP 503 – kein weiterer Versuch: Zeitrahmen erschöpft (0 s von 50 s verbraucht, nächste Pause 60 s)\n')

    def test_pausen_werden_zur_laufzeit_aus_dem_modul_gelesen(self):
        """Wer WIEDERHOLUNG_PAUSEN ersetzt (zum Beispiel eine Simulation), ändert Pausen und Zahl der Versuche; Gleitkommazahlen sind erlaubt."""
        with mock.patch.object(A, 'WIEDERHOLUNG_PAUSEN', (0.2, 0.3)):
            w = self.wdh()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                v = w.beginnen('Herren')
                self.assertEqual(w.nach_fehler(v, 1, 'HTTP 503', http_fehler(503)), 0.2)
                self.assertEqual(w.nach_fehler(v, 2, 'HTTP 503', http_fehler(503)), 0.3)
                self.assertIsNone(w.nach_fehler(v, 3, 'HTTP 503', http_fehler(503)))
            self.assertIn('neuer Versuch in 0.2 s (Versuch 2 von höchstens 3)', err.getvalue())
            self.assertIn('kein weiterer Versuch: endgültig gescheitert nach 3 Versuchen', err.getvalue())

    def test_zwei_objekte_teilen_keinen_zustand(self):
        a, b = self.wdh(), self.wdh()
        with contextlib.redirect_stderr(io.StringIO()):
            v = a.beginnen('Herren')
            a.nach_fehler(v, 1, 'HTTP 503', http_fehler(503))
            a.abschliessen(v, 2, None)
        self.assertEqual((len(a.notizen), a.zaehler, b.notizen, b.zaehler), (1, 1, [], 0))


# ---------------------------------------------------------------- main() gegen den lokalen Server

class Lauf:
    """Ein Lauf von A.main(): Exit-Code, stdout, stderr, der Bericht und die Anfragen an den Server."""

    def __init__(self, code, out, err, bericht, anfragen, zeit):
        self.code, self.out, self.err, self.bericht, self.anfragen, self.zeit = code, out, err, bericht, anfragen, zeit

    @property
    def abrufe(self):
        """Die Zeilen unter „## Abrufe dieses Laufs“ (ohne Leerzeilen)."""
        text = self.bericht.split('## Abrufe dieses Laufs', 1)[1].split('## Kontrolle', 1)[0]
        return [z for z in text.splitlines() if z.strip()]

    @property
    def wiederholungszeilen(self):
        return [z for z in self.abrufe if ': Wiederholung – ' in z]

    @property
    def erste_zeile(self):
        return self.out.splitlines()[0]


def lauf_modul(modul, tmp, welt, folge, extra=(), urlopen=None, zeit=None, csv=None, cache=None, berichte=None):
    """modul.main() (abgleich oder – für die Gegenprobe – ein anderer Stand dieses Moduls) mit fester Uhr und Zeitraffer; entweder gegen einen lokalen
    Skriptserver (`folge`: {Team-ID: [Antwort, …]}) oder mit einer urlopen-Attrappe. -> Lauf."""
    zeit = zeit or Zeitraffer()
    csv = csv or os.path.join(tmp, 'spiele.csv')
    cache = cache or os.path.join(tmp, 'cache')
    berichte = berichte or os.path.join(tmp, 'berichte')
    if not os.path.exists(csv):
        with open(csv, 'wb') as f:
            f.write(welt.csv_bytes())
    server = None if urlopen is not None else Skriptserver(welt)
    try:
        if server:
            server.folge.update(folge)
        argv = ['abgleich.py', '--csv', csv, '--cache', cache, '--bericht-ordner', berichte, '--heute', HEUTE, '--kein-wochenvergleich',
                '--url-vorlage', server.vorlage if server else 'http://127.0.0.1:9/{tid}/{von}/{bis}'] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as st:
            st.enter_context(mock.patch.object(sys, 'argv', argv))
            st.enter_context(mock.patch.object(modul, 'time', zeit))
            st.enter_context(mock.patch.object(modul, 'datetime', Fest))
            st.enter_context(mock.patch.object(modul, 'git_info', return_value=GIT_TEXT))
            if urlopen is not None:
                st.enter_context(mock.patch.object(urllib.request, 'urlopen', urlopen))
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(err))
            code = modul.main()
        with open(os.path.join(berichte, 'bericht-%s.md' % HEUTE), encoding='utf-8') as f:
            bericht = f.read()
        return Lauf(code, out.getvalue(), err.getvalue(), bericht, list(server.anfragen) if server else [], zeit)
    finally:
        if server:
            server.beenden()


class TestMain(Basis):
    """main() gegen einen lokalen HTTP-Server: die echten Fehlerklassen von urllib, die echten Kopfzeilen, die echten Berichte."""

    HERREN = A.TEAMS[0][3]
    D1 = A.TEAMS[2][3]
    D4 = A.ZUSATZQUELLEN[0][3]
    ALLE = [t[3] for t in A.TEAMS] + [D4]

    def setUp(self):
        super().setUp()
        self.welt = T.Welt()
        self.cache, self.berichte = os.path.join(self.dir, 'cache'), os.path.join(self.dir, 'berichte')

    def lauf(self, folge=None, *extra, **kw):
        return lauf_modul(A, self.dir, self.welt, folge or {}, extra, zeit=self.zeit, **kw)

    def marke(self, slug):
        return os.path.join(self.cache, slug, 'fehlversuch.txt')

    # (a) 503 einmal, dann der Plan
    def test_503_einmal_dann_plan_alle_zwoelf_plaene_exit_0_wiederholungszeile_keine_marke(self):
        r = self.lauf({self.HERREN: [fehler(503, {'Retry-After': '30', 'Via': '1.1 cdn'}), plan]})
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.anfragen, [self.HERREN] + self.ALLE, 'der Herren-Plan zweimal, danach alle elf übrigen Pläne und die D4, jeder einmal')
        self.assertEqual(r.zeit.wartezeiten(), [60])
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 12 Abrufe, 0 aus dem Cache, 0 fehlgeschlagen, 1 wiederholt')
        self.assertIn('Dieser Lauf: 12 Abrufe, 0 aus dem Cache, 0 fehlgeschlagen, 1 wiederholt.', r.bericht)
        self.assertEqual(r.wiederholungszeilen, [
            '- Herren: Wiederholung – Versuch 1 (07:10:00): HTTP 503 [Server: Testserver/1.0; Retry-After: 30; Via: 1.1 cdn; Seitentitel: '
            '„Wartungsarbeiten & Störung“], Pause 60 s · Versuch 2 (07:11:00): Erfolg · Ausgang: Erfolg im 2. Versuch'])
        self.assertEqual(r.abrufe[:12], ['- %s: Abruf 26.10.2026 07:10' % n for n in [t[2] for t in A.TEAMS] + [A.ZUSATZQUELLEN[0][2]]],
                         'die bisherigen zwölf Zeilen, dann erst die Wiederholungszeile')
        self.assertEqual(len(r.abrufe), 13)
        self.assertEqual(r.err, '  Herren: HTTP 503 – neuer Versuch in 60 s (Versuch 2 von höchstens 4)\n'
                                '    Diagnose: Server: Testserver/1.0; Retry-After: 30; Via: 1.1 cdn; Seitentitel: „Wartungsarbeiten & Störung“\n')
        self.assertFalse(os.path.exists(self.marke('herren')), 'keine Fehlversuch-Marke')
        self.assertEqual([os.path.exists(os.path.join(self.cache, t[1])) for t in A.TEAMS], [True] * 11)
        self.assertNotIn('nicht ausgewertet', r.bericht)

    def test_der_lauf_nach_wiederholung_gleicht_dem_ohne_stoerung_ausser_den_zaehlzeilen_und_der_wiederholungszeile(self):
        sauber = self.lauf()
        verz = os.path.join(self.dir, 'zweiter')
        os.makedirs(verz)
        gestoert = lauf_modul(A, verz, self.welt, {self.D1: [fehler(502), plan]}, zeit=Zeitraffer(), csv=os.path.join(self.dir, 'spiele.csv'),
                              berichte=self.berichte)

        def vergleichbar(text):
            text = re.sub(r', \d+ wiederholt', '', text)
            return [z for z in text.splitlines() if ': Wiederholung – ' not in z]
        self.assertEqual(vergleichbar(gestoert.bericht), vergleichbar(sauber.bericht))
        self.assertEqual(vergleichbar(gestoert.out), vergleichbar(sauber.out))
        self.assertEqual((gestoert.code, len(gestoert.wiederholungszeilen)), (sauber.code, 1))
        self.assertEqual(len(gestoert.out.splitlines()), len(sauber.out.splitlines()), 'kein zusätzliches Stück Ausgabe')

    def test_zwei_plaene_mit_stoerung_je_plan_eigene_zaehlung_und_zeilen(self):
        r = self.lauf({self.HERREN: [fehler(503), plan], self.D1: [fehler(500), fehler(504), plan]})
        self.assertEqual(r.code, 0, r.err)
        self.assertEqual(r.anfragen.count(self.HERREN), 2)
        self.assertEqual(r.anfragen.count(self.D1), 3)
        self.assertEqual(r.zeit.wartezeiten(), [60, 60, 120])
        self.assertEqual(len(r.wiederholungszeilen), 2)
        self.assertTrue(r.wiederholungszeilen[0].startswith('- Herren: Wiederholung – '))
        self.assertTrue(r.wiederholungszeilen[1].startswith('- D1: Wiederholung – '))
        self.assertIn('Ausgang: Erfolg im 3. Versuch', r.wiederholungszeilen[1])
        self.assertIn('fehlgeschlagen, 2 wiederholt', r.erste_zeile)

    # (b) 503 dauerhaft
    def test_503_dauerhaft_vier_versuche_danach_kein_weiterer_plan_exit_2_marke_geschrieben(self):
        r = self.lauf({self.HERREN: [fehler(503, {'Retry-After': '5'})]})
        self.assertEqual(r.code, 2)
        self.assertEqual(r.anfragen, [self.HERREN] * 4, 'vier Versuche für den ersten Plan, danach wird nichts mehr abgerufen (auch die D4 nicht)')
        self.assertEqual(r.zeit.wartezeiten(), [60, 120, 240])
        self.assertEqual(self.zeit.t, 420.0)
        self.assertEqual(r.abrufe[0], '- Herren: FEHLER (HTTP 503)', 'der Fehlertext bleibt genau HTTP 503')
        self.assertTrue(all('nicht abgerufen, weil ein vorheriger Abruf scheiterte (Herren: HTTP 503)' in z for z in r.abrufe[1:11]), r.abrufe)
        self.assertEqual(len(r.wiederholungszeilen), 1)
        zeile = r.wiederholungszeilen[0]
        self.assertTrue(zeile.startswith('- Herren: Wiederholung – Versuch 1 (07:10:00): HTTP 503 [Server: Testserver/1.0; Retry-After: 5; '))
        for n, um, pause in ((1, '07:10:00', 60), (2, '07:11:00', 120), (3, '07:13:00', 240)):
            self.assertIn('Versuch %d (%s): HTTP 503 [' % (n, um), zeile)
            self.assertIn('], Pause %d s' % pause, zeile)
        self.assertIn('Versuch 4 (07:17:00): HTTP 503 [', zeile)
        self.assertTrue(zeile.endswith('Ausgang: endgültig gescheitert nach 4 Versuchen'), zeile)
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 0 aus dem Cache, 11 fehlgeschlagen, 1 wiederholt')
        with open(self.marke('herren')) as f:
            self.assertEqual(f.read(), '20261026-071000')
        self.assertFalse(os.path.exists(os.path.join(self.cache, 'd4')), 'die D4 wurde nie angefragt')
        self.assertIn('  Herren: HTTP 503 – kein weiterer Versuch: endgültig gescheitert nach 4 Versuchen\n', r.err)
        self.assertTrue(r.err.endswith('  Herren: Abruf fehlgeschlagen (HTTP 503); weitere Abrufe in diesem Lauf entfallen\n'), r.err)
        self.assertEqual(r.err.count('neuer Versuch in'), 3)

    def test_nach_dem_endgueltigen_fehlschlag_der_d4_sind_alle_elf_plaene_gelesen_und_die_d4_hat_weniger_versuche(self):
        """Der Zeitrahmen gilt je Lauf: Die D4 kommt zuletzt, nach elf Abrufen mit je 3 s Pause (33 s); ihr vierter Versuch passt nicht mehr hinein."""
        r = self.lauf({self.D4: [fehler(503)]})
        self.assertEqual(r.code, 2)
        self.assertEqual(r.anfragen, [t[3] for t in A.TEAMS] + [self.D4] * 3)
        self.assertEqual(r.zeit.wartezeiten(), [60, 120])
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 11 Abrufe, 0 aus dem Cache, 1 fehlgeschlagen, 1 wiederholt')
        self.assertIn('- D4 (Freundschaftsspiele der D3): FEHLER (HTTP 503)', r.abrufe)
        self.assertIn('Ausgang: Zeitrahmen erschöpft (213 s von 450 s verbraucht, nächste Pause 240 s)', r.wiederholungszeilen[0])
        self.assertIn('**Zusatzquelle ausgefallen:** D4 (Freundschaftsspiele der D3) – HTTP 503;', r.bericht)

    # (c) Zeitrahmen
    def test_zeitrahmen_100_begrenzt_auf_zwei_versuche(self):
        r = self.lauf({self.HERREN: [fehler(503)]}, '--zeitrahmen', '100')
        self.assertEqual((r.code, r.anfragen, r.zeit.wartezeiten()), (2, [self.HERREN] * 2, [60]))
        self.assertIn('Ausgang: Zeitrahmen erschöpft', r.wiederholungszeilen[0])
        self.assertEqual(r.abrufe[0], '- Herren: FEHLER (HTTP 503)')

    def test_zeitrahmen_je_wert_die_zahl_der_versuche(self):
        for wert, versuche in (('59', 1), ('60', 2), ('179', 2), ('180', 3), ('419', 3), ('420', 4)):
            with self.subTest(zeitrahmen=wert):
                verz = os.path.join(self.dir, 'z' + wert)
                os.makedirs(verz)
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(503)]}, ('--zeitrahmen', wert), zeit=Zeitraffer())
                self.assertEqual(r.anfragen, [self.HERREN] * versuche)

    # (d) Retry-After über den echten Server
    def test_retry_after_sekunden_und_datum_kleiner_als_die_pause_die_pause_bleibt(self):
        for wert in ('30', 'datum'):
            with self.subTest(wert):
                verz = os.path.join(self.dir, 'k' + wert)
                os.makedirs(verz)
                z = Zeitraffer()
                w = email.utils.formatdate(z.wand + 30, usegmt=True) if wert == 'datum' else wert
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(503, {'Retry-After': w}), plan]}, zeit=z)
                self.assertEqual((r.code, r.zeit.wartezeiten()), (0, [60]))

    def test_retry_after_sekunden_und_datum_laenger_als_die_pause_bestimmt_die_pause(self):
        for wert in ('100', 'datum'):
            with self.subTest(wert):
                verz = os.path.join(self.dir, 'l' + wert)
                os.makedirs(verz)
                z = Zeitraffer()
                w = email.utils.formatdate(z.wand + 100, usegmt=True) if wert == 'datum' else wert
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(503, {'Retry-After': w}), plan]}, zeit=z)
                self.assertEqual((r.code, r.zeit.wartezeiten()), (0, [100]))
                self.assertIn('Retry-After: %s' % w, r.wiederholungszeilen[0])
                self.assertIn('Pause 100 s', r.wiederholungszeilen[0])

    def test_retry_after_groesser_als_der_zeitrahmen_kein_weiterer_versuch_grund_im_bericht(self):
        for wert in ('3600', 'datum'):
            with self.subTest(wert):
                verz = os.path.join(self.dir, 'g' + wert)
                os.makedirs(verz)
                z = Zeitraffer()
                w = email.utils.formatdate(z.wand + 3600, usegmt=True) if wert == 'datum' else wert
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(503, {'Retry-After': w}), plan]}, zeit=z)
                self.assertEqual((r.code, r.anfragen, r.zeit.pausen), (2, [self.HERREN], []))
                self.assertEqual(r.abrufe[0], '- Herren: FEHLER (HTTP 503)')
                self.assertIn('Ausgang: Retry-After länger als der Zeitrahmen (Retry-After 3600 s, im Zeitrahmen von 450 s noch 450 s übrig)',
                              r.wiederholungszeilen[0])
                self.assertIn('  Herren: HTTP 503 – kein weiterer Versuch: Retry-After länger als der Zeitrahmen', r.err)
                self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 0 aus dem Cache, 11 fehlgeschlagen',
                                 'nichts wiederholt, also kein Zusatz')

    def test_mehrere_retry_after_kopfzeilen_und_fremde_werte_ueber_den_echten_server(self):
        r = self.lauf({self.HERREN: [fehler(503, [('Retry-After', '10'), ('Retry-After', '100')]), fehler(503, {'Retry-After': 'ü'}), plan]})
        self.assertEqual((r.code, r.zeit.wartezeiten()), (0, [100, 120]))

    # (e) endgültig über den echten Server
    def test_403_ein_versuch_stderr_und_bericht_wie_bisher(self):
        r = self.lauf({self.HERREN: [fehler(403, {'Server': 'Testserver/1.0'}, '<title>Forbidden</title>')]})
        self.assertEqual((r.code, r.anfragen), (2, [self.HERREN]))
        self.assertEqual(r.zeit.pausen, [])
        self.assertEqual(r.abrufe[0], '- Herren: FEHLER (HTTP 403)')
        self.assertEqual(r.wiederholungszeilen, [])
        self.assertEqual(r.err, '  Herren: Abruf fehlgeschlagen (HTTP 403); weitere Abrufe in diesem Lauf entfallen\n', 'keine zusätzliche Meldung')
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 0 aus dem Cache, 11 fehlgeschlagen')
        self.assertTrue(os.path.exists(self.marke('herren')))

    def test_404_ein_versuch_bei_der_d4_die_elf_anderen_plaene_sind_gelesen(self):
        r = self.lauf({self.D4: [fehler(404)]})
        self.assertEqual((r.code, r.anfragen), (2, [t[3] for t in A.TEAMS] + [self.D4]))
        self.assertEqual(r.wiederholungszeilen, [])
        self.assertIn('- D4 (Freundschaftsspiele der D3): FEHLER (HTTP 404)', r.abrufe)

    def test_andere_4xx_und_501_ein_versuch(self):
        for code in (400, 401, 410, 425, 451, 501, 505, 519, 525, 526):
            with self.subTest(code):
                verz = os.path.join(self.dir, 's%d' % code)
                os.makedirs(verz)
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(code)]}, zeit=Zeitraffer())
                self.assertEqual((r.code, r.anfragen, r.wiederholungszeilen), (2, [self.HERREN], []))

    # (g) 429
    def test_429_mit_retry_after_wird_wiederholt(self):
        r = self.lauf({self.HERREN: [fehler(429, {'Retry-After': '120'}), plan]})
        self.assertEqual((r.code, r.zeit.wartezeiten(), r.anfragen.count(self.HERREN)), (0, [120], 2))
        self.assertIn('HTTP 429 [Server: Testserver/1.0; Retry-After: 120;', r.wiederholungszeilen[0])
        self.assertIn('Pause 120 s', r.wiederholungszeilen[0])

    def test_die_uebrigen_vorubergehenden_http_codes_werden_wiederholt(self):
        """Über den echten lokalen Server und main(): 408, 500, 502, 504 und die Cloudflare-Fehlerseiten 520 bis 524 (429 und 503 stehen oben)."""
        for code in (408, 500, 502, 504, 520, 521, 522, 523, 524):
            with self.subTest(code):
                verz = os.path.join(self.dir, 'w%d' % code)
                os.makedirs(verz)
                r = lauf_modul(A, verz, self.welt, {self.HERREN: [fehler(code), plan]}, zeit=Zeitraffer())
                self.assertEqual((r.code, r.anfragen.count(self.HERREN)), (0, 2))
                self.assertIn('HTTP %d [' % code, r.wiederholungszeilen[0])

    # (h) --zeitrahmen 0
    def test_zeitrahmen_0_ist_das_alte_verhalten(self):
        r = self.lauf({self.HERREN: [fehler(503, {'Retry-After': '5'}), plan]}, '--zeitrahmen', '0')
        self.assertEqual((r.code, r.anfragen, r.zeit.pausen), (2, [self.HERREN], []))
        self.assertEqual(r.abrufe[0], '- Herren: FEHLER (HTTP 503)')
        self.assertEqual(r.wiederholungszeilen, [])
        self.assertEqual(r.err, '  Herren: Abruf fehlgeschlagen (HTTP 503); weitere Abrufe in diesem Lauf entfallen\n')
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 0 aus dem Cache, 11 fehlgeschlagen')
        self.assertNotIn('wiederholt', r.bericht)

    # (k) Diagnose
    def test_server_kopfzeile_und_seitentitel_stehen_in_der_berichtzeile(self):
        seite = '<html><head><title>  Dienst\n   nicht &lt;b&gt;verfügbar&lt;/b&gt; &amp; offen </title>'
        r = self.lauf({self.HERREN: [fehler(503, {'Via': '1.1 cdn'}, seite), plan]})
        self.assertEqual(r.code, 0, r.err)
        self.assertIn('HTTP 503 [Server: Testserver/1.0; Via: 1.1 cdn; Seitentitel: „Dienst nicht verfügbar & offen“]', r.wiederholungszeilen[0])
        self.assertIn('    Diagnose: Server: Testserver/1.0; Via: 1.1 cdn; Seitentitel: „Dienst nicht verfügbar & offen“\n', r.err)

    def test_fehlerseite_ohne_koerper_und_ohne_titel(self):
        r = self.lauf({self.HERREN: [fehler(503, {}, ''), plan]})
        self.assertEqual(r.code, 0)
        self.assertIn('Versuch 1 (07:10:00): HTTP 503 [Server: Testserver/1.0], Pause 60 s', r.wiederholungszeilen[0])

    def test_ein_fehlerkoerper_dessen_lesen_wirft_bringt_den_lauf_nicht_zum_absturz(self):
        """Hier über main() mit einer urlopen-Attrappe, deren Fehlerantwort beim Lesen eine Ausnahme wirft; alle zwölf Pläne kommen trotzdem an."""
        koerper = Koerper(ausnahme=OSError('Verbindung getrennt'))
        urlopen = Urlopen(lambda: fehler_mit(koerper, {'Server': 'Testserver/1.0', 'Retry-After': '30'}), PLAN_TEXT, zeit=self.zeit)
        r = lauf_modul(A, self.dir, self.welt, {}, urlopen=urlopen, zeit=self.zeit)
        self.assertEqual((r.code, len(urlopen.aufrufe)), (0, 13))
        self.assertTrue(koerper.zu and koerper.gelesen == [4096])
        self.assertIn('HTTP 503 [Server: Testserver/1.0; Retry-After: 30], Pause 60 s', r.wiederholungszeilen[0])

    # unabhängige Läufe im selben Prozess
    def test_ein_lauf_hinterlaesst_keinen_zustand_fuer_den_naechsten(self):
        erster = self.lauf({self.HERREN: [fehler(503), plan]})
        self.assertEqual(len(erster.wiederholungszeilen), 1)
        verz = os.path.join(self.dir, 'zweiter')
        os.makedirs(verz)
        zweiter = lauf_modul(A, verz, self.welt, {}, zeit=Zeitraffer())
        self.assertEqual((zweiter.code, zweiter.wiederholungszeilen, zweiter.zeit.pausen), (0, [], [A.PAUSE_SEKUNDEN] * 12))
        self.assertNotIn('wiederholt', zweiter.erste_zeile)


# ---------------------------------------------------------------- Echte Fehlerklassen von urllib (lokaler Server, winziger TCP-Server)

class TestEchteFehler(Basis):
    """Die Einstufung gegen das, was urllib und http.client wirklich werfen: Fehler, die ein lokaler Server oder ein winziger TCP-Server auslöst."""

    def setUp(self):
        super().setUp()
        self.welt = T.Welt()
        self.server = Skriptserver(self.welt)
        self.addCleanup(self.server.beenden)
        self.cache = os.path.join(self.dir, 'cache')
        self.herren = A.TEAMS[0][3]

    def hole(self, vorlage=None, urlopen=None, zeitrahmen=A.ZEITRAHMEN_STANDARD):
        wdh = self.wdh(zeitrahmen)
        abbruch = []
        with contextlib.ExitStack() as st:
            if urlopen is not None:
                st.enter_context(mock.patch.object(urllib.request, 'urlopen', urlopen))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            ergebnis = A.hole(self.cache, A.TEAMS[0], 'v', 'b', False, JETZT, abbruch, vorlage or self.server.vorlage, wiederholung=wdh)
        return Holen(ergebnis, None, wdh, abbruch)

    def test_verbindung_ohne_antwort_remote_disconnected_wird_wiederholt(self):
        self.server.folge[self.herren] = [trennen, plan]
        h = self.hole()
        self.assertEqual((h.fehler, self.server.anfragen_an(self.herren), self.zeit.wartezeiten()), (None, 2, [60]))
        self.assertIn('RemoteDisconnected: Remote end closed connection without response', h.wdh.notizen[0])

    def test_abgeschnittene_antwort_incomplete_read_wird_wiederholt(self):
        self.server.folge[self.herren] = [abgeschnitten, plan]
        h = self.hole()
        self.assertEqual((h.fehler, self.server.anfragen_an(self.herren), self.zeit.wartezeiten()), (None, 2, [60]))
        self.assertIn('IncompleteRead(50 bytes read, 99950 more expected)', h.wdh.notizen[0])

    def test_zeitueberschreitung_wird_wiederholt(self):
        """Der Server antwortet nicht; urlopen läuft hier nach 0,3 s statt nach 60 s in die Zeitüberschreitung (der Rest ist echt)."""
        self.server.folge[self.herren] = [haengt, plan]
        echt = urllib.request.urlopen
        urlopen = lambda req, timeout=None: echt(req, timeout=0.3)      # noqa: E731
        beginn = time.perf_counter()
        h = self.hole(urlopen=urlopen)
        self.assertLess(time.perf_counter() - beginn, 5)
        self.assertEqual((h.fehler, self.server.anfragen_an(self.herren), self.zeit.wartezeiten()), (None, 2, [60]))
        self.assertIn(': timed out', h.wdh.notizen[0], 'der Klassenname der Ausnahme heißt vor Python 3.10 „timeout“, danach „TimeoutError“')

    def test_verbindung_verweigert_wird_viermal_versucht(self):
        port = freier_port()
        h = self.hole(vorlage='http://127.0.0.1:%d/plan/{tid}/{von}/{bis}' % port)
        self.assertEqual(self.zeit.wartezeiten(), [60, 120, 240])
        self.assertTrue(h.fehler.startswith('URLError: <urlopen error [Errno ') and 'onnection refused' in h.fehler, h.fehler)
        self.assertEqual(h.abbruch, ['Herren: ' + h.fehler])

    def test_tls_gegen_einen_server_ohne_tls_ist_endgueltig(self):
        roh = RohServer(b'HTTP/1.1 400 Bad Request\r\n\r\n', bis=b'')
        self.addCleanup(roh.beenden)
        h = self.hole(vorlage='https://127.0.0.1:%d/plan/{tid}/{von}/{bis}' % roh.port)
        self.assertTrue(h.fehler.startswith('URLError: <urlopen error '), h.fehler)
        self.assertEqual((roh.verbindungen, self.zeit.pausen, h.wdh.notizen), (1, [], []))

    def mit_proxy(self, antwort):
        """Ein „Proxy“, der auf CONNECT mit `antwort` antwortet, und ein urlopen, das über ihn zu einem https-Ziel geht (das Ziel wird nie erreicht)."""
        proxy = RohServer(antwort)
        self.addCleanup(proxy.beenden)
        adresse = 'http://127.0.0.1:%d' % proxy.port
        umgebung = mock.patch.dict(os.environ, {'https_proxy': adresse, 'HTTPS_PROXY': adresse})
        umgebung.start()
        self.addCleanup(umgebung.stop)
        os.environ.pop('no_proxy', None)             # no_proxy=* würde den Proxy umgehen; patch.dict stellt die Umgebung am Ende wieder her
        os.environ.pop('NO_PROXY', None)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': adresse}))
        return proxy, (lambda req, timeout=None: opener.open(req, timeout=timeout))

    def test_proxy_meldet_503_oder_502_oder_504_der_abruf_wird_wiederholt(self):
        for status in (b'503 Service Unavailable', b'502 Bad Gateway', b'504 Gateway Timeout'):
            with self.subTest(status):
                shutil.rmtree(self.cache, ignore_errors=True)      # sonst verhindert die Fehlversuch-Marke des vorigen Falls jeden Abruf
                self.zeit.pausen.clear()
                self.zeit.t = 0.0
                proxy, urlopen = self.mit_proxy(b'HTTP/1.1 ' + status + b'\r\n\r\n')
                h = self.hole(vorlage='https://plan.example.invalid/{tid}/{von}/{bis}', urlopen=urlopen)
                self.assertEqual((proxy.verbindungen, self.zeit.wartezeiten()), (4, [60, 120, 240]))
                self.assertEqual(h.fehler, 'URLError: <urlopen error Tunnel connection failed: %s>' % status.decode())

    def test_proxy_meldet_403_die_netzfreigabe_fehlt_ein_versuch(self):
        proxy, urlopen = self.mit_proxy(b'HTTP/1.1 403 Forbidden\r\n\r\n')
        h = self.hole(vorlage='https://plan.example.invalid/{tid}/{von}/{bis}', urlopen=urlopen)
        self.assertEqual((proxy.verbindungen, self.zeit.pausen, h.wdh.notizen), (1, [], []))
        self.assertEqual(h.fehler, 'URLError: <urlopen error Tunnel connection failed: 403 Forbidden>')

    def test_proxy_meldet_429_oder_500_der_abruf_wird_wiederholt_407_und_501_nicht(self):
        for status, versuche in ((b'429 Too Many Requests', 4), (b'500 Internal Server Error', 4), (b'407 Proxy Authentication Required', 1),
                                 (b'501 Not Implemented', 1), (b'403 Forbidden', 1)):
            with self.subTest(status):
                shutil.rmtree(self.cache, ignore_errors=True)
                self.zeit.pausen.clear()
                self.zeit.t = 0.0
                proxy, urlopen = self.mit_proxy(b'HTTP/1.1 ' + status + b'\r\n\r\n')
                h = self.hole(vorlage='https://plan.example.invalid/{tid}/{von}/{bis}', urlopen=urlopen)
                self.assertEqual(proxy.verbindungen, versuche)
                self.assertEqual(self.zeit.wartezeiten(), [60, 120, 240][:versuche - 1])
                self.assertEqual(h.fehler, 'URLError: <urlopen error Tunnel connection failed: %s>' % status.decode())

    def test_tls_handshake_abbruch_wird_wiederholt_egal_ob_ssleoferror_oder_connectionreset(self):
        """Ein Server, der die Verbindung beim TLS-Handshake sofort schließt: je nach FIN oder RST wirft urllib SSLEOFError („EOF occurred in violation of
        protocol“) oder ConnectionResetError. Beides ist jetzt vorübergehend (früher hing das Ergebnis vom Zufall ab): vier Versuche."""
        roh = RohServer(b'', bis=b'')
        self.addCleanup(roh.beenden)
        h = self.hole(vorlage='https://127.0.0.1:%d/plan/{tid}/{von}/{bis}' % roh.port)
        self.assertEqual((roh.verbindungen, self.zeit.wartezeiten()), (4, [60, 120, 240]))
        self.assertTrue(h.fehler, 'nach vier Versuchen endgültig gescheitert')

    def test_namensaufloesung_eai_again_wird_wiederholt_eai_noname_und_eai_fail_nicht(self):
        """Echter urllib-Weg bis socket.create_connection (der Name `localhost` passiert die Netzsperre), nur getaddrinfo wirft: gaierror als Grund einer URLError."""
        for code, versuche in ((socket.EAI_AGAIN, 4), (socket.EAI_NONAME, 1), (socket.EAI_FAIL, 1)):
            with self.subTest(code):
                shutil.rmtree(self.cache, ignore_errors=True)
                self.zeit.pausen.clear()
                self.zeit.t = 0.0
                aufgeloest = []

                def aufloesen(host, *a, **k):
                    aufgeloest.append(host)
                    raise socket.gaierror(code, 'Fehler der Namensauflösung')
                with mock.patch.object(socket, 'getaddrinfo', aufloesen):
                    h = self.hole(vorlage='http://localhost:9/plan/{tid}/{von}/{bis}')
                self.assertEqual((len(aufgeloest), self.zeit.wartezeiten()), (versuche, [60, 120, 240][:versuche - 1]))
                self.assertTrue(h.fehler.startswith('URLError: <urlopen error [Errno '), h.fehler)

    def test_netz_oder_host_nicht_erreichbar_wird_wiederholt_andere_oserror_nicht(self):
        """Echter urllib-Weg, nur der Verbindungsaufbau wirft: OSError mit ENETUNREACH oder EHOSTUNREACH (kein ConnectionError) als Grund einer URLError."""
        for nummer, versuche in ((errno.ENETUNREACH, 4), (errno.EHOSTUNREACH, 4), (errno.EACCES, 1)):
            with self.subTest(errno.errorcode[nummer]):
                shutil.rmtree(self.cache, ignore_errors=True)
                self.zeit.pausen.clear()
                self.zeit.t = 0.0
                versuche_gezaehlt = []

                def verbinden(sock, adresse):
                    versuche_gezaehlt.append(adresse)
                    raise OSError(nummer, 'Fehler beim Verbinden')
                with mock.patch.object(socket.socket, 'connect', verbinden):
                    h = self.hole(vorlage='http://127.0.0.1:9/plan/{tid}/{von}/{bis}')
                self.assertEqual((len(versuche_gezaehlt), self.zeit.wartezeiten()), (versuche, [60, 120, 240][:versuche - 1]))
                self.assertTrue(h.fehler.startswith('URLError: <urlopen error [Errno %d]' % nummer), h.fehler)

    def test_stockende_fehlerseite_haelt_den_abruf_hoechstens_diagnose_sekunden_auf(self):
        """Der Server schickt die Kopfzeilen eines HTTP 503 und dann nichts mehr: das Lesen der Fehlerseite endet nach DIAGNOSE_SEKUNDEN."""
        self.server.folge[self.herren] = [stockt, plan]
        beginn = time.perf_counter()
        with mock.patch.object(A, 'DIAGNOSE_SEKUNDEN', 0.3):
            h = self.hole()
        self.assertLess(time.perf_counter() - beginn, 5)
        self.assertEqual((h.fehler, self.server.anfragen_an(self.herren)), (None, 2))
        self.assertIn('HTTP 503 [Server: Testserver/1.0], Pause 60 s', h.wdh.notizen[0])

    def test_die_standardwartezeit_fuer_die_diagnose_ist_kurz(self):
        self.assertEqual(A.DIAGNOSE_SEKUNDEN, 10)


# ---------------------------------------------------------------- Ohne Wiederholung bleibt alles, wie es war

def mini_welt():
    """Eine kleine feste Welt (unabhängig von den Daten der anderen Testdateien): elf Mannschaften mit je einem Heimspiel, CSV und Plan stimmen
    überein; die D4 hat einen gültigen Plan ohne Spiele."""
    welt = T.Welt()
    for t in T.TEAMS:
        spiel = T.Spiel(t, T.LIGA[t], 10, '2026-11-01', '11:00', T.EIGEN[t], 'FC Muster 1', ort=T.HEIMORT[t])
        welt.csv[t], welt.seite[t] = [spiel], [spiel]
    return welt


# Ausgabe eines störungsfreien Laufs mit mini_welt() (feste Uhr 26.10.2026 07:10, Zeitraffer, Pfade als <tmp>), erzeugt mit dem Stand
# vor der Änderung (abgleich.py im Commit f1f35c8, ohne Wiederholung): so sehen stdout und Bericht ohne Wiederholung aus.
GOLDEN_STDOUT = '\n'.join([
    'Spielplan-Abgleich 26.10.2026 – 12 Abrufe, 0 aus dem Cache, 0 fehlgeschlagen',
    '',
    '| Mannschaft | fussball.de | CSV | fehlt in CSV | nur in CSV | abweichend | Staffeln | Vergangenheit (Info) |',
    '|---|---:|---:|---:|---:|---:|---|---:|',
    '| Herren | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| A-Jugend | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D3 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E3 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| F1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| F2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| G1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| **Summe** | 11 | 11 | **0** | **0** | **0** | | 0 |',
    '',
    'Kommend (Spiele ab heute): **keine Unterschiede**. In der Vergangenheit (nur zur Kenntnis): 0.',
    '',
    '',
    'Bericht: <tmp>/berichte/bericht-2026-10-26.md',
    '',
])

GOLDEN_BERICHT = '\n'.join([
    '# Spielplan-Abgleich · spiele.csv gegen fussball.de · 26.10.2026',
    '',
    'Rein lesender Bericht der Unterschiede. Nichts wurde in `spiele.csv` geschrieben, committet oder gepusht.',
    '',
    '- **Quelle CSV:** `<tmp>/spiele.csv` (11 Zeilen, Saisonfenster 01.07.2026 bis 30.06.2027); kein Git-Stand lesbar',
    '- **Quelle fussball.de:** öffentlicher Mannschafts-Spielplan je Mannschaft (`ajax.team.matchplan`), ein Abruf je Plan und Woche. Zusatzquellen: D4 (Freundschaftsspiele der D3), in der CSV bei D3. Dieser Lauf: 12 Abrufe, 0 aus dem Cache, 0 fehlgeschlagen.',
    '- **Schlüssel des Vergleichs:** Staffelkennung + Spielnummer; verglichen werden Datum, Uhrzeit, Heim/Auswärts, Gegner, Wettbewerbsart und Absagen. „Kommend“ heißt: Spieldatum heute (26.10.2026) oder später.',
    '',
    '## Kurzfassung',
    '',
    '| Mannschaft | fussball.de | CSV | fehlt in CSV | nur in CSV | abweichend | Staffeln | Vergangenheit (Info) |',
    '|---|---:|---:|---:|---:|---:|---|---:|',
    '| Herren | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| A-Jugend | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| D3 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| E3 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| F1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| F2 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| G1 | 1 | 1 | 0 | 0 | 0 | ok | 0 |',
    '| **Summe** | 11 | 11 | **0** | **0** | **0** | | 0 |',
    '',
    'Kommend (Spiele ab heute): **keine Unterschiede**. In der Vergangenheit (nur zur Kenntnis): 0.',
    '',
    '',
    '## Je Mannschaft',
    '',
    '### Herren – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### A-Jugend – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### D1 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### D2 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### D3 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    'Zusammengeführt aus 2 fussball.de-Plänen: D3 und D4 (Freundschaftsspiele der D3), ohne Spiele.',
    '',
    '### E1 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### E2 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### E3 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### F1 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### F2 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '### G1 – fussball.de 1 Spiele · CSV 1 Zeilen · **keine Unterschiede**',
    '',
    '## Abrufe dieses Laufs',
    '',
    '- Herren: Abruf 26.10.2026 07:10',
    '- A-Jugend: Abruf 26.10.2026 07:10',
    '- D1: Abruf 26.10.2026 07:10',
    '- D2: Abruf 26.10.2026 07:10',
    '- D3: Abruf 26.10.2026 07:10',
    '- E1: Abruf 26.10.2026 07:10',
    '- E2: Abruf 26.10.2026 07:10',
    '- E3: Abruf 26.10.2026 07:10',
    '- F1: Abruf 26.10.2026 07:10',
    '- F2: Abruf 26.10.2026 07:10',
    '- G1: Abruf 26.10.2026 07:10',
    '- D4 (Freundschaftsspiele der D3): Abruf 26.10.2026 07:10',
    '',
    '## Kontrolle',
    '',
    '- `spiele.csv` vor und nach dem Lauf identisch: ja (SHA-256 535fd1063627a6a3…).',
    '',
])


class TestUnveraendert(Basis):
    """Ohne Wiederholung (kein Fehler, oder ein endgültiger, oder --zeitrahmen 0) sind stdout, stderr und Bericht byte-gleich zum Stand vor der
    Änderung."""

    def lauf(self, verz, folge=None, *extra, welt=None):
        os.makedirs(verz, exist_ok=True)
        return lauf_modul(A, verz, welt or mini_welt(), folge or {}, extra, zeit=Zeitraffer(), csv=os.path.join(self.dir, 'spiele.csv'),
                          cache=os.path.join(verz, 'cache'), berichte=os.path.join(self.dir, 'berichte'))

    def normal(self, text):
        return text.replace(self.dir, '<tmp>')

    def test_stoerungsfreier_lauf_gleicht_dem_ausgabestand_vor_der_aenderung(self):
        """GOLDEN_STDOUT und GOLDEN_BERICHT stammen aus einem Lauf des Standes vor der Änderung (abgleich.py, Commit f1f35c8) mit denselben Eingaben."""
        r = self.lauf(os.path.join(self.dir, 'a'))
        self.assertEqual(r.code, 0)
        self.assertEqual(r.err, '')
        self.assertEqual(self.normal(r.out), GOLDEN_STDOUT)
        self.assertEqual(self.normal(r.bericht), GOLDEN_BERICHT)

    def test_zeitrahmen_0_und_standard_liefern_ohne_stoerung_dieselbe_ausgabe(self):
        a = self.lauf(os.path.join(self.dir, 'a'))
        b = self.lauf(os.path.join(self.dir, 'b'), None, '--zeitrahmen', '0')
        self.assertEqual((a.code, self.normal(a.out), a.err, self.normal(a.bericht)), (b.code, self.normal(b.out), b.err, self.normal(b.bericht)))

    def test_endgueltiger_fehlschlag_gibt_mit_und_ohne_zeitrahmen_dieselbe_ausgabe(self):
        folge = {A.TEAMS[2][3]: [fehler(403, {'Server': 'x'})]}
        a = self.lauf(os.path.join(self.dir, 'a'), folge)
        b = self.lauf(os.path.join(self.dir, 'b'), folge, '--zeitrahmen', '0')
        self.assertEqual(a.code, 2)
        self.assertEqual((a.code, self.normal(a.out), a.err, self.normal(a.bericht)), (b.code, self.normal(b.out), b.err, self.normal(b.bericht)))
        self.assertEqual(a.err, '  D1: Abruf fehlgeschlagen (HTTP 403); weitere Abrufe in diesem Lauf entfallen\n')
        self.assertNotIn('wiederholt', a.out + a.bericht)
        self.assertNotIn(': Wiederholung', a.bericht)

    def test_stoerung_mit_zeitrahmen_0_gibt_die_ausgabe_des_alten_standes(self):
        folge = {A.TEAMS[0][3]: [fehler(503, {'Retry-After': '5'}), plan]}
        a = self.lauf(os.path.join(self.dir, 'a'), folge, '--zeitrahmen', '0')
        self.assertEqual(a.code, 2)
        self.assertEqual(a.err, '  Herren: Abruf fehlgeschlagen (HTTP 503); weitere Abrufe in diesem Lauf entfallen\n')
        self.assertEqual(a.out.splitlines()[0], 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 0 aus dem Cache, 11 fehlgeschlagen')
        self.assertEqual(a.abrufe[0], '- Herren: FEHLER (HTTP 503)')
        self.assertEqual(len(a.abrufe), 11)

    def test_cache_lauf_gleicht_dem_ersten_lauf_ausser_der_herkunft(self):
        a = self.lauf(os.path.join(self.dir, 'a'))
        cache = os.path.join(self.dir, 'a', 'cache')
        b = lauf_modul(A, os.path.join(self.dir, 'a'), mini_welt(), {}, zeit=Zeitraffer(), csv=os.path.join(self.dir, 'spiele.csv'), cache=cache,
                       berichte=os.path.join(self.dir, 'berichte'))
        self.assertEqual((b.code, b.zeit.pausen, b.anfragen), (0, [], []))
        self.assertEqual(b.out.splitlines()[0], 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 12 aus dem Cache, 0 fehlgeschlagen')
        self.assertNotIn('wiederholt', b.out + b.bericht)


# ---------------------------------------------------------------- Laufzeit-Obergrenze (simulierte Zeit)

class TestLaufzeit(Basis):
    """Bei den Standardwerten (Zeitrahmen 450 s) endet die Abrufphase auch im ungünstigsten Fehlerfall nach höchstens 540 s (simulierte Zeit), solange jeder
    Verbindungsaufbau nach 60 s aufgibt (Zeitlimit von urlopen). Löst der Servername zu mehreren Adressen auf, die alle nicht antworten, kann ein Versuch je
    weiterer Adresse bis zu 60 s länger dauern (urllib probiert jede Adresse mit eigenem Zeitlimit): das belegt test_mehrere_nicht_antwortende_adressen…."""

    OBERGRENZE = 540      # Sekunden: nur bei den Standardwerten und höchstens 60 s je Verbindungsaufbau (siehe Docstring)

    def lauf(self, urlopen, *extra):
        return lauf_modul(A, self.dir, T.Welt(), {}, extra, urlopen=urlopen, zeit=self.zeit)

    def hole_echt(self, cache):
        """hole() mit dem echten urlopen (der Verbindungsaufbau wird vom Test ersetzt) und dem Wiederholungsobjekt des Zeitraffers -> (Text, Quelle, Stempel, Fehler)."""
        with contextlib.redirect_stderr(io.StringIO()):
            return A.hole(cache, A.TEAMS[0], 'v', 'b', False, JETZT, [], 'http://127.0.0.1:9/{tid}/{von}/{bis}', wiederholung=self.wdh())

    def test_jeder_versuch_scheitert_sofort_mit_503(self):
        urlopen = Urlopen(lambda: http_fehler(503))
        r = self.lauf(urlopen)
        self.assertEqual((r.code, len(urlopen.aufrufe)), (2, 4))
        self.assertEqual(self.zeit.t, 420.0)
        self.assertLessEqual(self.zeit.t, self.OBERGRENZE)

    def test_jeder_versuch_laeuft_in_das_zeitlimit_von_urlopen(self):
        urlopen = Urlopen(Dauer(60, TimeoutError('timed out')), zeit=self.zeit)
        r = self.lauf(urlopen)
        self.assertEqual((r.code, len(urlopen.aufrufe)), (2, 3), 'Versuche beginnen bei 0, 120 und 300 s; ein vierter begänne bei 360 + 240 = 600 > 450 s nicht mehr')
        self.assertEqual(self.zeit.t, 360.0)
        self.assertLessEqual(self.zeit.t, self.OBERGRENZE)
        self.assertEqual(urlopen.timeouts, [60, 60, 60], 'das Zeitlimit von urlopen bleibt bei 60 s')

    def test_bei_den_standardwerten_hoechstens_540_s_auch_wenn_nur_der_letzte_versuch_haengt(self):
        """Die ungünstigste Folge bei einer Adresse: drei schnelle Fehlschläge (Versuche bei 0, 60 und 180 s), der vierte beginnt bei 420 s und hängt 60 s."""
        urlopen = Urlopen(lambda: http_fehler(503), lambda: http_fehler(503), lambda: http_fehler(503), Dauer(60, TimeoutError('timed out')), zeit=self.zeit)
        self.lauf(urlopen)
        self.assertEqual((len(urlopen.aufrufe), self.zeit.t), (4, 480.0))
        self.assertLessEqual(self.zeit.t, self.OBERGRENZE)

    def test_der_letzte_plan_haengt_nach_elf_gelungenen_abrufen(self):
        """Die Reihenfolge der Abrufe ist die der Pläne: elf Antworten, danach die D4 mit Zeitüberschreitungen (sie beginnt bei 33 s)."""
        welt = T.Welt()
        folge = [Seite(T.seite_aus(welt.seite[t[0]])) for t in A.TEAMS] + [Dauer(60, TimeoutError('timed out'))]
        attrappe = Urlopen(*folge, zeit=self.zeit)
        r = lauf_modul(A, self.dir, welt, {}, urlopen=attrappe, zeit=self.zeit)
        self.assertEqual(r.code, 2)
        self.assertEqual(len(attrappe.aufrufe), 11 + 3, 'Versuche bei 33, 153 und 333 s (je 60 s lang); ein vierter begänne bei 393 + 240 > 450 s nicht mehr')
        self.assertEqual(self.zeit.t, 393.0)
        self.assertLessEqual(self.zeit.t, self.OBERGRENZE)

    def test_der_zeitrahmen_haelt_auch_viele_flatterhafte_plaene_in_grenzen(self):
        """Jeder Plan scheitert zuerst einmal mit 503: die Wiederholungen verbrauchen Zeitrahmen, bis er aufgebraucht ist (bei den Standardwerten höchstens sieben
        Pausen)."""
        welt = T.Welt()
        eintraege = []
        for t in A.TEAMS:
            eintraege += [lambda: http_fehler(503), Seite(T.seite_aus(welt.seite[t[0]]))]
        urlopen = Urlopen(*eintraege, zeit=self.zeit)
        r = lauf_modul(A, self.dir, welt, {}, urlopen=urlopen, zeit=self.zeit)
        self.assertEqual(self.zeit.wartezeiten(), [60] * 7,
                         'sieben Pausen von 60 s (die Obergrenze: 7 * 60 = 420 <= 450 < 8 * 60); die achte passt nicht mehr hinein')
        self.assertLessEqual(self.zeit.t, self.OBERGRENZE)
        self.assertEqual(r.code, 2)

    def test_mehrere_nicht_antwortende_adressen_verlaengern_einen_versuch_um_60_s_je_adresse(self):
        """Belegt die Einschränkung der Zusage: urllib probiert jede Adresse des Servernamens mit eigenem Zeitlimit (hier: getaddrinfo liefert n Adressen, jeder
        Verbindungsaufbau verbraucht 60 s und scheitert mit einer Zeitüberschreitung; sonst echter urllib-Weg). Ein Versuch dauert dann 60 * n Sekunden, der
        letzte beginnt weiter spätestens bei 450 s: das Ende liegt bei höchstens 450 + 60 * n Sekunden und überschreitet die 540 s je nach Zahl der Adressen
        (im Beispiel ab fünf)."""
        erwartet = {1: ([0, 120, 300], 360), 2: ([0, 180, 420], 540), 3: ([0, 240], 420), 5: ([0, 360], 660)}      # (Beginn der Versuche, Ende)
        for n, (beginne, ende) in erwartet.items():
            with self.subTest(adressen=n):
                self.zeit.t = 0.0
                self.zeit.pausen.clear()
                verbindungen = []

                def aufloesen(*a, **k):
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 9))] * n

                def verbinden(sock, adresse):
                    verbindungen.append(self.zeit.t)
                    self.zeit.vergehen(sock.gettimeout())
                    raise socket.timeout('timed out')
                with mock.patch.object(socket, 'getaddrinfo', aufloesen), mock.patch.object(socket.socket, 'connect', verbinden):
                    ergebnis = self.hole_echt(os.path.join(self.dir, 'cache%d' % n))
                self.assertTrue(ergebnis[3], 'alle Versuche scheitern')
                self.assertEqual(len(verbindungen), len(beginne) * n, 'jeder Versuch probiert alle %d Adressen' % n)
                self.assertEqual(verbindungen[::n], beginne)
                self.assertEqual([b - a_ for a_, b in zip(verbindungen[:n], verbindungen[1:n])], [60.0] * (n - 1), 'je Adresse ein eigenes Zeitlimit von 60 s')
                self.assertEqual(self.zeit.t, ende)
                self.assertLessEqual(ende, 450 + 60 * n)
                self.assertEqual(ende > self.OBERGRENZE, n >= 5, 'im Beispiel reißt erst die Abrufphase mit fünf Adressen die 540 s')


# ---------------------------------------------------------------- Weg durch main(): Objekt, Schlüsselwort, Optionen

class TestAbrufpfad(Basis):
    def test_plan_lesen_reicht_die_wiederholung_nur_als_schluesselwort_und_nur_wenn_sie_da_ist(self):
        aufrufe = []

        def hole(*a, **k):
            aufrufe.append((a, k))
            return None, '', None, 'HTTP 503'
        w = self.wdh()
        with mock.patch.object(A, 'hole', hole), contextlib.redirect_stderr(io.StringIO()):
            A.plan_lesen('c', A.TEAMS[0], 'v', 'b', False, JETZT, [], 'u')
            A.plan_lesen('c', A.TEAMS[0], 'v', 'b', False, JETZT, [], 'u', wiederholung=w)
        self.assertEqual([k for a, k in aufrufe], [{}, {'wiederholung': w}])
        self.assertEqual([len(a) for a, k in aufrufe], [8, 8], 'wie bisher acht Positionsargumente')

    def test_main_legt_das_objekt_nach_der_csv_und_vor_dem_ersten_abruf_an_und_reicht_dasselbe_an_alle_zwoelf_plaene(self):
        ereignisse = []
        echt_csv, echt_plan, echt_klasse = A.csv_lesen, A.plan_lesen, A.Wiederholung

        def csv_lesen(pfad):
            ereignisse.append('csv')
            return echt_csv(pfad)

        def plan_lesen(*a, **k):
            ereignisse.append(('plan', k.get('wiederholung')))
            return echt_plan(*a, **k)

        class Spion(echt_klasse):
            def __init__(self, *a, **k):
                ereignisse.append(('objekt', a))
                super().__init__(*a, **k)
        with contextlib.ExitStack() as st:
            for name, ersatz in (('csv_lesen', csv_lesen), ('plan_lesen', plan_lesen), ('Wiederholung', Spion)):
                st.enter_context(mock.patch.object(A, name, ersatz))
            r = lauf_modul(A, self.dir, T.Welt(), {}, zeit=self.zeit)
        self.assertEqual(r.code, 0)
        self.assertEqual(ereignisse[:2], ['csv', ('objekt', (450,))])
        plaene = ereignisse[2:]
        self.assertEqual(len(plaene), 12)
        self.assertTrue(all(e[0] == 'plan' and isinstance(e[1], Spion) for e in plaene))
        self.assertEqual(len({id(e[1]) for e in plaene}), 1, 'ein Objekt je Lauf')

    def test_die_wache_von_nachtrag_aktion_wuerde_das_schluesselwort_durchreichen(self):
        """hole_mit_wache(cache, team, von, bis, nur_cache, jetzt, abbruch, *weitere, **benannte): das Schlüsselwort geht in **benannte."""
        gesehen = []
        echt = A.hole

        def mit_wache(cache, team, von, bis, nur_cache, jetzt, abbruch, *weitere, **benannte):
            gesehen.append(benannte)
            return echt(cache, team, von, bis, nur_cache, jetzt, abbruch, *weitere, **benannte)
        with mock.patch.object(A, 'hole', mit_wache):
            r = lauf_modul(A, self.dir, T.Welt(), {}, zeit=self.zeit)
        self.assertEqual(r.code, 0)
        self.assertEqual(len(gesehen), 12)
        self.assertTrue(all(list(k) == ['wiederholung'] for k in gesehen))

    def argv_lauf(self, *args):
        argv = ['abgleich.py', '--csv', os.path.join(self.dir, 'spiele.csv'), '--cache', os.path.join(self.dir, 'cache'),
                '--bericht-ordner', os.path.join(self.dir, 'berichte')] + list(args)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                A.main()
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()
        return None, out.getvalue(), err.getvalue()

    def test_ungueltige_werte_fuer_zeitrahmen_beenden_mit_fehler_vor_dem_ersten_abruf(self):
        for wert in ('-1', 'abc', '1.5', '', '1e3', '--heute'):
            with self.subTest(wert):
                code, out, err = self.argv_lauf('--zeitrahmen', wert)
                self.assertEqual(code, 2)
                self.assertIn('--zeitrahmen', err)
                self.assertEqual(out, '')

    def hilfetext(self):
        """Der Hilfetext von --help in einer Zeile. COLUMNS ist sehr breit gesetzt: argparse bricht sonst je nach Terminalbreite auch an Bindestrichen
        um („TLS- Handshake“), und der Test hinge von der Umgebung ab."""
        with mock.patch.dict(os.environ, {'COLUMNS': '100000'}):
            code, out, err = self.argv_lauf('--help')
        self.assertEqual(code, 0)
        self.assertNotIn('--url-vorlage', out, 'versteckt, nur für Tests')
        return ' '.join(out.split())

    def test_hilfetext_nennt_zeitrahmen_pausen_und_null(self):
        flach = self.hilfetext()
        for text in ('--zeitrahmen SEKUNDEN', 'Zeitrahmen der Wiederholungen in Sekunden ab Beginn der Abrufe (Standard: 450)', '60, 120 und 240 s Pause',
                     '0 = keine Wiederholung, jeder Abruf nur einmal', 'Die Pausen lassen sich nicht verkürzen'):
            self.assertIn(text, flach)

    def test_hilfetext_nennt_jede_art_der_voruebergehenden_stoerung(self):
        """Die Liste im Hilfetext von --zeitrahmen stimmt mit voruebergehend() überein: jede Art, die wiederholt wird, steht darin (auch die schon
        in Runde 1 wiederholten Fälle „verweigerte Verbindung“ und „unvollständige Antwort“)."""
        flach = self.hilfetext()
        for text in ('HTTP 408, 429, 500, 502, 503, 504, 520 bis 524', 'Zeitüberschreitung', 'abgebrochene oder verweigerte Verbindung', 'unvollständige Antwort',
                     'Netz oder Host nicht erreichbar', 'vorübergehender Fehler der Namensauflösung', 'Abbruch beim TLS-Handshake',
                     'Proxy-Meldung „Tunnel connection failed“ mit 429, 500, 502, 503 oder 504'):
            self.assertIn(text, flach)

    def test_es_gibt_keine_option_die_die_pausen_verkuerzt(self):
        optionen = set(re.findall(r'--[a-z-]+', inspect.getsource(A.main)))
        self.assertEqual(optionen, {'--csv', '--bericht-ordner', '--cache', '--nur-cache', '--heute', '--saison', '--kein-wochenvergleich',
                                    '--url-vorlage', '--zeitrahmen'})

    def test_nur_cache_ruft_nie_ab_auch_mit_zeitrahmen(self):
        self.lauf_vorab()
        urlopen = Urlopen(lambda: http_fehler(503))
        r = lauf_modul(A, self.dir, T.Welt(), {}, ('--nur-cache', '--zeitrahmen', '600'), urlopen=urlopen, zeit=Zeitraffer())
        self.assertEqual((r.code, urlopen.aufrufe, r.zeit.pausen), (0, [], []))
        self.assertEqual(r.erste_zeile, 'Spielplan-Abgleich 26.10.2026 – 0 Abrufe, 12 aus dem Cache, 0 fehlgeschlagen')

    def lauf_vorab(self):
        lauf_modul(A, self.dir, T.Welt(), {}, zeit=Zeitraffer())


# ---------------------------------------------------------------- Doku

class TestDoku(unittest.TestCase):
    """Die Beschreibung der Wiederholung in der README nennt dieselben Zahlen und Regeln wie der Code (ohne README im Repo-Stamm entfällt der Test)."""

    def test_readme_beschreibt_pausen_zeitrahmen_option_grenzen_und_tests(self):
        pfad = os.path.join(os.path.dirname(HIER), 'README.md')
        if not os.path.exists(pfad):
            self.skipTest('keine README im Repo-Stamm')
        with open(pfad, encoding='utf-8') as f:
            flach = ' '.join(f.read().split())
        pausen = ', '.join(str(p) for p in A.WIEDERHOLUNG_PAUSEN[:-1]) + ' und %d s' % A.WIEDERHOLUNG_PAUSEN[-1]
        for text in ('Abrufregeln des Wochen-Abgleichs: Wiederholung bei vorübergehenden Störungen',
                     'HTTP 408, 429, 500, 502, 503 oder 504, die Cloudflare-Fehlerseiten 520 bis 524', '„Verbindung abgelehnt“, `ConnectionRefusedError`',
                     '`IncompleteRead`', 'Netz oder Host nicht erreichbar', 'vorübergehender Fehler der Namensauflösung', 'Verbindungsabbruch beim TLS-Handshake',
                     'Tunnel connection failed: 429/500/502/503/504', 'Tunnel connection failed: 403“ oder „407“',
                     '**%s** Pause' % pausen, '**%d s nach Beginn der Abrufe**' % A.ZEITRAHMEN_STANDARD, '`--zeitrahmen SEKUNDEN`',
                     '**Bei den Standardwerten** endet die Abrufphase auch im ungünstigsten Fehlerfall nach höchstens 540 s, solange jeder Verbindungsaufbau nach '
                     '60 s aufgibt', 'kann ein Versuch je weiterer Adresse bis zu 60 s länger dauern (urllib probiert jede Adresse mit eigenem Zeitlimit)',
                     'Ebenfalls **bei den Standardwerten** stellt ein Lauf höchstens sieben zusätzliche Anfragen',
                     'Retry-After', 'n wiederholt', 'ruft `abgleich.hole` weiter ohne Wiederholungsobjekt auf',
                     "python3 -I -B -m unittest discover -s tools -p 'test_abgleich.py'",
                     'derselbe Abruf wird nach 60, 120 und 240 s Pause wiederholt, höchstens bis 450 s nach Beginn der Abrufe',
                     'kommen bei den Standardwerten höchstens sieben Wiederholungen dazu',
                     'Abbruch beim TLS-Handshake, Proxy-Meldung „Tunnel connection failed“ mit 429, 500, 502, 503 oder 504) überbrückt der Abgleich selbst',
                     'Bei den Standardwerten dauert sie mit Wiederholungen höchstens neun Minuten (540 s), solange jeder Verbindungsaufbau nach 60 s aufgibt'):
            self.assertIn(text, flach)
        self.assertNotIn('ein Abruf je Plan und Woche, bei jedem Fehlschlag', flach)


# ---------------------------------------------------------------- Quelltext

class TestQuelltext(unittest.TestCase):
    def setUp(self):
        with open(A.__file__, encoding='utf-8') as f:
            self.quelle = f.read()

    def test_syntax_hoechstens_python_3_9(self):
        """Cloud-Sandbox und GitHub-Runner haben nicht dieselbe Python-Version wie der Mac: keine Syntax, die erst später dazukam."""
        for pfad in (A.__file__, __file__):
            with open(pfad, encoding='utf-8') as f:
                ast.parse(f.read(), filename=pfad, feature_version=(3, 9))

    def test_keine_laufzeit_annotationen_mit_vertikalstrich_und_keine_bibliotheksaufrufe_ab_3_10(self):
        baum = ast.parse(self.quelle)
        for knoten in ast.walk(baum):
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotationen = [a.annotation for a in knoten.args.args + knoten.args.kwonlyargs if a.annotation] + ([knoten.returns] if knoten.returns else [])
                for a in annotationen:
                    self.assertFalse(isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr), 'X | Y in einer Annotation: ' + knoten.name)
        for verboten in ('zip(strict', 'datetime.UTC', 'itertools.pairwise', 'match ', 'removeprefix(', 'TaskGroup', 'ExceptionGroup'):
            self.assertNotIn(verboten, self.quelle.replace('Bericht match', ''), verboten)

    def test_die_pause_zwischen_den_abrufen_steht_wortlich_im_quelltext(self):
        self.assertIn('\nPAUSE_SEKUNDEN = 3\n', self.quelle)
        self.assertEqual(A.PAUSE_SEKUNDEN, 3)

    def test_konstanten(self):
        self.assertEqual(A.WIEDERHOLUNG_PAUSEN, (60, 120, 240))
        self.assertEqual(A.ZEITRAHMEN_STANDARD, 450)
        self.assertEqual(A.VORUEBERGEHEND_HTTP, (408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524))
        self.assertEqual((A.MIN_ABSTAND_TAGE, A.MIN_SEITENLAENGE), (6, 500))

    def test_nur_standardbibliothek(self):
        stdlib = getattr(sys, 'stdlib_module_names', None)
        if stdlib is None:
            self.skipTest('sys.stdlib_module_names gibt es erst ab Python 3.10')
        module = set()
        for knoten in ast.walk(ast.parse(self.quelle)):
            if isinstance(knoten, ast.Import):
                module |= {a.name.split('.')[0] for a in knoten.names}
            elif isinstance(knoten, ast.ImportFrom):
                module.add(knoten.module.split('.')[0])
        self.assertEqual(module - set(stdlib), set())

    def test_docstring_beschreibt_die_wiederholung_die_option_und_die_grenzen(self):
        flach = ' '.join(A.__doc__.split())
        for text in ('[--zeitrahmen SEKUNDEN]', 'HTTP 408, 429, 500, 502, 503, 504 oder 520 bis 524', 'ConnectionRefusedError, „Verbindung abgelehnt“', 'IncompleteRead',
                     'ENETUNREACH, EHOSTUNREACH', 'EAI_AGAIN', 'SSLEOFError', 'Tunnel connection failed: 429/500/502/503/504',
                     '60, 120 und 240 s Pause', 'höchstens vier Versuche je Plan', 'Retry-After', '450 s nach Beginn der Abrufe',
                     'Bei den Standardwerten (--zeitrahmen 450) endet die Abrufphase auch im ungünstigsten Fehlerfall nach höchstens 540 s, solange jeder '
                     'Verbindungsaufbau nach 60 s aufgibt (Zeitlimit von urlopen)',
                     'löst der Servername zu mehreren Adressen auf, die alle nicht antworten, kann ein Versuch je weiterer Adresse bis zu 60 s länger dauern '
                     '(urllib probiert jede Adresse mit eigenem Zeitlimit)',
                     'Ebenfalls bei den Standardwerten gibt es höchstens sieben zusätzliche Anfragen je Lauf',
                     'andere HTTP-Fehler (403, 404, 501, 505 …)', 'andere Fehler der Namensauflösung', 'Tunnel connection failed: 403“ oder „407“',
                     'nach einem endgültigen Fehlschlag keine weiteren Abrufe', 'nachtrag.py', 'genau einen Versuch', 'Zusatz „n wiederholt“', '0 = keine Wiederholung'):
            self.assertIn(text, flach)

    def test_jede_zusage_von_540_s_und_sieben_zusaetzlichen_anfragen_nennt_ihre_bedingung_im_selben_satz(self):
        """Die Obergrenze von 540 s gilt nur bei den Standardwerten und wenn jeder Verbindungsaufbau nach 60 s aufgibt; die höchstens sieben zusätzlichen
        Anfragen gelten nur bei den Standardwerten. Geprüft in jedem Text, der die Zahlen nennt: Docstring von abgleich.py, README, nachtrag_aktion.py."""
        aktion = os.path.join(HIER, 'nachtrag_aktion.py')
        readme = os.path.join(os.path.dirname(HIER), 'README.md')
        quellen = [('abgleich.py', A.__doc__)]
        for name, pfad in (('nachtrag_aktion.py', aktion), ('README.md', readme)):
            if os.path.exists(pfad):
                with open(pfad, encoding='utf-8') as f:
                    quellen.append((name, f.read()))
        gefunden = 0
        for name, text in quellen:
            flach = ' '.join(text.split())
            for muster, bedingungen in ((r'höchstens 540 s|\(540 s\)', ('Standardwerten', 'Verbindungsaufbau nach 60 s aufgibt')),
                                        (r'höchstens sieben (?:zusätzliche Anfragen|Wiederholungen)', ('Standardwerten',))):
                for m in re.finditer(muster, flach):
                    gefunden += 1
                    anfang = flach.rfind('. ', 0, m.start()) + 2
                    ende = flach.find('. ', m.end())
                    satz = flach[anfang:ende if ende >= 0 else len(flach)]
                    for bedingung in bedingungen:
                        self.assertIn(bedingung, satz, '%s: „%s“ ohne Bedingung im selben Satz: %s' % (name, m.group(0), satz[:160]))
        self.assertGreaterEqual(gefunden, 6, 'die Zahlen stehen in Docstring, Aktions-Docstring und README')

    def test_diagnose_sekunden_gelten_je_datenpaket_und_nicht_fuer_die_gesamtdauer_dokumentiert(self):
        self.assertIn('begrenzt das Warten auf jedes einzelne Datenpaket', self.quelle)
        self.assertIn('nicht die Gesamtdauer', self.quelle)
        flach = ' '.join(A.fehlerseite_titel.__doc__.split())
        self.assertIn('DIAGNOSE_SEKUNDEN begrenzt das Warten auf jedes einzelne Datenpaket, nicht die Gesamtdauer', flach)
        self.assertIn('Byte für Byte tropft', flach)
        self.assertNotIn('wartet dabei höchstens DIAGNOSE_SEKUNDEN auf Daten', flach)


if __name__ == '__main__':
    unittest.main()
