import os
import re
from dataclasses import dataclass
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Update, Message
from aiogram.filters import Command

# === ENV ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "supersecret")  # придумай сам
BASE_PATH = f"/webhook/{WEBHOOK_SECRET}"

EUR_RUB = float(os.environ.get("EUR_RUB", "110"))
KRW_RUB = float(os.environ.get("KRW_RUB", "0.07"))
DELIVERY_RUB = float(os.environ.get("DELIVERY_RUB", "120000"))
BROKER_RUB   = float(os.environ.get("BROKER_RUB", "25000"))
SBKTS_RUB    = float(os.environ.get("SBKTS_RUB", "30000"))
EXTRA_RUB    = float(os.environ.get("EXTRA_RUB", "0"))

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

# ---------- Формулы ----------
def duty_phys_eur(volume_cm3: int, age_years: float, car_value_eur: float | None = None) -> float:
    v = volume_cm3
    if age_years > 5:
        if v <= 1000: rate = 3.0
        elif v <= 1500: rate = 3.2
        elif v <= 1800: rate = 3.5
        elif v <= 2300: rate = 4.8
        elif v <= 3000: rate = 5.0
        else: rate = 5.7
        return rate * v

    if 3 <= age_years <= 5:
        if v <= 1000: rate = 1.5
        elif v <= 1500: rate = 1.7
        elif v <= 1800: rate = 2.5
        elif v <= 2300: rate = 2.7
        elif v <= 3000: rate = 3.0
        else: rate = 3.6
        return rate * v

    if car_value_eur is None:
        raise ValueError("Нужна стоимость в EUR для авто <3 лет.")
    bands = [
        (0, 8500, 0.54, 2.5),
        (8500, 16700, 0.48, 3.5),
        (16700, 42300, 0.48, 5.5),
        (42300, 84500, 0.48, 7.5),
        (84500, 169000, 0.48, 15.0),
        (169000, float("inf"), 0.48, 20.0),
    ]
    for lo, hi, pct, min_eur_cm3 in bands:
        if lo <= car_value_eur < hi:
            return max(car_value_eur * pct, min_eur_cm3 * v)
    return max(car_value_eur * 0.48, 20.0 * v)

def util_fee_rub(volume_cm3: int, age_years: float) -> int:
    if volume_cm3 <= 3000:
        return 5200 if age_years > 3 else 3400
    if volume_cm3 <= 3500:
        return 3296800 if age_years > 3 else 2153400
    return 3604800 if age_years > 3 else 2742200

def customs_fee_rub(customs_value_rub: float) -> int:
    x = customs_value_rub
    if x <= 200_000:  return 1067
    if x <= 450_000:  return 2134
    if x <= 1_200_000:return 4269
    if x <= 2_700_000:return 11746
    if x <= 4_200_000:return 16524
    if x <= 5_500_000:return 21344
    if x <= 7_000_000:return 27540
    return 30000

def krw_to_rub(krw: float) -> float:
    return krw * KRW_RUB

def eur_to_rub(eur: float) -> float:
    return eur * EUR_RUB

@dataclass
class CalcInput:
    price_krw: float
    volume_cm3: int
    year: int
    hp: int | None = None
    delivery_rub: float = DELIVERY_RUB
    broker_rub: float = BROKER_RUB
    sbkts_rub: float = SBKTS_RUB
    extra_rub: float = EXTRA_RUB

def calc_full(ci: CalcInput) -> dict:
    age = max(0, datetime.now().year - ci.year)
    price_rub = krw_to_rub(ci.price_krw)
    price_eur = price_rub / EUR_RUB

    duty_eur = duty_phys_eur(ci.volume_cm3, age, car_value_eur=price_eur if age < 3 else None)
    duty_rub = eur_to_rub(duty_eur)
    util_rub = util_fee_rub(ci.volume_cm3, age)
    tfee_rub = customs_fee_rub(price_rub)

    total = (price_rub + ci.delivery_rub +
             duty_rub + util_rub + tfee_rub +
             ci.broker_rub + ci.sbkts_rub + ci.extra_rub)

    return {
        "age_years": age,
        "price_rub": round(price_rub),
        "duty_eur": round(duty_eur, 2),
        "duty_rub": round(duty_rub),
        "util_rub": util_rub,
        "tfee_rub": tfee_rub,
        "delivery_rub": int(ci.delivery_rub),
        "broker_rub": int(ci.broker_rub),
        "sbkts_rub": int(ci.sbkts_rub),
        "extra_rub": int(ci.extra_rub),
        "total_rub": round(total),
    }

# ---------- Команды ----------
@rt.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        "👋 Я считаю полную растаможку (физлицо, РФ).\n"
        "Команды:\n"
        "• /курс — показать курсы\n"
        "• /курс EUR 110 — задать курс евро\n"
        "• /курс KRW 0.07 — задать курс воны\n"
        "• /расчет ценаKRW объёмсм3 год [доставка₽]\n"
        "  пример: /расчет 6500000 1591 2011 120000\n"
        "• /расчет_подробно — пошаговый опрос"
    )

@rt.message(Command("курс"))
async def rate(msg: Message):
    global EUR_RUB, KRW_RUB
    parts = msg.text.strip().split()
    if len(parts) == 3:
        code = parts[1].upper()
        try:
            val = float(parts[2].replace(",", "."))
        except:
            return await msg.answer("Не понял число. Пример: /курс EUR 110")
        if code == "EUR":
            EUR_RUB = val
            return await msg.answer(f"✅ Курс EUR обновлён: {EUR_RUB} ₽")
        if code == "KRW":
            KRW_RUB = val
            return await msg.answer(f"✅ Курс KRW обновлён: {KRW_RUB} ₽")
        return await msg.answer("Используй: /курс EUR <число> или /курс KRW <число>")
    await msg.answer(f"Текущие курсы: 1€={EUR_RUB}₽, 1₩={KRW_RUB}₽")

def parse_fast_args(text: str):
    nums = re.findall(r"[\d]+(?:[.,]\d+)?", text)
    if len(nums) < 3:
        return None
    price_krw = float(nums[0].replace(",", "."))
    vol = int(float(nums[1]))
    year = int(float(nums[2]))
    delivery = float(nums[3]) if len(nums) >= 4 else DELIVERY_RUB
    return CalcInput(price_krw=price_krw, volume_cm3=vol, year=year, delivery_rub=delivery)

@rt.message(Command("расчет"))
async def calc_fast(msg: Message):
    ci = parse_fast_args(msg.text)
    if not ci:
        return await msg.answer("Формат: /расчет ценаKRW объёмсм3 год [доставка₽]\nПример: /расчет 6500000 1591 2011 120000")
    res = calc_full(ci)
    await msg.answer(
        "📦 Полный расчёт (физлицо, РФ):\n"
        f"Цена в Корее: ~{res['price_rub']:,} ₽\n"
        f"Доставка: {res['delivery_rub']:,} ₽\n"
        f"Пошлина: ≈ {res['duty_eur']:,} € (~{res['duty_rub']:,} ₽)\n"
        f"Утильсбор: {res['util_rub']:,} ₽\n"
        f"Там. сбор: {res['tfee_rub']:,} ₽\n"
        f"Брокер: {res['broker_rub']:,} ₽\n"
        f"СБКТС: {res['sbkts_rub']:,} ₽\n"
        f"Прочее: {res['extra_rub']:,} ₽\n"
        f"— — — — — — — —\n"
        f"ИТОГО: {res['total_rub']:,} ₽\n"
        f"(Возраст авто: {res['age_years']} лет)\n"
    )

# FSM режим (минимум шагов)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

class S(StatesGroup):
    price = State()
    vol = State()
    year = State()
    delivery = State()

@rt.message(Command("расчет_подробно"))
async def calc_step1(msg: Message, state: FSMContext):
    await state.set_state(S.price)
    await msg.answer("Введи цену в Корее (KRW), например 6500000")

@rt.message(S.price)
async def step_price(msg: Message, state: FSMContext):
    try:
        price = float(msg.text.replace(",", "."))
    except:
        return await msg.answer("Нужно число в вона́х. Например: 6500000")
    await state.update_data(price_krw=price)
    await state.set_state(S.vol)
    await msg.answer("Объём двигателя (см³), например 1591")

@rt.message(S.vol)
async def step_vol(msg: Message, state: FSMContext):
    try:
        vol = int(float(msg.text))
    except:
        return await msg.answer("Нужно целое число, см³. Например: 1591")
    await state.update_data(volume_cm3=vol)
    await state.set_state(S.year)
    await msg.answer("Год выпуска, например 2011")

@rt.message(S.year)
async def step_year(msg: Message, state: FSMContext):
    try:
        year = int(float(msg.text))
    except:
        return await msg.answer("Нужен год, например: 2011")
    await state.update_data(year=year)
    await state.set_state(S.delivery)
    await msg.answer(f"Доставка в ₽ (Enter — по умолчанию {int(DELIVERY_RUB)}):")

@rt.message(S.delivery)
async def step_delivery(msg: Message, state: FSMContext):
    delivery = DELIVERY_RUB if not msg.text.strip() else float(msg.text.replace(",", "."))
    data = await state.get_data()
    ci = CalcInput(
        price_krw=data["price_krw"],
        volume_cm3=data["volume_cm3"],
        year=data["year"],
        delivery_rub=delivery
    )
    res = calc_full(ci)
    await state.clear()
    await msg.answer(
        "✅ Готово.\n"
        f"Цена в Корее: ~{res['price_rub']:,} ₽\n"
        f"Доставка: {res['delivery_rub']:,} ₽\n"
        f"Пошлина: ≈ {res['duty_eur']:,} € (~{res['duty_rub']:,} ₽)\n"
        f"Утильсбор: {res['util_rub']:,} ₽\n"
        f"Там. сбор: {res['tfee_rub']:,} ₽\n"
        f"Брокер: {res['broker_rub']:,} ₽\n"
        f"СБКТС: {res['sbkts_rub']:,} ₽\n"
        f"Прочее: {res['extra_rub']:,} ₽\n"
        f"— — — — — — — —\n"
        f"ИТОГО: {res['total_rub']:,} ₽\n"
    )

# === FastAPI + Webhook ===
app = FastAPI()

@app.get("/")
async def root():
    return PlainTextResponse("OK")

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})

# ручка для установки вебхука один раз
# её вызовем вручную из браузера после деплоя
@app.get("/init")
async def init(request: Request):
    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}{BASE_PATH}"
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    return JSONResponse({"webhook": webhook_url, "status": "set"})

@app.post(f"{BASE_PATH}")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})
