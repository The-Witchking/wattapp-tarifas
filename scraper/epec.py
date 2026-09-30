"""Intérprete del cuadro tarifario EPEC: Tarifa Nº 1 Residencial, acápites a, b, c, e, f y h.

Devuelve un `schedule` con los mismos nombres de campo que `TariffSchedule` de la app
(Kotlin): name, validFrom, minimumBillableKwh, levels[id, name, ranges[upperLimitKwh,
fixedCharge, tiers[sizeKwh, pricePerKwh]]].
"""

import io
import re
from datetime import date

import pdfplumber

LEVELS = [
    ("SEF", "Con subsidio", "RESIDENCIALES CON SUBSIDIO ENERGETICO FOCALIZADO"),
    ("NOSEF", "Sin subsidio", "RESIDENCIALES SIN SUBSIDIO ENERGETICO FOCALIZADO"),
]

MONTHS = [
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO",
    "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
]

# Fin de la sección: notas, otro acápite (movilidad eléctrica, etc.) u otra sección.
SECTION_END_RE = re.compile(
    r"NOTA 1|Para los servicios comprendidos en el acápite|RESIDENCIALES |TARIFA Nº"
)

TOKEN_RE = re.compile(
    r"(?P<range>Suministros cuyos consumos sean (?P<cond>.*?)(?:se\s+aplicará|en\s+la\s+totalidad))"
    r"|(?P<cfm>Cargo Fijo Mensual)"
    r"|(?P<perkwh>Por cada kWh consumido)"
    r"|(?P<first>Los primeros (?P<first_n>\d+) kWh)"
    r"|(?P<next>Los siguientes (?P<next_n>\d+) kWh)"
    r"|(?P<excess>El excedente de (?P<excess_n>\d+) kWh)"
    r"|(?P<amount>(?P<neg>-)?\$\s*(?P<value>\d{1,3}(?:\.\d{3})*,\d+))",
    re.DOTALL,
)

MIN_KWH_RE = re.compile(r"Energía Mínima Mensual a facturar\s*=\s*(\d+)\s*kWh")


class ParseError(Exception):
    pass


def extract_text(pdf: bytes, max_pages: int = 15) -> str:
    """Texto de las primeras páginas. La tarifa residencial está al principio del cuadro."""
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        pages = doc.pages[:max_pages]
        return "\n".join(page.extract_text() or "" for page in pages)


def parse_number(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def _upper_limit(cond: str) -> float | None:
    cond = " ".join(cond.split())
    m = re.search(r"menores o iguales a (\d+) kWh", cond)
    if m:
        return float(m[1])
    if re.search(r"mayores a (\d+) kWh", cond):
        return None
    raise ParseError(f"Rango no reconocido: {cond!r}")


def _section(text: str, title: str) -> str:
    start = text.find(title)
    if start < 0:
        raise ParseError(f"No se encontró la sección {title!r}")
    body = text[start + len(title):]
    end = SECTION_END_RE.search(body)
    return body[: end.start()] if end else body


def _build_range(upper: float | None, labels: list, amounts: list[float]) -> dict:
    # "Por cada kWh consumido" es el precio único si no hay escalones; si los hay, es un título.
    stepped = any(kind in ("first", "next", "excess") for kind, _ in labels)
    labels = [
        ("single", None) if kind == "perkwh" else (kind, n)
        for kind, n in labels
        if not (kind == "perkwh" and stepped)
    ]
    if len(labels) != len(amounts):
        raise ParseError(
            f"Rango ≤{upper}: {len(labels)} conceptos y {len(amounts)} importes"
        )
    if not labels or labels[0][0] != "cfm":
        raise ParseError(f"Rango ≤{upper}: falta el cargo fijo")
    fixed = amounts[0]

    # Cada escalón se describe por el kWh donde empieza. "El excedente de N" empieza en N
    # y puede no ser el último (cuadros de ene/feb 2026: "El excedente de 150" y "de 300").
    starts: list[tuple[float, float]] = []  # (inicio, precio)
    end: float | None = 0.0  # fin del último escalón con tamaño conocido
    for (kind, n), price in zip(labels[1:], amounts[1:]):
        if kind == "cfm":
            raise ParseError(f"Rango ≤{upper}: cargo fijo repetido")
        if kind == "single":
            if starts or len(labels) != 2:
                raise ParseError(f"Rango ≤{upper}: precio único con escalones")
            starts.append((0.0, price))
            end = None
        elif kind in ("first", "next"):
            if end is None or (kind == "first" and starts):
                raise ParseError(f"Rango ≤{upper}: escalón fuera de lugar")
            starts.append((end, price))
            end += n
        elif kind == "excess":
            if end is not None and n != end:
                raise ParseError(f"Rango ≤{upper}: excedente de {n} kWh, pero los escalones suman {end}")
            if end is None and (not starts or n <= starts[-1][0]):
                raise ParseError(f"Rango ≤{upper}: excedente de {n} kWh fuera de orden")
            starts.append((float(n), price))
            end = None
    if not starts or end is not None:
        raise ParseError(f"Rango ≤{upper}: sin excedente")
    tiers = [
        {"sizeKwh": (starts[i + 1][0] - start) if i + 1 < len(starts) else None, "pricePerKwh": price}
        for i, (start, price) in enumerate(starts)
    ]
    return {"upperLimitKwh": upper, "fixedCharge": fixed, "tiers": tiers}


def parse_level(text: str, title: str) -> list[dict]:
    section = _section(text, title)
    ranges = []
    current = None  # (upper, labels, amounts)
    for m in TOKEN_RE.finditer(section):
        if m["range"]:
            if current:
                ranges.append(_build_range(*current))
            current = (_upper_limit(m["cond"]), [], [])
            continue
        if current is None:
            continue
        _, labels, amounts = current
        if m["cfm"]:
            labels.append(("cfm", None))
        elif m["perkwh"]:
            labels.append(("perkwh", None))
        elif m["first"]:
            labels.append(("first", int(m["first_n"])))
        elif m["next"]:
            labels.append(("next", int(m["next_n"])))
        elif m["excess"]:
            labels.append(("excess", int(m["excess_n"])))
        elif m["amount"]:
            if m["neg"]:
                raise ParseError(f"Importe negativo en {title!r}")
            amounts.append(parse_number(m["value"]))
    if current:
        ranges.append(_build_range(*current))
    _validate_ranges(ranges, title)
    return ranges


def _validate_ranges(ranges: list[dict], title: str) -> None:
    if len(ranges) < 2:
        raise ParseError(f"{title!r}: se esperaban al menos 2 rangos, hay {len(ranges)}")
    limits = [r["upperLimitKwh"] for r in ranges]
    if limits[-1] is not None or any(l is None for l in limits[:-1]):
        raise ParseError(f"{title!r}: el último rango tiene que ser el único sin límite")
    if limits[:-1] != sorted(limits[:-1]) or len(set(limits[:-1])) != len(limits) - 1:
        raise ParseError(f"{title!r}: los rangos no son crecientes")
    for r in ranges:
        if r["fixedCharge"] <= 0:
            raise ParseError(f"{title!r}: cargo fijo inválido")
        prices = [t["pricePerKwh"] for t in r["tiers"]]
        if any(p <= 0 for p in prices) or prices != sorted(prices):
            raise ParseError(f"{title!r}: precios inválidos o decrecientes en ≤{r['upperLimitKwh']}")


def header_month(text: str) -> tuple[int, int] | None:
    m = re.search(r"CUADRO TARIFARIO EPEC - (" + "|".join(MONTHS) + r") (\d{4})", text)
    return (int(m[2]), MONTHS.index(m[1]) + 1) if m else None


def parse(pdf: bytes, valid_from: date) -> dict:
    text = extract_text(pdf)
    month = header_month(text)
    if month is None:
        raise ParseError("No se encontró el mes del cuadro en el encabezado")
    if month != (valid_from.year, valid_from.month):
        raise ParseError(f"El encabezado dice {month} y el nombre del archivo {valid_from}")
    m = MIN_KWH_RE.search(text)
    minimum = float(m[1]) if m else 20.0
    return {
        "name": "EPEC Residencial",
        "validFrom": valid_from.isoformat(),
        "minimumBillableKwh": minimum,
        "levels": [
            {"id": level_id, "name": name, "ranges": parse_level(text, title)}
            for level_id, name, title in LEVELS
        ],
    }


def compare(new: dict, old: dict, max_change: float = 0.4) -> list[str]:
    """Diferencias mayores a ±max_change entre dos cuadros con la misma estructura."""
    problems = []
    old_levels = {l["id"]: l for l in old["levels"]}
    for level in new["levels"]:
        prev = old_levels.get(level["id"])
        if not prev or len(prev["ranges"]) != len(level["ranges"]):
            continue
        for r_new, r_old in zip(level["ranges"], prev["ranges"]):
            pairs = [(r_new["fixedCharge"], r_old["fixedCharge"])]
            if len(r_new["tiers"]) == len(r_old["tiers"]):
                pairs += [(a["pricePerKwh"], b["pricePerKwh"]) for a, b in zip(r_new["tiers"], r_old["tiers"])]
            for a, b in pairs:
                if b > 0 and abs(a / b - 1) > max_change:
                    problems.append(f"{level['id']} ≤{r_new['upperLimitKwh']}: {b} → {a}")
    return problems
