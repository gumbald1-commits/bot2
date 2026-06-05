import threading
import os
import logging
import json
import calendar
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from flask import Flask, render_template_string, request, session, redirect, jsonify

# ===== ОБЩИЕ НАСТРОЙКИ =====
TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = "128OL9NnhHepDEOuw3Wre-rpg0j7FwIDDjYe0f3Ft50Y"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "luna2024")

logging.basicConfig(level=logging.INFO)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

MASTERS = {
    "kameliya": {"name": "Камелия", "services": ["manicure_kameliya", "pedicure_kameliya", "both_kameliya"]},
    "novenkaya": {"name": "Новенькая", "services": ["manicure_novenkaya"]},
}
SERVICES = {
    "manicure_kameliya": {"name": "Маникюр", "price": "1 800 ₽", "dur": "1ч 30м", "min": 90, "slots": 3},
    "pedicure_kameliya": {"name": "Педикюр", "price": "2 500 ₽", "dur": "2ч", "min": 120, "slots": 4},
    "both_kameliya": {"name": "Маникюр + педикюр", "price": "3 800 ₽", "dur": "3ч", "min": 180, "slots": 6},
    "manicure_novenkaya": {"name": "Маникюр", "price": "1 800 ₽", "dur": "2ч 30м", "min": 150, "slots": 5},
}
MASTER_SERVICES = {
    "kameliya": ["manicure_kameliya", "pedicure_kameliya", "both_kameliya"],
    "novenkaya": ["manicure_novenkaya"],
}

bookings = {}


def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1


def generate_slots():
    slots = []
    t = datetime.strptime("09:00", "%H:%M")
    end = datetime.strptime("17:30", "%H:%M")
    while t <= end:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=30)
    return slots


SLOTS = generate_slots()


def get_booked_times(master_name, date):
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()
        blocked = set()
        for row in all_values[1:]:
            if len(row) >= 11 and row[4] == master_name and row[6] == date and row[9] not in ("Отменена", "Отменена клиентом"):
                start_time = row[7]
                try:
                    duration = int(str(row[10]).strip())
                    t = datetime.strptime(start_time, "%H:%M")
                    slots_needed = duration // 30
                    for i in range(slots_needed):
                        blocked.add((t + timedelta(minutes=30 * i)).strftime("%H:%M"))
                except Exception:
                    blocked.add(start_time)
        return blocked
    except Exception as e:
        logging.error(f"Get booked times error: {e}")
        return set()


def get_free_slots(master_name, date, duration_slots):
    blocked = get_booked_times(master_name, date)
    free = []
    for i, slot in enumerate(SLOTS):
        needed = SLOTS[i:i + duration_slots]
        if len(needed) == duration_slots and not any(s in blocked for s in needed):
            free.append(slot)
    return free


def get_upcoming_bookings():
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        result = []
        for i, row in enumerate(records, start=2):
            if row.get("Статус") not in ("Отменена", "Отменена клиентом"):
                result.append({"row": i, "data": row})
        return result
    except Exception as e:
        logging.error(f"Get bookings error: {e}")
        return []


def cancel_booking_by_row(row_num):
    try:
        sheet = get_sheet()
        sheet.update_cell(row_num, 10, "Отменена")
        return True
    except Exception as e:
        logging.error(f"Cancel booking error: {e}")
        return False


def save_booking(user_name, phone, username, user_id, master, service, date, time, price, duration_min):
    try:
        sheet = get_sheet()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet.append_row([now, user_name, phone, username, master, service, date, time, price, "Ожидает", duration_min, user_id, "Нет"])
    except Exception as e:
        logging.error(f"Save booking error: {e}")


def encode_svc(svc_id):
    return svc_id.replace("_kameliya", "~k").replace("_novenkaya", "~n")


def decode_svc(s):
    return s.replace("~k", "_kameliya").replace("~n", "_novenkaya")


def get_bookings_for_date(date_str):
    sheet = get_sheet()
    all_values = sheet.get_all_values()
    result = []
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) >= 10 and row[6] == date_str and row[9] not in ("Отменена", "Отменена клиентом"):
            result.append({
                "row": i,
                "master": row[4],
                "service": row[5],
                "date": row[6],
                "time": row[7],
                "price": row[8],
                "client": row[1],
                "phone": row[2],
                "username": row[3],
                "duration": int(row[10]) if len(row) >= 11 and row[10] else 60
            })
    return result


def build_schedule(bookings_list):
    web_slots = generate_web_slots()
    schedule = {
        "kameliya": {s: {"type": "slot-free"} for s in web_slots},
        "novenkaya": {s: {"type": "slot-free"} for s in web_slots},
    }
    master_map = {"Камелия": "kameliya", "Новенькая": "novenkaya"}
    for b in bookings_list:
        master_key = master_map.get(b["master"])
        if not master_key:
            continue
        duration = b["duration"]
        slots_needed = max(1, duration // 30)
        try:
            t = datetime.strptime(b["time"], "%H:%M")
            for i in range(slots_needed):
                slot = (t + timedelta(minutes=30 * i)).strftime("%H:%M")
                if slot not in schedule[master_key]:
                    continue
                if i == 0:
                    schedule[master_key][slot] = {
                        "type": "slot-busy",
                        "master_class": master_key,
                        "client": b["client"],
                        "service": b["service"],
                        "price": b["price"],
                        "row": b["row"],
                    }
                else:
                    schedule[master_key][slot] = {
                        "type": "slot-continuation",
                        "master_class": master_key,
                    }
        except Exception:
            pass
    return schedule


def generate_web_slots():
    slots = []
    t = datetime.strptime("08:00", "%H:%M")
    end = datetime.strptime("20:00", "%H:%M")
    while t <= end:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=30)
    return slots


WEB_SLOTS = generate_web_slots()

# ===== FLASK =====
flask_app = Flask(__name__)
flask_app.secret_key = os.environ.get("SECRET_KEY", "nailbar2024")

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nail Bar LUNA — Расписание</title>
<script>setTimeout(function(){ location.reload(); }, 60000);</script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #f5f0ff; color: #333; }
.header { background: linear-gradient(135deg, #9b59b6, #e8a0b4); padding: 16px 24px; color: white; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.header h1 { font-size: 20px; }
.date-nav { display: flex; align-items: center; gap: 10px; }
.date-nav a { color: white; text-decoration: none; font-size: 20px; padding: 2px 10px; border-radius: 6px; background: rgba(255,255,255,0.2); }
.date-nav span { font-size: 15px; font-weight: 600; cursor: pointer; border-bottom: 2px solid rgba(255,255,255,0.5); padding-bottom: 2px; }
.logout { color: rgba(255,255,255,0.8); text-decoration: none; font-size: 13px; }
.toolbar { padding: 12px 24px; display: flex; justify-content: space-between; align-items: center; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.legend-dot { width: 12px; height: 12px; border-radius: 3px; }
.add-btn { background: linear-gradient(135deg, #9b59b6, #e8a0b4); color: white; border: none; padding: 8px 18px; border-radius: 8px; font-size: 13px; cursor: pointer; font-weight: 600; }
.container { padding: 0 24px 24px; overflow-x: auto; }
.schedule { display: grid; grid-template-columns: 65px 1fr 1fr; gap: 2px; min-width: 480px; }
.col-header { background: #9b59b6; color: white; padding: 10px; text-align: center; font-weight: 600; border-radius: 6px; margin-bottom: 2px; font-size: 14px; }
.time-col { background: #e8e0f5; padding: 6px; text-align: center; font-size: 12px; font-weight: 600; color: #666; border-radius: 4px; display: flex; align-items: center; justify-content: center; }
.slot-free { background: white; border-radius: 4px; min-height: 34px; border: 1px solid #e0d0f0; cursor: pointer; transition: background 0.15s; }
.slot-free:hover { background: #f0e8ff; }
.slot-busy { border-radius: 4px; min-height: 34px; padding: 4px 8px; font-size: 11px; line-height: 1.4; color: white; overflow: hidden; position: relative; }
.slot-busy.kameliya { background: linear-gradient(135deg, #9b59b6, #c39bd3); }
.slot-busy.novenkaya { background: linear-gradient(135deg, #e8a0b4, #f1c0cc); color: #333; }
.slot-busy .client { font-weight: 700; font-size: 12px; }
.slot-busy .cancel-btn { position: absolute; top: 3px; right: 4px; background: rgba(0,0,0,0.2); border: none; color: white; border-radius: 3px; font-size: 10px; cursor: pointer; padding: 1px 4px; }
.slot-continuation { border-radius: 4px; min-height: 34px; }
.slot-continuation.kameliya { background: #d7b8e8; }
.slot-continuation.novenkaya { background: #f5d5de; }
.modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 100; justify-content: center; align-items: center; }
.modal-overlay.active { display: flex; }
.modal { background: white; border-radius: 16px; padding: 28px; width: 360px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
.modal h3 { color: #9b59b6; margin-bottom: 20px; font-size: 18px; }
.form-group { margin-bottom: 14px; }
.form-group label { display: block; font-size: 13px; color: #666; margin-bottom: 5px; font-weight: 600; }
.form-group input, .form-group select { width: 100%; padding: 9px 12px; border: 2px solid #e0d0f0; border-radius: 8px; font-size: 14px; outline: none; font-family: inherit; }
.form-group input:focus, .form-group select:focus { border-color: #9b59b6; }
.modal-btns { display: flex; gap: 10px; margin-top: 20px; }
.modal-btns button { flex: 1; padding: 10px; border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; border: none; }
.btn-save { background: linear-gradient(135deg, #9b59b6, #e8a0b4); color: white; }
.btn-cancel-modal { background: #f0e8ff; color: #9b59b6; }
.login-wrap { display: flex; justify-content: center; align-items: center; min-height: 100vh; }
.login-box { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 10px 40px rgba(155,89,182,0.2); width: 320px; text-align: center; }
.login-box h2 { color: #9b59b6; margin-bottom: 24px; }
.login-box input { width: 100%; padding: 12px; border: 2px solid #e0d0f0; border-radius: 8px; font-size: 15px; margin-bottom: 16px; outline: none; }
.login-box button { width: 100%; padding: 12px; background: linear-gradient(135deg, #9b59b6, #e8a0b4); color: white; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; font-weight: 600; }
.error { color: #e74c3c; font-size: 13px; margin-bottom: 12px; }
.cal-wrap { display:none; position:fixed; top:60px; left:50%; transform:translateX(-50%); background:white; border-radius:16px; padding:20px; box-shadow:0 10px 40px rgba(0,0,0,0.3); z-index:201; width:300px; }
.cal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; z-index:200; }
.cal-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
.cal-nav { background:none; border:none; font-size:20px; cursor:pointer; color:#9b59b6; padding:4px 8px; }
.cal-month { font-weight:700; color:#9b59b6; font-size:16px; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; text-align:center; }
.cal-day-name { font-size:11px; color:#999; font-weight:600; padding:4px 0; }
.cal-day { padding:6px 2px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:500; transition:background 0.15s; }
.cal-day:hover { background:#f0e8ff; }
.cal-day.today { background:#9b59b6; color:white; }
</style>
</head>
<body>
{% if not logged_in %}
<div class="login-wrap">
  <div class="login-box">
    <h2>💅 Nail Bar LUNA</h2>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post">
      <input type="password" name="password" placeholder="Введите пароль" autofocus>
      <button type="submit">Войти</button>
    </form>
  </div>
</div>
{% else %}
<div class="header">
  <h1>💅 Nail Bar LUNA</h1>
  <div class="date-nav">
    <a href="?date={{ prev_date }}">‹</a>
    <span onclick="toggleCalendar()">{{ display_date }}</span>
    <a href="?date={{ next_date }}">›</a>
  </div>
  <a href="/logout" class="logout">Выйти</a>
</div>
<div class="toolbar">
  <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#9b59b6"></div>Камелия</div>
    <div class="legend-item"><div class="legend-dot" style="background:#e8a0b4"></div>Новенькая</div>
    <div class="legend-item"><div class="legend-dot" style="background:white;border:1px solid #ddd"></div>Свободно</div>
  </div>
  <button class="add-btn" onclick="openModal()">+ Добавить запись</button>
</div>
<div class="container">
  <div class="schedule">
    <div class="col-header">Время</div>
    <div class="col-header">💅 Камелия</div>
    <div class="col-header">✨ Новенькая</div>
    {% for slot in slots %}
    <div class="time-col">{{ slot }}</div>
    {% for master_key in ['kameliya', 'novenkaya'] %}
    {% set s = schedule[master_key][slot] %}
    {% if s.type == 'slot-busy' %}
    <div class="slot-busy {{ master_key }}">
      <button class="cancel-btn" onclick="cancelBooking({{ s.row }})">✕</button>
      <div class="client">{{ s.client }}</div>
      <div>{{ s.service }}</div>
      <div>{{ s.price }}</div>
    </div>
    {% elif s.type == 'slot-continuation' %}
    <div class="slot-continuation {{ master_key }}"></div>
    {% else %}
    <div class="slot-free" onclick="openModal('{{ master_key }}', '{{ slot }}')"></div>
    {% endif %}
    {% endfor %}
    {% endfor %}
  </div>
</div>
<div class="cal-overlay" id="calOverlay" onclick="closeCalendar()"></div>
<div class="cal-wrap" id="calWrap">
  <div class="cal-header">
    <button class="cal-nav" onclick="changeMonth(-1)">‹</button>
    <span class="cal-month" id="calMonth"></span>
    <button class="cal-nav" onclick="changeMonth(1)">›</button>
  </div>
  <div class="cal-grid">
    <div class="cal-day-name">Пн</div><div class="cal-day-name">Вт</div><div class="cal-day-name">Ср</div>
    <div class="cal-day-name">Чт</div><div class="cal-day-name">Пт</div><div class="cal-day-name">Сб</div><div class="cal-day-name">Вс</div>
  </div>
  <div class="cal-grid" id="calDays"></div>
</div>
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3>📝 Новая запись</h3>
    <div class="form-group"><label>Имя клиента</label><input type="text" id="clientName" placeholder="Введите имя"></div>
    <div class="form-group"><label>Мастер</label>
      <select id="masterSelect" onchange="updateServices()">
        <option value="kameliya">Камелия</option>
        <option value="novenkaya">Новенькая</option>
      </select>
    </div>
    <div class="form-group"><label>Услуга</label><select id="serviceSelect"></select></div>
    <div class="form-group"><label>Дата</label><input type="text" id="bookingDate" placeholder="дд.мм.гггг"></div>
    <div class="form-group"><label>Время</label><input type="text" id="bookingTime" placeholder="чч:мм"></div>
    <div class="modal-btns">
      <button class="btn-cancel-modal" onclick="closeModal()">Отмена</button>
      <button class="btn-save" onclick="saveBooking()">Сохранить</button>
    </div>
  </div>
</div>
<script>
const services = {
  kameliya: [{id:"manicure_kameliya",name:"Маникюр — 1 800 ₽"},{id:"pedicure_kameliya",name:"Педикюр — 2 500 ₽"},{id:"both_kameliya",name:"Маникюр + педикюр — 3 800 ₽"}],
  novenkaya: [{id:"manicure_novenkaya",name:"Маникюр — 1 800 ₽"}]
};
function updateServices() {
  const master = document.getElementById('masterSelect').value;
  const sel = document.getElementById('serviceSelect');
  sel.innerHTML = '';
  services[master].forEach(s => { sel.innerHTML += `<option value="${s.id}">${s.name}</option>`; });
}
function openModal(master, time) {
  updateServices();
  if (master) { document.getElementById('masterSelect').value = master; updateServices(); }
  if (time) document.getElementById('bookingTime').value = time;
  document.getElementById('bookingDate').value = '{{ current_date }}';
  document.getElementById('clientName').value = '';
  document.getElementById('modal').classList.add('active');
}
function closeModal() { document.getElementById('modal').classList.remove('active'); }
async function saveBooking() {
  const data = {client:document.getElementById('clientName').value,master:document.getElementById('masterSelect').value,service:document.getElementById('serviceSelect').value,date:document.getElementById('bookingDate').value,time:document.getElementById('bookingTime').value};
  if (!data.client||!data.date||!data.time) { alert('Заполните все поля!'); return; }
  const res = await fetch('/add_booking',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await res.json();
  if (result.ok) { closeModal(); location.reload(); } else alert('Ошибка: '+result.error);
}
async function cancelBooking(row) {
  if (!confirm('Отменить эту запись?')) return;
  const res = await fetch('/cancel_booking',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({row})});
  const result = await res.json();
  if (result.ok) location.reload(); else alert('Ошибка отмены');
}
let calYear, calMonth;
const months = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
function toggleCalendar() {
  const wrap = document.getElementById('calWrap');
  const overlay = document.getElementById('calOverlay');
  if (!wrap.style.display || wrap.style.display==='none') {
    const now = new Date(); calYear=now.getFullYear(); calMonth=now.getMonth();
    renderCalendar(); wrap.style.display='block'; overlay.style.display='block';
  } else { closeCalendar(); }
}
function closeCalendar() { document.getElementById('calWrap').style.display='none'; document.getElementById('calOverlay').style.display='none'; }
function changeMonth(dir) { calMonth+=dir; if(calMonth>11){calMonth=0;calYear++;} if(calMonth<0){calMonth=11;calYear--;} renderCalendar(); }
function renderCalendar() {
  document.getElementById('calMonth').textContent = months[calMonth]+' '+calYear;
  const firstDay = new Date(calYear,calMonth,1).getDay();
  const daysInMonth = new Date(calYear,calMonth+1,0).getDate();
  const startOffset = firstDay===0?6:firstDay-1;
  const today = new Date();
  let html='';
  for(let i=0;i<startOffset;i++) html+='<div></div>';
  for(let d=1;d<=daysInMonth;d++) {
    const isToday = d===today.getDate()&&calMonth===today.getMonth()&&calYear===today.getFullYear();
    const dd=String(d).padStart(2,'0'); const mm=String(calMonth+1).padStart(2,'0');
    const dateStr=dd+'.'+mm+'.'+calYear;
    html+=`<div class="cal-day${isToday?' today':''}" onclick="selectDate('${dateStr}')">${d}</div>`;
  }
  document.getElementById('calDays').innerHTML=html;
}
function selectDate(dateStr) { closeCalendar(); window.location.href='?date='+dateStr; }
</script>
{% endif %}
</body>
</html>
"""


@flask_app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if request.form.get("password") == PANEL_PASSWORD:
            session["logged_in"] = True
            return redirect("/")
        return render_template_string(HTML, logged_in=False, error="Неверный пароль")
    if not session.get("logged_in"):
        return render_template_string(HTML, logged_in=False, error=None)
    date_str = request.args.get("date", datetime.now().strftime("%d.%m.%Y"))
    try:
        date_obj = datetime.strptime(date_str, "%d.%m.%Y")
    except Exception:
        date_obj = datetime.now()
        date_str = date_obj.strftime("%d.%m.%Y")
    prev_date = (date_obj - timedelta(days=1)).strftime("%d.%m.%Y")
    next_date = (date_obj + timedelta(days=1)).strftime("%d.%m.%Y")
    days_ru = {"Monday": "Пн", "Tuesday": "Вт", "Wednesday": "Ср", "Thursday": "Чт", "Friday": "Пт", "Saturday": "Сб", "Sunday": "Вс"}
    day_name = days_ru.get(date_obj.strftime("%A"), "")
    display_date = f"{date_obj.strftime('%d.%m.%Y')} ({day_name})"
    try:
        b_list = get_bookings_for_date(date_str)
        schedule = build_schedule(b_list)
    except Exception:
        schedule = {"kameliya": {s: {"type": "slot-free"} for s in WEB_SLOTS}, "novenkaya": {s: {"type": "slot-free"} for s in WEB_SLOTS}}
    return render_template_string(HTML, logged_in=True, slots=WEB_SLOTS, schedule=schedule,
                                  display_date=display_date, prev_date=prev_date, next_date=next_date, current_date=date_str)


@flask_app.route("/add_booking", methods=["POST"])
def add_booking():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "Не авторизован"})
    try:
        data = request.json
        svc = SERVICES.get(data["service"])
        master_name = MASTERS[data["master"]]["name"]
        sheet = get_sheet()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet.append_row([now, data["client"], "-", "-", master_name, svc["name"], data["date"], data["time"], svc["price"], "Подтверждена", svc["min"], "-", "Да"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@flask_app.route("/cancel_booking", methods=["POST"])
def cancel_booking():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "Не авторизован"})
    try:
        row = request.json["row"]
        sheet = get_sheet()
        sheet.update_cell(row, 10, "Отменена")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@flask_app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ===== TELEGRAM BOT =====
async def start(update: Update, context):
    user_id = update.effective_user.id
    bookings[user_id] = {}
    keyboard = [[
        InlineKeyboardButton("💅 Камелия", callback_data="master_kameliya"),
        InlineKeyboardButton("✨ Новенькая", callback_data="master_novenkaya"),
    ]]
    await update.message.reply_text(
        "Привет! 👋 Добро пожаловать в *Nail Bar LUNA* 🌙\n\nВыберите мастера:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_message(update: Update, context):
    user_id = update.effective_user.id
    if user_id in bookings and bookings[user_id].get("waiting_phone"):
        phone = update.message.text.strip()
        bookings[user_id]["phone"] = phone
        bookings[user_id]["waiting_phone"] = False
        await finish_booking(update, context, user_id)
        return
    bookings[user_id] = {}
    keyboard = [[
        InlineKeyboardButton("💅 Камелия", callback_data="master_kameliya"),
        InlineKeyboardButton("✨ Новенькая", callback_data="master_novenkaya"),
    ]]
    await update.message.reply_text(
        "Привет! 👋 Добро пожаловать в *Nail Bar LUNA* 🌙\n\nВыберите мастера:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def finish_booking(update, context, user_id):
    b = bookings[user_id]
    user = update.effective_user
    user_name = user.full_name
    username = f"@{user.username}" if user.username else "нет"
    phone = b.get("phone", "не указан")
    master = MASTERS[b["master"]]
    svc = SERVICES[b["service"]]
    free = get_free_slots(master["name"], b["date"], svc["slots"])
    if b["time"] not in free:
        await update.message.reply_text("❌ Это время только что заняли! Начните заново — /start")
        return
    save_booking(user_name, phone, username, user_id, master["name"], svc["name"], b["date"], b["time"], svc["price"], svc["min"])
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📋 *Новая запись!*\n\n👤 {user_name}\n📱 {phone}\n💬 {username}\n👩 {master['name']}\n💅 {svc['name']}\n📅 {b['date']}\n🕐 {b['time']}\n💰 {svc['price']}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Admin notify error: {e}")
    keyboard = [[InlineKeyboardButton("🔄 Новая запись", callback_data="restart")]]
    await update.message.reply_text(
        f"🎉 *Запись создана!*\n\n📍 Nail Bar LUNA\n👩 {master['name']}\n💅 {svc['name']}\n📅 {b['date']}\n🕐 {b['time']}\n⏱ {svc['dur']}\n💰 {svc['price']}\n\nЗа 24 часа пришлю напоминание 😊",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cancel_cmd(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только для администратора.")
        return
    bookings_list = get_upcoming_bookings()
    if not bookings_list:
        await update.message.reply_text("📭 Нет активных записей.")
        return
    keyboard = []
    for b in bookings_list[-10:]:
        d = b["data"]
        label = f"{d.get('Дата')} {d.get('Время')} — {d.get('Мастер')} — {d.get('Имя клиента')}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"cancel_{b['row']}")])
    await update.message.reply_text("Выберите запись для отмены:", reply_markup=InlineKeyboardMarkup(keyboard))


async def send_reminders(context):
    try:
        sheet = get_sheet()
        all_values = sheet.get_all_values()
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
        for i, row in enumerate(all_values[1:], start=2):
            if len(row) >= 13 and row[6] == tomorrow and row[9] == "Ожидает" and row[12] == "Нет" and row[11]:
                try:
                    keyboard = [[
                        InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_{i}"),
                        InlineKeyboardButton("❌ Отменить", callback_data=f"client_cancel_{i}"),
                    ]]
                    await context.bot.send_message(
                        chat_id=int(row[11]),
                        text=f"⏰ *Напоминание!*\n\nЗавтра в *Nail Bar LUNA* 🌙\n\n👩 {row[4]}\n💅 {row[5]}\n📅 {row[6]}\n🕐 {row[7]}\n\nПодтвердите или отмените:",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    sheet.update_cell(i, 13, "Да")
                except Exception as e:
                    logging.error(f"Reminder error: {e}")
    except Exception as e:
        logging.error(f"Send reminders error: {e}")


async def button(update: Update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "ignore":
        return

    if data.startswith("confirm_"):
        row_num = int(data.replace("confirm_", ""))
        try:
            sheet = get_sheet()
            sheet.update_cell(row_num, 10, "Подтверждена клиентом")
            row = sheet.row_values(row_num)
        except Exception:
            row = []
        await query.edit_message_text("✅ Запись подтверждена. Ждём вас! 💅")
        if ADMIN_ID and row:
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"✅ *Подтверждено!*\n\n👤 {row[1]}\n👩 {row[4]}\n📅 {row[6]} в {row[7]}", parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Confirm notify error: {e}")
        return

    if data.startswith("client_cancel_"):
        row_num = int(data.replace("client_cancel_", ""))
        try:
            sheet = get_sheet()
            sheet.update_cell(row_num, 10, "Отменена клиентом")
            row = sheet.row_values(row_num)
        except Exception:
            row = []
        await query.edit_message_text("❌ Запись отменена. До встречи! 💅")
        if ADMIN_ID and row:
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=f"❌ *Отменено клиентом!*\n\n👤 {row[1]}\n📱 {row[2]}\n👩 {row[4]}\n📅 {row[6]} в {row[7]}", parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Cancel notify error: {e}")
        return

    if data.startswith("cancel_"):
        row_num = int(data.replace("cancel_", ""))
        if cancel_booking_by_row(row_num):
            await query.edit_message_text("✅ Запись отменена!")
        else:
            await query.edit_message_text("❌ Ошибка при отмене.")
        return

    if data in ("back_to_masters", "restart"):
        bookings[user_id] = {}
        keyboard = [[
            InlineKeyboardButton("💅 Камелия", callback_data="master_kameliya"),
            InlineKeyboardButton("✨ Новенькая", callback_data="master_novenkaya"),
        ]]
        await query.edit_message_text("Привет! 👋 Добро пожаловать в *Nail Bar LUNA* 🌙\n\nВыберите мастера:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("master_"):
        master_id = data.replace("master_", "")
        bookings[user_id] = {"master": master_id}
        master = MASTERS[master_id]
        keyboard = [[InlineKeyboardButton(f"{SERVICES[s]['name']} — {SERVICES[s]['price']} ({SERVICES[s]['dur']})", callback_data=f"service_{s}")] for s in MASTER_SERVICES[master_id]]
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_masters")])
        await query.edit_message_text(f"Мастер *{master['name']}* ✅\n\nВыберите услугу:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("service_"):
        svc_id = data.replace("service_", "")
        bookings[user_id]["service"] = svc_id
        master_id = bookings[user_id].get("master", "kameliya")
        now = datetime.now()
        svc_enc = encode_svc(svc_id)
        keyboard = []
        row = []
        for i in range(2):
            d = now + timedelta(days=30 * i)
            month_name = MONTHS_RU[d.month] + " " + str(d.year)
            row.append(InlineKeyboardButton(month_name, callback_data=f"mo_{d.year}_{d.month}_{svc_enc}"))
        keyboard.append(row)
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"master_{master_id}")])
        svc = SERVICES[svc_id]
        await query.edit_message_text(f"Услуга *{svc['name']}* — {svc['dur']}, {svc['price']} ✅\n\nВыберите месяц:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("mo_"):
        parts = data.split("_")
        year = int(parts[1])
        month = int(parts[2])
        svc_enc = parts[3]
        svc_id = decode_svc(svc_enc)
        bookings[user_id]["service"] = svc_id
        today = datetime.now().date()
        days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        keyboard = []
        keyboard.append([InlineKeyboardButton(d, callback_data="ignore") for d in days_ru])
        month_days = calendar.monthcalendar(year, month)
        for week in month_days:
            row = []
            for day in week:
                if day == 0:
                    row.append(InlineKeyboardButton(" ", callback_data="ignore"))
                else:
                    date_obj = datetime(year, month, day).date()
                    if date_obj <= today:
                        row.append(InlineKeyboardButton(f"·{day}", callback_data="ignore"))
                    else:
                        date_str = f"{str(day).zfill(2)}.{str(month).zfill(2)}.{year}"
                        row.append(InlineKeyboardButton(str(day), callback_data=f"date_{date_str}"))
            keyboard.append(row)
        month_name = MONTHS_RU[month] + " " + str(year)
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"service_{svc_id}")])
        await query.edit_message_text(f"📅 *{month_name}*\n\nВыберите день:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("date_"):
        date = data.replace("date_", "")
        bookings[user_id]["date"] = date
        master_id = bookings[user_id].get("master", "kameliya")
        svc_id = bookings[user_id].get("service", "")
        master_name = MASTERS[master_id]["name"]
        svc = SERVICES[svc_id]
        free = get_free_slots(master_name, date, svc["slots"])
        parts = date.split(".")
        svc_enc = encode_svc(svc_id)
        if not free:
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f"mo_{parts[2]}_{int(parts[1])}_{svc_enc}")]]
            await query.edit_message_text("😔 На эту дату нет свободного времени.", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        keyboard = []
        row = []
        for t in free:
            row.append(InlineKeyboardButton(t, callback_data=f"time_{t}"))
            if len(row) == 4:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"mo_{parts[2]}_{int(parts[1])}_{svc_enc}")])
        await query.edit_message_text(f"📅 *{date}*\n\nВыберите время:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("time_"):
        time = data.replace("time_", "")
        bookings[user_id]["time"] = time
        await query.edit_message_text(f"🕐 Время *{time}* ✅\n\n📱 Введите номер телефона:", parse_mode="Markdown")
        bookings[user_id]["waiting_phone"] = True


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    from waitress import serve
    serve(flask_app, host="0.0.0.0", port=port)


def main():
    import asyncio

    async def run_bot():
        tg_app = Application.builder().token(TOKEN).build()
        tg_app.add_handler(CommandHandler("start", start))
        tg_app.add_handler(CommandHandler("cancel", cancel_cmd))
        tg_app.add_handler(CallbackQueryHandler(button))
        tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        job_queue = tg_app.job_queue
        job_queue.run_repeating(send_reminders, interval=3600, first=30)
        async with tg_app:
            await tg_app.start()
            await tg_app.updater.start_polling()
            await asyncio.Event().wait()

    def start_bot():
        asyncio.run(run_bot())

    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    from waitress import serve
    logging.info(f"Flask starting on port {port}")
    serve(flask_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
