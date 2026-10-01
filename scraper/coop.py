"""Intérprete del cuadro tarifario de una cooperativa, con la plantilla de ERSEP.

La tabla residencial trae los precios SIN subsidio (nivel NOSEF). El nivel CON subsidio
(SEF) se deriva restando el diferencial de B.1 a los primeros N kWh del mes (Decreto
943/2025). Devuelve un `schedule` con los nombres de campo de `TariffSchedule` (Kotlin).
"""

import io
import re
from datetime import date

import pdfplumber

from scraper.epec import ParseError, parse_number

MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _rx(pattern: str) -> re.Pattern:
    """Regex tolerante a espacios perdidos: cada espacio del patrón acepta cero o más."""
    return re.compile(pattern.replace(" ", r"\s*"), re.IGNORECASE)


# Primer importe de la línea: si hay dos columnas (Facturación Normal / Prepaga) vale la normal.
AMOUNT_RE = re.compile(r"(-?\d+(?:\.\d{3})*,\d+)")
LABEL_TAIL_RE = re.compile(r"(?:\s*(?:\$/kWh|\$))+\s*$", re.IGNORECASE)
INCLUDED_RE = _rx(r"incluye (\d+) kWh")
CF_RE = _rx(r"^(?:el )?cargo fijo")
CF_ANY_RE = _rx(r"cargo fijo")
MINIMUM_RE = _rx(r"consumo mínimo (?:mensual|a facturar)?:? (\d+) kWh")

# Encabezados de rango: devuelven (desde, hasta). hasta=None es "sin límite".
RANGE_RES = [
    (_rx(r"(?:mayores|superen?) (?:a|los) (\d+) ?kWh.*?(?:no superen?|no excedan?) (?:a |los )?(\d+) ?kWh"),
     lambda m: (int(m[1]), int(m[2]))),
    (_rx(r"entre (?:los )?(\d+) ?(?:kWh)? y (\d+) ?kWh"), lambda m: (int(m[1]), int(m[2]))),
    (_rx(r"(?:inferior|inferiores|menor|menores)(?: o igual(?:es)?)? (?:a )?(\d+) ?kWh"), lambda m: (0, int(m[1]))),
    (_rx(r"desde (\d+) hasta (\d+) ?kWh"), lambda m: (int(m[1]), int(m[2]))),
    (_rx(r"no superen? (?:a |los )?(\d+) ?kWh"), lambda m: (0, int(m[1]))),
    (_rx(r"\(?hasta (\d+) ?kWh"), lambda m: (0, int(m[1]))),
    (_rx(r"(?:superiores|mayores|superen?|iguales o superiores) (?:a |a los |los )?(\d+) ?kWh"), lambda m: (int(m[1]), None)),
    (_rx(r"\(más de (\d+) ?kWh\)"), lambda m: (int(m[1]), None)),
]
RANGE_LINE_RE = _rx(r"(?:^|\W)(?:consumos?|suministros?)\b|^(?:para |por )?residencial")

# Escalones dentro de un rango: (tipo, regex). El orden importa.
TIER_RES = [
    ("from_to", _rx(r"(?:de |del )(\d+) (?:kWh )?a(?:l)? (\d+) kWh")),
    ("excess", _rx(r"(?:exc?edente|ex?cedente|exedente|más) de (\d+) kWh")),
    ("onward", _rx(r"de (\d+) kWh(?: por mes)?,? en (?:adelante|más)")),
    ("rest", _rx(r"^los restantes")),
    ("first", _rx(r"primeros (\d+) kWh")),
    ("upto", _rx(r"hasta (\d+) kWh")),
    ("next", _rx(r"siguientes (\d+) kWh")),
    ("single", _rx(r"^(?:por cada kWh|para la totalidad)")),
]

# Primera línea que ya no es la tarifa residencial común.
END_RE = _rx(
    r"^(?:\d+ ?-? ?)?(?:T ?1\.2|T\.? ?2|T2\.|TARIFA N. ?2|.*COMBINADA|COMERCIAL|COMERCIALES|GENERAL|"
    r"INDUSTRIAL|RURAL|RURALES|ESTACIONAL|PARA DEMANDAS MAYORES|ALUMBRADO|SERVICIO DE PEAJE|GOBIERNO|"
    r"MUNICIPALIDAD|RESIDENCIAL PREPAGA|POLICIA|ESCUELA|T ?1\.[2-9]|TARIFAS? SOCIAL)"
)

MAX_PDF_AGE_DAYS = 92


def extract_text(pdf: bytes, max_pages: int = 4) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages[:max_pages])


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def is_electric(text: str) -> bool:
    head = _squash(text[:1500])
    return "gerenciadeenergiaelectrica" in head or "consumosdeenergíaeléctrica" in head


def valid_from(text: str) -> date:
    m = re.search(r"apartirdel(\d{1,2})de([a-z]+)de(\d{4})", _squash(text[:2000]))
    if not m or m[2] not in MONTHS:
        raise ParseError("No se encontró la fecha de vigencia")
    return date(int(m[3]), MONTHS.index(m[2]) + 1, int(m[1]))


def subsidy(text: str) -> tuple[float, dict[int, int]]:
    """Diferencial por kWh (positivo) y volumen subsidiado por mes, según B.1."""
    s = _squash(text)
    start, end = s.find("b.1"), s.find("b.2")
    if start < 0 or end < start:
        raise ParseError("No se encontró el apartado B.1 (subsidio)")
    block = s[start:end]
    amount = re.search(r"-\$(\d+(?:\.\d{3})*,\d+)", block)
    if not amount:
        raise ParseError("No se encontró el diferencial del subsidio")
    volumes: dict[int, int] = {}
    for m in re.finditer(r"engeneral,alosprimeros[a-z]*\((\d+)\)kwh,?para(?:el|los)mes(?:es)?de(.*?)(?:decada|;)", block):
        for month in re.findall("|".join(MONTHS), m[2]):
            volumes[MONTHS.index(month) + 1] = int(m[1])
    if sorted(volumes) != list(range(1, 13)):
        raise ParseError(f"Volúmenes del subsidio incompletos: {volumes}")
    return parse_number(amount[1]), volumes


def residential_block(text: str) -> list[str]:
    k = text.find("BOMBEROS")
    if k < 0:
        raise ParseError("No se encontró el apartado F (bomberos)")
    # El apartado F termina con "Para la totalidad de los kWh, … facture potencia -$ X".
    tail = text[k:]
    last = None
    for m in re.finditer(r"-\$ ?\d+(?:\.\d{3})*,\d+", tail[:2500]):
        last = m
    if not last:
        raise ParseError("No se encontró el fin del apartado F")
    lines = [" ".join(l.split()) for l in tail[last.end():].splitlines()]
    block = []
    for line in lines:
        if not line:
            continue
        if block and END_RE.search(line):
            break
        block.append(line)
    if not block:
        raise ParseError("Tarifa residencial vacía")
    return block


def _range_of(label: str) -> tuple[int, int | None] | None:
    if not RANGE_LINE_RE.search(label) and "kWh" not in label and "kwh" not in label.lower():
        return None
    if not (RANGE_LINE_RE.search(label) or re.search(r"\((?:hasta|más de)", label, re.I)):
        return None
    for rx, fn in RANGE_RES:
        m = rx.search(label)
        if m:
            return fn(m)
    return None


def _tier_of(label: str) -> tuple[str, tuple] | None:
    for kind, rx in TIER_RES:
        m = rx.search(label)
        if m:
            return kind, tuple(int(g) for g in m.groups())
    return None


def parse_table(lines: list[str]) -> list[dict]:
    """Rangos crudos: {lower, upper, cf, tiers[(kind, nums, price)]}, en orden."""
    ranges: list[dict] = []
    current: dict | None = None
    cf: float | None = None  # último cargo fijo visto (lo heredan los subrangos)
    pending: str | None = None  # etiqueta partida en dos líneas ("Los primeros 50 KWh por" / "mes $ 336,32")

    def new_range(bounds):
        nonlocal current
        current = {"lower": bounds[0], "upper": bounds[1], "cf": None, "tiers": []}
        ranges.append(current)

    def complete() -> bool:
        """Ya hay un rango sin límite con precios: lo que sigue es otra tabla (otra localidad,
        trifásicos, no residentes…), que no se usa."""
        return any(r["upper"] is None and r["tiers"] for r in ranges)

    for line in lines:
        if complete() and (_range_of(line) is not None or CF_RE.search(line)):
            break
        am = AMOUNT_RE.search(line)
        label = LABEL_TAIL_RE.sub("", line[: am.start()]).strip() if am else line
        price = parse_number(am[1]) if am else None
        if price is not None and price < 0:
            raise ParseError(f"Importe negativo: {line!r}")
        if not label and pending:
            label, pending = pending, None
        elif pending and price is not None and not (_tier_of(label) or _range_of(label) or CF_RE.search(label)):
            label, pending = f"{pending} {label}", None

        if CF_RE.search(label):
            if price is None:
                continue  # "El Cargo Fijo Mensual / incluye 20 kWh/mes"
            cf = price
            if current is not None and not current["tiers"] and current["cf"] is None:
                current["cf"] = price
            continue
        bounds = _range_of(label)
        if bounds is not None:
            new_range(bounds)
            if price is not None:  # precio único en la línea del rango (Las Arrias)
                current["tiers"].append(("single", (), price))
            pending = None
            continue
        tier = _tier_of(label)
        if tier is None:
            if price is not None:
                raise ParseError(f"Línea con importe no reconocida: {line!r}")
            if re.search(r"kwh", label, re.I) and not re.search(r"^por cada kwh", _squash(label)):
                pending = label
            continue
        if price is None:
            if tier[0] == "single":
                continue  # "Por cada kWh consumido:" es un título
            pending = label
            continue
        if current is None:
            new_range((0, None))
        if current["cf"] is None:
            current["cf"] = cf
        current["tiers"].append((tier[0], tier[1], price))
        pending = None
    for r in ranges:
        if r["cf"] is None:
            r["cf"] = cf
    return [r for r in ranges if r["tiers"]]


def _tiers(raw: list[tuple], upper: int | None, label: str) -> list[dict]:
    """Escalones crudos → [{sizeKwh, pricePerKwh}] por punto de inicio."""
    starts: list[tuple[float, float]] = []
    end: float | None = 0.0
    for kind, nums, price in raw:
        if kind == "single":
            if raw != [(kind, nums, price)]:
                raise ParseError(f"{label}: precio único con escalones")
            starts.append((0.0, price))
            end = None
        elif kind == "from_to":
            a, b = nums
            if end is None:
                raise ParseError(f"{label}: escalón después del excedente")
            if a not in (end, end + 1):
                raise ParseError(f"{label}: escalón de {a} a {b} no sigue al anterior ({end})")
            starts.append((end, price))
            end = float(b)
        elif kind == "upto":  # "Hasta 120 kWh": desde donde terminó el anterior
            if end is None or nums[0] <= end:
                raise ParseError(f"{label}: escalón hasta {nums[0]} fuera de orden")
            starts.append((end, price))
            end = float(nums[0])
        elif kind in ("first", "next"):
            if end is None or (kind == "first" and starts):
                raise ParseError(f"{label}: escalón fuera de lugar")
            starts.append((end, price))
            end += nums[0]
        elif kind == "rest":  # "Los restantes": desde donde terminó el anterior
            if end is None:
                raise ParseError(f"{label}: escalón después del excedente")
            starts.append((end, price))
            end = None
        elif kind in ("excess", "onward"):
            n = float(nums[0])
            if end is not None and n == end + 1:  # "De 1401 kWh en adelante", "más de 201 kWh"
                n = end
            if end is not None and n != end:
                raise ParseError(f"{label}: excedente de {n:g} kWh, pero los escalones suman {end:g}")
            starts.append((n, price))
            end = None
    if not starts:
        raise ParseError(f"{label}: sin escalones")
    if end is not None and (upper is None or end < upper):
        raise ParseError(f"{label}: los escalones terminan en {end:g} kWh sin excedente")
    return [
        {"sizeKwh": (starts[i + 1][0] - s) if i + 1 < len(starts) else None, "pricePerKwh": p}
        for i, (s, p) in enumerate(starts)
    ]


def _with_included(tiers: list[dict], included: int) -> list[dict]:
    """Los primeros `included` kWh van sin cargo (los cubre el cargo fijo)."""
    return _split(tiers, included, lambda p: 0.0)


def _split(tiers: list[dict], limit: float, fn) -> list[dict]:
    """Aplica fn al precio de los primeros `limit` kWh, partiendo el escalón que cruza el límite."""
    out, start = [], 0.0
    for t in tiers:
        size = t["sizeKwh"]
        end = None if size is None else start + size
        if start >= limit:
            out.append(dict(t))
        elif end is not None and end <= limit:
            out.append({"sizeKwh": size, "pricePerKwh": fn(t["pricePerKwh"])})
        else:
            out.append({"sizeKwh": limit - start, "pricePerKwh": fn(t["pricePerKwh"])})
            out.append({"sizeKwh": None if end is None else end - limit, "pricePerKwh": t["pricePerKwh"]})
        start = end if end is not None else start
    return out


def build_ranges(raw: list[dict], included: int, has_fixed: bool = True) -> list[dict]:
    """has_fixed=False: la tabla no menciona cargo fijo (Las Vertientes, Charras), va en 0."""
    if len(raw) < 2:
        raise ParseError(f"Se esperaban al menos 2 rangos, hay {len(raw)}")
    ranges = []
    previous = 0
    for i, r in enumerate(raw):
        upper = r["upper"]
        label = f"Rango ≤{upper}" if upper is not None else f"Rango >{r['lower']}"
        cf = r["cf"] if has_fixed else 0.0
        if cf is None or cf < 0:
            raise ParseError(f"{label}: sin cargo fijo")
        if upper is None and i != len(raw) - 1:
            raise ParseError(f"{label}: rango sin límite que no es el último")
        if upper is not None and upper <= previous:
            raise ParseError(f"{label}: los rangos no son crecientes")
        # Los subrangos anidados ("que no superen los 500" dentro de "superiores a 120") empiezan
        # en 0; lo que no puede haber es un hueco.
        if r["lower"] > previous + 1:
            raise ParseError(f"{label}: empieza en {r['lower']} y el anterior termina en {previous}")
        tiers = _tiers(r["tiers"], upper, label)
        prices = [t["pricePerKwh"] for t in tiers]
        # Algunas cooperativas cobran más barato el excedente: solo se exige que sean positivos.
        if any(p <= 0 for p in prices):
            raise ParseError(f"{label}: precios inválidos")
        if included:
            tiers = _with_included(tiers, included)
        ranges.append({"upperLimitKwh": None if upper is None else float(upper), "fixedCharge": cf, "tiers": tiers})
        previous = upper if upper is not None else previous
    if ranges[-1]["upperLimitKwh"] is not None:
        raise ParseError("El último rango tiene límite")
    return _merge_equal_tiers(ranges)


def _merge_equal_tiers(ranges: list[dict]) -> list[dict]:
    """Une escalones consecutivos con el mismo precio (Carnerillo: 0–40, 41–80 y 81–120 a 335,44)."""
    for r in ranges:
        merged = []
        for t in r["tiers"]:
            if merged and merged[-1]["pricePerKwh"] == t["pricePerKwh"] and merged[-1]["sizeKwh"] is not None:
                size = merged[-1]["sizeKwh"]
                merged[-1] = {"sizeKwh": None if t["sizeKwh"] is None else size + t["sizeKwh"], "pricePerKwh": t["pricePerKwh"]}
            else:
                merged.append(dict(t))
        r["tiers"] = merged
    return ranges


def sef_level(ranges: list[dict], differential: float, volume: int) -> list[dict]:
    out = []
    for r in ranges:
        def discount(p, r=r):
            if p == 0:
                return 0.0  # kWh incluidos en el cargo fijo
            if p <= differential:
                raise ParseError(f"El subsidio deja un precio negativo en ≤{r['upperLimitKwh']}")
            return round(p - differential, 6)
        out.append({**r, "tiers": _split(r["tiers"], volume, discount)})
    return _merge_equal_tiers(out)


def title_case(name: str) -> str:
    small = {"de", "del", "la", "las", "los", "y", "e", "el"}
    words = name.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def parse(pdf: bytes, name: str, today: date, month: date | None = None) -> tuple[dict, date]:
    """Cuadro de la cooperativa para el mes `month` (por defecto, el de vigencia o el actual).

    Devuelve (schedule, fecha del PDF).
    """
    text = extract_text(pdf)
    if not is_electric(text):
        raise ParseError("No es un cuadro de energía eléctrica de ERSEP")
    pdf_date = valid_from(text)
    if (today - pdf_date).days > MAX_PDF_AGE_DAYS:
        raise ParseError(f"Cuadro vencido: vigente desde {pdf_date}")
    differential, volumes = subsidy(text)
    lines = residential_block(text)
    for line in lines:  # el título de la primera tabla
        if _range_of(line) is not None or CF_RE.search(line):
            break
        if re.search(r"RURAL", line, re.IGNORECASE):
            raise ParseError("La primera tarifa residencial es rural")
    joined = " ".join(lines)
    included = INCLUDED_RE.search(joined)
    minimum = MINIMUM_RE.search(joined)
    nosef = build_ranges(parse_table(lines), int(included[1]) if included else 0, bool(CF_ANY_RE.search(joined)))
    effective = month or max(pdf_date, today.replace(day=1))
    return {
        "name": title_case(name),
        "validFrom": effective.isoformat(),
        "minimumBillableKwh": float(minimum[1]) if minimum else 0.0,
        "levels": [
            {"id": "SEF", "name": "Con subsidio", "ranges": sef_level(nosef, differential, volumes[effective.month])},
            {"id": "NOSEF", "name": "Sin subsidio", "ranges": nosef},
        ],
    }, pdf_date
