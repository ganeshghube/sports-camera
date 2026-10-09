"""
One-off migration: root admin.html / index.html  ->  frontend/admin.html / frontend/index.html

* Firebase SDK imports are swapped for ./js/localdb.js (same API, local SQLite backend)
* The hard-coded client-side password gate is replaced by real session auth (/api/me)
* Admin cards are tagged with data-views so the same engine serves
  Setup / Scoring / Overlay pages (admin.html?view=setup|scoring|overlay)
All scoring/overlay logic is left byte-for-byte untouched.

Run from the project root:  python tools/migrate_frontend.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend"
OUT.mkdir(exist_ok=True)


def read(p):
    return p.read_bytes().decode("utf-8")


def write(p, s):
    p.write_bytes(s.encode("utf-8"))


FB_IMPORT = re.compile(r'import\s*\{\s*initializeApp\s*\}\s*from\s*"https://www\.gstatic\.com/firebasejs/[^"]+/firebase-app\.js";\s*'
                       r'import\s*\{([^}]*)\}\s*from\s*"https://www\.gstatic\.com/firebasejs/[^"]+/firebase-database\.js";', re.S)
CFG = re.compile(r'const cfg = \{.*?\};\s*', re.S)
DB = "const db = getDatabase(initializeApp(cfg));"


def swap_firebase(s):
    s, n = FB_IMPORT.subn(lambda m: 'import { initializeApp, getDatabase, ' + m.group(1).replace("getDatabase,", "").strip()
                          + ' } from "./js/localdb.js";', s)
    assert n == 1, "firebase import not found"
    s, n = CFG.subn("", s, count=1)
    assert n == 1, "firebase cfg not found"
    s = s.replace(DB, "const db = getDatabase(initializeApp({}));")
    s = re.sub(r'\s*<link rel="dns-prefetch" href="https://[^"]*firebase[^"]*">', "", s)
    return s


# ------------------------------------------------------------------ admin.html
a = swap_firebase(read(ROOT / "admin.html"))

# card -> views (header text is the stable marker)
CARD_VIEWS = [
    ("Team Squads &amp; Match Setup", "setup"),
    ("Live Scoring", "scoring"),
    ("Overlay Controls", "overlay"),
    ("Live Commentary Audio", "overlay"),
    ("Announcements", "overlay"),
    ("Save &amp; History", "setup scoring"),
    ("Manual Target Setup", "scoring"),
]
for marker, views in CARD_VIEWS:
    pat = re.compile(r'<div class="card">(\s*<div class="card-hdr[^"]*">[^<]*' + re.escape(marker) + ')')
    a, n = pat.subn(lambda m: f'<div class="card" data-views="{views}">' + m.group(1), a, count=1)
    assert n == 1, marker
# un-headed innings/reset card
a, n = re.subn(r'<div class="card">(\s*<div class="card-body">\s*<div class="g2">\s*<button class="sb-inn")',
               r'<div class="card" data-views="scoring">\1', a, count=1)
assert n == 1, "innings card"
# live preview strip only where it helps the operator
a, n = re.subn(r'<div class="pv-bar">', '<div class="pv-bar" data-views="scoring overlay">', a, count=1)
a, n2 = re.subn(r'<div class="pv"><iframe', '<div class="pv" data-views="scoring overlay"><iframe', a, count=1)
assert n == 1 and n2 == 1

# real authentication instead of the hard-coded password
GATE = re.compile(r"<script>\s*\(function \(\) \{\s*const PW = .*?</script>", re.S)
AUTH = """<script>
        (async function () {
            // Session auth: the HTTP-only cookie is validated by the backend (/api/me).
            const gate = document.getElementById('pwGate');
            if (gate) gate.classList.add('gone');
            try {
                const me = await (await fetch('/api/me', { credentials: 'same-origin' })).json();
                if (!me.authenticated) { location.replace('/login.html?next=' + encodeURIComponent(location.pathname + location.search)); return; }
            } catch (e) { document.title = 'Backend unavailable'; alert('Backend unavailable — start it with: python backend/server.py'); return; }
            const mc = document.getElementById('mc');
            mc.style.filter = ''; mc.style.pointerEvents = ''; mc.style.userSelect = '';
            window.checkPw = window.togglePwEye = function () { };
        })();
    </script>
    <script src="js/app.js" data-page="console"></script>"""
a, n = GATE.subn(lambda m: AUTH, a, count=1)
assert n == 1, "password gate script"
write(OUT / "admin.html", a)

# ------------------------------------------------------------------ index.html
i = swap_firebase(read(ROOT / "index.html"))
write(OUT / "index.html", i)
print("wrote", OUT / "admin.html", OUT / "index.html")
