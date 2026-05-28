import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
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
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from io import BytesIO
from collections import deque
import json

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Стани для ConversationHandler
SET_LOWER, SET_UPPER, MONITORING = range(3)

# Отримуємо токен з Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Перевіряємо, чи токен встановлено
if not TELEGRAM_TOKEN:
    logger.error("❌ ПОМИЛКА: Не встановлено TELEGRAM_BOT_TOKEN в Environment Variables!")
    raise ValueError("TELEGRAM_BOT_TOKEN не знайдено в змінних оточення")

# Глобальний моніторинг (єдиний для всіх користувачів)
monitoring_task = None
last_notified_lower = {}
last_notified_upper = {}

async def get_eth_price():
    """Отримує поточний курс ETH у USD"""
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['ethereum']['usd']
    except Exception as e:
        logger.error(f"Помилка при отриманні курсу: {e}")
    return None

def init_user_data(context: ContextTypes.DEFAULT_TYPE):
    """Ініціалізує дані користувача"""
    if 'price_history' not in context.user_data:
        context.user_data['price_history'] = {}  # {date_str: [(time, price), ...]}
    if 'show_price_active' not in context.user_data:
        context.user_data['show_price_active'] = False
    if 'last_menu_message_id' not in context.user_data:
        context.user_data['last_menu_message_id'] = None

def add_price_to_history(context: ContextTypes.DEFAULT_TYPE, price: float):
    """Додає ціну до історії з датою як ключ"""
    today = datetime.now().strftime('%Y-%m-%d')
    time_str = datetime.now().strftime('%H:%M')
    
    if today not in context.user_data['price_history']:
        context.user_data['price_history'][today] = []
    
    context.user_data['price_history'][today].append((time_str, price))

def get_today_prices(context: ContextTypes.DEFAULT_TYPE) -> list:
    """Отримує ціни за сьогодні"""
    today = datetime.now().strftime('%Y-%m-%d')
    return context.user_data['price_history'].get(today, [])

def cleanup_old_dates(context: ContextTypes.DEFAULT_TYPE):
    """Видаляє дані старші за сьогодні"""
    today = datetime.now().strftime('%Y-%m-%d')
    dates_to_delete = []
    
    for date_key in context.user_data['price_history'].keys():
        if date_key != today:
            dates_to_delete.append(date_key)
    
    for date_key in dates_to_delete:
        del context.user_data['price_history'][date_key]
        logger.info(f"Видалено дані за {date_key}")

def create_price_chart(prices_data):
    """Створює графік цін"""
    try:
        if not prices_data:
            return None
        
        # Дані для графіка
        times = [item[0] for item in prices_data]
        prices = [item[1] for item in prices_data]
        
        # Створюємо графік
        fig, ax = plt.subplots(figsize=(10, 5), facecolor='#2b2d31')
        ax.set_facecolor('#1e1f22')
        
        # Малюємо лінію
        ax.plot(times, prices, color='#5865f2', linewidth=2.5, marker='o', markersize=4)
        
        # Заповнюємо область під лінією
        ax.fill_between(range(len(prices)), prices, alpha=0.2, color='#5865f2')
        
        # Встановлюємо межі осі Y з +/- 100
        min_price = min(prices)
        max_price = max(prices)
        y_min = max(0, min_price - 100)
        y_max = max_price + 100
        ax.set_ylim(y_min, y_max)
        
        # Форматування осей
        ax.set_xlabel('Час', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Ціна (USD)', color='#b5bac1', fontsize=10)
        ax.set_title('📈 Історія цін Ефіра (сьогодні)', color='#ffffff', fontsize=12, fontweight='bold')
        
        # Сітка
        ax.grid(True, alpha=0.2, color='#4f545c')
        
        # Колір текста
        ax.tick_params(colors='#b5bac1')
        
        # Форматування x-осі
        ax.xaxis.set_major_locator(plt.MaxNLocator(5))
        plt.xticks(rotation=45, ha='right')
        
        plt.tight_layout()
        
        # Зберігаємо в буфер
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='#2b2d31')
        buffer.seek(0)
        plt.close()
        
        return buffer
    except Exception as e:
        logger.error(f"Помилка при створенні графіка: {e}")
        return None

async def global_price_monitor(application: Application):
    """
    Глобальний моніторинг ціни кожні 5 хвилин.
    Перевіряє ціну та сигналізує ВСЕ користувачам що мають встановлені цілі.
    """
    global monitoring_task
    
    while True:
        try:
            await asyncio.sleep(300)  # 5 хвилин
            
            price = await get_eth_price()
            
            if price:
                logger.info(f"Поточна ціна ETH: ${price:,.2f}")
                
                # Проходимо по всім активним користувачам
                for user_id in list(last_notified_lower.keys()) + list(last_notified_upper.keys()):
                    try:
                        # Отримуємо контекст користувача
                        user_context = application.user_data.get(user_id)
                        if not user_context:
                            continue
                        
                        lower = user_context.get('lower_price')
                        upper = user_context.get('upper_price')
                        
                        if not lower and not upper:
                            continue
                        
                        # Перевіряємо нижню границю
                        if lower and price <= lower and not last_notified_lower.get(user_id, False):
                            await application.bot.send_message(
                                user_id,
                                f"🔴 <b>СИГНАЛ!</b> 🔴\n\n"
                                f"<b>Ціна Ефіра досягла нижнього показника!</b>\n"
                                f"💰 ${price:,.2f} ≤ ${lower:,.2f}\n"
                                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                parse_mode="HTML"
                            )
                            last_notified_lower[user_id] = True
                        elif price > lower * 1.05:
                            last_notified_lower[user_id] = False
                        
                        # Перевіряємо верхню границю
                        if upper and price >= upper and not last_notified_upper.get(user_id, False):
                            await application.bot.send_message(
                                user_id,
                                f"🟢 <b>СИГНАЛ!</b> 🟢\n\n"
                                f"<b>Ціна Ефіра досягла верхнього показника!</b>\n"
                                f"💰 ${price:,.2f} ≥ ${upper:,.2f}\n"
                                f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                parse_mode="HTML"
                            )
                            last_notified_upper[user_id] = True
                        elif price < upper * 0.95:
                            last_notified_upper[user_id] = False
                    
                    except Exception as e:
                        logger.error(f"Помилка при обробці користувача {user_id}: {e}")
            
            # Очищуємо старі дані за попередні дні
            for user_id in last_notified_lower.keys():
                user_context = application.user_data.get(user_id)
                if user_context:
                    cleanup_old_dates(user_context)
                    
        except asyncio.CancelledError:
            logger.info("Глобальний моніторинг зупинений")
            break
        except Exception as e:
            logger.error(f"Помилка в глобальному моніторингу: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start та автоматичний старт при першому повідомленні"""
    # Видаляємо користувацьке повідомлення
    try:
        await update.message.delete()
    except:
        pass
    
    # Ініціалізуємо дані
    init_user_data(context)
    
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("📈 Історія цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = (
        "🚀 <b>ETH Price Monitor</b>\n\n"
        "Слідкування за курсом Ефіра в реальному часі\n\n"
        "✨ <b>Доступні функції:</b>\n"
        "📊 Показати поточний курс\n"
        "📍 Встановити цільові ціни\n"
        "📈 Переглянути графік цін\n"
        "⚙️ Дивитися свої цілі\n\n"
        "<i>Бот автоматично сигналізує при досягненні цілей 🔔</i>"
    )
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def auto_update_price(query, context: ContextTypes.DEFAULT_TYPE):
    """Оновлює ціну коли користувач дивиться курс"""
    try:
        message_id = query.message.message_id
        chat_id = query.message.chat_id
        last_price = None
        
        while context.user_data.get('show_price_active', False):
            await asyncio.sleep(10)
            
            if not context.user_data.get('show_price_active'):
                break
            
            try:
                price = await get_eth_price()
                
                if price:
                    lower = context.user_data.get('lower_price')
                    upper = context.user_data.get('upper_price')
                    
                    # Додаємо ціну до історії
                    add_price_to_history(context, price)
                    
                    if last_price is None or abs(price - last_price) >= 0.01:
                        message = f"💰 <b>Поточний курс Ефіра</b>\n"
                        message += f"<code>${price:,.2f}</code>\n"
                        message += f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
                        message += "🔄 <i>(оновлюється кожні 10 сек)</i>"
                        
                        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        try:
                            await context.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=message,
                                reply_markup=reply_markup,
                                parse_mode="HTML"
                            )
                            last_price = price
                        except Exception as e:
                            if "not modified" not in str(e).lower():
                                logger.error(f"Помилка при оновленні: {e}")
                                break
                                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Помилка в циклі: {e}")
                break
                
    except Exception as e:
        logger.error(f"Помилка в auto_update_price: {e}")

async def show_current_price(query, context):
    """Показує поточний курс ETH з автооновленням"""
    init_user_data(context)
    context.user_data['show_price_active'] = True
    
    price = await get_eth_price()
    
    if price:
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        # Додаємо ціну до історії
        add_price_to_history(context, price)
        
        message = f"💰 <b>Поточний курс Ефіра</b>\n"
        message += f"<code>${price:,.2f}</code>\n"
        message += f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
        message += "🔄 <i>(оновлюється кожні 10 сек)</i>"
    else:
        message = "❌ Не вдалося отримати курс. Спробуй пізніше."
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Помилка: {e}")
    
    context.application.create_task(auto_update_price(query, context))

async def show_chart(query, context):
    """Показує графік історії цін за сьогодні"""
    context.user_data['show_price_active'] = False
    
    prices_data = get_today_prices(context)
    
    if len(prices_data) < 2:
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📈 <b>Історія цін</b>\n\n"
            "⚠️ Недостатньо даних для графіка.\n"
            "Дивись на курс хоча б 2 рази (20 сек) для накопичення даних.",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        try:
            chart_buffer = create_price_chart(prices_data)
            
            if chart_buffer:
                chart_buffer.seek(0)
                
                message_text = (
                    "📈 <b>Графік цін Ефіра (сьогодні)</b>\n\n"
                    f"📊 Дата: {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"🔢 Дані: {len(prices_data)} точок\n"
                    f"💰 Мінімум: ${min(p[1] for p in prices_data):,.2f}\n"
                    f"💰 Максимум: ${max(p[1] for p in prices_data):,.2f}\n"
                    f"📍 Остання: ${prices_data[-1][1]:,.2f}"
                )
                
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await context.bot.edit_message_media(
                        chat_id=query.message.chat_id,
                        message_id=query.message.message_id,
                        media=InputMediaPhoto(
                            media=chart_buffer,
                            caption=message_text,
                            parse_mode="HTML"
                        ),
                        reply_markup=reply_markup
                    )
                except:
                    try:
                        await query.message.delete()
                    except:
                        pass
                    
                    await query.message.reply_photo(
                        photo=chart_buffer,
                        caption=message_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
            else:
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "❌ Помилка при створенні графіка.",
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Помилка при показі графіка: {e}")
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "❌ Помилка при показі графіка.",
                reply_markup=reply_markup
            )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_price":
        await show_current_price(query, context)
    elif query.data == "show_chart":
        await show_chart(query, context)
    elif query.data == "set_target":
        context.user_data['show_price_active'] = False
        
        await query.edit_message_text(
            "📍 <b>Встановити цільові ціни</b>\n\n"
            "Введи <b>мінімальну ціну</b> (нижня границя):",
            parse_mode="HTML"
        )
        return SET_LOWER
    elif query.data == "show_targets":
        context.user_data['show_price_active'] = False
        await show_targets(query, context)
    elif query.data == "stop_monitoring":
        context.user_data['show_price_active'] = False
        
        # Видаляємо користувача з моніторингу
        user_id = update.effective_user.id
        if user_id in last_notified_lower:
            del last_notified_lower[user_id]
        if user_id in last_notified_upper:
            del last_notified_upper[user_id]
        
        await query.edit_message_text(
            "⏹️ <b>Моніторинг зупинено.</b>",
            parse_mode="HTML"
        )
        await send_main_menu(query, context)
    elif query.data == "back_menu":
        context.user_data['show_price_active'] = False
        await send_main_menu(query, context)

async def show_targets(query, context):
    """Показує встановлені цільові ціни"""
    lower = context.user_data.get('lower_price')
    upper = context.user_data.get('upper_price')
    
    if not lower and not upper:
        message = "⚙️ <b>Мої цільові ціни</b>\n\n"
        message += "❌ Ти ще не встановив цільові ціни.\n\n"
        message += "📍 Натисни '<b>Встановити ціль</b>' для додавання."
    else:
        message = "⚙️ <b>Мої цільові ціни</b>\n\n"
        if lower:
            message += f"📍 <b>Нижня границя:</b> <code>${lower:,.2f}</code>\n"
        if upper:
            message += f"📍 <b>Верхня границя:</b> <code>${upper:,.2f}</code>\n"
        message += f"\n💡 Бот подасть сигнал при досягненні цілей!"
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")

async def send_main_menu(query, context):
    """Відправляє головне меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("📈 Історія цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = "🏠 <b>Головне меню</b>"
    
    await query.edit_message_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення мінімальної ціни"""
    try:
        lower_price = float(update.message.text)
        context.user_data['lower_price'] = lower_price
        
        # Видаляємо старе повідомлення
        try:
            await update.message.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"✅ <b>Нижня границя встановлена:</b> <code>${lower_price:,.2f}</code>\n\n"
            "➡️ Тепер введи <b>ВЕРХНЮ ціну</b>:",
            parse_mode="HTML"
        )
        return SET_UPPER
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Помилка!</b>\n\n"
            "Введи коректне число\n"
            "<i>Приклад: 2500 або 2500.50</i>",
            parse_mode="HTML"
        )
        return SET_LOWER

async def handle_upper_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення максимальної ціни"""
    try:
        upper_price = float(update.message.text)
        context.user_data['upper_price'] = upper_price
        
        lower = context.user_data.get('lower_price')
        
        # Видаляємо старе повідомлення
        try:
            await update.message.delete()
        except:
            pass
        
        message = (
            "✅ <b>Цільові ціни встановлені!</b>\n\n"
            f"📍 <b>Нижня границя:</b> <code>${lower:,.2f}</code>\n"
            f"📍 <b>Верхня границя:</b> <code>${upper_price:,.2f}</code>\n\n"
            "🔔 <b>Моніторинг активний!</b>\n"
            "📤 Ти отримаєш сигнали при досягненні цілей."
        )
        
        # Додаємо користувача до глобального моніторингу
        user_id = update.effective_user.id
        last_notified_lower[user_id] = False
        last_notified_upper[user_id] = False
        
        keyboard = [
            [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
            [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
            [InlineKeyboardButton("🏠 Меню", callback_data="back_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Помилка!</b>\n\n"
            "Введи коректне число\n"
            "<i>Приклад: 3000 або 3000.50</i>",
            parse_mode="HTML"
        )
        return SET_UPPER

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасовує операцію"""
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє будь-яке повідомлення - автоматично стартує бот"""
    await start(update, context)

def main():
    """Запускає бот"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="set_target")],
        states={
            SET_LOWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lower_price)],
            SET_UPPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_upper_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаємо глобальний моніторинг
    async def startup(application):
        asyncio.create_task(global_price_monitor(application))
    
    application.post_init = startup
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
