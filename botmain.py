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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = "8967121003:AAEkJmhYWeN--lTQGxhH6UhrGIf97Bjgngc"
ADMIN_ID = 8953762615
BALANCE_PER_EMOJI = 1

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== БАЗА ДАННЫХ =====
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

# ===== ШАБЛОНЫ =====
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
    if int(user_id) == ADMIN_ID:
        return float('inf')
    return db["users"].get(str(user_id), {}).get("balance", 0)

def spend_balance(user_id, amount):
    if int(user_id) == ADMIN_ID:
        return True
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0, "emojis_created": 0}
    if db["users"][uid]["balance"] < amount:
        return False
    db["users"][uid]["balance"] -= amount
    save_db(db)
    return True

def increment_emoji_count(user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0, "emojis_created": 0}
    db["users"][uid]["emojis_created"] = db["users"][uid].get("emojis_created", 0) + 1
    save_db(db)

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return [r, g, b, 1.0]
    return [1.0, 0.0, 0.0, 1.0]

# ===== УНИВЕРСАЛЬНЫЙ ПАРСЕР ЦВЕТОВ =====
def is_color_list(val):
    if not isinstance(val, list):
        return False
    if len(val) not in (3, 4):
        return False
    return all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in val[:3])

def find_and_replace_colors(obj, text_rgb, fill_rgb, stroke_rgb):
    if isinstance(obj, dict):
        is_fill = 'fill' in str(obj).lower() or 'fl' in str(obj).lower()
        is_stroke = 'stroke' in str(obj).lower() or 'st' in str(obj).lower()
        new_obj = {}
        for key, value in obj.items():
            if is_color_list(value):
                if is_stroke:
                    new_obj[key] = stroke_rgb
                else:
                    new_obj[key] = fill_rgb
                logger.info(f"Заменён цвет в поле {key}")
            elif isinstance(value, dict) and 'k' in value and is_color_list(value['k']):
                if is_stroke:
                    value['k'] = stroke_rgb
                else:
                    value['k'] = fill_rgb
                new_obj[key] = value
                logger.info(f"Заменён цвет в поле {key}.k")
            else:
                new_obj[key] = find_and_replace_colors(value, text_rgb, fill_rgb, stroke_rgb)
        return new_obj
    elif isinstance(obj, list):
        return [find_and_replace_colors(item, text_rgb, fill_rgb, stroke_rgb) for item in obj]
    else:
        return obj

# ===== ФУНКЦИИ ДЛЯ ТЕКСТА =====
def remove_all_text_layers(layers):
    """Удаляем все старые текстовые слои (ty=5)."""
    new_layers = []
    for layer in layers:
        if layer.get("ty") == 5:
            logger.info(f"Удалён текстовый слой: {layer.get('nm', 'без имени')}")
            continue
        new_layers.append(layer)
    return new_layers

def ensure_fonts(data, font_name):
    """Добавляет системный шрифт Arial с origin system."""
    if "fonts" not in data:
        data["fonts"] = {"list": []}
    if not isinstance(data["fonts"], dict):
        data["fonts"] = {"list": []}
    if "list" not in data["fonts"]:
        data["fonts"]["list"] = []
    
    font_family = "Arial"
    font_style = "Bold"
    
    for f in data["fonts"]["list"]:
        if f.get("name") == font_family and f.get("style") == font_style:
            return data
    
    data["fonts"]["list"].append({
        "name": font_family,
        "id": font_family,
        "family": font_family,
        "style": font_style,
        "origin": "system"
    })
    return data

def add_text_layer(layers, new_text, text_rgb, stroke_rgb, font_name, width, height):
    """Добавляет текстовый слой с системным шрифтом Arial поверх всех."""
    center_x = width / 2.0
    center_y = height / 2.0
    font_size = 300
    line_height = font_size
    stroke_width = 3
    scale = 100

    # Всегда используем Arial
    actual_font = "Arial"

    # Определяем время жизни слоя из первого попавшегося
    ref_layer = None
    for layer in layers:
        if "ip" in layer and "op" in layer:
            ref_layer = layer
            break
    if ref_layer:
        ip = ref_layer.get("ip", 0)
        op = ref_layer.get("op", 180)
        st = ref_layer.get("st", 0)
    else:
        ip, op, st = 0, 180, 0

    text_layer = {
        "ty": 5,
        "nm": "Generated Text",
        "ks": {
            "o": {"a": 0, "k": 100},
            "r": {"a": 0, "k": 0},
            "p": {"a": 0, "k": [center_x, center_y, 0]},
            "a": {"a": 0, "k": [0, 0, 0]},
            "s": {"a": 0, "k": [scale, scale, 100]}
        },
        "t": {
            "d": {
                "k": [
                    {
                        "s": {
                            "f": actual_font,
                            "t": new_text,
                            "j": 1,
                            "tr": 0,
                            "lh": line_height,
                            "ls": 0,
                            "s": font_size,
                            "fc": text_rgb,
                            "sc": stroke_rgb,
                            "sw": stroke_width,
                            "of": 0
                        }
                    }
                ]
            }
        },
        "ip": ip,
        "op": op,
        "st": st,
        "bm": 0
    }
    # Добавляем в конец (поверх всех)
    layers.append(text_layer)
    return layers

def replace_text_and_colors(data, new_text, text_color_hex, fill_color_hex, stroke_color_hex, font_name="Arial-Bold"):
    text_rgb = hex_to_rgb(text_color_hex)
    fill_rgb = hex_to_rgb(fill_color_hex)
    stroke_rgb = hex_to_rgb(stroke_color_hex)

    data = find_and_replace_colors(data, text_rgb, fill_rgb, stroke_rgb)

    if "layers" in data:
        width = data.get("w", 512)
        height = data.get("h", 512)
        # Удаляем все старые текстовые слои
        data["layers"] = remove_all_text_layers(data["layers"])
        # Добавляем новый текстовый слой
        data["layers"] = add_text_layer(data["layers"], new_text, text_rgb, stroke_rgb, font_name, width, height)

    # Добавляем секцию fonts
    data = ensure_fonts(data, font_name)

    # ===== КРИТИЧЕСКОЕ ДОБАВЛЕНИЕ ДЛЯ TGS =====
    data["tgs"] = 1
    data["props"] = {}
    if "v" not in data:
        data["v"] = "5.5.2"

    # === ОТЛАДКА ===
    try:
        with open("debug_output.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("✅ debug_output.json сохранён. Проверьте наличие поля 'tgs': 1")
    except Exception as e:
        logger.warning(f"Не удалось сохранить debug: {e}")

    return data, True

# ===== ПРЕВЬЮ =====
def generate_preview(text, text_color, stroke_color, fill_color):
    img = Image.new('RGBA', (512, 512), fill_color)
    draw = ImageDraw.Draw(img)
    for i in range(8):
        draw.text((256 + i, 256 + i), text, font=get_font(250), fill=(0, 0, 0, 100), anchor="mm")
    if stroke_color:
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                if dx != 0 or dy != 0:
                    draw.text((256 + dx, 256 + dy), text, font=get_font(250), fill=stroke_color, anchor="mm")
    draw.text((256, 256), text, font=get_font(250), fill=text_color, anchor="mm")
    draw.rectangle([10, 10, 502, 502], outline=stroke_color, width=3)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

def get_font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except:
        return ImageFont.load_default()

# ===== КЛАВИАТУРЫ =====
def main_kb(user_id):
    kb = [
        [InlineKeyboardButton(text="✨ Создать эмодзи", callback_data="create")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="📋 Шаблоны", callback_data="list")]
    ]
    if int(user_id) == ADMIN_ID:
        kb.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def templates_kb():
    kb = []
    for num, name in TEMPLATES.items():
        kb.append([InlineKeyboardButton(text=f"{num}. {name}", callback_data=f"tmpl_{num}")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def font_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔤 Стандартный", callback_data="font_Arial")],
        [InlineKeyboardButton(text="🖋 Курсив", callback_data="font_Arial-Italic")],
        [InlineKeyboardButton(text="🖍 Жирный", callback_data="font_Arial-Bold")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
    ])

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
        db["users"][user_id] = {"balance": 10, "emojis_created": 0}
        save_db(db)
    await message.answer(
        "✨ Добро пожаловать в StarlitEmoji!\n"
        "Создавай уникальные анимированные эмодзи с текстом и цветами.",
        reply_markup=main_kb(user_id)
    )

@dp.callback_query(F.data == "main")
async def back_main(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await callback.message.edit_text("✨ Главное меню:", reply_markup=main_kb(user_id))
    await callback.answer()

@dp.callback_query(F.data == "create")
async def create_menu(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    user_states[user_id] = {"step": "font"}
    await callback.message.edit_text("🔤 Выбери шрифт:", reply_markup=font_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("font_"))
async def select_font(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    font_name = callback.data.split("_")[1]
    if user_id not in user_states:
        user_states[user_id] = {"step": "text"}
    user_states[user_id]["font"] = font_name  # сохраняем, но не используем
    user_states[user_id]["step"] = "text"
    await callback.message.edit_text("✏️ Введи текст (до 20 символов):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ]))
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
    if user_id not in user_states:
        user_states[user_id] = {"step": "text", "font": "Arial-Bold"}
    user_states[user_id]["template"] = num
    user_states[user_id]["step"] = "text"
    await callback.message.edit_text("✏️ Введи текст (до 20 символов):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ]))
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

    font_name = state.get("font", "Arial-Bold")
    edited, _ = replace_text_and_colors(
        data,
        state["text"],
        state["text_color"],
        state["fill_color"],
        state["stroke_color"],
        font_name=font_name
    )

    state["edited_data"] = edited
    state["step"] = "preview"

    preview_img = generate_preview(
        state["text"],
        state["text_color"],
        state["stroke_color"],
        state["fill_color"]
    )

    caption = (
        f"📸 Предпросмотр:\n"
        f"Текст: {state['text']}\n"
        f"Шрифт: Arial (системный)\n"
        f"Цвет текста: {state['text_color']}\n"
        f"Цвет заливки: {state['fill_color']}\n"
        f"Цвет обводки: {state['stroke_color']}\n"
    )

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
            state["stroke_color"],
            font_name=state.get("font", "Arial-Bold")
        )

    increment_emoji_count(int(user_id))
    out = gzip.compress(json.dumps(edited, separators=(',', ':')).encode())
    balance_display = "∞" if int(user_id) == ADMIN_ID else str(get_balance(int(user_id)))
    emoji_count = db["users"].get(user_id, {}).get("emojis_created", 0)

    await callback.message.answer_document(
        BufferedInputFile(out, filename="emoji.tgs"),
        caption=f"✅ Готово!\n📝 Текст: {state['text']}\n🎨 Цвет текста: {state['text_color']}\n🎨 Цвет заливки: {state['fill_color']}\n🎨 Цвет обводки: {state['stroke_color']}\n💰 Баланс: {balance_display}\n📊 Создано: {emoji_count}"
    )
    del user_states[user_id]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    balance = get_balance(user_id)
    emojis = db["users"].get(user_id, {}).get("emojis_created", 0)
    display = "∞" if int(user_id) == ADMIN_ID else f"{balance} P"
    await callback.message.edit_text(
        f"👤 Профиль\n"
        f"💰 Баланс: {display}\n"
        f"📊 Создано эмодзи: {emojis}\n"
        f"🆔 ID: {user_id}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ])
    )
    await callback.answer()

# ===== АДМИН-ПАНЕЛЬ =====
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.message.edit_text("👑 Админ-панель:", reply_markup=admin_kb())
    await callback.answer()

@dp.callback_query(F.data == "admin_add_balance")
async def admin_add_balance(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/add_balance [user_id] [сумма]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_sub_balance")
async def admin_sub_balance(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/sub_balance [user_id] [сумма]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_ban")
async def admin_ban(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/ban [user_id]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_unban")
async def admin_unban(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("✏️ Введи: `/unban [user_id]`", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_add_lottie")
async def admin_add_lottie(callback: CallbackQuery):
    if int(callback.from_user.id) != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещён")
        return
    await callback.message.edit_text("📥 Отправь мне JSON-файл с шаблоном.")
    await callback.answer()

# ===== АДМИН-КОМАНДЫ =====
@dp.message(Command("add_balance"))
async def add_balance(message: Message):
    if int(message.from_user.id) != ADMIN_ID:
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
    if int(message.from_user.id) != ADMIN_ID:
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
    if int(message.from_user.id) != ADMIN_ID:
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
    if int(message.from_user.id) != ADMIN_ID:
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

@dp.message(F.document)
async def add_lottie(message: Message):
    if int(message.from_user.id) != ADMIN_ID:
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
    logger.info("✅ БОТ ЗАПУЩЕН! ДОБАВЛЕНО ОБЯЗАТЕЛЬНОЕ ПОЛЕ 'tgs': 1")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
