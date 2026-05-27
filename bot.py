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
import matplotlib.dates as mdates
from io import BytesIO
from collections import deque

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

# API для отримання курсу ETH (використовуємо CoinGecko - вільна API)
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

def create_price_chart(prices_data):
    """Створює графік цін"""
    try:
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
        
        # Форматування осей
        ax.set_xlabel('Час', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Ціна (USD)', color='#b5bac1', fontsize=10)
        ax.set_title('📈 Історія цін Ефіра', color='#ffffff', fontsize=12, fontweight='bold')
        
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start та автоматичний старт при першому повідомленні"""
    # Видаляємо всі попередні повідомлення від користувача в чаті
    try:
        # Спробуємо видалити декілька останніх повідомлень
        chat_id = update.effective_chat.id
        message_id = update.message.message_id
        
        for i in range(1, 20):  # Видаляємо до 20 попередніх повідомлень
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id - i)
            except:
                pass
    except:
        pass
    
    # Ініціалізуємо дані користувача
    if 'price_history' not in context.user_data:
        context.user_data['price_history'] = deque(maxlen=20)
    if 'show_price_active' not in context.user_data:
        context.user_data['show_price_active'] = False
    
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("📈 Історія цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = (
        "╔════════════════════════════════════╗\n"
        "║   🚀 ETH PRICE MONITOR 🚀          ║\n"
        "║  Слідкування за курсом Ефіра      ║\n"
        "╚════════════════════════════════════╝\n\n"
        "✨ <b>Вибери дію:</b>\n"
        "• 📊 Переглянь поточний курс\n"
        "• 📍 Встанови цільові ціни\n"
        "• 📈 Дивись історію цін (графік)\n"
        "• ⚙️ Перегляд своїх цілей\n\n"
        "<i>Бот буде сигналізувати при досягненні цілей 🔔</i>"
    )
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def auto_update_price(query, context: ContextTypes.DEFAULT_TYPE):
    """Автоматично оновлює ціну кожні 10 секунд, тільки якщо вона змінилась"""
    try:
        message_id = query.message.message_id
        chat_id = query.message.chat_id
        last_price = None
        last_signal = None
        
        while context.user_data.get('show_price_active', False):
            await asyncio.sleep(10)
            
            if not context.user_data.get('show_price_active'):
                break
            
            try:
                price = await get_eth_price()
                
                if price:
                    lower = context.user_data.get('lower_price')
                    upper = context.user_data.get('upper_price')
                    
                    # Зберігаємо в історію
                    context.user_data['price_history'].append(
                        (datetime.now().strftime('%H:%M'), price)
                    )
                    
                    # Перевіряємо сигнали
                    current_signal = None
                    if lower and price <= lower:
                        current_signal = "lower"
                    elif upper and price >= upper:
                        current_signal = "upper"
                    
                    if last_price is None or abs(price - last_price) >= 0.01 or current_signal != last_signal:
                        message = "╔════════════════════════════════════╗\n"
                        message += f"║  💰 КУРС ЕФІРА: ${price:,.2f}        ║\n"
                        message += "╚════════════════════════════════════╝\n\n"
                        message += f"⏰ Час: {datetime.now().strftime('%H:%M:%S')}\n"
                        message += "🔄 <i>(оновлюється кожні 10 сек)</i>"
                        
                        if lower and price <= lower:
                            message += "\n\n🔴 <b>СИГНАЛ!</b> Ціна досягла нижнього показника!"
                        if upper and price >= upper:
                            message += "\n\n🟢 <b>СИГНАЛ!</b> Ціна досягла верхнього показника!"
                        
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
                            last_signal = current_signal
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
    context.user_data['show_price_active'] = True
    
    price = await get_eth_price()
    
    if price:
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        # Зберігаємо в історію
        context.user_data['price_history'].append(
            (datetime.now().strftime('%H:%M'), price)
        )
        
        message = "╔════════════════════════════════════╗\n"
        message += f"║  💰 КУРС ЕФІРА: ${price:,.2f}        ║\n"
        message += "╚════════════════════════════════════╝\n\n"
        message += f"⏰ Час: {datetime.now().strftime('%H:%M:%S')}\n"
        message += "🔄 <i>(оновлюється кожні 10 сек)</i>"
        
        if lower and price <= lower:
            message += "\n\n🔴 <b>СИГНАЛ!</b> Ціна досягла нижнього показника!"
        if upper and price >= upper:
            message += "\n\n🟢 <b>СИГНАЛ!</b> Ціна досягла верхнього показника!"
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
    """Показує графік історії цін"""
    context.user_data['show_price_active'] = False
    
    prices_data = list(context.user_data.get('price_history', []))
    
    if len(prices_data) < 2:
        await query.edit_message_text(
            "📈 <b>Історія цін</b>\n\n"
            "⚠️ Недостатньо даних для графіка.\n"
            "Дивись на курс хоча б 2 рази (20 сек) для накопичення даних.",
            parse_mode="HTML"
        )
    else:
        try:
            chart_buffer = create_price_chart(prices_data)
            
            if chart_buffer:
                chart_buffer.seek(0)
                
                message_text = (
                    "📈 <b>Графік цін Ефіра</b>\n\n"
                    f"📊 Дата поточна: {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"🔢 Дані в графіку: {len(prices_data)} точок\n"
                    f"💰 Мінімум: ${min(p[1] for p in prices_data):,.2f}\n"
                    f"💰 Максимум: ${max(p[1] for p in prices_data):,.2f}\n"
                    f"📍 Остання ціна: ${prices_data[-1][1]:,.2f}"
                )
                
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.message.reply_photo(
                    photo=chart_buffer,
                    caption=message_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                
                try:
                    await query.message.delete()
                except:
                    pass
            else:
                await query.edit_message_text("❌ Помилка при створенні графіка.")
        except Exception as e:
            logger.error(f"Помилка при показі графіка: {e}")
            await query.edit_message_text("❌ Помилка при показі графіка.")
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if len(prices_data) < 2:
        await query.edit_message_text(
            "📈 <b>Історія цін</b>\n\n"
            "⚠️ Недостатньо даних для графіка.\n"
            "Дивись на курс хоча б 2 рази (20 сек) для накопичення даних.",
            reply_markup=reply_markup,
            parse_mode="HTML"
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
            "╔════════════════════════════════════╗\n"
            "║   📍 ВСТАНОВИ ЦІЛЬОВІ ЦІНИ       ║\n"
            "╚════════════════════════════════════╝\n\n"
            "Введи <b>МІНІМАЛЬНУ ціну</b> (нижня границя):",
            parse_mode="HTML"
        )
        return SET_LOWER
    elif query.data == "show_targets":
        context.user_data['show_price_active'] = False
        await show_targets(query, context)
    elif query.data == "stop_monitoring":
        context.user_data['monitoring'] = False
        context.user_data['show_price_active'] = False
        
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
        message = "╔════════════════════════════════════╗\n"
        message += "║   ⚙️ МОЇ ЦІЛЬОВІ ЦІНИ             ║\n"
        message += "╚════════════════════════════════════╝\n\n"
        message += "❌ Ти ще не встановив цільові ціни.\n\n"
        message += "📍 Натисни '<b>Встановити ціль</b>' для додавання."
    else:
        message = "╔════════════════════════════════════╗\n"
        message += "║   ⚙️ МОЇ ЦІЛЬОВІ ЦІНИ             ║\n"
        message += "╚════════════════════════════════════╝\n\n"
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
    
    message_text = (
        "╔════════════════════════════════════╗\n"
        "║   🏠 ГОЛОВНЕ МЕНЮ                 ║\n"
        "╚════════════════════════════════════╝\n\n"
        "✨ <b>Вибери дію:</b>"
    )
    
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
        context.user_data['monitoring'] = True
        
        lower = context.user_data.get('lower_price')
        
        # Видаляємо старе повідомлення
        try:
            await update.message.delete()
        except:
            pass
        
        message = (
            "╔════════════════════════════════════╗\n"
            "║   ✅ ЦІЛЬОВІ ЦІНИ ВСТАНОВЛЕНІ!   ║\n"
            "╚════════════════════════════════════╝\n\n"
            f"📍 <b>Нижня границя:</b> <code>${lower:,.2f}</code>\n"
            f"📍 <b>Верхня границя:</b> <code>${upper_price:,.2f}</code>\n\n"
            "🔔 <b>Моніторинг активний!</b>\n"
            "📤 Ти отримаєш сигнали при досягненні цілей."
        )
        
        context.application.create_task(
            monitor_price(update.effective_user.id, context)
        )
        
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

async def monitor_price(user_id, context: ContextTypes.DEFAULT_TYPE):
    """Фоновий моніторинг ціни"""
    check_interval = 300
    last_notified_lower = False
    last_notified_upper = False
    
    while context.user_data.get('monitoring', True):
        try:
            price = await get_eth_price()
            lower = context.user_data.get('lower_price')
            upper = context.user_data.get('upper_price')
            
            if price:
                context.user_data['price_history'].append(
                    (datetime.now().strftime('%H:%M'), price)
                )
                
                if lower and price <= lower and not last_notified_lower:
                    await context.bot.send_message(
                        user_id,
                        f"🔴 <b>СИГНАЛ!</b> 🔴\n\n"
                        f"<b>Ціна досягла нижнього показника!</b>\n"
                        f"💰 ${price:,.2f} ≤ ${lower:,.2f}\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="HTML"
                    )
                    last_notified_lower = True
                elif price > lower * 1.05:
                    last_notified_lower = False
                
                if upper and price >= upper and not last_notified_upper:
                    await context.bot.send_message(
                        user_id,
                        f"🟢 <b>СИГНАЛ!</b> 🟢\n\n"
                        f"<b>Ціна досягла верхнього показника!</b>\n"
                        f"💰 ${price:,.2f} ≥ ${upper:,.2f}\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="HTML"
                    )
                    last_notified_upper = True
                elif price < upper * 0.95:
                    last_notified_upper = False
            
            await asyncio.sleep(check_interval)
        except Exception as e:
            logger.error(f"Помилка в моніторингу: {e}")
            await asyncio.sleep(check_interval)

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
    # Автоматичний старт при будь-якому повідомленні
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
