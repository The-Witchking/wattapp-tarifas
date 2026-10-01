"""Actualiza los cuadros de las cooperativas: cooperativas/<id>.json, index.json y report.md.

Uso: python -m scraper.coops_main [--catalog] [--today AAAA-MM-DD]
  --catalog  vuelve a leer el catálogo de ERSEP (si falla, se usa el anterior).

Una cooperativa que no se puede interpretar no se publica (o conserva su cuadro anterior si
todavía está vigente). Sale con código 1 solo si falla más del 30 %: eso indica que cambió
la plantilla de ERSEP.
"""

import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from scraper import coop, drive, epec, ersep
from scraper.main import SCHEMA_VERSION, _write

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "cooperativas"
CATALOG = DIR / "catalog.json"
INDEX = DIR / "index.json"
REPORT = DIR / "report.md"
MAX_FAILURE_RATE = 0.3


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def refresh_catalog(log) -> list[dict]:
    previous = _read(CATALOG) or []
    try:
        entries = ersep.catalog(log=log)
    except Exception as e:
        log(f"No se pudo leer el catálogo de ERSEP ({e}); se usa el anterior")
        return previous
    if len(entries) < len(previous) * 0.8:
        log(f"El catálogo nuevo tiene {len(entries)} cooperativas y el anterior {len(previous)}; se usa el anterior")
        return previous
    _write_list(CATALOG, entries)
    return entries


def _write_list(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def process(entry: dict, today: date) -> dict:
    """Fuente publicable de la cooperativa. Lanza ParseError/ValueError si no sirve."""
    url = drive.DOWNLOAD_URL.format(id=entry["driveId"])
    data = drive._get(url)
    if not data.startswith(b"%PDF"):
        raise ValueError("La descarga no es un PDF")
    schedule, pdf_date = coop.parse(data, entry["name"], today)
    return {
        "provider": entry["id"],
        "variant": "ERSEP",
        "sourceFile": f"{entry['id']}.pdf",
        "sourceUrl": url,
        "priceDate": pdf_date.isoformat(),
        "sourceHash": hashlib.sha256(data).hexdigest(),
        "schedule": schedule,
    }


def main(argv: list[str]) -> int:
    lines: list[str] = []
    log = lambda msg: (print(msg), lines.append(msg))
    today = date.fromisoformat(argv[argv.index("--today") + 1]) if "--today" in argv else date.today()

    catalog = refresh_catalog(log) if "--catalog" in argv else (_read(CATALOG) or [])
    if not catalog:
        print("No hay catálogo de cooperativas", file=sys.stderr)
        return 1

    published, rejected, skipped = [], [], []
    for entry in catalog:
        path = DIR / f"{entry['id']}.json"
        old = _read(path)
        old_source = old["sources"][0] if old else None
        try:
            source = process(entry, today)
            if old_source:
                problems = epec.compare(source["schedule"], old_source["schedule"])
                if problems:
                    raise epec.ParseError("cambios mayores al 40 %: " + "; ".join(problems[:3]))
        except (epec.ParseError, ValueError, OSError) as e:
            reason = str(e)
            if reason.startswith("No es un cuadro"):
                skipped.append((entry, reason))
            else:
                rejected.append((entry, reason))
            # El cuadro anterior se mantiene mientras siga vigente.
            if old_source and (today - date.fromisoformat(old_source["priceDate"])).days <= coop.MAX_PDF_AGE_DAYS:
                published.append((entry, old_source))
            elif path.exists():
                path.unlink()
            continue
        finally:
            time.sleep(0.5)
        if not old_source or old_source["schedule"] != source["schedule"] or old_source["sourceHash"] != source["sourceHash"]:
            _write(path, {"schemaVersion": SCHEMA_VERSION, "generatedAt": _now(), "sources": [source]})
        published.append((entry, source))

    index = [
        {
            "id": e["id"], "name": s["schedule"]["name"], "fullName": e["fullName"],
            "area": e["area"], "users": e["users"], "validFrom": s["schedule"]["validFrom"],
        }
        for e, s in sorted(published, key=lambda p: p[1]["schedule"]["name"])
    ]
    old_index = _read(INDEX)
    if not old_index or old_index["providers"] != index:
        _write(INDEX, {"schemaVersion": SCHEMA_VERSION, "generatedAt": _now(), "providers": index})

    electric = len(catalog) - len(skipped)
    report = [
        "# Cooperativas",
        "",
        f"Publicadas: {len(index)} de {electric} con cuadro eléctrico ({len(skipped)} enlaces no son cuadros eléctricos).",
        "",
    ]
    if rejected:
        report += ["## No publicadas o con el cuadro anterior", "", "| Cooperativa | Motivo |", "|---|---|"]
        report += [f"| {e['name']} | {reason.replace('|', '/')} |" for e, reason in rejected]
    if lines:
        report += ["", "## Avisos", ""] + [f"- {l}" for l in lines]
    text = "\n".join(report) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    print(f"Publicadas {len(index)}, rechazadas {len(rejected)}, no eléctricas {len(skipped)}")

    if electric and len(rejected) / electric > MAX_FAILURE_RATE:
        print("Fallan demasiadas cooperativas: ¿cambió la plantilla de ERSEP?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
