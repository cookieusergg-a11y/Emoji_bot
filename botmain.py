import json
import gzip
import os
import re
import logging
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from PIL import Image, ImageDraw, ImageFont
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8967121003:AAEkJmhYWeN--lTQGxhH6UhrGIf97Bjgngc"
ADMINS = [8953762615]
BALANCE_PER_EMOJI = 1

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_FILE = "db.json"
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}}
def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
db = load_db()

LOTTIES_DIR = "lotties"
os.makedirs(LOTTIES_DIR, exist_ok=True)

def load_templates():
    templates = {}
    if os.path.exists(LOTTIES_DIR):
        files = [f for f in os.listdir(LOTTIES_DIR) if f.endswith(".json")]
        for idx, filename in enumerate(files, start=1):
            templates[str(idx)] = filename
    return templates
TEMPLATES = load_templates()

user_states = {}

def get_balance(user_id):
    user_id = int(user_id)
    if user_id in ADMINS:
        return float('inf')
    return db["users"].get(str(user_id), {}).get("balance", 0)

def spend_balance(user_id, amount):
    user_id = int(user_id)
    if user_id in ADMINS:
        return True
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0}
    if db["users"][uid]["balance"] < amount:
        return False
    db["users"][uid]["balance"] -= amount
    save_db(db)
    return True

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return [r, g, b, 1.0]
    return [1.0, 0.0, 0.0, 1.0]

def process_layers(layers, new_text, text_rgb, fill_rgb, stroke_rgb, stroke_width=3):
    """
    Обходит все слои:
    - ty=5: заменяет текст и цвета
    - ty=4 с именем-буквой: скрывает (opacity=0)
    - меняет fill и stroke цвета во всех слоях
    Возвращает (новые слои, флаг найдена ли буква или текстовый слой)
    """
    new_layers = []
    found_text_layer = False
    found_letter_layer = False
    first_layer_ref = None

    for layer in layers:
        if "ip" in layer and "op" in layer:
            first_layer_ref = layer
            break

    if not first_layer_ref:
        ip_def, op_def, st_def = 0, 180, 0
    else:
        ip_def = first_layer_ref.get("ip", 0)
        op_def = first_layer_ref.get("op", 180)
        st_def = first_layer_ref.get("st", 0)

    for layer in layers:
        # === ТЕКСТОВЫЙ СЛОЙ (ty=5) ===
        if layer.get("ty") == 5:
            found_text_layer = True
            # Меняем текст
            if "t" in layer and "d" in layer["t"] and "k" in layer["t"]["d"]:
                if isinstance(layer["t"]["d"]["k"], dict):
                    layer["t"]["d"]["k"]["v"] = new_text
                elif isinstance(layer["t"]["d"]["k"], list) and len(layer["t"]["d"]["k"]) > 0:
                    if isinstance(layer["t"]["d"]["k"][0], dict) and "s" in layer["t"]["d"]["k"][0]:
                        layer["t"]["d"]["k"][0]["s"]["t"] = new_text
            # Меняем цвет текста
            if "c" in layer and "k" in layer["c"]:
                layer["c"]["k"] = text_rgb
            # Меняем цвет обводки текста
            if "sc" in layer and "k" in layer["sc"]:
                layer["sc"]["k"] = stroke_rgb
            if "sw" in layer:
                layer["sw"] = stroke_width
            new_layers.append(layer)
            continue

        # === ВЕКТОРНЫЕ СЛОИ-БУКВЫ (ty=4, nm из одной буквы/цифры) ===
        if layer.get("ty") == 4 and "nm" in layer:
            name = layer["nm"].strip()
            if len(name) == 1 and name.isalnum():
                found_letter_layer = True
                # Скрываем слой
                if "ks" not in layer:
                    layer["ks"] = {}
                if "o" not in layer["ks"]:
                    layer["ks"]["o"] = {"a": 0, "k": 0}
                else:
                    layer["ks"]["o"]["k"] = 0
                new_layers.append(layer)
                continue

        # === ОСТАЛЬНЫЕ СЛОИ (меняем fill и stroke) ===
        if "shapes" in layer:
            for shape in layer["shapes"]:
                if "it" in shape:
                    for item in shape["it"]:
                        if item.get("ty") == "fl" and "c" in item and "k" in item["c"]:
                            item["c"]["k"] = fill_rgb
                        if item.get("ty") == "st" and "c" in item and "k" in item["c"]:
                            item["c"]["k"] = stroke_rgb
        new_layers.append(layer)

    # Если есть текстовый слой — изменения уже внесены
    if found_text_layer:
        return new_layers, True

    # Если есть буквенные слои, но нет текстового — добавляем новый текстовый слой
    if found_letter_layer:
        text_layer = {
            "ty": 5,
            "nm": "Generated Text",
            "ks": {
                "o": {"a": 0, "k": 100},
                "r": {"a": 0, "k": 0},
                "p": {"a": 0, "k": [256, 256, 0]},
                "a": {"a": 0, "k": [0, 0, 0]},
                "s": {"a": 0, "k": [100, 100, 100]}
            },
            "t": {
                "d": {
                    "k": [
                        {
                            "s": {
                                "f": "Arial",
                                "t": new_text,
                                "j": 1,
                                "tr": 0,
                                "lh": 80,
                                "ls": 0,
                                "fc": text_rgb,
                                "sc": stroke_rgb,
                                "sw": stroke_width,
                                "of": 0
                            }
                        }
                    ]
                }
            },
            "ip": ip_def,
            "op": op_def,
            "st": st_def,
            "bm": 0
        }
        new_layers.insert(0, text_layer)
        logger.info("Добавлен текстовый слой, буквы скрыты")
        return new_layers, True

    # Ничего не нашли
    return new_layers, False

def replace_text_and_colors(data, new_text, text_color_hex, fill_color_hex, stroke_color_hex, stroke_width=3):
    text_rgb = hex_to_rgb(text_color_hex)
    fill_rgb = hex_to_rgb(fill_color_hex)
    stroke_rgb = hex_to_rgb(stroke_color_hex)

    if "layers" not in data:
        return data, False

    new_layers, changed = process_layers(
        data["layers"],
        new_text,
        text_rgb,
        fill_rgb,
        stroke_rgb,
        stroke_width
    )
    data["layers"] = new_layers
    return data, changed

def generate_preview(background_color, text, text_color, stroke_color, stroke_width=3):
    img = Image.new('RGBA', (512, 512), background_color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 80)
    except:
        font = ImageFont.load_default()
    if stroke_color and stroke_width > 0:
        for dx in range(-stroke_width, stroke_width+1):
            for dy in range(-stroke_width, stroke_width+1):
                if dx != 0 or dy != 0:
                    draw.text((256+dx, 256+dy), text, font=font, fill=stroke_color, anchor="mm")
    draw.text((256, 256), text, font=font, fill=text_color, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

# ===== КЛАВИАТУРЫ =====
def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Шаблоны", callback_data="list")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton(text="⭐ Пополнить", callback_data="topup")],
        [InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")]
    ])

def templates_kb():
    kb = []
    for num, name in TEMPLATES.items():
        kb.append([InlineKeyboardButton(text=f"{num}. {name}", callback_data=f"tmpl_{num}")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def color_kb(step):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Красный", callback_data=f"col_{step}_#FF0000"),
         InlineKeyboardButton(text="🟢 Зеленый", callback_data=f"col_{step}_#00FF00"),
         InlineKeyboardButton(text="🔵 Синий", callback_data=f"col_{step}_#0000FF")],
        [InlineKeyboardButton(text="⚫ Черный", callback_data=f"col_{step}_#000000"),
         InlineKeyboardButton(text="⚪ Белый", callback_data=f"col_{step}_#FFFFFF"),
         InlineKeyboardButton(text="🟡 Желтый", callback_data=f"col_{step}_#FFFF00")],
        [InlineKeyboardButton(text="🎨 Свой цвет", callback_data=f"custom_{step}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

def preview_kb(edit_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оплатить", callback_data=f"pay_{edit_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main")]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить баланс", callback_data="admin_add_balance")],
        [InlineKeyboardButton(text="➖ Снять баланс", callback_data="admin_sub_balance")],
        [InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban")],
        [InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="📥 Добавить шаблон", callback_data="admin_add_lottie")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

# ===== ОБРАБОТЧИКИ =====

@dp.message(Command("start"))
async def start_cmd(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in db["users"]:
        db["users"][user_id] = {"balance": 10}
        save_db(db)
    if user_id in user_states:
        del user_states[user_id]
    await message.answer("👋 Привет! Я создаю эмодзи.\nВыбери действие:", reply_markup=main_kb())

@dp.callback_query(F.data == "main")
async def back_main(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await callback.message.edit_text("👋 Главное меню:", reply_markup=main_kb())
    await callback.answer()

@dp.callback_query(F.data == "list")
async def list_templates(callback: CallbackQuery):
    if not TEMPLATES:
        await callback.message.edit_text("❌ Нет шаблонов.")
        return
    await callback.message.edit_text("📋 Выбери шаблон:", reply_markup=templates_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("tmpl_"))
async def select_template(callback: CallbackQuery):
    num = callback.data.split("_")[1]
    user_id = str(callback.from_user.id)
    if num not in TEMPLATES:
        await callback.answer("❌ Нет такого")
        return
    user_states[user_id] = {
        "template": num,
        "step": "text",
        "text": "",
        "text_color": "#FF0000",
        "fill_color": "#FF0000",
        "stroke_color": "#000000"
    }
    await callback.message.edit_text("✏️ Введи текст (до 20 символов):")
    await callback.answer()

@dp.message(F.text)
async def handle_text(message: Message):
    user_id = str(message.from_user.id)
    if user_id not in user_states:
        return
    state = user_states[user_id]
    
    if state["step"] == "text":
        if len(message.text) > 20:
            await message.answer("❌ Слишком длинно. Максимум 20.")
            return
        state["text"] = message.text
        state["step"] = "text_color"
        await message.answer("🎨 Выбери цвет ТЕКСТА:", reply_markup=color_kb("text"))
    
    elif state["step"].startswith("custom_"):
        hex_color = message.text.strip()
        if not re.match(r'^#[0-9A-Fa-f]{6}$', hex_color):
            await message.answer("❌ Неверный формат. Введи HEX как #RRGGBB")
            return
        part = state["step"].split("_")[1]
        if part == "text":
            state["text_color"] = hex_color
            state["step"] = "fill_color"
            await message.answer("🎨 Выбери цвет ЗАЛИВКИ:", reply_markup=color_kb("fill"))
        elif part == "fill":
            state["fill_color"] = hex_color
            state["step"] = "stroke_color"
            await message.answer("🎨 Выбери цвет ОБВОДКИ:", reply_markup=color_kb("stroke"))
        elif part == "stroke":
            state["stroke_color"] = hex_color
            await show_preview(message, state)

@dp.callback_query(F.data.startswith("col_"))
async def handle_color(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id not in user_states:
        await callback.answer("❌ Ошибка")
        return
    parts = callback.data.split("_")
    step = parts[1]
    color_hex = parts[2]
    state = user_states[user_id]
    
    if step == "text":
        state["text_color"] = color_hex
        state["step"] = "fill_color"
        await callback.message.edit_text("🎨 Выбери цвет ЗАЛИВКИ:", reply_markup=color_kb("fill"))
    elif step == "fill":
        state["fill_color"] = color_hex
        state["step"] = "stroke_color"
        await callback.message.edit_text("🎨 Выбери цвет ОБВОДКИ:", reply_markup=color_kb("stroke"))
    elif step == "stroke":
        state["stroke_color"] = color_hex
        await show_preview(callback, state)
    await callback.answer()

@dp.callback_query(F.data.startswith("custom_"))
async def custom_color(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id not in user_states:
        await callback.answer("❌ Ошибка")
        return
    step = callback.data.split("_")[1]
    state = user_states[user_id]
    state["step"] = f"custom_{step}"
    await callback.message.edit_text(f"🎨 Введи HEX-код для {'ТЕКСТА' if step=='text' else 'ЗАЛИВКИ' if step=='fill' else 'ОБВОДКИ'} (например, #FF5733):")
    await callback.answer()

async def show_preview(event, state):
    user_id = str(event.from_user.id)
    file_path = os.path.join(LOTTIES_DIR, TEMPLATES[state["template"]])
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    edited, changed = replace_text_and_colors(
        data,
        state["text"],
        state["text_color"],
        state["fill_color"],
        state["stroke_color"],
        stroke_width=3
    )
    
    state["edited_data"] = edited
    state["step"] = "preview"
    
    preview_img = generate_preview(
        state["fill_color"],
        state["text"],
        state["text_color"],
        state["stroke_color"]
    )
    
    caption = (
        f"📸 Предпросмотр:\n"
        f"Текст: {state['text']}\n"
        f"Цвет текста: {state['text_color']}\n"
        f"Цвет заливки: {state['fill_color']}\n"
        f"Цвет обводки: {state['stroke_color']}\n"
    )
    if changed:
        caption += "✅ Изменения применены"
    else:
        caption += "⚠️ Не найдено слоёв для замены — текст будет наложен поверх"
    
    await event.message.answer_photo(
        types.BufferedInputFile(preview_img.getvalue(), filename="preview.png"),
        caption=caption,
        reply_markup=preview_kb(state["template"] + "_" + user_id)
    )

@dp.callback_query(F.data.startswith("pay_"))
async def process_payment(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id not in user_states or user_states[user_id].get("step") != "preview":
        await callback.answer("❌ Ошибка. Начни сначала /start")
        return
    state = user_states[user_id]
    
    if not spend_balance(int(user_id), BALANCE_PER_EMOJI):
        await callback.message.edit_text("❌ Не хватает баллов!")
        await callback.answer()
        return
    
    edited = state.get("edited_data")
    if not edited:
        file_path = os.path.join(LOTTIES_DIR, TEMPLATES[state["template"]])
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        edited, _ = replace_text_and_colors(
            data,
            state["text"],
            state["text_color"],
            state["fill_color"],
            state["stroke_color"]
        )
    
    out = gzip.compress(json.dumps(edited, separators=(',', ':')).encode())
    balance_display = "∞" if int(user_id) in ADMINS else str(get_balance(int(user_id)))
    
    await callback.message.answer_document(
        BufferedInputFile(out, filename="emoji.tgs"),
        caption=f"✅ Готово!\n📝 Текст: {state['text']}\n🎨 Цвет текста: {state['text_color']}\n🎨 Цвет заливки: {state['fill_color']}\n🎨 Цвет обводки: {state['stroke_color']}\n💰 Баланс: {balance_display}"
    )
    del user_states[user_id]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

# ===== БАЛАНС И ПОПОЛНЕНИЕ =====
@dp.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery):
    user_id = int(callback.from_user.id)
    bal = get_balance(user_id)
    display = "∞ (админ)" if user_id in ADMINS else str(bal)
    await callback.message.edit_text(f"💰 Баланс: {display} баллов", reply_markup=main_kb())
    await callback.answer()

@dp.callback_query(F.data == "topup")
async def topup(callback: CallbackQuery):
    await callback.message.edit_text("⭐ Пополнение через звёзды временно отключено.\nИспользуй /add_balance", reply_markup=main_kb())
    await callback.answer()

# ===== АДМИН-ПАНЕЛЬ =====
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("👑 Админ-панель:", reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_add_balance")
async def admin_add_balance(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/add_balance [user_id] [сумма]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_sub_balance")
async def admin_sub_balance(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/sub_balance [user_id] [сумма]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_ban")
async def admin_ban(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/ban [user_id]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_unban")
async def admin_unban(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/unban [user_id]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_add_lottie")
async def admin_add_lottie(callback: CallbackQuery):
    if callback.from_user.id not in ADMINS:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("📥 Отправь мне JSON-файл с шаблоном.")
    await callback.answer()

# ===== АДМИН-КОМАНДЫ =====
@dp.message(Command("add_balance"))
async def add_balance(message: Message):
    if message.from_user.id not in ADMINS:
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ /add_balance [user_id] [сумма]")
        return
    uid = args[1]
    amount = int(args[2])
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0}
    db["users"][uid]["balance"] += amount
    save_db(db)
    await message.answer(f"✅ Баланс {uid} увеличен на {amount}")

@dp.message(Command("sub_balance"))
async def sub_balance(message: Message):
    if message.from_user.id not in ADMINS:
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("❌ /sub_balance [user_id] [сумма]")
        return
    uid = args[1]
    amount = int(args[2])
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0}
    db["users"][uid]["balance"] -= amount
    save_db(db)
    await message.answer(f"✅ Баланс {uid} уменьшен на {amount}")

@dp.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id not in ADMINS:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ /ban [user_id]")
        return
    uid = args[1]
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0}
    db["users"][uid]["banned"] = True
    save_db(db)
    await message.answer(f"✅ Пользователь {uid} забанен")

@dp.message(Command("unban"))
async def unban_user(message: Message):
    if message.from_user.id not in ADMINS:
        return
    args = message.text.split()
    if len(args) != 2:
        await message.answer("❌ /unban [user_id]")
        return
    uid = args[1]
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0}
    db["users"][uid]["banned"] = False
    save_db(db)
    await message.answer(f"✅ Пользователь {uid} разбанен")

@dp.message(F.document, Command("add_lottie"))
async def add_lottie(message: Message):
    if message.from_user.id not in ADMINS:
        return
    doc = message.document
    if not doc.file_name.endswith(".json"):
        await message.answer("❌ Только JSON")
        return
    file = await bot.get_file(doc.file_id)
    content = (await bot.download_file(file.file_path)).read().decode("utf-8")
    try:
        json.loads(content)
    except:
        await message.answer("❌ Невалидный JSON")
        return
    path = os.path.join(LOTTIES_DIR, doc.file_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    global TEMPLATES
    TEMPLATES = load_templates()
    await message.answer(f"✅ Шаблон {doc.file_name} добавлен!")

async def main():
    logger.info("✅ БОТ ЗАПУЩЕН! УНИВЕРСАЛЬНЫЙ ПАРСЕР АКТИВЕН.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
