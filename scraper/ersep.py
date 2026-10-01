"""Catálogo de cooperativas eléctricas publicado por ERSEP (Córdoba).

Cada prestador tiene una página /prestador/<slug>/ con el área de concesión y un link fijo a
Drive con el cuadro tarifario; ERSEP reemplaza el archivo cada mes sin cambiar el link.
"""

import html
import re
import time
import urllib.request

BASE = "https://ersep.cba.gov.ar"
LIST_URL = f"{BASE}/prestadores-por-gerencia/"

# Sin encabezados de navegador CloudFront responde 403.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "es-AR,es;q=0.9",
}

PAGE_RE = re.compile(rf'href="{re.escape(BASE)}/prestador/([\w-]+)/"')
TITLE_RE = re.compile(r"<title>(.*?)\s*-\s*ERSeP</title>", re.S)
CONTENT_RE = re.compile(r'<div class="entry-content.*?<!-- \.entry-content', re.S)
DRIVE_RE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=)([\w-]{20,})")
USERS_RE = re.compile(r"(\d[\d.]*)\s+usuarios")
LABEL_RE = re.compile(r"^(Nombre del Prestador|Área de (?:Concesión|Prestación)|Dirección|Tel[ée]fono|"
                      r"Correo|Email|E-mail|Cantidad de Usuarios|Tarifa)", re.I)


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def provider_slugs(page: str) -> list[str]:
    """Slugs de las páginas de cooperativas del listado, sin repetir y en orden."""
    seen = []
    for slug in PAGE_RE.findall(page):
        if "coop" in slug and slug not in seen:
            seen.append(slug)
    return seen


def _lines(fragment: str) -> list[str]:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</strong>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return [" ".join(line.split()).strip(" :") for line in text.splitlines() if line.strip(" :\t")]


def _field(lines: list[str], label: str) -> str | None:
    """Texto que sigue a la etiqueta, hasta la próxima etiqueta."""
    for i, line in enumerate(lines):
        if re.match(label, line, re.I):
            rest = line[len(re.match(label, line, re.I)[0]):].strip(" :")
            values = [rest] if rest else []
            for nxt in lines[i + 1:]:
                if LABEL_RE.match(nxt):
                    break
                values.append(nxt)
            return " ".join(values).strip() or None
    return None


def parse_provider(slug: str, page: str) -> dict | None:
    """Datos de la página del prestador. None si no tiene link de Drive."""
    m = CONTENT_RE.search(page)
    content = m[0] if m else ""
    drive = DRIVE_RE.search(content)
    if not drive:
        return None
    lines = _lines(content)
    title = TITLE_RE.search(page)
    users = USERS_RE.search(_field(lines, "Cantidad de Usuarios") or "")
    return {
        "id": slug,
        "name": " ".join(html.unescape(title[1]).split()) if title else slug,
        "fullName": _field(lines, "Nombre del Prestador") or "",
        "area": _field(lines, r"Área de (?:Concesión|Prestación)") or "",
        "users": int(users[1].replace(".", "")) if users else None,
        "driveId": drive[1],
    }


def catalog(delay: float = 1.0, log=print) -> list[dict]:
    entries = []
    for slug in provider_slugs(get(LIST_URL)):
        try:
            entry = parse_provider(slug, get(f"{BASE}/prestador/{slug}/"))
        except Exception as e:  # una página caída no frena el resto
            log(f"{slug}: {e}")
            entry = None
        if entry:
            entries.append(entry)
        time.sleep(delay)
    return entries
