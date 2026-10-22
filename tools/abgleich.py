#!/usr/bin/env python3
"""Spielplan-Abgleich: spiele.csv gegen die öffentlichen Mannschafts-Spielpläne bei fussball.de.

Prüft die Spieltermine in spiele.csv (daraus baut build_ics.py die Kalender-Abos) gegen die öffentlichen Spielpläne der elf
Mannschaften des FFV Sportfreunde 04 Frankfurt und berichtet NUR die Unterschiede (Markdown). Das Skript ist REIN LESEND: Es
schreibt nie in spiele.csv oder sonst ins Repo, committet und pusht nichts und sendet keine Mails. Es gehört nicht zum
Kalender-Workflow (.github/workflows/build.yml), der fussball.de bewusst nicht ausliest.

Rücksicht auf fussball.de:
  - nur die öffentlichen Mannschafts-Spielpläne (ajax.team.matchplan), erkennbarer User-Agent, 3 s Pause zwischen den Abrufen
  - höchstens ein Abruf je Mannschaft und Woche: liegt ein Abruf jünger als 6 Tage im Cache, wird er benutzt und nichts
    abgerufen (eine Option zum Erzwingen gibt es absichtlich nicht); nach einem Fehlschlag keine weiteren Abrufe im Lauf
  - Rohdaten und Bericht landen nie im Repo: Cache im Benutzer-Cache-Ordner, Bericht standardmäßig im temporären Ordner

Aufruf (aus dem Repo-Stamm):
  python3 -I tools/abgleich.py [--csv DATEI] [--bericht-ordner ORDNER] [--cache ORDNER] [--nur-cache]
                               [--heute JJJJ-MM-TT] [--saison JJJJ] [--kein-wochenvergleich]
  --nur-cache              nichts abrufen, den neuesten Cache-Stand je Mannschaft nehmen (zum erneuten Auswerten)
  --kein-wochenvergleich   keinen Vergleich mit dem Bericht der Vorwoche anstellen (z. B. wenn jeder Lauf ohne Vorlauf startet)
Exit-Code: 0 = Bericht erstellt, 2 = Bericht erstellt, aber mindestens eine Mannschaft nicht ausgewertet, 1 = Fehler.
"""
import argparse
import csv
import difflib
import glob
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

HOME = os.path.expanduser('~')
CSV_STANDARD = 'spiele.csv'        # relativ zum aktuellen Verzeichnis (Repo-Stamm)
BERICHTE_STANDARD = os.path.join(tempfile.gettempdir(), 'speuzer-spielplan-abgleich', 'berichte')
CACHE_STANDARD = (os.path.join(HOME, 'Library', 'Caches', 'speuzer-spielplan-abgleich') if sys.platform == 'darwin'
                  else os.path.join(HOME, '.cache', 'speuzer-spielplan-abgleich'))
MIN_ABSTAND_TAGE = 6
PAUSE_SEKUNDEN = 3
USER_AGENT = 'Mozilla/5.0 (compatible; SpeuzerSpielplanAbgleich/1.0; rein lesend, ein Abruf je Mannschaft und Woche)'
URL = ('https://www.fussball.de/ajax.team.matchplan/-/datum-von/{von}/datum-bis/{bis}'
       '/max/999/offset/0/mode/PAGE/team-id/{tid}')
EIGEN = 'sportfreunde 04'          # unser Vereinsname in normalisierter Schreibweise
AEHNLICH = 0.88                    # ab hier gilt ein Namensunterschied nur als Schreibweise

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


def hole(cache, team, von, bis, nur_cache, jetzt, abbruch, url_vorlage=URL):
    """Gibt (html, Quelle, Zeitstempel, Fehler) zurück. Ruft höchstens einmal je MIN_ABSTAND_TAGE ab.
    Wird ein Abruf abgewiesen oder scheitert er, landet der Grund in `abbruch`; danach wird gar nichts mehr abgerufen.
    Nach einem Fehlversuch gibt es für diese Mannschaft 20 Stunden lang keinen neuen Versuch."""
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
    fehler, text = None, ''
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode('utf-8', 'replace')
            if r.status != 200 or len(text) < 500:
                fehler = 'unerwartete Antwort (Status %s, %d Zeichen)' % (r.status, len(text))
    except urllib.error.HTTPError as e:
        fehler = 'HTTP %s' % e.code
    except Exception as e:  # Netz, Zeitüberschreitung, Zertifikat …
        fehler = '%s: %s' % (type(e).__name__, e)
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

def main():
    ap = argparse.ArgumentParser(description='Spielplan-Abgleich spiele.csv gegen fussball.de (rein lesend)')
    ap.add_argument('--csv', default=CSV_STANDARD)
    ap.add_argument('--bericht-ordner', default=BERICHTE_STANDARD)
    ap.add_argument('--cache', default=CACHE_STANDARD)
    ap.add_argument('--nur-cache', action='store_true')
    ap.add_argument('--heute', help='JJJJ-MM-TT (nur zum Testen)')
    ap.add_argument('--saison', type=int, help='Startjahr der Saison, Standard: aus dem Datum')
    ap.add_argument('--kein-wochenvergleich', action='store_true', help='keinen Vergleich mit dem Vorwochenbericht, keine Standdatei')
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

    ergebnisse, abrufe, abbruch = {}, [], []
    for team in TEAMS:
        kuerzel, slug, name, tid = team
        text, quelle, stempel, fehler = hole(a.cache, team, von, bis, a.nur_cache, jetzt, abbruch, a.url_vorlage)
        if fehler:
            ergebnisse[kuerzel] = dict(fehler=fehler)
            abrufe.append('%s: FEHLER (%s)' % (name, fehler))
            if not fehler.startswith('nicht abgerufen'):
                print('  %s: Abruf fehlgeschlagen (%s); weitere Abrufe in diesem Lauf entfallen' % (name, fehler), file=sys.stderr)
            continue
        abrufe.append('%s: %s %s' % (name, quelle, stempel.strftime('%d.%m.%Y %H:%M')))
        fb = [fb_normalisieren(g) for g in parse_spielplan(text)]
        diffs, staffeln, info, ohne_termin = vergleiche(team, fb, csv_je.get(kuerzel, []), heute)
        n_fb = sum(1 for g in fb if g['status'] in ('angesetzt', 'nichtantritt'))
        ergebnisse[kuerzel] = dict(diffs=diffs, staffeln=staffeln, info=info, ohne_termin=ohne_termin, n_fb=n_fb,
                                   n_csv=sum(1 for r in csv_je.get(kuerzel, []) if not r['abgesagt']), csv_ausserhalb=ausserhalb.get(kuerzel, 0),
                                   zeilen=len(fb))
        if not fb:
            ergebnisse[kuerzel]['fehler'] = 'fussball.de lieferte keine Spielzeilen (Seitenaufbau geändert oder Plan leer)'
    alle = [d for e in ergebnisse.values() if not e.get('fehler') for d in e['diffs']]

    vorher = None if a.kein_wochenvergleich else stand_laden(a.bericht_ordner, heute)
    kf = kurzfassung(ergebnisse, alle, jetzt, vorher, not a.kein_wochenvergleich)
    n_fehler = sum(1 for e in ergebnisse.values() if e.get('fehler'))
    n_cache = sum(1 for z in abrufe if ': Cache ' in z)
    n_abruf = sum(1 for z in abrufe if ': Abruf ' in z)

    md = ['# Spielplan-Abgleich · spiele.csv gegen fussball.de · %s' % h.strftime('%d.%m.%Y'), '',
          'Rein lesender Bericht der Unterschiede. Nichts wurde in `spiele.csv` geschrieben, committet oder gepusht.', '',
          '- **Quelle CSV:** `%s` (%d Zeilen, Saisonfenster %s bis %s); %s' % (a.csv, len(csv_alle), kurz_datum(von) + str(sj), kurz_datum(bis) + str(sj + 1), git_info(a.csv)),
          '- **Quelle fussball.de:** öffentlicher Mannschafts-Spielplan je Mannschaft (`ajax.team.matchplan`), ein Abruf je Mannschaft und Woche. Dieser Lauf: %d Abrufe, %d aus dem Cache, %d fehlgeschlagen.' % (n_abruf, n_cache, n_fehler),
          '- **Schlüssel des Vergleichs:** Staffelkennung + Spielnummer; verglichen werden Datum, Uhrzeit, Heim/Auswärts, Gegner, Wettbewerbsart und Absagen. „Kommend“ heißt: Spieldatum heute (%s) oder später.' % h.strftime('%d.%m.%Y'), '',
          '## Kurzfassung', ''] + kf + ['']
    if unbekannt:
        md += ['**Achtung:** Mannschaften in der CSV ohne fussball.de-Team-ID im Skript, nicht geprüft: %s.' % ', '.join(unbekannt), '']
    if csv_fehler:
        md += ['**Achtung:** %d CSV-Zeilen nicht lesbar (%s).' % (len(csv_fehler), '; '.join(csv_fehler[:5])), '']
    md += ['## Je Mannschaft', '']
    for team in TEAMS:
        md += team_abschnitt(team, ergebnisse[team[0]], heute)
    md += ['## Abrufe dieses Laufs', ''] + ['- ' + z for z in abrufe] + ['']
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

    print('Spielplan-Abgleich %s – %d Abrufe, %d aus dem Cache, %d fehlgeschlagen\n' % (h.strftime('%d.%m.%Y'), n_abruf, n_cache, n_fehler))
    print('\n'.join(kf))
    print('\nBericht: %s' % bericht)
    return 2 if n_fehler else 0


if __name__ == '__main__':
    sys.exit(main())
