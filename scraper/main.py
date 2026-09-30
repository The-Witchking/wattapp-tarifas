"""Busca el cuadro EPEC más reciente en Drive y actualiza tariffs.json e history/.

Uso: python -m scraper.main [--force]
Sale con código 1 si algo no cierra; en ese caso no escribe nada.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from scraper import drive, epec

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "tariffs.json"
HISTORY = ROOT / "history" / "epec"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(force: bool = False) -> int:
    files = drive.list_folder()
    if not files:
        print("El listado de Drive está vacío o cambió de formato", file=sys.stderr)
        return 1
    newest = drive.latest(files)
    if newest is None:
        print("No hay cuadros PARCIAL en el listado", file=sys.stderr)
        return 1

    current = json.loads(LATEST.read_text(encoding="utf-8")) if LATEST.exists() else None
    current_source = current["sources"][0] if current else None
    if not force and current_source and current_source["sourceFile"] == newest.name:
        print(f"Sin cambios: {newest.name}")
        return 0

    print(f"Procesando {newest.name}")
    try:
        schedule = epec.parse(drive.download(newest), newest.valid_from)
    except epec.ParseError as e:
        print(f"Error al interpretar {newest.name}: {e}", file=sys.stderr)
        return 1

    if current_source:
        problems = epec.compare(schedule, current_source["schedule"])
        if problems:
            print("Cambios mayores al 40 % contra el cuadro anterior:", file=sys.stderr)
            print("\n".join(problems), file=sys.stderr)
            return 1

    source = {
        "provider": "EPEC",
        "variant": newest.variant,
        "sourceFile": newest.name,
        "sourceUrl": newest.url,
        "schedule": schedule,
    }
    _write(HISTORY / f"{newest.valid_from.isoformat()}.json", source)
    _write(LATEST, {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sources": [source],
    })
    print(f"Actualizado: {schedule['validFrom']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))
