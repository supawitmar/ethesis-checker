import unittest

from checker import NOT_CHECKED, Report, fuzzy_contains, norm, resolve_option


class NormalizationTests(unittest.TestCase):
    def test_thai_combining_mark_reordering_is_normalized(self):
        self.assertEqual(norm("บทคัดย่อ"), norm("บทคดัยอ่"))

    def test_exact_fuzzy_match(self):
        found, score = fuzzy_contains(norm("สมชาย ใจดี"), "สมชาย ใจดี")
        self.assertTrue(found)
        self.assertEqual(score, 1.0)

    def test_empty_needle_is_not_a_match(self):
        self.assertEqual(fuzzy_contains(norm("ข้อความ"), ""), (False, 0.0))


class ReportTests(unittest.TestCase):
    def test_red_takes_precedence(self):
        report = Report()
        report.add("ORANGE", "-", "x", "x", "x")
        report.add("RED", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "ไม่ผ่าน")

    def test_orange_means_pending(self):
        report = Report()
        report.add("ORANGE", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "รอยืนยัน")

    def test_yellow_can_pass(self):
        report = Report()
        report.add("YELLOW", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "ผ่าน")

    def test_out_of_scope_items_are_declared(self):
        self.assertGreaterEqual(len(NOT_CHECKED), 4)


class OptionResolutionTests(unittest.TestCase):
    def test_free_mode_uses_selected_option(self):
        body = [(1, "Custom chapter title", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "2"}, "free"), 2)

    def test_strict_mode_infers_published_option(self):
        body = [(1, "SUMMARY", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "1"}, "strict"), 2)

    def test_strict_mode_defaults_to_traditional_option(self):
        body = [(1, "INTRODUCTION", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "2"}, "strict"), 1)


if __name__ == "__main__":
    unittest.main()
