import json
import gzip
import os
import re
import logging
import io
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery, LabeledPrice
from aiogram.filters import Command
from PIL import Image, ImageDraw, ImageFont
import asyncio
import random
import string

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8967121003:AAEkJmhYWeN--lTQGxhH6UhrGIf97Bjgngc"
ADMINS = [8953762615]
BALANCE_PER_EMOJI = 1
REFERRAL_BONUS = 5

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_FILE = "db.json"
def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "referrals": {}}
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
referral_codes = {}

def get_balance(user_id):
    user_id = int(user_id)
    if user_id in ADMINS:
        return float('inf')
    return db["users"].get(str(user_id), {}).get("balance", 0)

def get_emoji_count(user_id):
    return db["users"].get(str(user_id), {}).get("emojis_created", 0)

def generate_referral_code(user_id):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    referral_codes[code] = str(user_id)
    return code

def get_referral_link(user_id):
    code = generate_referral_code(user_id)
    return f"https://t.me/{bot.me.username}?start=ref_{code}"

def spend_balance(user_id, amount):
    user_id = int(user_id)
    if user_id in ADMINS:
        return True
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0, "emojis_created": 0}
    if db["users"][uid]["balance"] < amount:
        return False
    db["users"][uid]["balance"] -= amount
    save_db(db)
    return True

def add_balance(user_id, amount):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0, "emojis_created": 0}
    db["users"][uid]["balance"] += amount
    save_db(db)

def increment_emoji_count(user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {"balance": 0, "emojis_created": 0}
    db["users"][uid]["emojis_created"] = db["users"][uid].get("emojis_created", 0) + 1
    save_db(db)

# ===== УНИВЕРСАЛЬНЫЙ ПАРСЕР =====
def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 6:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return [r, g, b, 1.0]
    return [1.0, 0.0, 0.0, 1.0]

def process_layers(layers, new_text, text_rgb, fill_rgb, stroke_rgb, stroke_width=3, font_name="Arial"):
    new_layers = []
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

    found_text_layer = False
    for layer in layers:
        # Если это текстовый слой (ty=5) — просто заменяем текст и цвета
        if layer.get("ty") == 5:
            found_text_layer = True
            if "t" in layer and "d" in layer["t"] and "k" in layer["t"]["d"]:
                if isinstance(layer["t"]["d"]["k"], dict):
                    layer["t"]["d"]["k"]["v"] = new_text
                elif isinstance(layer["t"]["d"]["k"], list) and len(layer["t"]["d"]["k"]) > 0:
                    if isinstance(layer["t"]["d"]["k"][0], dict) and "s" in layer["t"]["d"]["k"][0]:
                        layer["t"]["d"]["k"][0]["s"]["t"] = new_text
            if "c" in layer and "k" in layer["c"]:
                layer["c"]["k"] = text_rgb
            if "sc" in layer and "k" in layer["sc"]:
                layer["sc"]["k"] = stroke_rgb
            if "sw" in layer:
                layer["sw"] = stroke_width
            new_layers.append(layer)
            continue

        # Если это векторный слой (ty=4) и имя состоит из одной буквы/цифры — удаляем
        if layer.get("ty") == 4 and "nm" in layer:
            name = layer["nm"].strip()
            if len(name) == 1 and name.isalnum():
                # Это буква — пропускаем (удаляем)
                continue

        # Для всех остальных слоёв меняем fill и stroke
        if "shapes" in layer:
            for shape in layer["shapes"]:
                if "it" in shape:
                    for item in shape["it"]:
                        if item.get("ty") == "fl" and "c" in item and "k" in item["c"]:
                            item["c"]["k"] = fill_rgb
                        if item.get("ty") == "st" and "c" in item and "k" in item["c"]:
                            item["c"]["k"] = stroke_rgb
        new_layers.append(layer)

    # Если не было текстового слоя, но мы удалили буквы — вставляем новый текстовый слой поверх
    # В любом случае добавляем текстовый слой, чтобы текст точно появился
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
                            "f": font_name,
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
    new_layers.insert(0, text_layer)  # всегда добавляем поверх
    return new_layers, True

def replace_text_and_colors(data, new_text, text_color_hex, fill_color_hex, stroke_color_hex, stroke_width=3, font_name="Arial"):
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
        stroke_width,
        font_name
    )
    data["layers"] = new_layers
    return data, True

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
        [InlineKeyboardButton(text="✨ Создать эмодзи", callback_data="create")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
         InlineKeyboardButton(text="📋 Шаблоны", callback_data="list")],
        [InlineKeyboardButton(text="🔗 Рефералка", callback_data="referral"),
         InlineKeyboardButton(text="ℹ️ Поддержка", callback_data="support")]
    ])

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
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        ref_code = args[1][4:]
        referrer_id = referral_codes.get(ref_code)
        if referrer_id and referrer_id != user_id:
            if user_id not in db["users"]:
                db["users"][user_id] = {"balance": 0, "emojis_created": 0}
                save_db(db)
            add_balance(referrer_id, REFERRAL_BONUS)
            await message.answer(f"🎉 Вы пришли по реферальной ссылке!\nРеферер получил +{REFERRAL_BONUS} баллов.")

    if user_id not in db["users"]:
        db["users"][user_id] = {"balance": 10, "emojis_created": 0}
        save_db(db)
    if user_id in user_states:
        del user_states[user_id]
    await message.answer(
        "✨ Добро пожаловать в StarlitEmoji!\n"
        "Создавай уникальные анимированные эмодзи с текстом и цветами.\n"
        "Выбери действие:",
        reply_markup=main_kb()
    )

@dp.callback_query(F.data == "main")
async def back_main(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    if user_id in user_states:
        del user_states[user_id]
    await callback.message.edit_text("✨ Главное меню:", reply_markup=main_kb())
    await callback.answer()

@dp.callback_query(F.data == "create")
async def create_menu(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    user_states[user_id] = {"step": "font"}
    await callback.message.edit_text("🔤 Сначала выбери шрифт для текста:", reply_markup=font_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("font_"))
async def select_font(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    font_name = callback.data.split("_")[1]
    if user_id not in user_states:
        user_states[user_id] = {"step": "text"}
    user_states[user_id]["font"] = font_name
    user_states[user_id]["step"] = "text"
    await callback.message.edit_text("✏️ Введи текст для эмодзи (до 20 символов):",
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
        user_states[user_id] = {"step": "text", "font": "Arial"}
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

    font_name = state.get("font", "Arial")
    edited, changed = replace_text_and_colors(
        data,
        state["text"],
        state["text_color"],
        state["fill_color"],
        state["stroke_color"],
        stroke_width=3,
        font_name=font_name
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
        f"Шрифт: {font_name}\n"
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
            font_name=state.get("font", "Arial")
        )

    increment_emoji_count(int(user_id))
    out = gzip.compress(json.dumps(edited, separators=(',', ':')).encode())
    balance_display = "∞" if int(user_id) in ADMINS else str(get_balance(int(user_id)))
    emoji_count = get_emoji_count(int(user_id))

    await callback.message.answer_document(
        BufferedInputFile(out, filename="emoji.tgs"),
        caption=f"✅ Готово!\n📝 Текст: {state['text']}\n🎨 Цвет текста: {state['text_color']}\n🎨 Цвет заливки: {state['fill_color']}\n🎨 Цвет обводки: {state['stroke_color']}\n💰 Баланс: {balance_display}\n📊 Создано эмодзи: {emoji_count}"
    )
    del user_states[user_id]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()

# ===== ПРОФИЛЬ, РЕФЕРАЛКА, БАЛАНС =====
@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user_id = int(callback.from_user.id)
    balance = get_balance(user_id)
    emojis = get_emoji_count(user_id)
    display = "∞ (админ)" if user_id in ADMINS else f"{balance} P"
    await callback.message.edit_text(
        f"👤 Профиль\n💰 Баланс: {display}\n📊 Создано эмодзи: {emojis}\n🆔 ID: {user_id}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Рефералка", callback_data="referral")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    link = get_referral_link(int(user_id))
    await callback.message.edit_text(
        f"🔗 Твоя реферальная ссылка:\n`{link}`\n\nЗа каждого нового пользователя ты получишь +{REFERRAL_BONUS} баллов!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data="copy_ref")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ]),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "copy_ref")
async def copy_referral(callback: CallbackQuery):
    await callback.answer("📋 Ссылка скопирована! Нажми и удерживай, чтобы скопировать.", show_alert=True)

@dp.callback_query(F.data == "support")
async def show_support(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ Поддержка\n\nПо всем вопросам пиши:\n📧 support@starlitemoji.com\n💬 @starlit_support",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="main")]
        ])
    )
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
