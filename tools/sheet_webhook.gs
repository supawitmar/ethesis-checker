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
 * สิ่งที่สคริปต์นี้แตะได้มีแค่ ช่องผลการพิจารณา และช่อง Pass or not ถึง Other
 * ของ "แถวเดียว" ที่หารหัสนักศึกษาเจอ — คอลัมน์อื่น แถวอื่น แท็บอื่น ไม่ถูกแตะ
 */

var TOKEN = 'PUT-YOUR-SHARED-TOKEN-HERE';

var HEADER_DECISION = 'ผลการพิจารณา';   // ช่อง H
var HEADER_STUDENT = 'รหัส';            // ช่อง C
var HEADER_PASS = 'Pass or not';        // ช่อง L
var FLAG_HEADERS = ['Cover', 'Abstract', 'LoC', 'Main Content',
                    'Reference', 'Appendix', 'Biography', 'Other'];
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

/** แท็บที่ใช้ = แท็บซ้ายสุด (เดือนปัจจุบัน) */
function currentSheet_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
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
      if (!(name in map)) map[name] = c + 1;
      if (name === HEADER_DECISION) seen = true;
    }
    if (seen && (HEADER_STUDENT in map) && (HEADER_PASS in map)) {
      map.__row__ = r + 1;
      return map;
    }
  }
  return null;
}

/** แถวล่างสุดที่รหัสนักศึกษาตรงกัน (การตรวจรอบล่าสุดของคนนั้น) */
function findRow_(sheet, column, headerRow, studentId) {
  var last = sheet.getLastRow();
  if (last <= headerRow) return 0;
  var values = sheet.getRange(headerRow + 1, column, last - headerRow, 1).getValues();
  var want = key_(studentId);
  for (var i = values.length - 1; i >= 0; i--) {
    if (key_(values[i][0]) === want) return headerRow + 1 + i;
  }
  return 0;
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
    var map = headerMap_(sheet);
    if (!map) {
      return json_({ok: false, code: 'header_not_found',
                    error: 'หาแถวหัวตารางในแท็บ "' + sheet.getName() + '" ไม่เจอ'});
    }
    var row = findRow_(sheet, map[HEADER_STUDENT], map.__row__, body.student_id);
    if (!row) {
      return json_({ok: false, code: 'row_not_found',
                    error: 'ไม่พบแถวของรหัส ' + body.student_id +
                           ' ในแท็บ "' + sheet.getName() + '"'});
    }
    var decisionCell = sheet.getRange(row, map[HEADER_DECISION]);
    var current = String(decisionCell.getValue() || '').trim();
    if (current && !body.overwrite) {
      return json_({ok: false, code: 'already_filled', needs_overwrite: true,
                    current: current, tab: sheet.getName(), row: row,
                    error: 'แถวที่ ' + row + ' กรอกผลการพิจารณาไว้แล้วว่า "' + current + '"'});
    }

    decisionCell.setValue(body.decision);
    sheet.getRange(row, map[HEADER_PASS]).setValue(Number(body.pass_or_not) ? 1 : 0);
    var flags = body.flags || {};
    for (var i = 0; i < FLAG_HEADERS.length; i++) {
      var name = FLAG_HEADERS[i];
      if (!(name in map)) continue;      // แท็บเก่าที่ไม่มีช่องนี้ ก็แค่ข้ามไป
      sheet.getRange(row, map[name]).setValue(!!flags[name]);
    }
    // ช่องข้อความสั้นอยู่ถัดจาก Other ไปหนึ่งช่อง เขียนเมื่อมีข้อความเท่านั้น
    // (ไม่มีข้อความแล้วไปล้างของเดิม = ลบสิ่งที่เจ้าหน้าที่พิมพ์ไว้เอง)
    if (body.note && (FLAG_HEADERS[FLAG_HEADERS.length - 1] in map)) {
      sheet.getRange(row, map['Other'] + 1).setValue(body.note);
    }
    SpreadsheetApp.flush();
    return json_({ok: true, tab: sheet.getName(), row: row});
  } catch (err) {
    return json_({ok: false, code: 'script_error', error: String(err)});
  } finally {
    lock.releaseLock();
  }
}
