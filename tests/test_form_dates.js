// เทสต์วันที่ของหน้าแบบฟอร์ม (templates/index.html) นอกเบราว์เซอร์
//
// สองเรื่อง
//   1. thaiDate — อ่านวันที่ที่เลือกกลับเป็นข้อความไทย (พ.ศ.) ใต้ช่อง เพราะช่องวันที่ของเบราว์เซอร์
//      เรียงวัน/เดือนตามภาษาของเครื่อง (เครื่องหนึ่งขึ้น 09/30/2026 อีกเครื่องขึ้น 30/09/2026)
//      ส่วนวันที่เข้าคิวเป็นตัวชี้แถวในชีท อ่านสลับวันกับเดือนเมื่อไหร่ก็เขียนผิดแถว
//   2. fillCheckedDate — วันที่ตรวจ = วันนี้ตั้งแต่เปิดหน้า ต้องคิดจากเวลาในเครื่อง ไม่ใช่ UTC
//      (ก่อนเจ็ดโมงเช้าบ้านเรา UTC ยังเป็นเมื่อวาน)
//
// ดึงเฉพาะฟังก์ชันจากไฟล์จริงมารัน (ไม่ใช่สำเนาในเทสต์) แก้ของจริงเมื่อไหร่ เทสต์นี้รันโค้ดที่แก้แล้วทันที
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'index.html'), 'utf8');
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

// ---------------------------------------------------------------- thaiDate
const thaiSandbox = {};
vm.createContext(thaiSandbox);
vm.runInContext(take('thaiDate'), thaiSandbox, { filename: 'index.html' });
const thaiDate = (iso) => vm.runInContext(`thaiDate(${JSON.stringify(iso)})`, thaiSandbox);

check('30 ก.ย. 2026 เป็น พ.ศ. 2569', thaiDate('2026-09-30'), '30 กันยายน 2569');
// วันเดียวกันที่อ่านสลับวัน/เดือนได้ — ข้อความไทยต้องแยกออก ไม่มีทางอ่านผิด
check('9 มี.ค. 2026 (ไม่ใช่ 3 ก.ย.)', thaiDate('2026-03-09'), '9 มีนาคม 2569');
check('3 ก.ย. 2026 (ไม่ใช่ 9 มี.ค.)', thaiDate('2026-09-03'), '3 กันยายน 2569');
check('วันแรกของปี', thaiDate('2026-01-01'), '1 มกราคม 2569');
check('วันสุดท้ายของปี', thaiDate('2026-12-31'), '31 ธันวาคม 2569');
check('29 ก.พ. ปีอธิกสุรทิน', thaiDate('2028-02-29'), '29 กุมภาพันธ์ 2571');
// วันที่ที่ไม่มีจริงต้องไม่ถูกปั้นเป็นข้อความ (Date ปัดไปเดือนถัดไปเงียบ ๆ)
check('29 ก.พ. ปีธรรมดา', thaiDate('2026-02-29'), '');
check('31 เม.ย.', thaiDate('2026-04-31'), '');
check('เดือน 13', thaiDate('2026-13-01'), '');
check('วันที่ 0', thaiDate('2026-09-00'), '');
check('ว่าง (ยังไม่ได้เลือก)', thaiDate(''), '');
check('ไม่ใช่ yyyy-mm-dd', thaiDate('30/09/2026'), '');
check('ปีสองหลัก (Date.UTC ปัดเป็น 19xx)', thaiDate('0026-09-30'), '');

// ---------------------------------------------------------------- ช่องพิมพ์ วว/ดด/ปปปป พ.ศ. (อัปเดต 1.6.2)
// ค่าที่ส่งเซิร์ฟเวอร์ต้องเป็น yyyy-mm-dd ค.ศ. เหมือนเดิมทุกตัวอักษร (เจ้าของสั่ง "อย่าให้ส่วนนี้มีผลกับการตรวจ")
const typingSandbox = {};
vm.createContext(typingSandbox);
for (const name of ['formatDateTyping', 'isoFromThaiText', 'thaiTextFromIso']) {
  vm.runInContext(take(name), typingSandbox, { filename: 'index.html' });
}
const call = (name, arg) => vm.runInContext(`${name}(${JSON.stringify(arg)})`, typingSandbox);

check('พิมพ์ตัวเลขล้วน ใส่ / ให้เอง', call('formatDateTyping', '26102569'), '26/10/2569');
check('ยังพิมพ์ไม่ถึงเดือน ไม่ใส่ / ค้าง', call('formatDateTyping', '26'), '26');
check('พิมพ์ถึงหลักแรกของเดือน', call('formatDateTyping', '261'), '26/1');
check('ตัวที่ไม่ใช่ตัวเลขถูกตัดทิ้ง', call('formatDateTyping', '26-10-2569'), '26/10/2569');
check('พิมพ์ / เองหลังเลขหลักเดียว เติม 0', call('formatDateTyping', '2/9/2569'), '02/09/2569');
check('ยาวเกินถูกตัดที่ 8 หลัก', call('formatDateTyping', '261025691'), '26/10/2569');
check('ว่าง', call('formatDateTyping', ''), '');

check('พ.ศ. เป็น ค.ศ.', call('isoFromThaiText', '26/10/2569'), '2026-10-26');
check('ต้นปี', call('isoFromThaiText', '01/01/2570'), '2027-01-01');
check('29 ก.พ. ปีอธิกสุรทิน (2571 = 2028)', call('isoFromThaiText', '29/02/2571'), '2028-02-29');
check('29 ก.พ. ปีธรรมดา (2569 = 2026) ไม่มีจริง', call('isoFromThaiText', '29/02/2569'), '');
check('31 เม.ย. ไม่มีจริง', call('isoFromThaiText', '31/04/2569'), '');
check('เดือน 13', call('isoFromThaiText', '01/13/2569'), '');
check('วันที่ 0', call('isoFromThaiText', '00/10/2569'), '');
check('พิมพ์ไม่ครบ', call('isoFromThaiText', '26/10/25'), '');
check('พิมพ์ปี ค.ศ. มาแทน พ.ศ. ถือว่ายังไม่กรอก', call('isoFromThaiText', '26/10/2026'), '');
check('ว่าง', call('isoFromThaiText', ''), '');

check('ค.ศ. เป็น พ.ศ. (ตอนเลือกจากปฏิทิน)', call('thaiTextFromIso', '2026-10-26'), '26/10/2569');
check('เติม 0 ตามที่เก็บ', call('thaiTextFromIso', '2027-01-05'), '05/01/2570');
check('ไปกลับได้ค่าเดิม', call('isoFromThaiText', call('thaiTextFromIso', '2028-02-29')), '2028-02-29');
check('วันที่ไม่มีจริงไม่ปั้นข้อความ', call('thaiTextFromIso', '2026-02-29'), '');
check('ผิดรูป', call('thaiTextFromIso', '26/10/2026'), '');

// ---------------------------------------------------------------- fillCheckedDate
// เครื่องเวลา 06:30 น. วันที่ 30 ก.ย. 2026 (UTC+7) — UTC ยังเป็นเช้ามืดของวันที่ 29
class MorningInBangkok {
  getFullYear() { return 2026; }
  getMonth() { return 8; }
  getDate() { return 30; }
  toISOString() { return '2026-09-29T23:30:00.000Z'; }
}
class NewYearsDay {
  getFullYear() { return 2027; }
  getMonth() { return 0; }
  getDate() { return 5; }
  toISOString() { return '2027-01-04T17:30:00.000Z'; }
}

// อัปเดต 1.6.2: ช่องที่เห็น (checked-date) เป็น วว/ดด/ปปปป พ.ศ. ส่วนค่าที่ส่งอยู่ในช่องซ่อน checked-date-iso (ค.ศ.)
function runFill({ existing = '', fieldExists = true, clock = MorningInBangkok } = {}) {
  const field = { id: 'checked-date', value: existing, validity: '', setCustomValidity(msg) { this.validity = msg; } };
  const hidden = { value: '' };
  let progressCalls = 0;
  const sandbox = {
    // new Date() = นาฬิกาปลอม · Date.UTC (ตัวแปลงวันที่ใช้ตรวจวันที่มีจริง) = ของจริง
    Date: new Proxy(clock, {
      construct: (target, args) => (args.length ? new Date(...args) : new target()),
      get: (target, key) => (key === 'UTC' ? Date.UTC : target[key]),
    }),
    document: {
      getElementById: (id) => (!fieldExists ? null
        : id === 'checked-date' ? field : id === 'checked-date-iso' ? hidden : null),
    },
    updateProgress: () => { progressCalls += 1; },
  };
  vm.createContext(sandbox);
  for (const name of ['isoFromThaiText', 'thaiTextFromIso', 'syncDateField', 'setDateFromIso']) {
    vm.runInContext(take(name), sandbox, { filename: 'index.html' });
  }
  vm.runInContext(`(${take('fillCheckedDate')})()`, sandbox, { filename: 'index.html' });
  return { value: field.value, iso: hidden.value, validity: field.validity, progressCalls };
}

const morning = runFill();
check('ก่อนเจ็ดโมงเช้าได้วันที่ในเครื่อง ไม่ใช่ UTC (ช่องที่เห็นเป็น พ.ศ.)', morning.value, '30/09/2569');
check('ค่าที่ส่งเซิร์ฟเวอร์ยังเป็น ค.ศ. yyyy-mm-dd', morning.iso, '2026-09-30');
check('เติมแล้วไม่ติดสถานะกรอกผิด', morning.validity, '');
check('เติมแล้วนับความพร้อมใหม่', morning.progressCalls, 1);
check('เดือน/วันหลักเดียวเติมศูนย์นำหน้า', runFill({ clock: NewYearsDay }).iso, '2027-01-05');
check('ช่องที่เจ้าหน้าที่กรอกไว้แล้วห้ามทับ', runFill({ existing: '01/09/2569' }).value, '01/09/2569');
check('ไม่เติมทับแล้วไม่ต้องนับใหม่', runFill({ existing: '01/09/2569' }).progressCalls, 0);
check('ไม่มีช่องนี้ในหน้า ต้องไม่พัง', runFill({ fieldExists: false }).progressCalls, 0);

if (failures) {
  console.error(`\n${failures} ข้อไม่ผ่าน`);
  process.exit(1);
}
console.log('test_form_dates.js ผ่านทุกข้อ');
