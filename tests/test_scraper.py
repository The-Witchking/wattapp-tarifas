import unittest
from datetime import date
from pathlib import Path

from scraper import drive, epec

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str, valid_from: date) -> dict:
    return epec.parse((FIXTURES / name).read_bytes(), valid_from)


def level(schedule: dict, level_id: str) -> dict:
    return next(l for l in schedule["levels"] if l["id"] == level_id)


def tiers(r: dict) -> list:
    return [(t["sizeKwh"], t["pricePerKwh"]) for t in r["tiers"]]


class DriveTest(unittest.TestCase):
    def test_parse_listing(self):
        files = drive.parse_listing((FIXTURES / "drive-listing.html").read_text(encoding="utf-8"))
        self.assertGreater(len(files), 50)
        newest = drive.latest(files)
        self.assertEqual(newest.valid_from, date(2026, 9, 1))
        self.assertEqual(newest.variant, "PARCIAL")
        self.assertIn("PARCIAL", newest.name)

    def test_parse_name_variants(self):
        f = drive.parse_name("x", "0521-079785_2024 - CT EPEC DISTRIB PLENO Y GD 01-OCT-24.pdf")
        self.assertEqual((f.variant, f.valid_from), ("PLENO", date(2024, 10, 1)))
        self.assertIsNone(drive.parse_name("x", "0521-082069_2025 - CT EPEC PLENO Y GD_compressed.pdf"))


class EpecTest(unittest.TestCase):
    def test_may_2026_matches_invoice(self):
        s = load("epec-parcial-2026-05-01.pdf", date(2026, 5, 1))
        r = level(s, "SEF")["ranges"][1]
        self.assertEqual(r["upperLimitKwh"], 500.0)
        self.assertEqual(r["fixedCharge"], 2296.0479)
        self.assertEqual(tiers(r), [(120.0, 142.13539), (30.0, 181.60687), (150.0, 181.60687), (None, 254.65565)])

    def test_sep_2026_structure(self):
        s = load("epec-parcial-2026-09-01.pdf", date(2026, 9, 1))
        self.assertEqual(s["validFrom"], "2026-09-01")
        self.assertEqual(s["minimumBillableKwh"], 20.0)
        self.assertEqual([l["id"] for l in s["levels"]], ["SEF", "NOSEF"])
        for l in s["levels"]:
            self.assertEqual([r["upperLimitKwh"] for r in l["ranges"]], [120.0, 500.0, 700.0, None])

        sef = level(s, "SEF")["ranges"]
        self.assertEqual(tiers(sef[0]), [(None, 111.87372)])
        self.assertEqual(sef[0]["fixedCharge"], 1847.307)
        self.assertEqual(tiers(sef[3]), [(120.0, 186.87803), (30.0, 238.74032), (50.0, 238.74032),
                                         (350.0, 336.25357), (None, 336.25357)])

        nosef = level(s, "NOSEF")["ranges"]
        self.assertEqual(nosef[1]["fixedCharge"], 2893.2813)
        self.assertEqual(tiers(nosef[1]), [(120.0, 248.21954), (None, 297.95809)])

    def test_feb_2026_intermediate_excess(self):
        # "Por cada kWh consumido:" como precio único y "El excedente de 150" como escalón intermedio
        s = load("epec-parcial-2026-02-01.pdf", date(2026, 2, 1))
        sef = level(s, "SEF")["ranges"]
        self.assertEqual([t[0] for t in tiers(sef[0])], [None])
        self.assertEqual([t[0] for t in tiers(sef[1])], [120.0, 30.0, 150.0, None])

    def test_header_month_must_match(self):
        with self.assertRaises(epec.ParseError):
            load("epec-parcial-2026-09-01.pdf", date(2026, 8, 1))

    def test_compare(self):
        may = load("epec-parcial-2026-05-01.pdf", date(2026, 5, 1))
        sep = load("epec-parcial-2026-09-01.pdf", date(2026, 9, 1))
        self.assertEqual(epec.compare(sep, may), [])
        doubled = {**sep, "levels": [{**l, "ranges": [{**r, "fixedCharge": r["fixedCharge"] * 2}
                                                        for r in l["ranges"]]} for l in sep["levels"]]}
        self.assertTrue(epec.compare(doubled, sep))


if __name__ == "__main__":
    unittest.main()
