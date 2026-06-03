import os
import logging
import json
import calendar
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = "128OL9NnhHepDEOuw3Wre-rpg0j7FwIDDjYe0f3Ft50Y"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

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


def init_sheet():
    try:
        sheet = get_sheet()
        if sheet.cell(1, 1).value != "Дата записи":
            sheet.insert_row(
                ["Дата записи", "Имя клиента", "Телефон", "Username", "Мастер",
                 "Услуга", "Дата", "Время", "Стоимость", "Статус", "Длительность",
                 "User ID", "Напомнено"], 1
            )
    except Exception as e:
        logging.error(f"Sheet init error: {e}")


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
                text=f"📋 *Новая запись!*\n\n"
                     f"👤 Клиент: {user_name}\n"
                     f"📱 Телефон: {phone}\n"
                     f"💬 Telegram: {username}\n"
                     f"👩 Мастер: {master['name']}\n"
                     f"💅 Услуга: {svc['name']}\n"
                     f"📅 Дата: {b['date']}\n"
                     f"🕐 Время: {b['time']}\n"
                     f"💰 Стоимость: {svc['price']}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Admin notify error: {e}")
    text = (
        f"🎉 *Запись создана!*\n\n"
        f"📍 Nail Bar LUNA\n"
        f"👩 Мастер: {master['name']}\n"
        f"💅 Услуга: {svc['name']}\n"
        f"📅 Дата: {b['date']}\n"
        f"🕐 Время: {b['time']}\n"
        f"⏱ Длительность: {svc['dur']}\n"
        f"💰 Стоимость: {svc['price']}\n\n"
        f"За 24 часа до визита пришлю напоминание с просьбой подтвердить запись 😊"
    )
    keyboard = [[InlineKeyboardButton("🔄 Новая запись", callback_data="restart")]]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def cancel_cmd(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Эта команда только для администратора.")
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
            if len(row) >= 13:
                date = row[6]
                status = row[9]
                reminded = row[12]
                user_id = row[11]
                if date == tomorrow and status == "Ожидает" and reminded == "Нет" and user_id:
                    try:
                        keyboard = [[
                            InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_{i}"),
                            InlineKeyboardButton("❌ Отменить", callback_data=f"client_cancel_{i}"),
                        ]]
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"⏰ *Напоминание о записи!*\n\n"
                                 f"Завтра вас ждём в *Nail Bar LUNA* 🌙\n\n"
                                 f"👩 Мастер: {row[4]}\n"
                                 f"💅 Услуга: {row[5]}\n"
                                 f"📅 Дата: {row[6]}\n"
                                 f"🕐 Время: {row[7]}\n\n"
                                 f"Пожалуйста подтвердите или отмените запись:",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        sheet.update_cell(i, 13, "Да")
                    except Exception as e:
                        logging.error(f"Reminder error for {user_id}: {e}")
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
        await query.edit_message_text("✅ Отлично! Ваша запись подтверждена. Ждём вас! 💅")
        if ADMIN_ID and row:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"✅ *Клиент подтвердил запись!*\n\n"
                         f"👤 {row[1]}\n"
                         f"👩 Мастер: {row[4]}\n"
                         f"📅 {row[6]} в {row[7]}",
                    parse_mode="Markdown"
                )
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
        await query.edit_message_text("❌ Ваша запись отменена. Будем рады видеть вас в другой раз! 💅")
        if ADMIN_ID and row:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"❌ *Клиент отменил запись!*\n\n"
                         f"👤 {row[1]}\n"
                         f"📱 {row[2]}\n"
                         f"👩 Мастер: {row[4]}\n"
                         f"📅 {row[6]} в {row[7]}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Cancel notify error: {e}")
        return

    if data.startswith("cancel_"):
        row_num = int(data.replace("cancel_", ""))
        if cancel_booking_by_row(row_num):
            await query.edit_message_text("✅ Запись отменена! Слот освобождён.")
        else:
            await query.edit_message_text("❌ Ошибка при отмене.")
        return

    if data in ("back_to_masters", "restart"):
        bookings[user_id] = {}
        keyboard = [[
            InlineKeyboardButton("💅 Камелия", callback_data="master_kameliya"),
            InlineKeyboardButton("✨ Новенькая", callback_data="master_novenkaya"),
        ]]
        await query.edit_message_text(
            "Привет! 👋 Добро пожаловать в *Nail Bar LUNA* 🌙\n\nВыберите мастера:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("master_"):
        master_id = data.replace("master_", "")
        bookings[user_id] = {"master": master_id}
        master = MASTERS[master_id]
        keyboard = [[InlineKeyboardButton(
            f"{SERVICES[s]['name']} — {SERVICES[s]['price']} ({SERVICES[s]['dur']})",
            callback_data=f"service_{s}"
        )] for s in MASTER_SERVICES[master_id]]
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_masters")])
        await query.edit_message_text(
            f"Мастер *{master['name']}* выбран ✅\n\nВыберите услугу:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

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
        await query.edit_message_text(
            f"Услуга *{svc['name']}* — {svc['dur']}, {svc['price']} ✅\n\nВыберите месяц:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

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
        await query.edit_message_text(
            f"📅 *{month_name}*\n\nВыберите день:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

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
            await query.edit_message_text(
                "😔 На эту дату у мастера нет свободного времени.\n\nВыберите другой день:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
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
        await query.edit_message_text(
            f"📅 *{date}*\n\nВыберите свободное время:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("time_"):
        time = data.replace("time_", "")
        bookings[user_id]["time"] = time
        await query.edit_message_text(
            f"🕐 Время *{time}* выбрано ✅\n\n📱 Введите ваш номер телефона для связи:",
            parse_mode="Markdown"
        )
        bookings[user_id]["waiting_phone"] = True


def main():
    init_sheet()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    job_queue = app.job_queue
    job_queue.run_repeating(send_reminders, interval=60, first=10)
    app.run_polling()


if __name__ == "__main__":
    main()
