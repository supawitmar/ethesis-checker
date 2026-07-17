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
const expected = {
  student_id: '6438046',
  student_name_th: 'วิสิฐ กวยะปาณิก',
  student_name: 'WISIT KAWAYAPANIK',
  title_th: 'การศึกษาพฤติกรรมและสมรรถนะของบังเกอร์ UHPC ภายใต้แรงระเบิด',
  title_en: 'Blast Protection Performance of UHPC Bunkers',
  program_language: 'international',
  degree_source: 'MASTER OF ENGINEERING(CIVIL ENGINEERING)',
  degree: 'Master of Engineering (Civil Engineering)',
  degree_abbr: 'M.Eng. (CIVIL ENGINEERING)',
  exam_date: '7 May 2026',
  year: '2026',
  doc_type: 'THESIS'
};

if (JSON.stringify(actual) !== JSON.stringify(expected)) {
  console.error('Unexpected parser result:', actual);
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
