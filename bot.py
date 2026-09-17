import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
import aiohttp
import asyncio
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from io import BytesIO
import pytz

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SET_LOWER, SET_UPPER = range(2)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TZ = pytz.timezone('Europe/Kyiv')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не знайдено!")

# --- Список валют, доступних для моніторингу ---
CURRENCIES = {
    "cap":      {"cg_id": "cap-4",    "label": "CAP",      "symbol": "CAP", "emoji": "🟡"},
    "ethereum": {"cg_id": "ethereum", "label": "Ethereum", "symbol": "ETH", "emoji": "🔷"},
    "ripple":   {"cg_id": "ripple",   "label": "Ripple",   "symbol": "XRP", "emoji": "💧"},
    "solana":   {"cg_id": "solana",   "label": "Solana",   "symbol": "SOL", "emoji": "🟣"},
}
DEFAULT_CURRENCY = "cap"

# Глобальні змінні для моніторингу: окремо ціна та історія для кожної валюти
price_store = {cid: {"current": None, "history": []} for cid in CURRENCIES}
active_users = set()       # Користувачі, які взаємодіяли з ботом


def format_price(price, dollar=True):
    """
    Форматує ціну з достатньою кількістю знаків після коми.
    Без явної точності Python сам показує стільки знаків, скільки реально
    несе число (0.05871 -> "0.05871", 3000.4 -> "3,000.4"), нічого не
    округлюючи і не дописуючи зайвих нулів.
    """
    if price is None:
        return "н/д"
    price = float(price)
    s = f"{price:,}"
    return f"${s}" if dollar else s


def get_currency_meta(user_data):
    """Повертає (id, meta) обраної користувачем валюти, з дефолтом"""
    cid = user_data.get('currency', DEFAULT_CURRENCY)
    if cid not in CURRENCIES:
        cid = DEFAULT_CURRENCY
    return cid, CURRENCIES[cid]


async def fetch_all_prices():
    """Отримує ціни одразу для всіх валют зі списку CURRENCIES одним запитом"""
    ids = ",".join(meta["cg_id"] for meta in CURRENCIES.values())
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as response:
                if response.status == 200:
                    data = await response.json()
                    result = {}
                    for cid, meta in CURRENCIES.items():
                        cg_id = meta["cg_id"]
                        if cg_id in data and "usd" in data[cg_id]:
                            result[cid] = data[cg_id]["usd"]
                    return result
                elif response.status == 429:
                    logger.warning("CoinGecko API Rate Limit hit (429).")
    except Exception as e:
        logger.error(f"Помилка при отриманні курсів: {e}")
    return {}


def get_current_time():
    return datetime.now(TZ)


def create_price_chart(currency_id, lower_target=None, upper_target=None):
    """Створює графік цін з відображенням екстремумів та встановлених меж користувача"""
    try:
        history = price_store.get(currency_id, {}).get("history", [])
        if len(history) < 2:
            return None

        today_str = get_current_time().strftime('%Y-%m-%d')

        times = [t.strftime('%H:%M:%S') for t, p in history if t.strftime('%Y-%m-%d') == today_str]
        prices = [p for t, p in history if t.strftime('%Y-%m-%d') == today_str]

        if len(prices) < 2:
            return None

        min_today = min(prices)
        max_today = max(prices)
        meta = CURRENCIES[currency_id]

        # Рахуємо межі шкали Y з урахуванням і самих цін, і цільових рівнів,
        # щоб графік не "приплюснувало" через невідповідний масштаб.
        all_values = list(prices)
        if lower_target:
            all_values.append(lower_target)
        if upper_target:
            all_values.append(upper_target)
        data_min, data_max = min(all_values), max(all_values)
        span = data_max - data_min
        if span <= 0:
            span = (data_max * 0.02) if data_max else 1.0
        padding = span * 0.18
        y_min = data_min - padding
        y_max = data_max + padding

        fig, ax = plt.subplots(figsize=(11, 6), facecolor='#2b2d31')
        ax.set_facecolor('#1e1f22')

        ax.plot(times, prices, color='#5865f2', linewidth=2.5, marker='o', markersize=4, label=f'Курс {meta["symbol"]}')
        ax.fill_between(range(len(prices)), prices, y_min, alpha=0.15, color='#5865f2')

        if lower_target:
            ax.axhline(y=lower_target, color='#ed4245', linestyle='--', linewidth=1.5, label=f'Ціль Мін: {format_price(lower_target)}')
        if upper_target:
            ax.axhline(y=upper_target, color='#57f287', linestyle='--', linewidth=1.5, label=f'Ціль Макс: {format_price(upper_target)}')

        ax.set_ylim(y_min, y_max)
        ax.set_xlabel('Час', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Ціна (USD)', color='#b5bac1', fontsize=10)

        title_text = (
            f"📈 {meta['emoji']} Історія цін {meta['label']} (сьогодні)\n"
            f"Min за день: {format_price(min_today)}  |  Max за день: {format_price(max_today)}"
        )
        ax.set_title(title_text, color='#ffffff', fontsize=12, fontweight='bold', pad=14)

        ax.grid(True, alpha=0.15, color='#4f545c')
        ax.tick_params(colors='#b5bac1', labelsize=9)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: format_price(v, dollar=False)))

        ax.legend(loc='upper left', facecolor='#1e1f22', edgecolor='#4f545c', labelcolor='#ffffff', fontsize=9)

        ax.xaxis.set_major_locator(plt.MaxNLocator(8))
        plt.xticks(rotation=30, ha='right')
        plt.tight_layout()

        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=130, bbox_inches='tight', facecolor='#2b2d31')
        buffer.seek(0)
        plt.close(fig)
        return buffer, min_today, max_today
    except Exception as e:
        logger.error(f"Помилка при створенні графіка: {e}")
        return None


async def global_price_monitor(application: Application):
    """Глобальний моніторинг цін кожні 10 секунд та аналіз імпульсів зміни ціни"""
    global price_store

    while True:
        try:
            prices = await fetch_all_prices()
            now = get_current_time()

            if prices:
                for cid, price in prices.items():
                    price_store[cid]["current"] = price
                    price_store[cid]["history"].append((now, price))
                    price_store[cid]["history"] = [
                        item for item in price_store[cid]["history"]
                        if (now - item[0]).total_seconds() < 86400
                    ]

                for user_id in list(active_users):
                    user_data = application.user_data.get(user_id)
                    if not user_data:
                        continue

                    cid, meta = get_currency_meta(user_data)
                    price = price_store.get(cid, {}).get("current")
                    if price is None:
                        continue

                    # --- 1. Живе оновлення екрану поточного курсу ---
                    active_price_msg_id = user_data.get('active_price_msg_id')
                    if active_price_msg_id:
                        try:
                            msg_text = (
                                f"💰 <b>Поточний курс {meta['emoji']} {meta['label']}</b>\n\n"
                                f"<code>{format_price(price)}</code>\n\n"
                                f"⏰ Оновлено: {now.strftime('%H:%M:%S')}"
                            )
                            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]

                            await application.bot.edit_message_text(
                                chat_id=user_id,
                                message_id=active_price_msg_id,
                                text=msg_text,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode="HTML"
                            )
                        except:
                            pass

                    # --- 2. Логіка імпульсних сповіщень (зміна на 2%) ---
                    ref_price = user_data.get('reference_price')
                    if ref_price is None:
                        # Якщо бот щойно запустився або користувач новий, фіксуємо поточну ціну як першу опорну
                        user_data['reference_price'] = price
                    else:
                        # Рахуємо відсоткову зміну від опорної ціни
                        percent_change = ((price - ref_price) / ref_price) * 100

                        if abs(percent_change) >= 2.0:
                            # Визначаємо емодзі та текст залежно від напрямку руху
                            if percent_change > 0:
                                emoji, trend_str = "🚀 <b>ІМПУЛЬС ВГОРУ!</b>", "зросла"
                            else:
                                emoji, trend_str = "⚠️ <b>ІМПУЛЬС ВНИЗ!</b>", "впала"

                            alert_msg = (
                                f"{emoji}\n\n"
                                f"Ціна {meta['label']} {trend_str} на <b>{abs(percent_change):.2f}%</b>\n"
                                f"Попередня опорна: <code>{format_price(ref_price)}</code>\n"
                                f"Поточна ціна: <b>{format_price(price)}</b>\n\n"
                                f"📌 <i>Цю ціну ({format_price(price)}) зафіксовано як нову опорну точку.</i>"
                            )

                            try:
                                await application.bot.send_message(user_id, alert_msg, parse_mode="HTML")
                            except Exception as send_err:
                                logger.error(f"Не вдалося надіслати імпульсне сповіщення: {send_err}")

                            # Оновлюємо опорну ціну: поточний курс стає новим орієнтиром
                            user_data['reference_price'] = price

                    # --- 3. Перевірка статичних лімітів користувачів (Мін/Макс) ---
                    lower = user_data.get('lower_price')
                    upper = user_data.get('upper_price')

                    if lower and price <= lower and not user_data.get('notified_lower', False):
                        await application.bot.send_message(
                            user_id,
                            f"🔴 <b>СИГНАЛ! Ціна {meta['label']} впала нижче межі!</b>\n\nЦільова: {format_price(lower)}\nПоточна: <b>{format_price(price)}</b>",
                            parse_mode="HTML"
                        )
                        user_data['notified_lower'] = True
                    elif lower and price > lower * 1.01:
                        user_data['notified_lower'] = False

                    if upper and price >= upper and not user_data.get('notified_upper', False):
                        await application.bot.send_message(
                            user_id,
                            f"🟢 <b>СИГНАЛ! Ціна {meta['label']} зросла вище межі!</b>\n\nЦільова: {format_price(upper)}\nПоточна: <b>{format_price(price)}</b>",
                            parse_mode="HTML"
                        )
                        user_data['notified_upper'] = True
                    elif upper and price < upper * 0.99:
                        user_data['notified_upper'] = False

        except Exception as e:
            logger.error(f"Помилка в циклі моніторингу: {e}")

        await asyncio.sleep(10)


def get_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити цілі", callback_data="set_target")],
        [InlineKeyboardButton("📈 Графік цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🔄 Змінити валюту", callback_data="change_currency")],
        [InlineKeyboardButton("🛑 Скинути цілі", callback_data="stop")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_currency_keyboard():
    keyboard = []
    for cid, meta in CURRENCIES.items():
        keyboard.append([InlineKeyboardButton(f"{meta['emoji']} {meta['label']} ({meta['symbol']})", callback_data=f"set_currency_{cid}")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт бота або повернення до головного меню"""
    user_id = update.effective_user.id
    active_users.add(user_id)
    context.user_data['active_price_msg_id'] = None

    if 'currency' not in context.user_data:
        context.user_data['currency'] = DEFAULT_CURRENCY

    cid, meta = get_currency_meta(context.user_data)

    # Примусово скидаємо/оновлюємо опорну ціну при старті, якщо хочеться свіжого відліку
    if 'reference_price' not in context.user_data:
        cur_price = price_store.get(cid, {}).get('current')
        if cur_price:
            context.user_data['reference_price'] = cur_price

    if update.message:
        try:
            await update.message.delete()
        except:
            pass
        await update.message.reply_text(
            f"🚀 <b>Price Monitor</b>\n\n"
            f"Моніторю: {meta['emoji']} <b>{meta['label']} ({meta['symbol']})</b>\n"
            f"Опитування курсу відбувається кожні 10 секунд.\nАвтоматично сповіщаю про коливання ринку на ±2%.",
            reply_markup=get_menu_keyboard(),
            parse_mode="HTML"
        )
    return ConversationHandler.END


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник натискань інлайн-кнопок меню"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    active_users.add(user_id)

    if 'currency' not in context.user_data:
        context.user_data['currency'] = DEFAULT_CURRENCY

    if query.data == "show_price":
        context.user_data['active_price_msg_id'] = query.message.message_id
        cid, meta = get_currency_meta(context.user_data)
        display_price = price_store.get(cid, {}).get('current')
        display_time = get_current_time()

        if not display_price and price_store.get(cid, {}).get('history'):
            display_time, display_price = price_store[cid]['history'][-1]

        if display_price:
            msg = f"💰 <b>Поточний курс {meta['emoji']} {meta['label']}</b>\n\n<code>{format_price(display_price)}</code>\n\n⏰ Оновлено: {display_time.strftime('%H:%M:%S')}"
        else:
            msg = "⏳ Зачекайте, завантажуються перші дані..."

        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ConversationHandler.END

    elif query.data == "show_chart":
        context.user_data['active_price_msg_id'] = None
        cid, meta = get_currency_meta(context.user_data)

        lower_target = context.user_data.get('lower_price')
        upper_target = context.user_data.get('upper_price')
        ref_price = context.user_data.get('reference_price')

        chart_data = create_price_chart(cid, lower_target, upper_target)

        if not chart_data:
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
            await query.edit_message_text("📈 <b>Недостатньо даних для графіка</b>\n\nЗачекайте кілька хвилин, поки назбирається історія (мін. 2 точки).", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            chart_buffer, min_today, max_today = chart_data
            try:
                await query.message.delete()
            except:
                pass

            caption_text = (
                f"📊 <b>Аналітика за сьогодні — {meta['emoji']} {meta['label']}:</b>\n"
                f"🔹 Найнижча фіксація: <code>{format_price(min_today)}</code>\n"
                f"🔸 Найвища фіксація: <code>{format_price(max_today)}</code>\n"
                f"📍 Поточна опорна ціна: " + (f"<code>{format_price(ref_price)}</code>" if ref_price else "не зафіксована") + "\n\n"
                f"🎯 <b>Ваші цілі:</b>\n"
                f"🔴 Нижній поріг: " + (f"<code>{format_price(lower_target)}</code>" if lower_target else "не вказано") + "\n"
                f"🟢 Верхній поріг: " + (f"<code>{format_price(upper_target)}</code>" if upper_target else "не вказано")
            )

            keyboard = [[InlineKeyboardButton("🏠 Меню", callback_data="back_from_chart")]]
            await query.message.reply_photo(
                photo=chart_buffer,
                caption=caption_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        return ConversationHandler.END

    elif query.data == "show_targets":
        context.user_data['active_price_msg_id'] = None
        cid, meta = get_currency_meta(context.user_data)
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        ref = context.user_data.get('reference_price')

        msg = f"📍 <b>Ваші налаштування:</b>\n\n"
        msg += f"💱 Валюта: {meta['emoji']} {meta['label']}\n\n"
        msg += f"🔴 Мін ціль: " + (format_price(lower) if lower else "немає") + f"\n"
        msg += f"🟢 Макс ціль: " + (format_price(upper) if upper else "немає") + f"\n"
        msg += f"⚓️ Опорний курс: " + (format_price(ref) if ref else "очікування даних")

        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return ConversationHandler.END

    elif query.data == "change_currency":
        context.user_data['active_price_msg_id'] = None
        cid, meta = get_currency_meta(context.user_data)
        text = f"🔄 <b>Оберіть валюту для моніторингу</b>\n\nЗараз обрано: {meta['emoji']} {meta['label']} ({meta['symbol']})"
        await query.edit_message_text(text, reply_markup=get_currency_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    elif query.data.startswith("set_currency_"):
        new_cid = query.data.replace("set_currency_", "")
        if new_cid in CURRENCIES:
            context.user_data['currency'] = new_cid
            # Скидаємо цілі та опорну ціну — вони стосувались іншої валюти
            context.user_data['lower_price'] = None
            context.user_data['upper_price'] = None
            context.user_data['notified_lower'] = False
            context.user_data['notified_upper'] = False
            context.user_data['reference_price'] = price_store.get(new_cid, {}).get('current')
            context.user_data['active_price_msg_id'] = None

            meta = CURRENCIES[new_cid]
            text = f"✅ Валюту моніторингу змінено на {meta['emoji']} <b>{meta['label']} ({meta['symbol']})</b>\n\nЦілі та опорну ціну скинуто."
            await query.edit_message_text(text, reply_markup=get_menu_keyboard(), parse_mode="HTML")
        return ConversationHandler.END

    elif query.data == "stop":
        cid, meta = get_currency_meta(context.user_data)
        context.user_data['active_price_msg_id'] = None
        context.user_data['lower_price'] = None
        context.user_data['upper_price'] = None
        context.user_data['notified_lower'] = False
        context.user_data['notified_upper'] = False
        # Опорну ціну скидаємо до поточного значення, щоб не спамило сповіщеннями при очищенні
        context.user_data['reference_price'] = price_store.get(cid, {}).get('current')

        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text("⏹️ Статичні цілі видалено. Опорну ціну скинуто до актуальної.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    elif query.data == "back":
        context.user_data['active_price_msg_id'] = None
        cid, meta = get_currency_meta(context.user_data)
        await query.edit_message_text(
            f"🚀 <b>Price Monitor</b>\n\nМоніторю: {meta['emoji']} <b>{meta['label']} ({meta['symbol']})</b>\nОпитування курсу відбувається кожні 10 секунд.",
            reply_markup=get_menu_keyboard(), parse_mode="HTML"
        )
        return ConversationHandler.END

    elif query.data == "back_from_chart":
        context.user_data['active_price_msg_id'] = None
        try:
            await query.message.delete()
        except:
            pass
        cid, meta = get_currency_meta(context.user_data)
        await update.effective_message.reply_text(
            f"🚀 <b>Price Monitor</b>\n\nМоніторю: {meta['emoji']} <b>{meta['label']} ({meta['symbol']})</b>",
            reply_markup=get_menu_keyboard(), parse_mode="HTML"
        )
        return ConversationHandler.END


async def start_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок діалогу встановлення меж ціни"""
    query = update.callback_query
    await query.answer()

    context.user_data['active_price_msg_id'] = None
    context.user_data['conv_menu_msg_id'] = query.message.message_id

    cid, meta = get_currency_meta(context.user_data)
    await query.edit_message_text(
        f"📍 <b>Встановлення цілей для {meta['emoji']} {meta['label']}</b>\n\nВведіть <b>мінімальну ціну</b> (або 0, якщо не потрібна):",
        parse_mode="HTML"
    )
    return SET_LOWER


async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(',', '.'))
        context.user_data['lower_price'] = val if val > 0 else None
        context.user_data['notified_lower'] = False

        try:
            await update.message.delete()
        except:
            pass

        chat_id = update.effective_chat.id
        msg_id = context.user_data.get('conv_menu_msg_id')

        lower_status = format_price(val) if val > 0 else "Вимкнено"
        text = f"✅ Нижня межа: {lower_status}\n\nТепер введіть <b>максимальну ціну</b> (або 0):"

        if msg_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")

        return SET_UPPER
    except ValueError:
        err_msg = await update.message.reply_text("❌ Будь ласка, введіть коректне число! Спробуйте ще раз:")
        context.user_data['last_err_msg_id'] = err_msg.message_id
        return SET_LOWER


async def handle_upper_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(',', '.'))
        context.user_data['upper_price'] = val if val > 0 else None
        context.user_data['notified_upper'] = False

        try:
            await update.message.delete()
        except:
            pass

        if 'last_err_msg_id' in context.user_data:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=context.user_data['last_err_msg_id'])
            except:
                pass

        chat_id = update.effective_chat.id
        msg_id = context.user_data.get('conv_menu_msg_id')
        lower = context.user_data.get('lower_price')

        text = (
            f"🎯 <b>Цілі успішно оновлені!</b>\n\n"
            f"🔴 Мін: " + (format_price(lower) if lower else "немає") + f"\n"
            f"🟢 Макс: " + (format_price(val) if val > 0 else "немає") + f"\n\n"
            f"Бот автоматично сповістить вас у разі пробиття меж."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="back_from_chart")]])

        if msg_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

        return ConversationHandler.END
    except ValueError:
        err_msg = await update.message.reply_text("❌ Будь ласка, введіть коректне число. Спробуйте ще раз:")
        context.user_data['last_err_msg_id'] = err_msg.message_id
        return SET_UPPER


async def handle_unknown_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass
    await start(update, context)


def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_conversation, pattern="set_target")],
        states={
            SET_LOWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lower_price)],
            SET_UPPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_upper_price)],
        },
        fallbacks=[
            CallbackQueryHandler(button_handler)
        ],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_messages))

    async def startup(app):
        asyncio.create_task(global_price_monitor(app))

    application.post_init = startup

    logger.info("Бот успішно запущений.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
