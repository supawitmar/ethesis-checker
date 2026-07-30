# -*- coding: utf-8 -*-
"""unittest ของการถอดชื่อกรรมการไทยเป็นอังกฤษ (ไม่ใช้ API)

ทุกคู่ชื่อในไฟล์นี้มาจากเล่มจริง — ชื่อไทยจาก eThesis กับตัวสะกดอังกฤษที่พิมพ์
บนหน้าลงนามจริง จึงเป็นการล็อกพฤติกรรมกับข้อมูลจริง ไม่ใช่เคสสมมติ
"""
import difflib
import unittest

import translit
from checker import _committee_keyname, _strip_committee_title

# (ชื่อไทยใน eThesis, ตัวสะกดอังกฤษที่พิมพ์บนหน้าลงนามจริง)
REAL_PAIRS = [
    ("วิริชดา ปานงาม", "Wirichada Pan-Ngum"),
    ("นพ. กิตติยศ ภู่วรวรรณ", "Kittiyod Poovorawan"),
    ("งามพล สุนทรวรสิริ", "Ngamphol Soonthornworasiri"),
    ("พันเอก ศักรินทร์ จิรพงศธร", "Col. Sakkarin Chirapongsathorn"),
    ("นพ. พิสิฐ ตั้งกิจวานิชย์", "Pisit Tangkijvanich"),
    ("นริศรา จันทราทิตย์", "Narisara Chantratita"),
    ("นิตยา อินทราวัฒนา", "Nitaya Indrawattana"),
    ("อมรรัตน์ อรุณนวล", "Amornrat Aroonnual"),
    ("ประสาท กิตตะคุปต์", "Prasat Kittakoop"),
    ("ยอด สุขะมงคล", "Yod Sukamongkol"),
    ("ทวีศักดิ์ สมานชื่น", "Taweesak Samanchuen"),
    ("เกียรติศักดิ์ ศรีตระกูลชัย", "Kiattisak Sritrakulchai"),
    ("ศุภชัญ ราษฎร์ศิริ", "Supphachan Rajsiri"),
]


def ratio(a, b):
    """ใช้ตรรกะเทียบตัวเดียวกับ checker เป๊ะ ๆ"""
    return difflib.SequenceMatcher(
        None, _committee_keyname(a, True), _committee_keyname(b, True)).ratio()


class StripTitleBeforeRomanizing(unittest.TestCase):
    """คำนำหน้าต้องถูกตัดทั้งฝั่งไทยและอังกฤษ ไม่งั้นเทียบไม่ตรง"""

    def test_thai_medical_and_military_titles(self):
        for name, expected in (
            ("นพ. กิตติยศ ภู่วรวรรณ", "กิตติยศ ภู่วรวรรณ"),
            ("พันเอก ศักรินทร์ จิรพงศธร", "ศักรินทร์ จิรพงศธร"),
            ("แพทย์หญิง นภา ปริญญานิติกูล", "นภา ปริญญานิติกูล"),
            ("พญ. สารนาถ ล้อพูลศรี", "สารนาถ ล้อพูลศรี"),
        ):
            self.assertEqual(_strip_committee_title(name), expected)

    def test_english_military_title(self):
        self.assertEqual(_strip_committee_title("Col. Sakkarin Chirapongsathorn"),
                         "Sakkarin Chirapongsathorn")

    def test_plain_name_untouched(self):
        self.assertEqual(_strip_committee_title("Kittiyod Poovorawan"),
                         "Kittiyod Poovorawan")


@unittest.skipUnless(translit.enabled(), "ยังไม่ได้ติดตั้ง pythainlp")
class RomanizeRealNames(unittest.TestCase):
    """ถอดชื่อแล้วต้องเทียบกับตัวสะกดจริงในเล่มได้ตามเกณฑ์ของ engine ที่ใช้อยู่"""

    @classmethod
    def setUpClass(cls):
        cls.thai = [t for t, _ in REAL_PAIRS]
        cls.eng = [e for _, e in REAL_PAIRS]
        cls.rom = translit.romanize_names(
            [_strip_committee_title(t) or t for t in cls.thai])
        cls.threshold = translit.match_threshold()

    def test_returns_one_spelling_per_name(self):
        self.assertEqual(len(self.rom), len(REAL_PAIRS))
        self.assertTrue(all(s.strip() for s in self.rom))

    def test_no_false_positive_between_different_people(self):
        """คนละคนต้องไม่ทะลุเกณฑ์ — สำคัญกว่าการจับถูกครบ

        ถ้าข้อนี้พัง ระบบจะปล่อยผ่านเล่มที่กรรมการผิดคน
        """
        worst = max(ratio(self.rom[i], self.eng[j])
                    for i in range(len(REAL_PAIRS))
                    for j in range(len(REAL_PAIRS)) if i != j)
        self.assertLess(worst, self.threshold,
                        f"คู่คนละคนได้ ratio {worst:.2f} ทะลุเกณฑ์ {self.threshold}")

    def test_matches_most_real_names(self):
        """จับถูกอย่างน้อย 12 จาก 13 คู่ (ที่เหลือลงส้มให้เจ้าหน้าที่ตรวจ ไม่ใช่ฟ้องผิด)"""
        hit = sum(1 for i in range(len(REAL_PAIRS))
                  if ratio(self.rom[i], self.eng[i]) >= self.threshold)
        self.assertGreaterEqual(hit, 12, f"จับถูกแค่ {hit}/{len(REAL_PAIRS)}")

    def test_threshold_matches_engine(self):
        self.assertIn(translit.engine_name(), ("thai2rom_onnx", "royin"))
        self.assertGreaterEqual(self.threshold, 0.60)


class RomanizeEdgeCases(unittest.TestCase):

    def test_empty_input(self):
        self.assertEqual(translit.romanize_names([]), [])
        self.assertEqual(translit.romanize_names(None), [])
        self.assertEqual(translit.romanize_names(["", "  "]), [])

    def test_threshold_has_a_safe_default(self):
        self.assertGreaterEqual(translit.match_threshold(), 0.60)


if __name__ == "__main__":
    unittest.main()
