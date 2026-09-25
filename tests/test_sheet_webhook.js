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

let tzSeen = [];
let parseTz = [];
let noParseDate = false;
function run(sheet, body, token, storedToken, asGet) {
  tzSeen = [];
  parseTz = [];
  const sandbox = {
    console,
    // โทเค็นที่เก็บใน Script properties ของโปรเจกต์ (undefined = ยังไม่เคยตั้ง)
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (name) => (name === 'TOKEN' && storedToken !== undefined
          ? storedToken : null),
      }),
    },
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: (text) => ({ text, setMimeType: () => ({ text }) }),
    },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    // เขตเวลาของสเปรดชีตกับของโปรเจกต์สคริปต์ตั้งแยกกัน และมักไม่ตรงกัน
    // (โปรเจกต์ใหม่ของ Apps Script ตั้งต้นเป็นเขตเวลาอเมริกา)
    SpreadsheetApp: {
      getActiveSpreadsheet: () => ({
        getSheets: () => [sheet],
        getSpreadsheetTimeZone: () => 'Asia/Bangkok',
      }),
      flush() {},
    },
    Session: { getScriptTimeZone: () => 'America/New_York' },
    Utilities: {
      formatDate: (d, tz) => {
        tzSeen.push(tz);
        return [d.getFullYear(),
                ('0' + (d.getMonth() + 1)).slice(-2),
                ('0' + d.getDate()).slice(-2)].join('-');
      },
      // ของจริงแปลงข้อความเป็นเวลาจริงตามเขตเวลาที่ระบุ ตัวจำลองแค่จำว่าใช้เขตเวลาไหน
      parseDate: (text, tz) => {
        parseTz.push(tz);
        if (noParseDate) throw new Error('parseDate ใช้ไม่ได้');
        const m = String(text).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):/);
        return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]));
      },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'sheet_webhook.gs' });
  sandbox.TOKEN = token === undefined ? 'test-token' : token;
  const answer = vm.runInContext(asGet ? 'doGet()' : 'doPost(__e__)',
    Object.assign(sandbox, { __e__: { postData: { contents: JSON.stringify(body) } } }));
  return JSON.parse(answer.text);
}

function runGet(sheet, token, storedToken) {
  const answer = run(sheet, { token: '__ไม่ใช้__' }, token, storedToken, true);
  return answer;
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

// ---- วันที่ต้องไม่เลื่อนไปหนึ่งวันเพราะเขตเวลา ----
// ของจริง (ก.ย. 2569): เจ้าหน้าที่กรอกวันที่ตรวจ 25/9/2026 แต่ชีทได้ 24/9/2026
// ต้นเหตุคือเขียนเป็นเที่ยงคืนของเขตเวลาหนึ่ง แล้วชีทอ่านด้วยอีกเขตเวลาหนึ่ง
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, Object.assign({}, SAVE, { checked_date: '2026-09-25' }));
const written = sheet.wrote['วันที่ตรวจ'];
check('เขียนวันที่ตรงกับที่กรอก', written.getDate(), 25);
check('เขียนเดือนตรงกับที่กรอก', written.getMonth() + 1, 9);
// ห้ามมีเวลาติดไปด้วย — ชีทเก็บวันที่เป็นตัวเลขวัน เวลาที่ติดมากลายเป็นเศษของวัน
// ช่อง "จำนวนวันที่ใช้ตรวจ" ที่ลบวันที่กันจะได้ 0.5 แทนที่จะเป็น 0 (เจ้าหน้าที่แจ้ง ก.ย. 2569)
check('ไม่มีชั่วโมงติดมา', written.getHours(), 0);
check('ไม่มีนาทีติดมา', written.getMinutes(), 0);
check('ไม่มีวินาทีติดมา', written.getSeconds(), 0);
// สร้างวันที่ "ในเขตเวลาของสเปรดชีต" ตรง ๆ ไม่ใช่หวังว่าสองเขตเวลาจะห่างกันไม่เกินครึ่งวัน
// (ของจริงห่างกันได้ถึง 25 ชั่วโมง: GMT-11 ถึง GMT+14)
check('สร้างวันที่ด้วยเขตเวลาของสเปรดชีต', parseTz.join(','), 'Asia/Bangkok');

// ---- ถ้าตัวแปลงวันที่ใช้ไม่ได้ ก็ยังต้องเขียนวันที่ถูกวัน ----
noParseDate = true;
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, Object.assign({}, SAVE, { checked_date: '2026-09-25' }));
noParseDate = false;
check('ตัวแปลงพังก็ยังเขียนวันที่ถูก', sheet.wrote['วันที่ตรวจ'].getDate(), 25);
check('ตัวแปลงพังก็ยังไม่มีเวลาติดมา', sheet.wrote['วันที่ตรวจ'].getHours(), 0);
check('ตัวแปลงพังก็ยังบันทึกสำเร็จ', out.ok, true);

// ---- อ่านช่องวันที่จริงในชีท ต้องใช้เขตเวลาของสเปรดชีต ----
// ถ้าใช้เขตเวลาของโปรเจกต์สคริปต์ (คนละค่า) วันที่ในชีทจะอ่านได้เป็นวันก่อนหน้า
// แล้วหาแถวตามวันที่เข้าคิวไม่เจอทั้งที่ข้อมูลถูก
const realDate = rowFor('6736605 NSCN/M', new Date(2026, 8, 20, 12, 0, 0));
sheet = fakeSheet(HEADERS_NEW, [realDate]);
out = run(sheet, SAVE);
check('หาแถวจากช่องวันที่จริงได้', out.ok, true);
check('อ่านวันที่ด้วยเขตเวลาของสเปรดชีต', tzSeen.indexOf('Asia/Bangkok') >= 0, true);
check('ไม่ใช้เขตเวลาของโปรเจกต์สคริปต์', tzSeen.indexOf('America/New_York'), -1);

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

// ---- โทเค็นเก็บใน Script properties ต้องใช้ได้ ----
// วางสคริปต์ฉบับใหม่ทับ = บรรทัด TOKEN ในไฟล์กลับเป็นค่าว่างทุกครั้ง (เจอจริง ก.ย. 2569
// เจ้าหน้าที่ได้ "โทเค็นไม่ถูกต้อง" หลังวางสคริปต์ใหม่) ค่าที่เก็บนอกไฟล์จึงต้องใช้ได้
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE, '', 'test-token');
check('โทเค็นจาก Script properties ใช้ได้ทั้งที่ในไฟล์ว่าง', out.ok, true);

sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE, 'ของเก่าในไฟล์', 'test-token');
check('ค่าใน Script properties มาก่อนค่าในไฟล์', out.ok, true);

// ---- ยังไม่ได้ตั้งโทเค็นเลย ต้องบอกคนละอย่างกับโทเค็นไม่ตรง ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE, '');
check('ยังไม่ได้ตั้งโทเค็น', out.code, 'no_token');
check('บอกว่าไปตั้งที่ไหน', /Script properties/.test(out.error || ''), true);
check('ยังไม่ได้ตั้งโทเค็นต้องไม่เขียนอะไร', Object.keys(sheet.wrote).length, 0);

sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
out = run(sheet, SAVE, 'PUT-YOUR-SHARED-TOKEN-HERE');
check('ค่าตั้งต้นในไฟล์ไม่นับว่าตั้งแล้ว', out.code, 'no_token');

// ---- หน้าตรวจสถานะ (doGet) ต้องบอกว่าตั้งโทเค็นหรือยัง แต่ห้ามบอกค่า ----
sheet = fakeSheet(HEADERS_NEW, [rowFor('6736605 NSCN/M', '20/9/2026')]);
let status = runGet(sheet, '', 'test-token');
check('บอกว่าตั้งโทเค็นแล้ว', status.token_set, true);
check('พร้อมใช้งาน', status.ok, true);
check('ไม่ส่งค่าโทเค็นออกมา', JSON.stringify(status).indexOf('test-token'), -1);
status = runGet(sheet, '');
check('ยังไม่ตั้งโทเค็นต้องบอกว่ายังไม่พร้อม', status.ok, false);
check('บอกว่ายังไม่ได้ตั้งโทเค็น', status.token_set, false);
status = runGet(fakeSheet(HEADERS_NEW.map((h) => (h === 'Biography' ? '' : h)), []),
                'test-token');
check('บอกชื่อช่องที่แท็บนี้ไม่มี', (status.missing_columns || []).join(','), 'Biography');

if (failures) {
  console.error(`\nsheet_webhook.gs tests: ${failures} ข้อไม่ผ่าน`);
  process.exit(1);
}
console.log('sheet_webhook.gs tests passed');
