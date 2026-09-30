// เทสต์สคริปต์ของหน้ารายงาน (templates/report.html) นอกเบราว์เซอร์
//
// คุมสองกลไกที่พังแล้วมองไม่เห็นจากหน้าจอ
//   applyVerdictScope() ซ่อนหัวข้อค่าปรับที่ไม่ตรงผลตรวจ — ถ้าพัง เจ้าหน้าที่จะกดปุ่ม
//     ที่ฝั่งเซิร์ฟเวอร์ทิ้งทุกครั้ง แล้วนักศึกษาไม่ได้ถ้อยคำนั้นโดยไม่มีใครรู้
//   trSummary() แปลถ้อยคำปิดท้าย "ทั้งก้อน" — ถ้าพัง ข้อความอังกฤษจะออกมาปนไทย
//
// รัน:  node tests/test_report_summary.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'templates', 'report.html'), 'utf8');
// บล็อก <script> ที่ไม่มี attribute คือสคริปต์หลัก ส่วน <script id=... type="application/json">
// เป็นข้อมูลที่ฝั่งเซิร์ฟเวอร์ฝังมา จึงไม่ถูกตัดมาด้วย
const source = html.split('<script>')[1].split('</script>')[0];

let failures = 0;
function check(label, actual, wanted) {
  const ok = String(actual) === String(wanted);
  if (!ok) {
    failures += 1;
    console.error(`FAIL ${label}\n  ได้   ${actual}\n  ต้องเป็น ${wanted}`);
  }
}

// ทะเบียนจำลอง — เทสต์นี้คุม "กลไก" ถ้อยคำจริงมีเทสต์ฝั่ง python คุมอยู่แล้ว
const REGISTRY = [
  { id: 'SIGNATURE_LAYOUT', placement: 'section', choices: [
    { id: 'SIGNATURE_LAYOUT_WRONG', text: 'ในหน้าลงนาม\nปรับโครงสร้างของหน้า',
      text_en: 'Approval pages\nplease restructure the page' }] },
  { id: 'LATE_FEE', placement: 'closing', applies_to: 'not_pass', choices: [
    { id: 'LATE_FEE_NONE', text: 'นศ. ไม่มีค่าปรับ\nกรุณาส่งกลับเข้าสู่ระบบอีกครั้ง',
      text_en: 'You have no fine\nPlease resubmit the document' }] },
  { id: 'PASS_FEE', placement: 'closing', applies_to: 'pass', choices: [
    { id: 'PASS_FEE_NONE',
      text: 'การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว\nสอบถามข้อมูลเพิ่มเติม\nhttps://bit.ly/4cwqxAd',
      text_en: 'Your E-thesis has been completed.\nIf you have any further questions,\n'
               + 'please contact us.\nhttps://bit.ly/4cwqxAd' }] },
];

function makeCard(applies, pressed) {
  const buttons = [{ on: pressed }];
  const stateLine = { hidden: !pressed, textContent: pressed ? 'เพิ่มถ้อยคำแล้ว' : '' };
  const classes = new Set(pressed ? ['hc', 'sf', 'state-fail'] : ['hc', 'sf']);
  return {
    hidden: false, stateLine,
    classList: {
      remove: (...cs) => cs.forEach((c) => classes.delete(c)),
      contains: (c) => classes.has(c),
    },
    getAttribute: (k) => (k === 'data-applies' ? applies : null),
    querySelector: (sel) => {
      if (sel === '.sf-state') return stateLine;
      if (sel === '.pf.on[data-choice]') {
        const on = buttons.filter((b) => b.on);
        return on.length ? { dataset: {}, textContent: 'ปุ่ม',
                             getAttribute: () => null } : null;
      }
      return null;
    },
    querySelectorAll: (sel) => (sel === '.pf.on'
      ? buttons.filter((b) => b.on).map((b) => ({
          classList: { remove: () => { b.on = false; } } }))
      : []),
    pressed: () => buttons.filter((b) => b.on).length,
    styled: () => classes.has('state-fail') || classes.has('state-pass'),
  };
}

function load(cards, summaryText) {
  const counter = { textContent: String(cards.length) };
  const pill = { className: 'verdict-pill pass', textContent: 'ผ่าน (Passed)' };
  const data = {
    'staff-findings-data': { textContent: JSON.stringify(REGISTRY) },
    'plain-summary-data': { textContent: JSON.stringify(summaryText || '') },
    'verdict-pill': pill,
  };
  const noop = () => {};
  const sandbox = {
    document: {
      getElementById: (id) => data[id] || null,
      querySelector: (sel) => (sel === '#sf-head .count b' ? counter : null),
      querySelectorAll: (sel) => {
        if (sel === '.sf[data-applies]') return cards;
        if (sel === '.sf:not([hidden])') return cards.filter((c) => !c.hidden);
        return [];
      },
      addEventListener: noop,
      createElement: () => ({ dataset: {}, style: {} }),
      documentElement: {}, body: {},
    },
    navigator: {}, localStorage: { getItem: () => null, setItem: noop },
    console, setTimeout, addEventListener: noop,
    matchMedia: () => ({ matches: false, addEventListener: noop }),
    location: { href: '' },
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'report.html' });
  return { sandbox, counter, pill };
}

// ---- หัวข้อค่าปรับต้องขึ้นตามผลตรวจ ----
{
  const cards = [makeCard('', false), makeCard('not_pass', false),
                 makeCard('pass', false)];
  const { sandbox, counter } = load(cards);
  sandbox.applyVerdictScope('ผ่าน');
  check('เล่มผ่าน: หัวข้อที่ใช้ได้ทุกผลตรวจยังโชว์', cards[0].hidden, false);
  check('เล่มผ่าน: หัวข้อของเล่มไม่ผ่านถูกซ่อน', cards[1].hidden, true);
  check('เล่มผ่าน: หัวข้อของเล่มผ่านโชว์', cards[2].hidden, false);
  check('เล่มผ่าน: ตัวนับ', counter.textContent, '2');

  cards.forEach((c) => { c.hidden = false; });
  sandbox.applyVerdictScope('ไม่ผ่าน');
  check('เล่มไม่ผ่าน: หัวข้อของเล่มไม่ผ่านโชว์', cards[1].hidden, false);
  check('เล่มไม่ผ่าน: หัวข้อของเล่มผ่านถูกซ่อน', cards[2].hidden, true);

  cards.forEach((c) => { c.hidden = false; });
  sandbox.applyVerdictScope('รอยืนยัน');
  check('รอยืนยันไม่ใช่ผ่าน: หัวข้อของเล่มผ่านถูกซ่อน', cards[2].hidden, true);
}

// ---- หัวข้อที่ถูกซ่อนต้องถูกล้างคำตอบให้หมด ไม่ใช่แค่ปลดปุ่ม ----
{
  const dirty = makeCard('pass', true);
  const { sandbox } = load([dirty]);
  sandbox.applyVerdictScope('ไม่ผ่าน');
  check('ปุ่มถูกปลด', dirty.pressed(), 0);
  check('บรรทัด "เพิ่มถ้อยคำแล้ว" ถูกซ่อน', dirty.stateLine.hidden, true);
  check('บรรทัด "เพิ่มถ้อยคำแล้ว" ถูกล้างข้อความ', dirty.stateLine.textContent, '');
  check('สีการ์ดถูกล้าง', dirty.styled(), false);
}

// ---- อ่านผลตรวจไม่ได้ = ไม่รู้ ต้องโชว์ทุกหัวข้อ ไม่ใช่เดาว่าไม่ผ่าน ----
{
  const cards = [makeCard('not_pass', false), makeCard('pass', true)];
  const { sandbox } = load(cards);
  sandbox.applyVerdictScope('');
  check('ผลตรวจว่าง: หัวข้อของเล่มไม่ผ่านโชว์', cards[0].hidden, false);
  check('ผลตรวจว่าง: หัวข้อของเล่มผ่านโชว์', cards[1].hidden, false);
  check('ผลตรวจว่าง: ไม่ไปล้างคำตอบที่กดไว้', cards[1].pressed(), 1);
}

// ---- ป้ายผลพิจารณาต้องตามคำตัดสินของเจ้าหน้าที่ ----
// เจ้าหน้าที่กดเพิ่มจุดกับเล่มที่ระบบว่าผ่าน แล้วข้อความสรุปเขียนว่า "ไม่ผ่าน"
// ป้ายบนหัวต้องเปลี่ยนตาม ไม่ใช่ค้างที่ "ผ่าน (Passed)"
{
  const { sandbox, pill } = load([]);
  sandbox.renderVerdictPill('ไม่ผ่าน');
  check('ป้ายเปลี่ยนเป็นไม่ผ่าน', pill.textContent, 'ไม่ผ่าน (Not passed)');
  check('สีป้ายเปลี่ยนตาม', pill.className, 'verdict-pill fail');

  sandbox.renderVerdictPill('รอยืนยัน');
  check('ป้ายรอยืนยัน', pill.textContent, 'รอยืนยัน (Pending confirmation)');
  check('สีป้ายรอยืนยัน', pill.className, 'verdict-pill pending');

  sandbox.renderVerdictPill('ผ่าน');
  check('ป้ายกลับเป็นผ่านได้', pill.textContent, 'ผ่าน (Passed)');
  check('สีป้ายกลับเป็นผ่าน', pill.className, 'verdict-pill pass');

  // ผลตรวจที่อ่านไม่ได้ ต้องไม่ไปล้างป้ายเป็นค่าว่าง
  sandbox.renderVerdictPill('');
  check('ผลตรวจที่อ่านไม่ได้ ปล่อยป้ายเดิมไว้', pill.textContent, 'ผ่าน (Passed)');
}

// ---- ผลตรวจอ่านจากบรรทัดแรกของข้อความสรุป ----
{
  const { sandbox } = load([], 'ผลการตรวจ: ไม่ผ่าน\n\nกรุณาแก้ไขทั้งหมด 2 จุด ดังต่อไปนี้');
  check('อ่านผลตรวจจากข้อความสรุป', sandbox.REPORT_VERDICT, 'ไม่ผ่าน');
}

// ---- ถ้อยคำปิดท้ายต้องถูกแปลทั้งก้อน ----
{
  const wording = REGISTRY[2].choices[0];
  const summary = 'ผลการตรวจ: ผ่าน\n\n' + wording.text;
  const { sandbox } = load([], summary);
  const en = sandbox.trSummary(summary);
  check('บรรทัดผลตรวจถูกแปล', en.split('\n')[0], 'Result: Passed');
  check('ถ้อยคำปิดท้ายถูกแทนทั้งก้อน', en.indexOf(wording.text_en) !== -1, true);
  check('ไม่เหลืออักษรไทย', /[฀-๿]/.test(en), false);
  check('ลิงก์แบบสอบถามไม่ถูกแตะ',
        (en.match(/https:\/\/bit\.ly\/4cwqxAd/g) || []).length, 1);

  // ถ้อยคำของเล่มไม่ผ่านมีบรรทัดไทยที่ซ้ำกับชุดผ่านได้ จับทั้งก้อนจึงไม่ปนกัน
  const failWording = REGISTRY[1].choices[0];
  const failSummary = 'ผลการตรวจ: ไม่ผ่าน\n\n' + failWording.text;
  const failEn = sandbox.trSummary(failSummary);
  check('ชุดไม่ผ่านแปลถูกก้อนของตัวเอง',
        failEn.indexOf(failWording.text_en) !== -1, true);
  check('ชุดไม่ผ่านไม่ปนถ้อยคำของชุดผ่าน',
        failEn.indexOf('has been completed') === -1, true);
}

// ---- ช่องรายละเอียดในชีทต้องเป็นภาษาเดียวกับที่หน้ารายงานเปิดอยู่ ----
// เจ้าหน้าที่เจอจริง (ก.ย. 2569): หน้าจอเป็นอังกฤษ กดบันทึกแล้วชีทได้ภาษาไทย
async function sheetLanguage() {
  const details = 'กรุณาแก้ไขทั้งหมด 1 จุด ดังต่อไปนี้\n\nหน้าลงนาม\n1. '
    + REGISTRY[0].choices[0].text.split('\n').join('\n   ');
  const { sandbox } = load([], 'ผลการตรวจ: ไม่ผ่าน\n\n' + details);
  const asked = [];
  sandbox.fetch = (url, options) => {
    asked.push([url, JSON.parse(options.body)]);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ details }) });
  };

  const thai = { failed: ['ORANGE:0'], passed: [], staff: ['X'], overwrite: false };
  await sandbox.sheetDetailsInPageLanguage('job1', thai);
  check('หน้าไทย: ไม่ขอข้อความมาแปล', asked.length, 0);
  check('หน้าไทย: ไม่แนบฉบับอังกฤษ', 'details_en' in thai, false);

  sandbox.LANG = 'en';
  const english = { failed: ['ORANGE:0'], passed: [], staff: ['X'], overwrite: false };
  await sandbox.sheetDetailsInPageLanguage('job1', english);
  check('หน้าอังกฤษ: ขอข้อความชุดของชีท', asked[0][0], '/summary/job1');
  check('หน้าอังกฤษ: ส่งคำตัดสินชุดเดียวกับที่จะบันทึก',
        JSON.stringify(asked[0][1]),
        JSON.stringify({ failed: ['ORANGE:0'], passed: [], staff: ['X'] }));
  check('หน้าอังกฤษ: บอกภาษา', english.lang, 'en');
  check('หน้าอังกฤษ: แนบฉบับที่แปลด้วย trSummary', english.details_en,
        sandbox.trSummary(details));
  check('หน้าอังกฤษ: ไม่เหลืออักษรไทย', /[฀-๿]/.test(english.details_en), false);
  check('หน้าอังกฤษ: เลขข้อยังอยู่ครบ',
        (english.details_en.match(/^\d+\. /gm) || []).length, 1);

  // เล่มที่ไม่ได้ส่งกลับแก้ไข เซิร์ฟเวอร์ให้ข้อความว่าง = ไม่มีอะไรให้แปล
  sandbox.fetch = () => Promise.resolve(
    { ok: true, json: () => Promise.resolve({ details: '' }) });
  const finished = { failed: [], passed: [], staff: [], overwrite: false };
  await sandbox.sheetDetailsInPageLanguage('job1', finished);
  check('เล่มเสร็จสิ้น: ไม่แนบฉบับอังกฤษ', 'details_en' in finished, false);

  // ขอข้อความไม่สำเร็จต้องไม่ไปต่อ (ไม่งั้นชีทได้ภาษาไทยไปเงียบ ๆ)
  sandbox.fetch = () => Promise.resolve({ ok: false, status: 401 });
  let refused = '';
  await sandbox.sheetDetailsInPageLanguage('job1', { failed: [], passed: [], staff: [] })
    .catch((err) => { refused = err.message; });
  check('ขอข้อความไม่สำเร็จ: หยุด ไม่บันทึกต่อ', refused, '401');
}

sheetLanguage().then(() => {
  if (failures) {
    console.error(`\nreport.html summary tests: ${failures} ข้อไม่ผ่าน`);
    process.exit(1);
  }
  console.log('report.html summary tests passed');
}, (err) => { console.error(err); process.exit(1); });
