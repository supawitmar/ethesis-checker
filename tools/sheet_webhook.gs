/**
 * บันทึกผลตรวจ e-thesis ลงชีท "บันทึกการตรวจ E-thesis"
 * ============================================================================
 * สคริปต์นี้ติดอยู่กับชีทเอง ไม่ใช่หน้าเว็บสำหรับคน — ระบบตรวจเล่มเรียกมาเบื้องหลัง
 * ตอนเจ้าหน้าที่กดปุ่ม "บันทึกลงชีท" บนหน้ารายงาน เจ้าหน้าที่ไม่ต้องเปิดอะไรเพิ่ม
 *
 * วิธีติดตั้ง (ทำครั้งเดียว)
 *   1. เปิดชีท > ส่วนขยาย (Extensions) > Apps Script
 *   2. ลบโค้ดเดิมทิ้ง แล้ววางไฟล์นี้ทั้งไฟล์
 *   3. แก้บรรทัด TOKEN ข้างล่างเป็นค่าสุ่มยาว ๆ ของตัวเอง (สร้างจาก
 *      python -c "import secrets; print(secrets.token_urlsafe(32))")
 *   4. Deploy > New deployment > ประเภท Web app
 *        Execute as: Me       ·  Who has access: Anyone
 *      (Anyone = "ใครก็เรียกได้ถ้ารู้ URL" สคริปต์จึงตรวจ TOKEN เองอีกชั้น)
 *   5. คัดลอก Web app URL ไปตั้งเป็น SHEET_WEBHOOK_URL ในระบบ
 *      และเอา TOKEN ไปตั้งเป็น SHEET_WEBHOOK_TOKEN ให้ตรงกัน
 *   6. เปิด URL นั้นในเบราว์เซอร์ครั้งเดียวเพื่อดูว่าตอบ {"ok":true,...} (doGet
 *      เป็นการอ่านอย่างเดียว ไม่เขียนอะไรลงชีท)
 *
 * สิ่งที่สคริปต์นี้แตะได้มีแค่ ช่องวันที่ตรวจ ช่องผลการพิจารณา และช่อง Pass or not
 * ถึง Other ของ "แถวเดียว" ที่หาเจอ — คอลัมน์อื่น แถวอื่น แท็บอื่น ไม่ถูกแตะ
 */

var TOKEN = 'PUT-YOUR-SHARED-TOKEN-HERE';

var HEADER_DECISION = 'ผลการพิจารณา';   // ช่อง H
var HEADER_STUDENT = 'รหัส';            // ช่อง C
var HEADER_QUEUE = 'Queue';             // ช่อง D — วันที่นักศึกษาเข้าคิว ใช้ระบุรอบ
var HEADER_CHECKED = 'วันที่ตรวจ';       // ช่อง E
var HEADER_PASS = 'Pass or not';        // ช่อง L

/**
 * ช่องติ๊กจุดผิด — key คือชื่อที่ระบบตรวจส่งมา ส่วน names คือชื่อหัวตารางที่ยอมรับ
 *
 * แท็บของเดือนล่าสุดใช้หัวว่า "ToC" ส่วนแท็บเก่าใช้ "LoC" (เจ้าหน้าที่ทักมา ก.ย. 2569)
 * ถ้ารับชื่อเดียว ช่องสารบัญจะถูกข้ามเงียบ ๆ โดยไม่มีอะไรบอกว่าติ๊กไม่ลง
 */
var FLAG_COLUMNS = [
  {key: 'Cover',        names: ['Cover']},
  {key: 'Abstract',     names: ['Abstract']},
  {key: 'LoC',          names: ['LoC', 'ToC', 'TOC', 'สารบัญ']},
  {key: 'Main Content', names: ['Main Content']},
  {key: 'Reference',    names: ['Reference', 'References']},
  {key: 'Appendix',     names: ['Appendix', 'Appendices']},
  {key: 'Biography',    names: ['Biography']},
  {key: 'Other',        names: ['Other', 'Others']},
];
var HEADER_SCAN_ROWS = 10;              // หัวตารางอยู่ไม่เกินสิบแถวแรก (มีแถวสถิติอยู่บน)

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/** ตัดช่องว่างและตัวพิมพ์เล็ก-ใหญ่ทิ้งก่อนเทียบ — รหัสในชีทพิมพ์เว้นวรรคไม่เท่ากัน */
function key_(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/\s+/g, '').toUpperCase();
}

/**
 * ทำวันที่ให้เป็น yyyy-mm-dd ก่อนเทียบ
 *
 * ช่องวันที่ในชีทเป็นได้ทั้งวันที่จริง (หลังแปลงจาก .xlsx) และข้อความ "31/8/2026"
 * ถ้าเทียบเป็นข้อความดิบจะไม่มีวันตรงกัน
 */
function dateKey_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  var text = String(value === null || value === undefined ? '' : value).trim();
  if (!text) return '';
  var iso = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (iso) return iso[1] + '-' + pad2_(iso[2]) + '-' + pad2_(iso[3]);
  var dmy = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);   // รูปแบบที่ชีทใช้อยู่
  if (dmy) return dmy[3] + '-' + pad2_(dmy[2]) + '-' + pad2_(dmy[1]);
  return text;
}

function pad2_(value) {
  return ('0' + String(value)).slice(-2);
}

/** yyyy-mm-dd -> วันที่จริง (เขียนเป็นวันที่ ไม่ใช่ข้อความ ชีทจะได้จัดรูปแบบเองตามคอลัมน์) */
function toDate_(iso) {
  var m = String(iso || '').match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** แท็บที่ใช้ = แท็บซ้ายสุด (เดือนปัจจุบัน) */
function currentSheet_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
}

/** ชื่อหัวตารางที่ตัดช่องว่างและตัวพิมพ์ทิ้งแล้ว — "ToC" กับ "TOC" ต้องนับเป็นชื่อเดียวกัน */
function headerKey_(name) {
  return String(name === null || name === undefined ? '' : name)
    .replace(/\s+/g, '').toLowerCase();
}

/** เลขคอลัมน์ของหัวตารางชื่อแรกที่หาเจอ (0 = ไม่มีในแท็บนี้) */
function col_(map, names) {
  for (var i = 0; i < names.length; i++) {
    var k = headerKey_(names[i]);
    if (k in map) return map[k];
  }
  return 0;
}

/**
 * หาแถวหัวตาราง แล้วทำทะเบียน "ชื่อหัวตาราง -> เลขคอลัมน์"
 * แท็บเก่าบางแท็บคอลัมน์ไม่ตรงกัน (ไม่มี Template) จึงห้ามฝังตัวอักษรคอลัมน์ไว้ในโค้ด
 */
function headerMap_(sheet) {
  var rows = Math.min(HEADER_SCAN_ROWS, sheet.getLastRow());
  if (!rows) return null;
  var values = sheet.getRange(1, 1, rows, sheet.getLastColumn()).getValues();
  for (var r = 0; r < values.length; r++) {
    var map = {}, seen = false;
    for (var c = 0; c < values[r].length; c++) {
      var name = String(values[r][c] || '').trim();
      if (!name) continue;
      var k = headerKey_(name);
      if (!(k in map)) map[k] = c + 1;
      if (k === headerKey_(HEADER_DECISION)) seen = true;
    }
    if (seen && col_(map, [HEADER_STUDENT]) && col_(map, [HEADER_PASS])) {
      map.__row__ = r + 1;
      return map;
    }
  }
  return null;
}

/**
 * แถวของนักศึกษาคนนี้
 *
 * นักศึกษาคนเดียวส่งเล่มได้หลายรอบ (เล่มใหม่ แล้วเล่มแก้ไข) ในเดือนเดียวกัน
 * "วันที่เข้าคิว" จึงเป็นตัวชี้ว่าแถวไหน — เจ้าหน้าที่กรอกมาก็ใช้กรองเลย
 * ถ้าไม่กรอกแล้วเจอหลายแถว ต้องไม่เดา (คืนรายการวันที่ให้เลือก)
 */
function findRows_(sheet, map, studentId, queueKey) {
  var last = sheet.getLastRow();
  var headerRow = map.__row__;
  if (last <= headerRow) return [];
  var idAt = col_(map, [HEADER_STUDENT]);
  var queueAt = col_(map, [HEADER_QUEUE]);
  var width = Math.max(idAt, queueAt);
  var values = sheet.getRange(headerRow + 1, 1, last - headerRow, width).getValues();
  var want = key_(studentId);
  var found = [];
  for (var i = 0; i < values.length; i++) {
    if (key_(values[i][idAt - 1]) !== want) continue;
    var queue = queueAt ? dateKey_(values[i][queueAt - 1]) : '';
    if (queueKey && queue !== queueKey) continue;
    found.push({row: headerRow + 1 + i, queue: queue});
  }
  return found;
}

function doGet() {
  var sheet = currentSheet_();
  var map = headerMap_(sheet);
  return json_({ok: !!map, tab: sheet.getName(),
                header_row: map ? map.__row__ : 0,
                rows: sheet.getLastRow()});
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
  } catch (err) {
    return json_({ok: false, code: 'busy', error: 'ชีทกำลังถูกเขียนอยู่ กรุณากดใหม่อีกครั้ง'});
  }
  try {
    var body = {};
    try {
      body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    } catch (err) {
      return json_({ok: false, code: 'bad_body', error: 'ข้อมูลที่ส่งมาอ่านไม่ได้'});
    }
    if (!TOKEN || TOKEN === 'PUT-YOUR-SHARED-TOKEN-HERE' || body.token !== TOKEN) {
      return json_({ok: false, code: 'bad_token', error: 'โทเค็นไม่ถูกต้อง'});
    }
    var sheet = currentSheet_();
    var tab = sheet.getName();
    var map = headerMap_(sheet);
    if (!map) {
      return json_({ok: false, code: 'header_not_found',
                    error: 'หาแถวหัวตารางในแท็บ "' + tab + '" ไม่เจอ'});
    }
    var queueKey = dateKey_(body.queue_date || '');
    var found = findRows_(sheet, map, body.student_id, queueKey);
    if (!found.length) {
      return json_({ok: false, code: 'row_not_found',
                    error: 'ไม่พบแถวของรหัส ' + body.student_id +
                           (queueKey ? ' ที่เข้าคิววันที่ ' + queueKey : '') +
                           ' ในแท็บ "' + tab + '"'});
    }
    if (found.length > 1 && !queueKey) {
      // ไม่เดาว่าแถวไหน — บอกวันที่เข้าคิวที่มีอยู่ให้เจ้าหน้าที่เลือก
      var dates = found.map(function (item) { return item.queue || '(ไม่มีวันที่)'; });
      return json_({ok: false, code: 'many_rows', tab: tab, queues: dates,
                    error: 'รหัส ' + body.student_id + ' มี ' + found.length +
                           ' แถวในแท็บ "' + tab + '" (เข้าคิว ' + dates.join(', ') +
                           ') กรุณาระบุวันที่เข้าคิวให้ตรงแถวที่ต้องการ'});
    }
    var row = found[found.length - 1].row;
    var decisionCell = sheet.getRange(row, col_(map, [HEADER_DECISION]));
    var current = String(decisionCell.getValue() || '').trim();
    if (current && !body.overwrite) {
      return json_({ok: false, code: 'already_filled', needs_overwrite: true,
                    current: current, tab: tab, row: row,
                    error: 'แถวที่ ' + row + ' กรอกผลการพิจารณาไว้แล้วว่า "' + current + '"'});
    }

    var checkedAt = col_(map, [HEADER_CHECKED]);
    var checked = toDate_(body.checked_date);
    if (checked && checkedAt) {
      sheet.getRange(row, checkedAt).setValue(checked);
    }
    decisionCell.setValue(body.decision);
    sheet.getRange(row, col_(map, [HEADER_PASS])).setValue(Number(body.pass_or_not) ? 1 : 0);
    var flags = body.flags || {};
    var missing = [];
    for (var i = 0; i < FLAG_COLUMNS.length; i++) {
      var spec = FLAG_COLUMNS[i];
      var at = col_(map, spec.names);
      if (!at) {                         // แท็บนี้ไม่มีช่องนั้น — ต้องบอก ไม่ใช่ข้ามเงียบ ๆ
        missing.push(spec.key);
        continue;
      }
      sheet.getRange(row, at).setValue(!!flags[spec.key]);
    }
    // ช่องข้อความสั้นอยู่ถัดจาก Other ไปหนึ่งช่อง เขียนเมื่อมีข้อความเท่านั้น
    // (ไม่มีข้อความแล้วไปล้างของเดิม = ลบสิ่งที่เจ้าหน้าที่พิมพ์ไว้เอง)
    var otherAt = col_(map, ['Other', 'Others']);
    if (body.note && otherAt) {
      sheet.getRange(row, otherAt + 1).setValue(body.note);
    }
    SpreadsheetApp.flush();
    return json_({ok: true, tab: tab, row: row, missing: missing,
                  queue: found[found.length - 1].queue});
  } catch (err) {
    return json_({ok: false, code: 'script_error', error: String(err)});
  } finally {
    lock.releaseLock();
  }
}
