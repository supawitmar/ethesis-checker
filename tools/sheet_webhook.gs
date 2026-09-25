/**
 * บันทึกผลตรวจ e-thesis ลงชีท "บันทึกการตรวจ E-thesis"
 * ============================================================================
 * สคริปต์นี้ติดอยู่กับชีทเอง ไม่ใช่หน้าเว็บสำหรับคน — ระบบตรวจเล่มเรียกมาเบื้องหลัง
 * ตอนเจ้าหน้าที่กดปุ่ม "บันทึกลงชีท" บนหน้ารายงาน เจ้าหน้าที่ไม่ต้องเปิดอะไรเพิ่ม
 *
 * วิธีติดตั้ง (ทำครั้งเดียว)
 *   1. เปิดชีท > ส่วนขยาย (Extensions) > Apps Script
 *   2. ลบโค้ดเดิมทิ้ง แล้ววางไฟล์นี้ทั้งไฟล์
 *   3. ตั้งโทเค็นลับ (ค่าสุ่มยาว ๆ สร้างจาก
 *      python -c "import secrets; print(secrets.token_urlsafe(32))")
 *      วิธีที่แนะนำ: ไอคอนเฟือง (Project Settings) > Script properties >
 *      Add script property > ชื่อ TOKEN ค่าคือโทเค็นของตัวเอง
 *      ตั้งแบบนี้แล้ว "วางสคริปต์ใหม่ทับกี่ครั้งโทเค็นก็ไม่หาย"
 *      (จะใส่ในบรรทัด TOKEN ข้างล่างแทนก็ได้ แต่จะหายทุกครั้งที่วางสคริปต์ใหม่)
 *   4. Deploy > New deployment > ประเภท Web app
 *        Execute as: Me       ·  Who has access: Anyone
 *      (Anyone = "ใครก็เรียกได้ถ้ารู้ URL" สคริปต์จึงตรวจ TOKEN เองอีกชั้น)
 *   5. คัดลอก Web app URL ไปตั้งเป็น SHEET_WEBHOOK_URL ในระบบ
 *      และเอา TOKEN ไปตั้งเป็น SHEET_WEBHOOK_TOKEN ให้ตรงกัน
 *   6. เปิด URL นั้นในเบราว์เซอร์ครั้งเดียวเพื่อดูว่าตอบ {"ok":true,...} (doGet
 *      เป็นการอ่านอย่างเดียว ไม่เขียนอะไรลงชีท)
 *
 * สิ่งที่สคริปต์นี้แตะได้มีแค่ ช่องวันที่ตรวจ ช่องผลการพิจารณา ช่อง Pass or not ถึง Other
 * และช่อง "รายละเอียดที่ส่งให้แก้ไข" (ถ้ามี) ของ "แถวเดียว" ที่หาเจอ
 * คอลัมน์อื่น แถวอื่น แท็บอื่น ไม่ถูกแตะ
 *
 * ช่อง "รายละเอียดที่ส่งให้แก้ไข" เป็นของเพิ่มทีหลัง ไม่บังคับ — เพิ่มหัวตารางชื่อนี้
 * ต่อท้ายตารางของแท็บไหน แท็บนั้นก็จะได้ข้อความเต็มที่ส่งให้นักศึกษาไปด้วย
 * (เขียนเฉพาะเล่มที่ผลเป็น "ส่งกลับแก้ไข") แท็บที่ไม่มีหัวตารางนี้ทำงานเหมือนเดิมทุกอย่าง
 * แนะนำให้ตั้งช่องนั้นเป็น "ตัดส่วนเกิน" (clip) ไม่ใช่ "ตัดข้อความ" ไม่งั้นแถวจะสูงมาก
 */

// ใส่ตรงนี้ก็ได้ แต่ค่าจะหายทุกครั้งที่วางสคริปต์ฉบับใหม่ทับ — ที่ปลอดภัยกว่าคือ
// Project Settings > Script properties ชื่อ TOKEN (ดูวิธีติดตั้งข้อ 3)
var TOKEN = '';

/** โทเค็นที่ใช้จริง: เอาจาก Script properties ก่อน ไม่มีค่อยใช้ค่าในไฟล์ */
function token_() {
  var stored = '';
  try {
    stored = PropertiesService.getScriptProperties().getProperty('TOKEN') || '';
  } catch (err) {
    stored = '';
  }
  stored = String(stored).trim();
  var inline = String(TOKEN || '').trim();
  if (inline === 'PUT-YOUR-SHARED-TOKEN-HERE') inline = '';
  return stored || inline;
}

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
/**
 * ช่อง "รายละเอียดที่ส่งให้แก้ไข" — ข้อความเต็มที่ส่งให้นักศึกษา (เจ้าหน้าที่สั่ง ก.ย. 2569)
 *
 * เป็นคอลัมน์ที่เพิ่มต่อท้ายตาราง แท็บไหนยังไม่ได้เพิ่มหัวตารางก็ข้ามไป (แล้วบอกว่าข้าม)
 * ไม่ใช่ไปเขียนทับคอลัมน์อื่นเพราะเดาตำแหน่งเอง
 */
var HEADER_DETAILS = ['รายละเอียดที่ส่งให้แก้ไข', 'รายละเอียดที่ส่งให้นักศึกษาแก้ไข',
                      'รายละเอียด', 'Details'];
var HEADER_SCAN_ROWS = 10;              // หัวตารางอยู่ไม่เกินสิบแถวแรก (มีแถวสถิติอยู่บน)

/**
 * ข้อความที่ปลอดภัยพอจะเขียนลงช่อง — ขึ้นต้นด้วย = + - @ ชีทจะอ่านเป็นสูตรทันที
 * ใส่ ' นำหน้าไว้ ชีทจะเก็บเป็นข้อความล้วน (เครื่องหมายนี้ไม่ถูกนับเป็นตัวอักษรในช่อง)
 */
function asText_(value) {
  var text = String(value === null || value === undefined ? '' : value);
  return /^[=+\-@]/.test(text) ? "'" + text : text;
}

/**
 * ตั้งช่องให้ "ตัดส่วนเกิน" — ข้อความเต็มยังอยู่ครบในช่อง แค่ไม่ดันแถวให้สูง
 *
 * เจ้าหน้าที่สั่ง (ก.ย. 2569) "ไม่ต้องตัดข้อความ (ไม่ให้แถวเลื่อน หรือกว้าง)" — ข้อความ
 * ที่ส่งให้นักศึกษายาว 800-1500 ตัวอักษรและมีหลายบรรทัด ถ้าปล่อยให้ช่องตัดข้อความ
 * ขึ้นบรรทัดใหม่ แถวเดียวจะสูงเป็นสิบบรรทัด ตารางทั้งเดือนอ่านไม่ได้
 * ตั้งทีละช่องที่เขียน ไม่ยุ่งกับช่องอื่นหรือทั้งคอลัมน์
 *
 * ล้อมด้วย try ไว้เพราะเป็นแค่การจัดรูปแบบ — ถ้าทำไม่ได้ก็ไม่ควรทำให้การบันทึกล้มทั้งครั้ง
 */
function clip_(range) {
  try {
    range.setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
  } catch (err) {
    try { range.setWrap(false); } catch (err2) { /* ไม่เป็นไร ข้อความเขียนลงไปแล้ว */ }
  }
}

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
function isDate_(value) {
  // instanceof ใช้ไม่ได้เสมอไป (ค่าที่มาจากคนละบริบทมี Date คนละตัว) จึงดูจากชนิดที่แท้จริง
  return Object.prototype.toString.call(value) === '[object Date]';
}

function dateKey_(value) {
  if (isDate_(value)) {
    // ต้องอ่านด้วยเขตเวลา "ของสเปรดชีต" ไม่ใช่ของโปรเจกต์สคริปต์ — สองค่านี้ตั้งแยกกัน
    // และโปรเจกต์ใหม่ของ Apps Script ตั้งต้นเป็นเขตเวลาอเมริกา ถ้าใช้ของสคริปต์
    // ช่องวันที่จริงในชีทจะอ่านได้เป็นวันก่อนหน้า แล้วหาแถวตามวันที่เข้าคิวไม่เจอ
    return Utilities.formatDate(value, sheetTimeZone_(), 'yyyy-MM-dd');
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

/** เขตเวลาของสเปรดชีต (ไม่ใช่ของโปรเจกต์สคริปต์ ซึ่งตั้งแยกกันและมักไม่ตรงกัน) */
function sheetTimeZone_() {
  try {
    return SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone()
           || Session.getScriptTimeZone();
  } catch (err) {
    return Session.getScriptTimeZone();
  }
}

/**
 * yyyy-mm-dd -> วันที่จริง (เขียนเป็นวันที่ ไม่ใช่ข้อความ ชีทจะได้จัดรูปแบบเองตามคอลัมน์)
 *
 * ค่าที่เขียนลงชีทเป็น "เวลาจริงหนึ่งจุด" ส่วนชีทแสดงผลตามเขตเวลาของสเปรดชีต ถ้าสร้าง
 * วันที่ด้วยเขตเวลาของสคริปต์ (คนละค่ากับของสเปรดชีต ตั้งคนละที่) วันจะเลื่อนไปหนึ่งวัน
 * ทันทีเมื่อสองค่าไม่ตรงกัน — เจ้าหน้าที่กรอก 25/9/2026 แล้วชีทได้ 24/9/2026 (ก.ย. 2569)
 * จึงสร้างวันที่ "ในเขตเวลาของสเปรดชีต" ตรง ๆ ด้วย Utilities.parseDate
 * ไม่ใช่หวังว่าสองเขตเวลาจะห่างกันไม่เกินครึ่งวัน (ของ Google ห่างกันได้ถึง 25 ชั่วโมง)
 *
 * **เที่ยงคืนเท่านั้น ห้ามมีเวลาติดไปด้วย** — ชีทเก็บวันที่เป็นตัวเลขวัน เวลาที่ติดมา
 * กลายเป็นเศษของวัน ช่อง "จำนวนวันที่ใช้ตรวจ" ที่ลบวันที่กันจึงได้ 0.5 แทนที่จะเป็น 0
 * (เจ้าหน้าที่แจ้ง ก.ย. 2569 หลังรอบที่ตั้งไว้เที่ยงวันเพื่อกันวันเลื่อน)
 * ตอนนี้สร้างในเขตเวลาของสเปรดชีตแล้ว เที่ยงคืนจึงตรงวันอยู่แล้วโดยไม่ต้องเผื่อ
 */
function toDate_(iso) {
  var m = String(iso || '').match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (!m) return null;
  var day = m[1] + '-' + pad2_(m[2]) + '-' + pad2_(m[3]);
  try {
    return Utilities.parseDate(day + ' 00:00:00', sheetTimeZone_(), 'yyyy-MM-dd HH:mm:ss');
  } catch (err) {
    // กันพังไว้ — เที่ยงคืนของเขตเวลาสคริปต์ ยังเป็นวันเต็มเหมือนกัน
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }
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

/**
 * เปิด URL นี้ในเบราว์เซอร์เพื่อดูว่าติดตั้งครบหรือยัง (อ่านอย่างเดียว ไม่เขียนอะไร)
 *
 * token_set บอกแค่ว่า "ตั้งโทเค็นไว้แล้วหรือยัง" ไม่ได้บอกค่า — ห้ามส่งค่าจริงออกไป
 * เพราะหน้านี้ใครก็เปิดได้ถ้ารู้ URL
 */
function doGet() {
  var sheet = currentSheet_();
  var map = headerMap_(sheet);
  var missing = [];
  for (var i = 0; map && i < FLAG_COLUMNS.length; i++) {
    if (!col_(map, FLAG_COLUMNS[i].names)) missing.push(FLAG_COLUMNS[i].key);
  }
  return json_({ok: !!map && !!token_(), tab: sheet.getName(),
                token_set: !!token_(),
                header_row: map ? map.__row__ : 0,
                missing_columns: missing,
                // ช่องนี้ไม่บังคับ (เพิ่มเองทีหลัง) จึงแยกออกมา ไม่ปนกับช่องที่ขาด
                details_column: !!(map && col_(map, HEADER_DETAILS)),
                // สองค่านี้ตั้งแยกกัน ถ้าไม่ตรงกันเคยทำให้วันที่เลื่อนไปหนึ่งวัน
                sheet_timezone: sheetTimeZone_(),
                script_timezone: Session.getScriptTimeZone(),
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
    // แยกสองกรณีให้ชัด: ยังไม่ได้ตั้งโทเค็นในชีท กับตั้งแล้วแต่ไม่ตรงกับฝั่งระบบ
    // ทางแก้คนละทางกัน และกรณีแรกเกิดทุกครั้งที่วางสคริปต์ใหม่โดยลืมตั้งโทเค็น
    var secret = token_();
    if (!secret) {
      return json_({ok: false, code: 'no_token',
                    error: 'ยังไม่ได้ตั้งโทเค็นในสคริปต์ของชีท — ไปที่ Project Settings > ' +
                           'Script properties แล้วเพิ่มค่าชื่อ TOKEN ให้ตรงกับ ' +
                           'SHEET_WEBHOOK_TOKEN ของระบบ แล้ว Deploy เวอร์ชันใหม่'});
    }
    if (String(body.token || '') !== secret) {
      return json_({ok: false, code: 'bad_token',
                    error: 'โทเค็นไม่ตรงกับที่ตั้งไว้ในชีท — ตรวจว่า SHEET_WEBHOOK_TOKEN ' +
                           'ของระบบกับ TOKEN ในชีทเป็นค่าเดียวกัน แล้ว Deploy เวอร์ชันใหม่'});
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
      sheet.getRange(row, otherAt + 1).setValue(asText_(body.note));
    }
    // รายละเอียดที่ส่งให้นักศึกษาแก้ไข — ระบบส่งมาเฉพาะเล่มที่ "ส่งกลับแก้ไข"
    // ไม่มีข้อความ = ไม่ต้องแตะช่องนี้ (ไปล้างของเดิม = ลบสิ่งที่เจ้าหน้าที่พิมพ์ไว้เอง)
    if (body.details) {
      var detailsAt = col_(map, HEADER_DETAILS);
      if (detailsAt) {
        var detailsCell = sheet.getRange(row, detailsAt);
        detailsCell.setValue(asText_(body.details));
        clip_(detailsCell);
      } else {
        missing.push('รายละเอียดที่ส่งให้แก้ไข');   // ต้องบอก ไม่ใช่ทิ้งข้อความหายเงียบ ๆ
      }
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
