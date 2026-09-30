"""Listado y descarga de la carpeta pública de Drive donde EPEC publica los cuadros."""

import html
import re
import urllib.request
from dataclasses import dataclass
from datetime import date

FOLDER_ID = "1pkF_dODi3sjEsf5Kvly_Q8kNamfIaH97"
LISTING_URL = f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}"
DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id={id}"

MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

# "0521-096017_2026 - CT EPEC DISTRIB PARCIAL Y GD  01-sep-2026.pdf"
NAME_RE = re.compile(
    r"CT EPEC DISTRIB (?P<variant>PLENO|PARCIAL) Y GD\s+"
    r"(?P<day>\d{1,2})-(?P<month>[a-z]{3})-(?P<year>\d{2}|\d{4})\.pdf$",
    re.IGNORECASE,
)
ENTRY_RE = re.compile(
    r'href="https://drive\.google\.com/file/d/(?P<id>[\w-]+)/view[^"]*".*?'
    r'flip-entry-title">(?P<name>[^<]*)<',
    re.DOTALL,
)


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    variant: str  # PLENO | PARCIAL
    valid_from: date

    @property
    def url(self) -> str:
        return DOWNLOAD_URL.format(id=self.id)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wattapp-tarifas"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def parse_name(file_id: str, name: str) -> DriveFile | None:
    """Interpreta el nombre del archivo. Devuelve None si no tiene el formato esperado."""
    m = NAME_RE.search(name)
    if not m:
        return None
    month = MONTHS.get(m["month"].lower())
    if month is None:
        return None
    year = int(m["year"])
    if year < 100:
        year += 2000
    try:
        valid_from = date(year, month, int(m["day"]))
    except ValueError:
        return None
    return DriveFile(file_id, name, m["variant"].upper(), valid_from)


def parse_listing(page: str) -> list[DriveFile]:
    files = []
    for m in ENTRY_RE.finditer(page):
        f = parse_name(m["id"], html.unescape(m["name"]).strip())
        if f:
            files.append(f)
    return files


def latest(files: list[DriveFile], variant: str = "PARCIAL") -> DriveFile | None:
    candidates = [f for f in files if f.variant == variant]
    return max(candidates, key=lambda f: f.valid_from, default=None)


def list_folder() -> list[DriveFile]:
    return parse_listing(_get(LISTING_URL).decode("utf-8"))


def download(f: DriveFile) -> bytes:
    data = _get(f.url)
    if not data.startswith(b"%PDF"):
        raise ValueError(f"La descarga de {f.name} no es un PDF")
    return data
