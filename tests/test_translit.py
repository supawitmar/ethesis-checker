# -*- coding: utf-8 -*-
"""unittest ของการถอดชื่อกรรมการไทยเป็นอังกฤษ (ไม่ใช้ API)

ทุกคู่ชื่อในไฟล์นี้มาจากเล่มจริง — ชื่อไทยจาก eThesis กับตัวสะกดอังกฤษที่พิมพ์
บนหน้าลงนามจริง จึงเป็นการล็อกพฤติกรรมกับข้อมูลจริง ไม่ใช่เคสสมมติ
"""
import difflib
import unittest

import translit
from checker import (Report, _check_abstract_committees,
                     _committee_keyname, _strip_committee_title)

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


class EnglishAbstractUsesTransliteratedNames(unittest.TestCase):
    """หน้าบทคัดย่ออังกฤษต้องเทียบกับ 'ชื่อที่ถอดแล้ว' ของคณะกรรมการที่ปรึกษาจาก eThesis

    ข้อมูลอนุมัติเป็นชื่อไทย แต่หน้านี้พิมพ์ชื่ออังกฤษ ถ้าไม่ถอดก่อนก็เทียบไม่ได้
    """

    ADVISORY = [{"name": "ยอด สุขะมงคล"}, {"name": "ทวีศักดิ์ สมานชื่น"}]
    PAGE = ("ADVISORY COMMITTEE: YOD SUKAMONGKOL, Ph.D., "
            "TAWEESAK SAMANCHUEN, Ph.D.\n\nABSTRACT\n")

    def _run(self, name_en, translation_ok, reason):
        """คืน (ข้อที่ฟ้องเรื่องกรรมการ, จำนวนที่เป็นสีแดง)"""
        rep = Report()
        _check_abstract_committees(
            rep, {"advisory": self.ADVISORY}, [0], [], [self.PAGE],
            lambda i: "หน้า iv", name_en, translation_ok, reason)
        picked = {z: [it for it in rep.zones[z] if "กรรมการ" in it["location"]]
                  for z in ("RED", "ORANGE")}
        return picked["RED"] + picked["ORANGE"], len(picked["RED"])

    @unittest.skipUnless(translit.enabled(), "ยังไม่ได้ติดตั้ง pythainlp")
    def test_matching_names_pass_quietly(self):
        rom = translit.romanize_names([m["name"] for m in self.ADVISORY])
        name_en = dict(zip((m["name"] for m in self.ADVISORY), rom))
        issues, _reds = self._run(name_en, True, "offline")
        self.assertEqual(issues, [])

    def test_reports_when_transliteration_unavailable(self):
        """เดิมข้ามไปเงียบ ๆ เจ้าหน้าที่จึงไม่รู้ว่าหน้านี้ยังไม่ได้ตรวจชื่อ"""
        issues, reds = self._run({}, False, "no_tool")
        self.assertEqual(len(issues), 1)
        self.assertEqual(reds, 0)          # เป็นส้ม ไม่ใช่แดง
        self.assertIn("เทียบชื่ออัตโนมัติไม่ได้", issues[0]["found"])
        # เป็นข้อจำกัดของระบบ ไม่ใช่จุดที่นักศึกษาแก้ได้
        self.assertTrue(issues[0]["system_note"])

    @unittest.skipUnless(translit.enabled(), "ยังไม่ได้ติดตั้ง pythainlp")
    def test_wrong_name_is_orange_not_red_on_offline_path(self):
        """ถอดเสียงเองเป็นการเทียบเคียง ห้ามฟันธงแดง"""
        name_en = {"ยอด สุขะมงคล": "somchai jaidee",
                   "ทวีศักดิ์ สมานชื่น": "taweesak samanchuen"}
        issues, reds = self._run(name_en, True, "offline")
        self.assertTrue(issues, "ควรฟ้องว่าชื่อไม่ตรง")
        self.assertEqual(reds, 0, "ห้ามมีสีแดงจากคำที่ถอดเสียงเอง")


class RomanizeEdgeCases(unittest.TestCase):

    def test_empty_input(self):
        self.assertEqual(translit.romanize_names([]), [])
        self.assertEqual(translit.romanize_names(None), [])
        self.assertEqual(translit.romanize_names(["", "  "]), [])

    def test_threshold_has_a_safe_default(self):
        self.assertGreaterEqual(translit.match_threshold(), 0.60)


if __name__ == "__main__":
    unittest.main()
