// วันที่สอบจากหน้าประวัตินักศึกษา MIS ปรับรูปตามภาษาหลักสูตร (อัปเดต 1.16 · ข้อมูลสมมติ)
//
// ดึงฟังก์ชันจริงจาก templates/index.html มารัน — เซิร์ฟเวอร์ส่งวันที่สองรูป หน้าเว็บแค่เลือกชุด ไม่มีตารางเดือน
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function take(name) {
  const at = script.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('หาฟังก์ชัน ' + name + ' ในหน้าแบบฟอร์มไม่เจอ');
  let depth = 0;
  for (let i = script.indexOf('{', at); i < script.length; i += 1) {
    if (script[i] === '{') depth += 1;
    if (script[i] === '}') {
      depth -= 1;
      if (depth === 0) return script.slice(at, i + 1);
    }
  }
  throw new Error('ฟังก์ชัน ' + name + ' ไม่จบ');
}

let failures = 0;
function check(label, actual, wanted) {
  if (String(actual) !== String(wanted)) {
    failures += 1;
    console.error(`FAIL ${label}\n  ได้   ${actual}\n  ต้องเป็น ${wanted}`);
  }
}

const context = {};
vm.createContext(context);
vm.runInContext(take('applyExamDateVariant'), context);
const apply = context.applyExamDateVariant;

const VARIANTS = {thai: {exam_date: '7 พฤศจิกายน 2569', year: '2569'},
                  english: {exam_date: '7 November 2026', year: '2026'}};
function field(value, green) {
  return {value, classList: {contains: (name) => name === 'autofilled' && green}};
}
function fields(date, year, green = true) {
  return {exam_date: field(date, green), year: field(year, green)};
}

// เลือกไทย-อังกฤษ → อังกฤษ ค.ศ. · กลับเป็นไทย → ไทย พ.ศ. · นานาชาติ → อังกฤษ · ไปกลับหลายรอบไม่เพี้ยน
const f = fields('7 พฤศจิกายน 2569', '2569');
check('thai_english เปลี่ยน', apply(VARIANTS, 'thai_english', f), true);
check('thai_english วันที่', f.exam_date.value, '7 November 2026');
check('thai_english ปี', f.year.value, '2026');
apply(VARIANTS, 'thai', f);
check('thai วันที่', f.exam_date.value, '7 พฤศจิกายน 2569');
check('thai ปี', f.year.value, '2569');
apply(VARIANTS, 'international', f);
apply(VARIANTS, 'thai', f);
apply(VARIANTS, 'thai_english', f);
check('ไปกลับหลายรอบ', f.exam_date.value + ' / ' + f.year.value, '7 November 2026 / 2026');
check('ภาษาเดิม ไม่เปลี่ยนอะไร', apply(VARIANTS, 'international', f), false);

// แก้เองแล้วไม่ทับ (ค่าที่ไม่ใช่สองรูปของระบบ) — ทีละช่อง
const edited = fields('8 November 2026', '2569');
apply(VARIANTS, 'thai_english', edited);
check('วันที่ที่แก้เองไม่ถูกทับ', edited.exam_date.value, '8 November 2026');
check('ปีที่ยังเป็นของระบบยังปรับ', edited.year.value, '2026');

// ช่องที่ไม่ใช่สีเขียว (ไม่ได้เติมจากระบบ) ไม่แตะ
const typed = fields('7 พฤศจิกายน 2569', '2569', false);
check('ช่องไม่เขียว', apply(VARIANTS, 'thai_english', typed), false);
check('ช่องไม่เขียว ค่าเดิม', typed.exam_date.value, '7 พฤศจิกายน 2569');

// ทาง eThesis / ยังไม่เลือกภาษา: ไม่มีสองรูป = ไม่ทำอะไร
const eth = fields('7 November 2026', '2026');
check('ไม่มีสองรูป', apply(null, 'thai', eth), false);
check('ไม่มีสองรูป ค่าเดิม', eth.exam_date.value, '7 November 2026');
check('ยังไม่เลือกภาษา', apply(VARIANTS, '', fields('7 พฤศจิกายน 2569', '2569')), false);

// ตัวเลือกรูปวันที่ไม่มีตารางเดือน และหน้าเว็บไม่มีชื่อเดือนอังกฤษ (การแปลงอยู่ที่เซิร์ฟเวอร์ที่เดียว)
// ส่วน thaiDate ที่มีชื่อเดือนไทยอยู่ก่อนแล้ว ใช้แสดงวันดำเนินการเป็นภาษาไทยเท่านั้น ไม่เกี่ยวกับวันที่สอบ
const picker = take('applyExamDateVariant') + take('syncExamDateToLanguage');
for (const month of ['มกราคม', 'พฤศจิกายน', 'January', 'November', 'THAI_MONTHS']) {
  check('ตัวเลือกรูปวันที่ไม่มีชื่อเดือน ' + month, picker.includes(month), false);
}
for (const month of ['January', 'February', 'November', 'December']) {
  check('หน้าเว็บไม่มีชื่อเดือนอังกฤษ ' + month, script.includes(month), false);
}

if (failures) {
  console.error(`${failures} ข้อไม่ผ่าน`);
  process.exit(1);
}
console.log('exam date variant tests passed');
