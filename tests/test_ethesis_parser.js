const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
const parserSource = script.slice(0, script.indexOf('let ethSourceHtml'));
const parseEthesisText = new Function(`${parserSource}; return parseEthesisText;`)();

const sample = `
**รหัสนักศึกษา** 6438046 EGCE/M
**ชื่อ-สกุล** นาย วิสิฐ กวยะปาณิก
Mr. WISIT KAWAYAPANIK
**หลักสูตร** วิศวกรรมศาสตรมหาบัณฑิต สาขาวิชาวิศวกรรมโยธา (หลักสูตรนานาชาติ) [3805M02G]
**ชื่อปริญญา**
วิศวกรรมศาสตรมหาบัณฑิต(วิศวกรรมโยธา)
MASTER OF ENGINEERING(CIVIL ENGINEERING)
**แผนการศึกษา** วิทยานิพนธ์
**ภาษาที่เขียน** ภาษาอังกฤษ
**ชื่อหัวข้อภาษาไทย** การศึกษาพฤติกรรมและสมรรถนะของบังเกอร์ UHPC ภายใต้แรงระเบิด
**ชื่อหัวข้อภาษาอังกฤษ** Blast Protection Performance of UHPC Bunkers
**วันที่สอบผ่าน** 7 พฤษภาคม 2569
ล่าสุด รูปแบบที่ 1: รูปแบบดั้งเดิม รูปแบบที่ 2: รูปแบบจากผลงานตีพิมพ์
`;

const actual = parseEthesisText(sample);
// ชื่อช่องต้องตรงกับที่ main.py รับจากฟอร์ม (degree_cover_* / degree_sig_* / degree_abbr_*)
// ชื่อชุดเก่า (degree_source / degree / degree_abbr) เลิกใช้ไปตั้งแต่ ส.ค. 2569
// ปกของเล่มใช้ตัวพิมพ์ใหญ่ทั้งหมด ส่วนหน้าลงนามใช้ตัวพิมพ์ใหญ่ต้นคำ จึงแยกกันคนละช่อง
const expected = {
  student_id: '6438046',
  student_name_th: 'วิสิฐ กวยะปาณิก',
  student_name: 'WISIT KAWAYAPANIK',
  title_th: 'การศึกษาพฤติกรรมและสมรรถนะของบังเกอร์ UHPC ภายใต้แรงระเบิด',
  title_en: 'Blast Protection Performance of UHPC Bunkers',
  program_language: 'international',
  program: 'วิศวกรรมศาสตรมหาบัณฑิต สาขาวิชาวิศวกรรมโยธา (หลักสูตรนานาชาติ)',
  degree_cover_en: 'MASTER OF ENGINEERING (CIVIL ENGINEERING)',
  degree_sig_en: 'Master of Engineering (Civil Engineering)',
  degree_abbr_en: 'M.Eng. (CIVIL ENGINEERING)',
  degree_cover_th: 'วิศวกรรมศาสตรมหาบัณฑิต (วิศวกรรมโยธา)',
  degree_sig_th: 'วิศวกรรมศาสตรมหาบัณฑิต (วิศวกรรมโยธา)',
  degree_abbr_th: 'วศ.ม. (วิศวกรรมโยธา)',
  exam_date: '7 May 2026',
  year: '2026',
  doc_type: 'THESIS'
};

// เทียบทีละช่อง ไม่ใช่ JSON.stringify ทั้งก้อน เพราะแบบนั้นลำดับคีย์เปลี่ยนก็พังทั้งที่
// ค่าถูกทุกช่อง และตอนพังก็อ่านไม่ออกว่าช่องไหนต่าง
const wrong = [];
Object.keys(expected).forEach(function (key) {
  if (actual[key] !== expected[key]) {
    wrong.push(`  ${key}\n    ได้      ${actual[key]}\n    ต้องเป็น ${expected[key]}`);
  }
});
Object.keys(actual).forEach(function (key) {
  if (!(key in expected)) wrong.push(`  ${key} — ช่องที่ไม่ได้คาดไว้ (${actual[key]})`);
});
if (wrong.length) {
  console.error('Unexpected parser result:\n' + wrong.join('\n'));
  process.exit(1);
}

const markedLatest = sample.replace(
  'ล่าสุด รูปแบบที่ 1: รูปแบบดั้งเดิม รูปแบบที่ 2: รูปแบบจากผลงานตีพิมพ์',
  'ล่าสุด ☑ รูปแบบที่ 2: รูปแบบจากผลงานตีพิมพ์ รูปแบบที่ 1: รูปแบบดั้งเดิม'
);
if (parseEthesisText(markedLatest).format !== '2') {
  console.error('The selected format from the latest row was not detected');
  process.exit(1);
}

const thaiBook = sample
  .replace('(หลักสูตรนานาชาติ)', '(หลักสูตรไทย)')
  .replace('**ภาษาที่เขียน** ภาษาอังกฤษ', '**ภาษาที่เขียน** ภาษาไทย');
const thaiResult = parseEthesisText(thaiBook);
if (thaiResult.exam_date !== '7 พฤษภาคม 2569' || thaiResult.year !== '2569') {
  console.error('Thai-book date should remain in the Buddhist Era:', thaiResult);
  process.exit(1);
}

console.log('eThesis parser sample passed');
