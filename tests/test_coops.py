import unittest
from datetime import date
from pathlib import Path

from scraper import coop, ersep
from scraper.epec import ParseError

FIXTURES = Path(__file__).parent / "fixtures" / "coops"
SEPT = date(2026, 9, 20)


def load(name: str, today: date = SEPT, month: date | None = None) -> dict:
    schedule, _ = coop.parse((FIXTURES / f"{name}-2026-09.pdf").read_bytes(), "COOPERATIVA DE PRUEBA", today, month)
    return schedule


def ranges(schedule: dict, level_id: str) -> list:
    level = next(l for l in schedule["levels"] if l["id"] == level_id)
    return [
        (r["upperLimitKwh"], r["fixedCharge"], [(t["sizeKwh"], t["pricePerKwh"]) for t in r["tiers"]])
        for r in level["ranges"]
    ]


class ErsepTest(unittest.TestCase):
    def test_provider_page(self):
        entry = ersep.parse_provider("achiras", (FIXTURES / "ersep-achiras.html").read_text(encoding="utf-8"))
        self.assertEqual(entry["name"], "COOPERATIVA DE ACHIRAS")
        self.assertEqual(entry["area"], "ACHIRAS, CUATRO VIENTOS, RODEO VIEJO, LA AGUADA")
        self.assertEqual(entry["users"], 1891)
        self.assertEqual(entry["driveId"], "1PH9tsQM2W1BkCaXB40jISjpcW0Pv5K2o")


class CoopTest(unittest.TestCase):
    def test_achiras(self):
        s = load("achiras")
        self.assertEqual(s["validFrom"], "2026-09-01")
        self.assertEqual(ranges(s, "NOSEF"), [
            (120.0, 980.626, [(None, 344.7042)]),
            (500.0, 980.626, [(120.0, 344.7042), (None, 349.5307)]),
            (700.0, 980.626, [(120.0, 346.3226), (None, 352.0868)]),
            (None, 980.626, [(120.0, 348.7937), (None, 354.5578)]),
        ])
        # Septiembre: −97,21 a los primeros 200 kWh.
        self.assertEqual(ranges(s, "SEF")[1], (500.0, 980.626, [(120.0, 247.4942), (80.0, 252.3207), (None, 349.5307)]))

    def test_carnerillo_subranges(self):
        s = load("carnerillo")
        limits = [r[0] for r in ranges(s, "NOSEF")]
        self.assertEqual(limits, [40.0, 80.0, 120.0, 500.0, 700.0, 1400.0, None])
        self.assertEqual(ranges(s, "NOSEF")[4], (700.0, 1564.16, [(None, 337.29)]))

    def test_las_acequias_included_kwh(self):
        # "El Cargo Fijo Mensual incluye 20 kWh/mes": esos kWh van a $ 0.
        s = load("las-acequias")
        self.assertEqual(ranges(s, "NOSEF")[0], (500.0, 1587.39, [(20.0, 0.0), (30.0, 336.32), (None, 339.73)]))
        self.assertEqual(ranges(s, "SEF")[0][2], [(20.0, 0.0), (30.0, 239.11), (150.0, 242.52), (None, 339.73)])

    def test_variants(self):
        self.assertEqual(ranges(load("monte-ralo"), "NOSEF")[:3], [
            (80.0, 2365.58, [(None, 348.21)]),
            (120.0, 2369.9, [(None, 349.15)]),
            (500.0, 2783.03, [(120.0, 350.1), (None, 353.92)]),
        ])
        self.assertEqual(ranges(load("las-arrias"), "NOSEF")[0], (500.0, 3838.94, [(None, 351.88)]))
        self.assertEqual(ranges(load("villa-ascasubi"), "NOSEF")[1],
                         (500.0, 3362.7965, [(120.0, 338.2184), (80.0, 341.4439), (None, 343.5943)]))
        # Dos columnas (Facturación Normal / Prepaga): vale la normal.
        self.assertEqual(ranges(load("justiniano-posse"), "NOSEF")[3], (500.0, 3135.63, [(120.0, 334.39), (None, 340.11)]))

    def test_subsidy_follows_the_month(self):
        # Octubre: el PDF de septiembre sigue vigente, pero el subsidio cubre 150 kWh.
        s = load("achiras", today=date(2026, 10, 3))
        self.assertEqual(s["validFrom"], "2026-10-01")
        self.assertEqual(ranges(s, "SEF")[1][2], [(120.0, 247.4942), (30.0, 252.3207), (None, 349.5307)])

    def test_outdated_pdf(self):
        with self.assertRaises(ParseError):
            load("achiras", today=date(2027, 1, 15))

    def test_subsidy_header(self):
        text = coop.extract_text((FIXTURES / "achiras-2026-09.pdf").read_bytes())
        differential, volumes = coop.subsidy(text)
        self.assertEqual(differential, 97.21)
        self.assertEqual((volumes[1], volumes[3], volumes[9], volumes[10]), (300, 150, 200, 150))

    def test_title_case(self):
        self.assertEqual(coop.title_case("COOPERATIVA DE LAS ACEQUIAS"), "Cooperativa de las Acequias")


if __name__ == "__main__":
    unittest.main()
