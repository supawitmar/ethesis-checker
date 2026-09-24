// เทสต์สคริปต์ที่ติดอยู่กับชีทบันทึกการตรวจ (tools/sheet_webhook.gs) นอก Google
//
// สคริปต์นี้เป็นชิ้นที่พังแล้วเงียบที่สุด: มันรันอยู่ในชีท ไม่มี log ให้ดูในระบบตรวจ และ
// เจ้าหน้าที่ต้องเอาไปวางเองทุกครั้งที่แก้ ถ้าไม่มีเทสต์ ความผิดพลาดจะไปโผล่เอาที่ข้อมูลจริง
//
// ของจริงที่เจอ (ก.ย. 2569): แท็บเดือนล่าสุดใช้หัวตาราง "ToC" แต่สคริปต์รู้จักแค่ "LoC"
// ช่องสารบัญจึงถูกข้ามไปเงียบ ๆ ทั้งที่หน้าจอขึ้นว่าบันทึกแล้ว
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'tools', 'sheet_webhook.gs'), 'utf8');

let failures = 0;
function check(label, actual, wanted) {
  if (String(actual) !== String(wanted)) {
    failures += 1;
    console.error(`FAIL ${label}\n  ได้   ${actual}\n  ต้องเป็น ${wanted}`);
  }
}

// ---- ชีทจำลอง: ตาราง 2 มิติ ที่จำได้ว่าถูกเขียนช่องไหนไปบ้าง ----
function fakeSheet(headers, rows) {
  const grid = [['สถิติ'], [], headers.slice()];      // หัวตารางอยู่แถวที่ 3
  rows.forEach((r) => grid.push(r.slice()));
  const width = Math.max(...grid.map((r) => r.length));
  grid.forEach((r) => { while (r.length < width) r.push(''); });
  const wrote = {};
  return {
    grid, wrote,
    getName: () => 'กย69',
    getLastRow: () => grid.length,
    getLastColumn: () => width,
    getRange(row, col, nRows, nCols) {
      return {
        getValues: () => grid.slice(row - 1, row - 1 + (nRows || 1))
          .map((r) => r.slice(col - 1, col - 1 + (nCols || 1))),
        getValue: () => grid[row - 1][col - 1],
        setValue: (v) => {
          grid[row - 1][col - 1] = v;
          wrote[headers[col - 1] || ('คอลัมน์ ' + col)] = v;
        },
      };
    },
  };
}

function run(sheet, body, token) {
  const sandbox = {
    console,
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: (text) => ({ text, setMimeType: () => ({ text }) }),
    },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    SpreadsheetApp: { getActiveSpreadsheet: () => ({ getSheets: () => [sheet] }), flush() {} },
    Session: { getScriptTimeZone: () => 'Asia/Bangkok' },
    Utilities: {
      formatDate: (d) => [d.getFullYear(),
                          ('0' + (d.getMonth() + 1)).slice(-2),
                          ('0' + d.getDate()).slice(-2)].join('-'),
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'sheet_webhook.gs' });
  sandbox.TOKEN = token === undefined ? 'test-token' : token;
  const answer = vm.runInContext('doPost(__e__)',
    Object.assign(sandbox, { __e__: { postData: { contents: JSON.stringify(body) } } }));
  return JSON.parse(answer.text);
}

const HEADERS_NEW = ['ลำดับ', 'ชื่อ-สกุล', 'รหัส', 'Queue', 'วันที่ตรวจ', 'รูปแบบไฟล์เล่ม',
                     'Template', 'ผลการพิจารณา', 'จำนวนวันที่ใช้ตรวจ', '', '', 'Pass or not',
                     'Cover', 'Abstract', 'ToC', 'Main Content', 'Reference', 'Appendix',
                     'Biography', 'Other', ''];
const HEADERS_OLD = HEADERS_NEW.map((h) => (h === 'ToC' ? 'LoC' : h));

function rowFor(id, queue) {
  const r = new Array(HEADERS_NEW.length).fill('');
  r[2] = id;
  r[3] = queue;
  return r;
}

const SAVE = {
  token: 'test-token', student_id: '6736605 NSCN/M', queue_date: '2026-09-20',
  checked_date: '2026-09-24', decision: 'ส่งกลับแก้ไข', pass_or_not: 1,
  flags: { Cover: true, Abstract: false, LoC: true, 'Main Content': false,
           Reference: false, Appendix: false, Biography: false, Other: false },
  note: '', overwrite: false,
};

// ---- แท็บที่ใช้หัวตาราง "ToC" ต้องติ๊กช่องสารบัญลงจริง ----
let sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
let out = run(sheet, SAVE);
check('บันทึกสำเร็จ', out.ok, true);
check('ติ๊กช่องสารบัญที่หัวว่า ToC', sheet.wrote.ToC, true);
check('ติ๊กช่อง Cover', sheet.wrote.Cover, true);
check('ช่องที่ไม่ผิดต้องเป็น false', sheet.wrote.Abstract, false);
check('เขียนผลการพิจารณา', sheet.wrote['ผลการพิจารณา'], 'ส่งกลับแก้ไข');
check('เขียน Pass or not', sheet.wrote['Pass or not'], 1);
check('ไม่มีช่องไหนติ๊กไม่ลง', (out.missing || []).length, 0);
check('ไม่แตะช่องจำนวนวันที่ใช้ตรวจ',
      Object.prototype.hasOwnProperty.call(sheet.wrote, 'จำนวนวันที่ใช้ตรวจ'), false);

// ---- แท็บเก่าที่ใช้ "LoC" ต้องยังทำงานเหมือนเดิม ----
sheet = fakeSheet(HEADERS_OLD, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE);
check('แท็บเก่าหัว LoC ก็ติ๊กลง', sheet.wrote.LoC, true);
check('แท็บเก่าไม่มีช่องไหนตกหล่น', (out.missing || []).length, 0);

// ---- หัวตารางที่พิมพ์ตัวเล็กใหญ่หรือเว้นวรรคไม่เหมือนกัน ต้องยังหาเจอ ----
// (แท็บทำมือ 34 แท็บ พิมพ์ไม่เหมือนกันทุกแท็บอยู่แล้ว)
sheet = fakeSheet(HEADERS_NEW.map(function (h) {
  if (h === 'ToC') return 'TOC ';
  if (h === 'Main Content') return 'Main content';
  if (h === 'Pass or not') return 'Pass Or Not';
  return h;
}), [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE);
check('หัวตารางพิมพ์ต่างกันก็ยังหาเจอ', (out.missing || []).join(','), '');
check('ติ๊กลงช่องที่หัวว่า "TOC "', sheet.wrote['TOC '], true);
check('เขียน Pass or not ที่พิมพ์ต่างกัน', sheet.wrote['Pass Or Not'], 1);

// ---- แท็บที่ไม่มีช่องนั้นเลย ต้องบอก ไม่ใช่ข้ามเงียบ ๆ ----
sheet = fakeSheet(HEADERS_NEW.map((h) => (h === 'Biography' ? '' : h)),
                  [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE);
check('บอกชื่อช่องที่ติ๊กไม่ลง', (out.missing || []).join(','), 'Biography');

// ---- วันที่เข้าคิวใช้ชี้แถว และวันที่ตรวจเขียนเป็นวันที่จริง ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '2/9/2026'),
                                rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE);
check('เลือกแถวตามวันที่เข้าคิว', out.row, 5);          // หัวตารางแถว 3 → แถวที่สองของข้อมูล
// instanceof ใช้ไม่ได้ข้ามแซนด์บ็อกซ์ (Date คนละตัว) จึงดูจากชนิดที่แท้จริงแทน
check('วันที่ตรวจเขียนเป็นวันที่จริง ไม่ใช่ข้อความ',
      Object.prototype.toString.call(sheet.wrote['วันที่ตรวจ']), '[object Date]');

// ---- รหัสซ้ำแต่ไม่ได้ระบุวันที่เข้าคิว = ไม่เดา ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '2/9/2026'),
                                rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, Object.assign({}, SAVE, { queue_date: '' }));
check('รหัสซ้ำต้องไม่เดาแถว', out.code, 'many_rows');
check('บอกวันที่เข้าคิวที่มีให้เลือก', (out.queues || []).join(','), '2026-09-02,2026-09-20');
check('ยังไม่เขียนอะไรลงชีท', Object.keys(sheet.wrote).length, 0);

// ---- แถวที่กรอกผลไว้แล้ว ต้องถามก่อนเขียนทับ ----
const filled = rowFor('6736605 NSCN/M', '20/9/2026');
filled[7] = 'เสร็จสิ้น';
sheet = fakeSheet(HEADERS_NEW, [filled]);
out = run(sheet, SAVE);
check('ไม่เขียนทับทันที', out.code, 'already_filled');
check('บอกว่าต้องยืนยัน', out.needs_overwrite, true);
check('ยังไม่เขียนอะไรลงชีท', Object.keys(sheet.wrote).length, 0);
out = run(sheet, Object.assign({}, SAVE, { overwrite: true }));
check('ยืนยันแล้วจึงเขียนทับ', sheet.wrote['ผลการพิจารณา'], 'ส่งกลับแก้ไข');

// ---- หารหัสไม่เจอ = ไม่เขียนอะไรเลย ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6000000 AAAA/M', '20/9/2026')]);
out = run(sheet, SAVE);
check('หารหัสไม่เจอ', out.code, 'row_not_found');
check('ไม่เขียนอะไรเลย', Object.keys(sheet.wrote).length, 0);

// ---- โทเค็นผิด = ไม่แตะชีท ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, Object.assign({}, SAVE, { token: 'wrong' }));
check('โทเค็นผิดต้องไม่ผ่าน', out.code, 'bad_token');
check('โทเค็นผิดต้องไม่เขียนอะไร', Object.keys(sheet.wrote).length, 0);

if (failures) {
  console.error(`\nsheet_webhook.gs tests: ${failures} ข้อไม่ผ่าน`);
  process.exit(1);
}
console.log('sheet_webhook.gs tests passed');
