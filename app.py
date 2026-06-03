import os
import json
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, session, redirect, jsonify
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nailbar2024")

SPREADSHEET_ID = "128OL9NnhHepDEOuw3Wre-rpg0j7FwIDDjYe0f3Ft50Y"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "luna2024")

MASTERS = {
    "kameliya": {"name": "Камелия", "services": ["manicure_kameliya", "pedicure_kameliya", "both_kameliya"]},
    "novenkaya": {"name": "Новенькая", "services": ["manicure_novenkaya"]},
}
SERVICES = {
    "manicure_kameliya": {"name": "Маникюр", "price": "1 800 ₽", "dur": "1ч 30м", "min": 90},
    "pedicure_kameliya": {"name": "Педикюр", "price": "2 500 ₽", "dur": "2ч", "min": 120},
    "both_kameliya": {"name": "Маникюр + педикюр", "price": "3 800 ₽", "dur": "3ч", "min": 180},
    "manicure_novenkaya": {"name": "Маникюр", "price": "1 800 ₽", "dur": "2ч 30м", "min": 150},
}

def get_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).sheet1

def get_bookings_for_date(date_str):
    sheet = get_sheet()
    all_values = sheet.get_all_values()
    bookings = []
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) >= 10 and row[6] == date_str and row[9] not in ("Отменена", "Отменена клиентом"):
            bookings.append({
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
    return bookings

def generate_slots():
    slots = []
    t = datetime.strptime("08:00", "%H:%M")
    end = datetime.strptime("20:00", "%H:%M")
    while t <= end:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=30)
    return slots

SLOTS = generate_slots()

def build_schedule(bookings):
    schedule = {
        "kameliya": {s: {"type": "slot-free"} for s in SLOTS},
        "novenkaya": {s: {"type": "slot-free"} for s in SLOTS},
    }
    master_map = {"Камелия": "kameliya", "Новенькая": "novenkaya"}
    for b in bookings:
        master_key = master_map.get(b["master"])
        if not master_key:
            continue
        duration = b["duration"]
        slots_needed = max(1, duration // 30)
        try:
            t = datetime.strptime(b["time"], "%H:%M")
            for i in range(slots_needed):
                slot = (t + timedelta(minutes=30*i)).strftime("%H:%M")
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
        except:
            pass
    return schedule

HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nail Bar LUNA — Расписание</title>
<script>
  setTimeout(function(){ location.reload(); }, 30000);
</script>
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

<!-- Календарь -->
<div class="cal-overlay" id="calOverlay" onclick="closeCalendar()"></div>
<div class="cal-wrap" id="calWrap">
  <div class="cal-header">
    <button class="cal-nav" onclick="changeMonth(-1)">‹</button>
    <span class="cal-month" id="calMonth"></span>
    <button class="cal-nav" onclick="changeMonth(1)">›</button>
  </div>
  <div class="cal-grid" id="calDayNames">
    <div class="cal-day-name">Пн</div>
    <div class="cal-day-name">Вт</div>
    <div class="cal-day-name">Ср</div>
    <div class="cal-day-name">Чт</div>
    <div class="cal-day-name">Пт</div>
    <div class="cal-day-name">Сб</div>
    <div class="cal-day-name">Вс</div>
  </div>
  <div class="cal-grid" id="calDays"></div>
</div>

<!-- Модальное окно -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <h3>📝 Новая запись</h3>
    <div class="form-group">
      <label>Имя клиента</label>
      <input type="text" id="clientName" placeholder="Введите имя">
    </div>
    <div class="form-group">
      <label>Мастер</label>
      <select id="masterSelect" onchange="updateServices()">
        <option value="kameliya">Камелия</option>
        <option value="novenkaya">Новенькая</option>
      </select>
    </div>
    <div class="form-group">
      <label>Услуга</label>
      <select id="serviceSelect"></select>
    </div>
    <div class="form-group">
      <label>Дата</label>
      <input type="text" id="bookingDate" placeholder="дд.мм">
    </div>
    <div class="form-group">
      <label>Время</label>
      <input type="text" id="bookingTime" placeholder="чч:мм">
    </div>
    <div class="modal-btns">
      <button class="btn-cancel-modal" onclick="closeModal()">Отмена</button>
      <button class="btn-save" onclick="saveBooking()">Сохранить</button>
    </div>
  </div>
</div>

<script>
const services = {
  kameliya: [
    {id: "manicure_kameliya", name: "Маникюр — 1 800 ₽"},
    {id: "pedicure_kameliya", name: "Педикюр — 2 500 ₽"},
    {id: "both_kameliya", name: "Маникюр + педикюр — 3 800 ₽"},
  ],
  novenkaya: [
    {id: "manicure_novenkaya", name: "Маникюр — 1 800 ₽"},
  ]
};

function updateServices() {
  const master = document.getElementById('masterSelect').value;
  const sel = document.getElementById('serviceSelect');
  sel.innerHTML = '';
  services[master].forEach(s => {
    sel.innerHTML += `<option value="${s.id}">${s.name}</option>`;
  });
}

function openModal(master, time) {
  updateServices();
  if (master) { document.getElementById('masterSelect').value = master; updateServices(); }
  if (time) document.getElementById('bookingTime').value = time;
  document.getElementById('bookingDate').value = '{{ current_date }}';
  document.getElementById('clientName').value = '';
  document.getElementById('modal').classList.add('active');
}

function closeModal() {
  document.getElementById('modal').classList.remove('active');
}

async function saveBooking() {
  const data = {
    client: document.getElementById('clientName').value,
    master: document.getElementById('masterSelect').value,
    service: document.getElementById('serviceSelect').value,
    date: document.getElementById('bookingDate').value,
    time: document.getElementById('bookingTime').value,
  };
  if (!data.client || !data.date || !data.time) { alert('Заполните все поля!'); return; }
  const res = await fetch('/add_booking', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await res.json();
  if (result.ok) { closeModal(); location.reload(); }
  else alert('Ошибка: ' + result.error);
}

async function cancelBooking(row) {
  if (!confirm('Отменить эту запись?')) return;
  const res = await fetch('/cancel_booking', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({row})});
  const result = await res.json();
  if (result.ok) location.reload();
  else alert('Ошибка отмены');
}

let calYear, calMonth;
const months = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];

function toggleCalendar() {
  const wrap = document.getElementById('calWrap');
  const overlay = document.getElementById('calOverlay');
  if (wrap.style.display === 'none' || !wrap.style.display) {
    const now = new Date();
    calYear = now.getFullYear();
    calMonth = now.getMonth();
    renderCalendar();
    wrap.style.display = 'block';
    overlay.style.display = 'block';
  } else {
    closeCalendar();
  }
}

function closeCalendar() {
  document.getElementById('calWrap').style.display = 'none';
  document.getElementById('calOverlay').style.display = 'none';
}

function changeMonth(dir) {
  calMonth += dir;
  if (calMonth > 11) { calMonth = 0; calYear++; }
  if (calMonth < 0) { calMonth = 11; calYear--; }
  renderCalendar();
}

function renderCalendar() {
  document.getElementById('calMonth').textContent = months[calMonth] + ' ' + calYear;
  const firstDay = new Date(calYear, calMonth, 1).getDay();
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const startOffset = firstDay === 0 ? 6 : firstDay - 1;
  const today = new Date();
  let html = '';
  for (let i = 0; i < startOffset; i++) html += '<div></div>';
  for (let d = 1; d <= daysInMonth; d++) {
    const isToday = d === today.getDate() && calMonth === today.getMonth() && calYear === today.getFullYear();
    const dd = String(d).padStart(2,'0');
    const mm = String(calMonth+1).padStart(2,'0');
    const dateStr = dd + '.' + mm;
    html += `<div class="cal-day${isToday?' today':''}" onclick="selectDate('${dateStr}')">${d}</div>`;
  }
  document.getElementById('calDays').innerHTML = html;
}

function selectDate(dateStr) {
  closeCalendar();
  window.location.href = '?date=' + dateStr;
}
</script>

{% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
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
    except:
        date_obj = datetime.now()
        date_str = date_obj.strftime("%d.%m.%Y")

    prev_date = (date_obj - timedelta(days=1)).strftime("%d.%m.%Y")
    next_date = (date_obj + timedelta(days=1)).strftime("%d.%m.%Y")
    days_ru = {"Monday":"Пн","Tuesday":"Вт","Wednesday":"Ср","Thursday":"Чт","Friday":"Пт","Saturday":"Сб","Sunday":"Вс"}
    day_name = days_ru.get(date_obj.strftime("%A"), "")
    display_date = f"{date_obj.strftime('%d.%m.%Y')} ({day_name})"

    try:
        bookings = get_bookings_for_date(date_str)
        schedule = build_schedule(bookings)
    except Exception as e:
        schedule = {"kameliya": {s: {"type": "slot-free"} for s in SLOTS}, "novenkaya": {s: {"type": "slot-free"} for s in SLOTS}}

    return render_template_string(HTML,
        logged_in=True, slots=SLOTS, schedule=schedule,
        display_date=display_date, prev_date=prev_date,
        next_date=next_date, current_date=date_str
    )

@app.route("/add_booking", methods=["POST"])
def add_booking():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "Не авторизован"})
    try:
        data = request.json
        svc = SERVICES.get(data["service"])
        master_name = MASTERS[data["master"]]["name"]
        sheet = get_sheet()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        sheet.append_row([now, data["client"], master_name, svc["name"], data["date"], data["time"], svc["price"], "Подтверждена", svc["min"]])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/cancel_booking", methods=["POST"])
def cancel_booking():
    if not session.get("logged_in"):
        return jsonify({"ok": False, "error": "Не авторизован"})
    try:
        row = request.json["row"]
        sheet = get_sheet()
        sheet.update_cell(row, 8, "Отменена")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
