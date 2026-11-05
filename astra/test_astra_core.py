import unittest

from astra_core import (
    _occ_search_url,
    _parse_occ_cards,
    astra_score,
    compensation_fit,
    monthly_salary_mxn,
    city_bucket,
    classify_seniority,
    company_is_target,
    extract_salary_from_text,
    is_mexico_location,
    normalize_company_name,
)


class AstraRankingTests(unittest.TestCase):
    def test_seniority(self):
        cases = {
            "CFO": "Executive",
            "Head of Finance Mexico": "Executive",
            "Finance Director": "Executive",
            "Senior Finance Manager": "Senior",
            "Plant Controller": "Manager",
            "FP&A Manager": "Manager",
            "Financial Analyst": "IC",
            "Accounting Assistant": "IC",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify_seniority(title), expected)

    def test_target_company_alias(self):
        targets = {
            normalize_company_name("Baxter"),
            normalize_company_name("Hewlett Packard Enterprise"),
            normalize_company_name("Johnson & Johnson"),
        }
        self.assertTrue(company_is_target("Baxter International", targets))
        self.assertTrue(company_is_target("Hewlett Packard Enterprise", targets))
        self.assertTrue(company_is_target("Johnson and Johnson", targets))
        self.assertFalse(company_is_target("Unknown Company", targets))

    def test_score_prefers_executive_target_role(self):
        targets = {normalize_company_name("Baxter")}
        executive = {
            "title": "Finance Director",
            "company": "Baxter",
            "seniority": "Executive",
            "source": "workday",
            "sources": "workday",
            "freshness": "HOT 0–24h",
            "salary": "",
        }
        analyst = {
            "title": "Financial Analyst",
            "company": "Other",
            "seniority": "IC",
            "source": "linkedin",
            "sources": "linkedin",
            "freshness": "HOY (sin hora)",
            "salary": "",
        }
        self.assertGreater(astra_score(executive, targets)[0], astra_score(analyst, targets)[0])


class AstraCompensationFitTests(unittest.TestCase):
    def test_monthly_salary_normalization(self):
        row = {
            "min_amount": 70000,
            "max_amount": 70000,
            "currency": "MXN",
            "interval": "mensual",
        }
        self.assertEqual(monthly_salary_mxn(row), 70000)

    def test_annual_salary_normalization(self):
        row = {
            "min_amount": 1200000,
            "max_amount": 1200000,
            "currency": "MXN",
            "interval": "anual",
        }
        self.assertEqual(monthly_salary_mxn(row), 100000)

    def test_low_cfo_salary_gets_max_penalty(self):
        row = {
            "title": "CFO / Director de Finanzas",
            "seniority": "Executive",
            "min_amount": 70000,
            "max_amount": 70000,
            "currency": "MXN",
            "interval": "mensual",
        }
        adjustment, label, monthly, floor = compensation_fit(
            row, {"Executive": 140000, "Senior": 110000, "Manager": 90000}
        )
        self.assertEqual(monthly, 70000)
        self.assertEqual(floor, 140000)
        self.assertEqual(adjustment, -40)
        self.assertIn("Muy bajo", label)

    def test_missing_salary_has_no_penalty(self):
        row = {"title": "Finance Director", "seniority": "Executive"}
        adjustment, label, monthly, floor = compensation_fit(
            row, {"Executive": 140000, "Senior": 110000, "Manager": 90000}
        )
        self.assertEqual(adjustment, 0)
        self.assertIsNone(monthly)
        self.assertEqual(label, "Sin ajuste")


class AstraSalaryTests(unittest.TestCase):
    def test_mxn_salary_range_from_description(self):
        hit = extract_salary_from_text(
            "Compensación: $89,000 a $108,000 MXN mensuales, según experiencia."
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["currency"], "MXN")
        self.assertEqual(hit["min_amount"], 89000)
        self.assertEqual(hit["max_amount"], 108000)
        self.assertEqual(hit["interval"], "mensual")

    def test_usd_salary_range(self):
        hit = extract_salary_from_text(
            "Base salary USD 120,000 - 150,000 annual plus bonus."
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["currency"], "USD")
        self.assertEqual(hit["min_amount"], 120000)
        self.assertEqual(hit["max_amount"], 150000)
        self.assertEqual(hit["interval"], "anual")

    def test_ignores_unlabelled_numbers_in_strict_mode(self):
        self.assertIsNone(extract_salary_from_text("30 días de aguinaldo y 100% prima vacacional."))


class AstraMexicoTests(unittest.TestCase):
    def test_region_specificity(self):
        self.assertEqual(city_bucket("Los Cabos, Baja California Sur, Mexico"), "Baja California Sur")
        self.assertEqual(city_bucket("Monterrey, Nuevo León, México"), "Monterrey")
        self.assertEqual(city_bucket("León, Guanajuato, México"), "Guanajuato")
        self.assertEqual(city_bucket("Querétaro, Querétaro, Mexico"), "Querétaro")

    def test_mexico_guard(self):
        self.assertTrue(is_mexico_location("Querétaro, México"))
        self.assertFalse(is_mexico_location("Albuquerque, New Mexico, USA"))


class AstraOccTests(unittest.TestCase):
    def test_occ_url_shapes(self):
        self.assertEqual(
            _occ_search_url("gerente de finanzas", None, 1),
            "https://www.occ.com.mx/empleos/de-gerente-de-finanzas/",
        )
        self.assertEqual(
            _occ_search_url("finanzas", "Nuevo León", 2),
            "https://www.occ.com.mx/empleos/de-finanzas-pagina-2/en-nuevo-leon/",
        )

    def test_occ_card_parser(self):
        fixture = """
        <div class="card-job-offer is-highlighted" data-id='21319670' id="jobcard-21319670">
          <span>Hoy</span>
          <h2 class="text-grey-900">Finance Manager</h2>
          <span class="mr-2 text-grey-900 font-base font-light">$ 89,000 - $ 108,000 Mensual</span>
          <div class="h-[21px] flex items-center gap-1">
            <span class="text-grey-900 no-underline">Empresa Industrial</span>
          </div>
          <div class="no-alter-loc-text mt-1">
            <span></span><p>Apodaca, Nuevo Le&#xF3;n</p>
          </div>
        </div>
        """
        cards = _parse_occ_cards(fixture)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["title"], "Finance Manager")
        self.assertEqual(card["company"], "Empresa Industrial")
        self.assertEqual(card["location"], "Apodaca, Nuevo León")
        self.assertEqual(card["min_amount"], 89000)
        self.assertEqual(card["max_amount"], 108000)
        self.assertIsNotNone(card["posted_at"])


if __name__ == "__main__":
    unittest.main()
