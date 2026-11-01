#!/usr/bin/env python3
"""Cloud-Sicherung des Spielplan-Nachtrags: der Rahmen für die GitHub-Aktion (Vorlage tools/nachtrag-aktion.yml, nach dem Verschieben
.github/workflows/nachtrag.yml).

tools/nachtrag.py schreibt spiele.csv aus den öffentlichen fussball.de-Mannschaftsplänen fort, bisher nur im täglichen Mac-Task des
Vereins. Fällt der Mac aus, bleibt die Tabelle stehen. Dieses Skript lässt dasselbe in der Cloud laufen, als Sicherung neben dem Mac,
nicht als Ersatz. Es ändert weder nachtrag.py noch abgleich.py und ruft beide im selben Prozess auf:
  1. tools/abgleich.py holt die zwölf Pläne (elf Mannschaften und die Zusatzquelle D4) in einen frischen Cache außerhalb des Repos.
     Zu Beginn werden dort die Unterordner der zwölf Pläne gelöscht (nur diese): die Aktion ist zustandslos, jeder Lauf ruft selbst ab und
     findet nichts Altes.
  2. tools/nachtrag.py schreibt mit --anwenden --push fort und pusht spiele.csv; mit --nur-bericht gibt es nur den Bericht (kein
     --anwenden, kein Commit, kein Push). Der Nachtrag liest dabei nur den Cache (--nur-cache): sein eigener Abruf der G-Jugend bleibt
     aus, damit ein Lauf nie mehr als einen Abruf-Durchgang macht und nach einem Fehlschlag oder einer unbrauchbaren Antwort nichts
     wiederholt.
  3. Die Schlusszeile des Nachtrags wird bewertet (bewerten) und der Aktion über GITHUB_OUTPUT gemeldet: geaendert=true|false und
     status=<Schlusszeile>. Nur bei geaendert=true stößt die Aktion danach build.yml an, auch wenn der Lauf rot endet. Kalender baut dieses
     Skript nie: build_ics.py setzt SEQUENCE aus github.run_number von build.yml, ein eigener Lauf-Zähler würde sie senken.

Rücksicht auf fussball.de: nur die öffentlichen Mannschaftspläne (ajax.team.matchplan), ein Durchgang je Lauf (höchstens zwölf Abrufe),
erkennbarer User-Agent (SpeuzerSpielplanNachtrag, GitHub Actions), 3 s Pause zwischen den Abrufen (abgleich.PAUSE_SEKUNDEN, hier
unverändert) und nach einem Fehlschlag oder einer unbrauchbaren Antwort (HTTP 200 mit Sperr- oder Einwilligungsseite, geänderter Seitenaufbau)
keine weiteren Abrufe und keine Wiederholung. Für HTTP- und Netzfehler gilt die Abbruchregel von abgleich.py unverändert; eine unbrauchbare
Antwort zählt abgleich.py nicht als Fehlschlag, deshalb legt der Wrapper für die Dauer des Aufrufs eine Wache um abgleich.hole: sie trägt den
Grund in die Abbruchliste ein. Ein gültiger Plan ohne Spiele (Rahmen der Seite erkennbar, keine Spielzeilen) löst die Wache nicht aus.

Aufruf (aus dem Repo-Stamm; unter python3 -I trägt sich das eigene Verzeichnis selbst in sys.path ein):
  python3 -I tools/nachtrag_aktion.py [--csv DATEI] [--cache ORDNER] [--bericht-ordner ORDNER] [--zustand-ordner ORDNER] [--nur-bericht]
  --csv                  Standard: spiele.csv im Repo-Stamm
  --cache, --bericht-ordner, --zustand-ordner
                         Standard: die Unterordner cache, berichte und zustand von <$RUNNER_TEMP, sonst der Temp-Ordner>/speuzer-nachtrag-aktion,
                         immer außerhalb des Repos; der Zustandsordner trägt die Sperre lauf.lock und die Not-Aus-Datei AUS des Nachtrags
  --nur-bericht          Probelauf: nichts schreiben, nichts committen, nichts pushen
Zu Beginn werden im Cache nur die Unterordner der zwölf Pläne gelöscht. Der Lauf endet mit Fehler, ohne etwas zu löschen, wenn ein Ordner im Repo
liegt, wenn der Cache das Repo enthält, wenn er der Cache des Mac-Tasks (abgleich.CACHE_STANDARD) oder ein Ordner darüber oder darin ist oder
wenn er irgendetwas anderes enthält (unbekannte Dateien oder Ordner). Ein leerer oder nicht vorhandener Cache ist in Ordnung.

Bewertung der Schlusszeile des Nachtrags (bewerten): maßgeblich ist die Schlusszeile, der Exit-Code des Nachtrags dient als Gegenprobe
(ein Widerspruch ist ein Fehler):
  ok       NACHTRAG: unverändert · n geändert (…), gepusht · Nur-Bericht – … · n liegen gebliebene Commits nachgepusht (Exit-Code 0)
  warnung  NACHTRAG: Auffällig, nicht übernommen – n Hinweise (siehe oben): bloße Hinweise, alltäglich (Exit-Code 2)
  fehler   NACHTRAG: Fehler – … · jede andere Auffälligkeit (Mengenbremse, Not-Aus) · geändert ohne „gepusht“ · fehlende oder unbekannte
           Schlusszeile · Abruf/Abgleich fehlgeschlagen (der Nachtrag läuft dann nicht)
geaendert=true, wenn die Schlusszeile irgendwo „n geändert (…), gepusht“ enthält und sich HEAD gegenüber vorher geändert hat (git rev-parse HEAD
vor und nach dem Nachtrag), unabhängig von der Bewertung: fällt nur ein Plan aus, schreibt und pusht der Nachtrag die übrigen Mannschaften
(„Fehler – …; n geändert (…), gepusht“), der Lauf ist rot, und der Kalenderbau wird trotzdem angestoßen.
Die letzte Ausgabezeile ist immer: NACHTRAG-AKTION: <ok|warnung|fehler> – <Schlusszeile des Nachtrags>; lief der Nachtrag nicht, steht dort
der Grund (z. B. Abruf/Abgleich fehlgeschlagen (Exit 1)). Davor steht bei einer Warnung ::warning::…, bei einem Fehler ::error::… (eine
Zeile, maskiert nach der GitHub-Regel), und die Zusammenfassung des Laufs geht an GITHUB_STEP_SUMMARY.
Exit-Code: 0 = ok oder warnung, 1 = fehler. Umgebungsvariablen und Zugangsdaten gibt dieses Skript nie aus.
"""
import sys

sys.dont_write_bytecode = True  # GANZ OBEN, vor jedem weiteren Import (auch vor abgleich und nachtrag): kein tools/__pycache__, auch ohne -B

import argparse
import contextlib
import os
import re
import shutil
import tempfile
import traceback

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:  # unter python3 -I fehlt das Skriptverzeichnis in sys.path
    sys.path.insert(0, HIER)
import abgleich as A  # noqa: E402  (holt die Pläne; USER_AGENT wird nur für diesen Aufruf ersetzt)
import nachtrag as N  # noqa: E402  (schreibt spiele.csv fort; N.main ist der ganze Nachtrag)

REPO = os.path.dirname(HIER)
CSV_STANDARD = os.path.join(REPO, 'spiele.csv')
ARBEITSORDNER = 'speuzer-nachtrag-aktion'     # Unterordner von $RUNNER_TEMP (sonst des Temp-Ordners): cache, berichte, zustand
USER_AGENT = ('Mozilla/5.0 (compatible; SpeuzerSpielplanNachtrag/1.0; GitHub Actions; '
              'rein lesend, ein Abruf je Mannschaft und Tag)')
PRAEFIX = 'NACHTRAG: '                        # Beginn der Schlusszeile des Nachtrags
AKTION_PRAEFIX = 'NACHTRAG-AKTION: '          # Beginn der letzten Ausgabezeile dieses Skripts
FEHLER = 'Fehler – '                          # Zustand „Fehler“ in der Schlusszeile des Nachtrags (steht vorn, auch wenn mehr dabeisteht)
AUFFAELLIG = 'Auffällig'                      # Zustand „Auffällig, nicht übernommen“: Mengenbremse, Not-Aus oder bloße Hinweise
# Bloße Hinweise (alltäglich, Exit-Code 2 des Nachtrags): nichts geschrieben, nichts Auffälliges außer den Hinweisen im Protokoll
NUR_HINWEISE = re.compile(r'Auffällig, nicht übernommen – \d+ Hinweis(?:e)? \(siehe oben\)')
# „n geändert (…), gepusht“ irgendwo in der Zeile (auch hinter „Fehler – …;“): nicht „…, nicht gepusht“ und nicht „… nachgepusht“, die das Wort
# ebenfalls enthalten; die Einzelheiten in der Klammer haben nie selbst Klammern
GEPUSHT = re.compile(r'\d+ geändert \([^()]*\), gepusht(?=[,;]|$)')
NACHGEPUSHT = re.compile(r'\d+ liegen gebliebene[r]? Commits? nachgepusht')   # Folge eines früheren Push-Fehlschlags (nur lokal denkbar)


class AufrufFehler(Exception):
    """Ungültiger Aufruf (statt des SystemExit(2) von argparse): main meldet ihn mit der letzten Zeile und Exit-Code 1."""

    def __init__(self, text, usage=''):
        super().__init__(text)
        self.usage = usage


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AufrufFehler(message, self.format_usage().strip())


class Mitschnitt:
    """Ersatz für sys.stdout: gibt alles unverändert weiter und merkt es sich (der Nachtrag druckt seine Zeilen und die Schlusszeile)."""

    def __init__(self, ziel):
        self.ziel, self.teile = ziel, []

    def write(self, text):
        self.teile.append(text)
        return self.ziel.write(text)

    def flush(self):
        self.ziel.flush()

    def zeilen(self):
        return ''.join(self.teile).split('\n')


# ---------------------------------------------------------------- Bewertung und Ausgabeformate

def beurteilen(schluss, code, nur_bericht):
    """-> (Bewertung, Grund): Bewertung 'ok', 'warnung' oder 'fehler'; der Grund (nur bei einem Widerspruch oder einer unbekannten
    oder fehlenden Schlusszeile, sonst leer) erklärt einen Fehler, den die Zeile selbst nicht erklärt. Siehe die Tabelle im Docstring."""
    if not schluss or not schluss.startswith(PRAEFIX):
        return 'fehler', 'keine Schlusszeile des Nachtrags'
    text = schluss[len(PRAEFIX):]
    if FEHLER in text:
        return 'fehler', ''
    if AUFFAELLIG in text:
        if not NUR_HINWEISE.fullmatch(text):
            return 'fehler', ''                  # Mengenbremse, Not-Aus: die Zeile nennt den Grund
        bewertung, erwartet = 'warnung', 2
    else:
        bewertung, erwartet = 'ok', 0
        if 'geändert (' in text:
            if nur_bericht:
                return 'fehler', 'geändert, obwohl nur berichtet werden sollte'
            if not GEPUSHT.search(text):
                return 'fehler', 'geändert, aber nicht gepusht'
        elif not (text == 'unverändert' or text.startswith('Nur-Bericht') or NACHGEPUSHT.match(text)):
            return 'fehler', 'unbekannte Schlusszeile'
    if code != erwartet:
        return 'fehler', 'Exit-Code %s des Nachtrags widerspricht der Schlusszeile (erwartet: %s)' % (code, erwartet)
    return bewertung, ''


def bewerten(schluss, code, nur_bericht):
    """Bewertung der Schlusszeile des Nachtrags: 'ok' | 'warnung' | 'fehler' (`code`: Exit-Code des Nachtrags als Gegenprobe,
    `nur_bericht`: lief der Nachtrag nur im Berichtsmodus?). `schluss` None oder leer = fehlende Schlusszeile = Fehler."""
    return beurteilen(schluss, code, nur_bericht)[0]


def einzeilig(text):
    """Text in einer Zeile (jede Folge von Zeilenumbrüchen und Leerräumen wird ein Leerzeichen): ein Wert in GITHUB_OUTPUT darf keine
    weitere Zeile und damit keine fremde Ausgabe einschmuggeln."""
    return ' '.join(str(text).split())


def maskiert(text):
    """Nachricht einer GitHub-Anmerkung (::warning:: und ::error::): „%“, Wagenrücklauf und Zeilenumbruch maskiert, nach der GitHub-Regel."""
    return text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def laengster_lauf(text, zeichen):
    """Länge der längsten ununterbrochenen Folge von `zeichen` im Text (0, wenn es nicht vorkommt)."""
    return max((len(m) for m in re.findall(re.escape(zeichen) + '+', text)), default=0)


def inline_code(text):
    """Text als Markdown-Code in einer Zeile; der Zaun ist länger als jede Gravis-Folge im Text, er bricht also nicht aus."""
    zaun = '`' * (laengster_lauf(text, '`') + 1)
    return zaun + text + zaun if len(zaun) == 1 else '%s %s %s' % (zaun, text, zaun)


def codeblock(zeilen):
    """Zeilen als Markdown-Codeblock; der Zaun ist länger als jede Gravis-Folge im Text."""
    text = '\n'.join(zeilen)
    zaun = '`' * max(3, laengster_lauf(text, '`') + 1)
    return '%stext\n%s\n%s' % (zaun, text, zaun)


def zusammenfassung(bewertung, schluss, zeilen, modus):
    """Markdown für GITHUB_STEP_SUMMARY: Überschrift, Modus, Bewertung mit Schlusszeile, die Ausgabezeilen des Nachtrags im Codeblock."""
    teile = ['## Spielplan-Nachtrag (Cloud-Sicherung)', '']
    if modus:
        teile += ['Modus: %s' % modus, '']
    teile += ['**%s**: %s' % (bewertung, inline_code(einzeilig(schluss))), '']
    zeilen = list(zeilen)
    while zeilen and not zeilen[-1].strip():
        zeilen.pop()
    if zeilen:
        teile += [codeblock(zeilen), '']
    return '\n'.join(teile) + '\n'


def anhaengen(umgebung, text):
    """Hängt `text` an die Datei, die die Umgebungsvariable `umgebung` nennt (GITHUB_OUTPUT, GITHUB_STEP_SUMMARY); ohne die Variable
    geschieht nichts. -> Fehlertext oder ''. Der Wert der Variablen wird nie ausgegeben."""
    pfad = os.environ.get(umgebung)
    if not pfad:
        return ''
    try:
        with open(pfad, 'a', encoding='utf-8', newline='\n') as f:
            f.write(text)
    except OSError as ex:
        return '%s nicht beschreibbar (%s)' % (umgebung, type(ex).__name__)
    return ''


def abschliessen(bewertung, schluss, zeilen=(), geaendert=False, modus=''):
    """Alles, was die Aktion braucht: GITHUB_OUTPUT (geaendert, status), GITHUB_STEP_SUMMARY, bei Warnung oder Fehler die Anmerkung und
    zuletzt die letzte Ausgabezeile -> Exit-Code (0 bei ok und warnung, 1 bei fehler). Lässt sich GITHUB_OUTPUT nicht schreiben, erführe
    die Aktion nichts vom Ergebnis: das ist ein Fehler."""
    problem = anhaengen('GITHUB_OUTPUT', 'geaendert=%s\nstatus=%s\n' % ('true' if geaendert else 'false', einzeilig(schluss)))
    if problem:
        bewertung, schluss = 'fehler', '%s (%s)' % (schluss, problem)
    hinweis = anhaengen('GITHUB_STEP_SUMMARY', zusammenfassung(bewertung, schluss, zeilen, modus))
    if hinweis:
        print('Hinweis: ' + hinweis, file=sys.stderr)
    art = {'warnung': 'warning', 'fehler': 'error'}.get(bewertung)
    if art:
        print('::%s::%s' % (art, maskiert(schluss)))
    print('%s%s – %s' % (AKTION_PRAEFIX, bewertung, einzeilig(schluss)))
    return 1 if bewertung == 'fehler' else 0


# ---------------------------------------------------------------- Ordner

def innerhalb(pfad, ordner):
    """Liegt `pfad` im Ordner (oder ist er selbst)? Verknüpfungen werden aufgelöst. Wo es den Ordner gibt, entscheidet zusätzlich die Identität
    (os.path.samefile über die Vorfahren des Pfads): auf dem Mac ist ~/library derselbe Ordner wie ~/Library, ein Vergleich der Schreibweise
    allein ließe den Cache des Mac-Tasks mit anderer Groß- und Kleinschreibung durch."""
    pfad, ordner = os.path.realpath(pfad), os.path.realpath(ordner)
    try:
        if os.path.commonpath([pfad, ordner]) == ordner:
            return True
    except ValueError:       # verschiedene Laufwerke
        return False
    if not os.path.isdir(ordner):
        return False
    aktuell = pfad
    while True:
        if os.path.exists(aktuell) and os.path.samefile(aktuell, ordner):
            return True
        eltern = os.path.dirname(aktuell)
        if eltern == aktuell:
            return False
        aktuell = eltern


def slugs():
    """Namen der Cache-Unterordner der zwölf Pläne (elf Mannschaften und die Zusatzquelle D4): nur diese löscht der Wrapper."""
    return {quelle[1] for quelle in A.TEAMS + A.ZUSATZQUELLEN}


def cache_fremdes(cache):
    """Alles im Cache-Ordner, was kein Unterordner eines der zwölf Pläne ist (andere Namen, Dateien, Verknüpfungen) -> sortierte Namen; ein Ordner,
    den es nicht gibt, enthält nichts Fremdes."""
    if not os.path.isdir(cache):
        return []
    bekannt = slugs()
    return sorted(n for n in os.listdir(cache)
                  if n not in bekannt or os.path.islink(os.path.join(cache, n)) or not os.path.isdir(os.path.join(cache, n)))


def ordner_problem(csv, cache, berichte, zustand):
    """Grund, warum die Ordner nicht taugen, sonst None (dann ist noch nichts angelegt oder gelöscht): Cache, Berichte und Zustand liegen immer
    außerhalb des Repos (dem Ordner der CSV-Datei). Der Cache, dessen Unterordner zu Beginn gelöscht werden, darf das Repo nicht enthalten (sonst
    wäre die CSV-Datei weg), nicht der Cache des Mac-Tasks sein (abgleich.CACHE_STANDARD: seine älteren Stände speisen dort die Rücknahme von
    Absagen und die Aufstellungs-Apps), ihn nicht enthalten, nicht in ihm liegen und nichts außer den Unterordnern der zwölf Pläne enthalten."""
    repo = os.path.dirname(os.path.abspath(csv))
    for name, pfad in (('Cache', cache), ('Bericht-Ordner', berichte), ('Zustandsordner', zustand)):
        if innerhalb(pfad, repo):
            return '%s liegt im Repo, nichts getan' % name
    if innerhalb(repo, cache):
        return 'Cache-Ordner enthält das Repo, nichts gelöscht'
    if innerhalb(cache, A.CACHE_STANDARD):        # der Cache des Mac-Tasks selbst oder ein Ordner darin
        return 'Cache-Ordner gehört dem Mac-Task, nichts gelöscht'
    if innerhalb(A.CACHE_STANDARD, cache):        # ein Ordner darüber (z. B. ~/Library/Caches oder das Home-Verzeichnis)
        return 'Cache-Ordner gehört dem Mac-Task, nichts gelöscht'
    try:
        fremdes = cache_fremdes(cache)
    except OSError as ex:
        return 'Cache-Ordner nicht lesbar (%s), nichts gelöscht' % type(ex).__name__
    if fremdes:
        return 'Cache-Ordner enthält Fremdes, nichts gelöscht'
    return None


def cache_leeren(cache):
    """Cache anlegen und leeren: gelöscht werden die Unterordner der zwölf Pläne, sonst nichts (ordner_problem hat vorher ausgeschlossen, dass etwas
    anderes im Ordner liegt). Die Aktion ist zustandslos: ein früherer Lauf im selben Ordner hinterlässt nichts, das der Abgleich als frischen
    Abruf nähme (er ruft höchstens einmal je sechs Tage ab, auch wenn nur ein alter Stand im Cache liegt)."""
    os.makedirs(cache, exist_ok=True)
    for slug in sorted(slugs()):
        pfad = os.path.join(cache, slug)
        if os.path.isdir(pfad) and not os.path.islink(pfad):
            shutil.rmtree(pfad)


# ---------------------------------------------------------------- Abgleich und Nachtrag

def antwort_unbrauchbar(text):
    """Grund, warum eine Antwort, die abgleich.hole ohne Fehler geliefert hat (HTTP 200, lang genug), kein Mannschaftsplan ist, sonst None: eine
    Sperr- oder Einwilligungsseite ohne Spielplan-Merkmale (abgleich.seite_defekt) oder ein geänderter Seitenaufbau (Spielzeilen vorhanden, aber
    keine lesbar). Ein gültiger Plan ohne Spiele (Rahmen der Seite erkennbar, keine Spielzeilen: abgleich.leerer_plan, z. B. die D4, solange sie
    kein Freundschaftsspiel hat) ist keine unbrauchbare Antwort."""
    grund = A.seite_defekt(text)
    if grund:
        return grund
    if A.leerer_plan(text):
        return None
    try:
        gelesen = A.parse_spielplan(text)
    except Exception as ex:
        return 'Seite nicht lesbar (%s)' % type(ex).__name__
    return None if gelesen else 'Spielzeilen vorhanden, aber keine lesbar (Seitenaufbau geändert)'


def hole_mit_wache(hole):
    """Hülle um abgleich.hole (nur für die Dauer des Abgleichs): hole zählt nur HTTP- und Netzfehler als Fehlschlag, trägt dann den Grund in die
    Liste `abbruch` ein, und danach wird nichts mehr abgerufen. Eine Antwort mit HTTP 200, die kein Plan ist (Sperrseite, geänderter Seitenaufbau),
    ginge sonst durch, und die übrigen Pläne würden trotzdem abgerufen. Die Wache trägt für eine frisch abgerufene, unbrauchbare Antwort denselben
    Grund ein; Cache-Stände und Fehlschläge (die hole selbst meldet) lässt sie in Ruhe."""
    def mit_wache(cache, team, von, bis, nur_cache, jetzt, abbruch, *weitere, **benannte):
        ergebnis = hole(cache, team, von, bis, nur_cache, jetzt, abbruch, *weitere, **benannte)
        text, herkunft, _, fehler = ergebnis
        if fehler is None and herkunft == 'Abruf':
            grund = antwort_unbrauchbar(text)
            if grund:
                abbruch.append('%s: unbrauchbare Antwort (%s)' % (team[2], grund))
                print('  %s: unbrauchbare Antwort (%s); weitere Abrufe in diesem Lauf entfallen' % (team[2], grund), file=sys.stderr)
        return ergebnis
    return mit_wache


def abgleich_ausfuehren(csv, cache, berichte, url_vorlage=None):
    """tools/abgleich.py im selben Prozess: holt die zwölf Pläne in den (leeren) Cache -> Exit-Code des Abgleichs (0 = Bericht, 2 =
    Bericht, aber ein Plan fehlt; ein Fehler gleich welcher Art ist 1). User-Agent, sys.argv und die Wache um abgleich.hole (hole_mit_wache)
    gelten nur für diesen Aufruf und stehen danach wieder, wie sie waren, auch nach einer Ausnahme; die Pause zwischen den Abrufen, die
    Wochensperre und die Abbruchregel von abgleich.py bleiben, wie sie sind."""
    argv = ['abgleich.py', '--csv', csv, '--cache', cache, '--bericht-ordner', berichte, '--kein-wochenvergleich']
    if url_vorlage:
        argv += ['--url-vorlage', url_vorlage]
    argv_alt, agent_alt, hole_alt = sys.argv, A.USER_AGENT, A.hole
    sys.argv, A.USER_AGENT, A.hole = argv, USER_AGENT, hole_mit_wache(hole_alt)
    try:
        return A.main()
    except SystemExit as ex:      # argparse bei ungültigem Aufruf: nie ein „in Ordnung“
        print('Abgleich: beendet mit Code %s' % ex.code, file=sys.stderr)
        return 1
    except Exception:             # nichts darf den Lauf ohne letzte Zeile beenden
        traceback.print_exc(file=sys.stderr)
        return 1
    finally:
        sys.argv, A.USER_AGENT, A.hole = argv_alt, agent_alt, hole_alt


def nachtrag_argv(csv, cache, berichte, zustand, nur_bericht):
    """Kommandozeile des Nachtrags. --nur-cache: der Nachtrag ruft nie selbst ab (der Abruf ist Sache von abgleich_ausfuehren; sonst
    holte er die G-Jugend nach einem Fehlschlag noch einmal). Die Sperre lauf.lock liegt neben der Not-Aus-Datei, also im Zustandsordner."""
    argv = ['--csv', csv, '--cache', cache, '--bericht-ordner', berichte, '--notaus-datei', os.path.join(zustand, 'AUS'), '--nur-cache']
    if not nur_bericht:
        argv += ['--anwenden', '--push']
    return argv


def nachtrag_ausfuehren(argv):
    """N.main(argv) im selben Prozess, die Ausgabe wird mitgeschnitten und dennoch ausgegeben -> (Exit-Code oder None bei einer
    unerwarteten Ausnahme oder einem SystemExit, Ausgabezeilen). N.main meldet sonst jeden Fehler selbst mit einer Schlusszeile."""
    mitschnitt = Mitschnitt(sys.stdout)
    try:
        with contextlib.redirect_stdout(mitschnitt):
            code = N.main(argv)
    except (Exception, SystemExit):
        traceback.print_exc(file=sys.stderr)
        code = None
    return code, mitschnitt.zeilen()


def kopf_von(repo):
    """Commit von HEAD im Repo (git rev-parse HEAD) oder None, wenn Git ihn nicht nennt."""
    rc, ausgabe, _ = N.git(repo, 'rev-parse', 'HEAD')
    return ausgabe if rc == 0 and ausgabe else None


# ---------------------------------------------------------------- Kommandozeile und Lauf

def optionen_aus(argv=None):
    ap = _Parser(description='Cloud-Sicherung des Spielplan-Nachtrags (Rahmen für die GitHub-Aktion)', allow_abbrev=False)
    ap.add_argument('--csv', default=CSV_STANDARD, help='spiele.csv (Standard: im Repo-Stamm)')
    ap.add_argument('--cache', help='Cache der Mannschaftspläne; zu Beginn werden nur die Unterordner der zwölf Pläne gelöscht (Standard: <Temp>/%s/cache)'
                    % ARBEITSORDNER)
    ap.add_argument('--bericht-ordner', help='Ordner für die Berichte (Standard: <Temp>/%s/berichte)' % ARBEITSORDNER)
    ap.add_argument('--zustand-ordner', help='Ordner für Sperre und Not-Aus-Datei des Nachtrags (Standard: <Temp>/%s/zustand)' % ARBEITSORDNER)
    ap.add_argument('--nur-bericht', action='store_true', help='Probelauf: nichts schreiben, nichts committen, nichts pushen')
    ap.add_argument('--url-vorlage', help=argparse.SUPPRESS)      # nur für Tests gegen einen lokalen Server: {von}, {bis}, {tid}
    return ap.parse_args(argv)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)     # Zeilen und Fehlermeldungen in der richtigen Reihenfolge
    except Exception:
        pass
    try:
        opt = optionen_aus(argv)
    except AufrufFehler as ex:            # --help beendet weiter mit Exit 0 (SystemExit), alles andere ist ein Fehler mit letzter Zeile
        print(ex.usage, file=sys.stderr)
        return abschliessen('fehler', 'ungültiger Aufruf (%s)' % ex)
    csv = os.path.abspath(opt.csv)
    basis = os.path.join(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir(), ARBEITSORDNER)
    cache = os.path.abspath(opt.cache or os.path.join(basis, 'cache'))
    berichte = os.path.abspath(opt.bericht_ordner or os.path.join(basis, 'berichte'))
    zustand = os.path.abspath(opt.zustand_ordner or os.path.join(basis, 'zustand'))
    modus = 'nur Bericht (nichts geschrieben, nichts gepusht)' if opt.nur_bericht else 'schreiben und pushen'
    problem = ordner_problem(csv, cache, berichte, zustand)
    if problem:
        return abschliessen('fehler', problem, modus=modus)
    try:
        os.makedirs(berichte, exist_ok=True)
        os.makedirs(zustand, exist_ok=True)
        cache_leeren(cache)
    except OSError as ex:
        return abschliessen('fehler', 'Arbeitsordner nicht vorbereitet (%s)' % type(ex).__name__, modus=modus)

    abgleich = abgleich_ausfuehren(csv, cache, berichte, opt.url_vorlage)      # genau ein Abruf-Durchgang, keine Wiederholung
    if abgleich not in (0, 2):
        return abschliessen('fehler', 'Abruf/Abgleich fehlgeschlagen (Exit %s)' % abgleich, modus=modus)

    repo = os.path.dirname(csv)
    kopf_vorher = kopf_von(repo)
    code, zeilen = nachtrag_ausfuehren(nachtrag_argv(csv, cache, berichte, zustand, opt.nur_bericht))
    kopf_nachher = kopf_von(repo)
    schluss = next((z for z in reversed(zeilen) if z.startswith(PRAEFIX)), None)
    bewertung, grund = beurteilen(schluss, code, opt.nur_bericht)
    if grund:
        print('Auswertung: ' + grund)
    # Unabhängig von der Bewertung: auch bei einem teilweisen Ausfall („Fehler – …; n geändert (…), gepusht“, der Lauf bleibt rot) liegt Neues auf
    # main, und der Kalender muss gebaut werden
    geaendert = bool(schluss) and GEPUSHT.search(schluss) is not None and kopf_vorher != kopf_nachher
    ohne = ('Nachtrag unerwartet beendet (Ausnahme oder SystemExit)' if code is None
            else 'keine Schlusszeile des Nachtrags (Exit %s)' % code)
    return abschliessen(bewertung, schluss or ohne, zeilen, geaendert, modus)


if __name__ == '__main__':
    sys.exit(main())
