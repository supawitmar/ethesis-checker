// เทสต์การนับ "ความพร้อมก่อนตรวจ" ของหน้าแบบฟอร์ม (templates/index.html) นอกเบราว์เซอร์
//
// ขั้น "วันดำเนินการ" อยู่ซ้อนอยู่ในการ์ดขั้นที่ 1 (เจ้าหน้าที่สั่ง ก.ย. 2569 ให้รวมการ์ด
// แต่ลำดับชิปคงเดิม) ตัวนับจึงต้องถือว่าช่องหนึ่งช่องเป็นของ "ขั้นในสุด" ที่ครอบมัน
// ไม่งั้นชิป "ไฟล์เล่ม" จะเขียวก็ต่อเมื่อกรอกวันที่ครบด้วย ซึ่งอ่านแล้วไม่มีทางเดาถูก
//
// ดึงเฉพาะสองฟังก์ชันนี้จากไฟล์จริงมารัน (ไม่ใช่สำเนาในเทสต์) แก้ของจริงเมื่อไหร่
// เทสต์นี้รันโค้ดที่แก้แล้วทันที
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function take(name) {
  const at = script.indexOf('function ' + name + '(');
  if (at < 0) throw new Error('หาฟังก์ชัน ' + name + ' ในหน้าแบบฟอร์มไม่เจอ');
  // ตัดถึงบรรทัด "}" ที่ระดับนอกสุด
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

// ---- ผังจำลอง: การ์ดขั้นที่ 1 มีช่องไฟล์ และมีบล็อกวันดำเนินการซ้อนอยู่ข้างใน ----
function field(owner, valid) {
  return { owner, validity: { valid },
           closest(sel) { return sel.split(', ').includes('#' + owner.id) ? owner : null; } };
}

function build(fileOk, datesOk) {
  const secDates = { id: 'sec-dates' };
  const secFiles = { id: 'sec-files' };
  const book = field(secFiles, fileOk);
  const queue = field(secDates, datesOk);
  const checked = field(secDates, datesOk);
  secDates.querySelectorAll = () => [queue, checked];
  secFiles.querySelectorAll = () => [book, queue, checked];   // ซ้อนอยู่ข้างใน
  const steps = [{ dataset: { sec: 'sec-files' }, classList: { toggle() {} } },
                 { dataset: { sec: 'sec-dates' }, classList: { toggle() {} } }];
  return {
    document: {
      querySelectorAll: (sel) => (sel === '#progress li' ? steps : []),
      getElementById: (id) => (id === 'sec-files' ? secFiles
                             : (id === 'sec-dates' ? secDates : null)),
    },
  };
}

const source = take('stepSelector') + '\n' + take('stepFields');

function ready(sandbox, id) {
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'index.html' });
  const fields = vm.runInContext(
    'stepFields(document.getElementById(' + JSON.stringify(id) + '))', sandbox);
  // เงื่อนไขเดียวกับ updateProgress: ต้องมีช่องของตัวเอง และทุกช่องต้องผ่าน
  return fields.length > 0 && fields.every((f) => f.validity.valid);
}

// กรอกวันที่ครบแต่ยังไม่ได้แนบไฟล์ → ชิปไฟล์เล่มต้องยังไม่ผ่าน ชิปวันดำเนินการผ่าน
check('ยังไม่แนบไฟล์ ชิปไฟล์เล่มต้องยังไม่ผ่าน', ready(build(false, true), 'sec-files'), false);
check('กรอกวันครบ ชิปวันดำเนินการต้องผ่าน', ready(build(false, true), 'sec-dates'), true);
// แนบไฟล์แล้วแต่ยังไม่กรอกวัน → ชิปไฟล์เล่มต้องผ่านโดยไม่รอวันที่
check('แนบไฟล์แล้ว ชิปไฟล์เล่มต้องผ่านทันที', ready(build(true, false), 'sec-files'), true);
check('ยังไม่กรอกวัน ชิปวันดำเนินการต้องยังไม่ผ่าน', ready(build(true, false), 'sec-dates'), false);
check('ครบทั้งคู่ ชิปไฟล์เล่มผ่าน', ready(build(true, true), 'sec-files'), true);
check('ครบทั้งคู่ ชิปวันดำเนินการผ่าน', ready(build(true, true), 'sec-dates'), true);

if (failures) {
  console.error(`\nindex.html progress tests: ${failures} ข้อไม่ผ่าน`);
  process.exit(1);
}
console.log('index.html progress tests passed');
