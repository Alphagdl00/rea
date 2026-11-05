import unittest

from astra_core import classify_seniority, company_is_target, normalize_company_name, astra_score


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


if __name__ == "__main__":
    unittest.main()
