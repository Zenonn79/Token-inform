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

# Глобальні змінні для моніторингу
current_eth_price = None
global_price_history = []  # Список кортежів: (datetime_obj, price)
active_users = set()       # Користувачі, які взаємодіяли з ботом

async def get_eth_price():
    """Отримує актуальну ціну ETH з Coingecko"""
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['ethereum']['usd']
                elif response.status == 429:
                    logger.warning("CoinGecko API Rate Limit hit (429).")
    except Exception as e:
        logger.error(f"Помилка при отриманні курсу: {e}")
    return None

def get_current_time():
    return datetime.now(TZ)

def create_price_chart():
    """Створює графік цін на основі глобальної історії за сьогодні"""
    try:
        if len(global_price_history) < 2:
            return None
            
        today_str = get_current_time().strftime('%Y-%m-%d')
        
        # Фільтруємо точки лише за сьогодні
        times = [t.strftime('%H:%M:%S') for t, p in global_price_history if t.strftime('%Y-%m-%d') == today_str]
        prices = [p for t, p in global_price_history if t.strftime('%Y-%m-%d') == today_str]
        
        if len(prices) < 2:
            return None

        fig, ax = plt.subplots(figsize=(10, 5), facecolor='#2b2d31')
        ax.set_facecolor('#1e1f22')
        
        ax.plot(times, prices, color='#5865f2', linewidth=2.5, marker='o', markersize=4)
        ax.fill_between(range(len(prices)), prices, min(prices) - 10, alpha=0.2, color='#5865f2')
        
        ax.set_xlabel('Час', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Ціна (USD)', color='#b5bac1', fontsize=10)
        ax.set_title('📈 Історія цін Ефіра (сьогодні)', color='#ffffff', fontsize=12, fontweight='bold')
        
        ax.grid(True, alpha=0.2, color='#4f545c')
        ax.tick_params(colors='#b5bac1')
        
        ax.xaxis.set_major_locator(plt.MaxNLocator(8))
        plt.xticks(rotation=30, ha='right')
        plt.tight_layout()
        
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='#2b2d31')
        buffer.seek(0)
        plt.close()
        return buffer
    except Exception as e:
        logger.error(f"Помилка при створенні графіка: {e}")
        return None

async def global_price_monitor(application: Application):
    """Глобальний моніторинг ціни кожні 5 секунд"""
    global current_eth_price, global_price_history
    
    while True:
        try:
            price = await get_eth_price()
            now = get_current_time()
            
            if price:
                current_eth_price = price
                global_price_history.append((now, price))
                
                # Очищення старого кешу (залишаємо лише за останні 24 години, щоб не переповнювати пам'ять)
                global_price_history = [item for item in global_price_history if (now - item[0]).total_seconds() < 86400]
                
                # Перевірка лімітів користувачів
                for user_id in list(active_users):
                    user_data = application.user_data.get(user_id)
                    if not user_data:
                        continue
                        
                    lower = user_data.get('lower_price')
                    upper = user_data.get('upper_price')
                    
                    # Перевірка нижньої межі
                    if lower and price <= lower and not user_data.get('notified_lower', False):
                        await application.bot.send_message(
                            user_id,
                            f"🔴 <b>СИГНАЛ! Ціна впала!</b>\n\nЦільова: {lower}\nПоточна: <b>${price:,.2f}</b>",
                            parse_mode="HTML"
                        )
                        user_data['notified_lower'] = True
                    elif lower and price > lower * 1.01:
                        user_data['notified_lower'] = False  # Скидання флагу сповіщення
                        
                    # Перевірка верхньої межі
                    if upper and price >= upper and not user_data.get('notified_upper', False):
                        await application.bot.send_message(
                            user_id,
                            f"🟢 <b>СИГНАЛ! Ціна зросла!</b>\n\nЦільова: {upper}\nПоточна: <b>${price:,.2f}</b>",
                            parse_mode="HTML"
                        )
                        user_data['notified_upper'] = True
                    elif upper and price < upper * 0.99:
                        user_data['notified_upper'] = False  # Скидання флагу сповіщення

        except Exception as e:
            logger.error(f"Помилка в циклі моніторингу: {e}")
            
        await asyncio.sleep(5)

def get_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити цілі", callback_data="set_target")],
        [InlineKeyboardButton("📈 Графік цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Скинути цілі", callback_data="stop")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт бота або повернення до головного меню"""
    user_id = update.effective_user.id
    active_users.add(user_id)
    
    # Спробуємо видалити текстову команду користувача /start, щоб очистити чат
    if update.message:
        try:
            await update.message.delete()
        except:
            pass
        await update.message.reply_text(
            "🚀 <b>ETH Price Monitor</b>\n\nОпитування курсу відбувається кожні 5 секунд.",
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

    if query.data == "show_price":
        # Замість запиту до API беремо миттєве значення з нашого 5-секундного потоку
        if current_eth_price:
            msg = f"💰 <b>Поточний курс ETH</b>\n\n<code>${current_eth_price:,.2f}</code>\n\n⏰ Оновлено: {get_current_time().strftime('%H:%M:%S')}"
        else:
            msg = "⏳ Зачекайте, завантажуються перші дані..."
            
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif query.data == "show_chart":
        chart_buffer = create_price_chart()
        if not chart_buffer:
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
            await query.edit_message_text("📈 <b>Недостатньо даних для графіка</b>\n\nЗачекайте кілька хвилин, поки назбирається історія (мін. 2 точки).", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            # Графік надсилається новим повідомленням (фото), а старе меню видаляємо
            try:
                await query.message.delete()
            except:
                pass
            
            keyboard = [[InlineKeyboardButton("🏠 Меню", callback_data="back_from_chart")]]
            await query.message.reply_photo(
                photo=chart_buffer,
                caption=f"📈 Графік за сьогодні. Точок в базі: {len(global_price_history)}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif query.data == "show_targets":
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        if not lower and not upper:
            msg = "❌ Сповіщення не налаштовані."
        else:
            msg = f"📍 <b>Ваші встановлені межі:</b>\n\n🔴 Мін: " + (f"${lower:,.2f}" if lower else "немає") + f"\n🟢 Макс: " + (f"${upper:,.2f}" if upper else "немає")
            
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif query.data == "stop":
        context.user_data['lower_price'] = None
        context.user_data['upper_price'] = None
        context.user_data['notified_lower'] = False
        context.user_data['notified_upper'] = False
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text("⏹️ Усі цілі видалено. Моніторинг меж вимкнено.", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "back":
        await query.edit_message_text("🚀 <b>ETH Price Monitor</b>\n\nОпитування курсу відбувається кожні 5 секунд.", reply_markup=get_menu_keyboard(), parse_mode="HTML")

    elif query.data == "back_from_chart":
        # Якщо ми повертаємось від графіка (який є Photo), видаляємо фото та створюємо чисте меню текстовим повідомленням
        try:
            await query.message.delete()
        except:
            pass
        await query.message.reply_text("🚀 <b>ETH Price Monitor</b>", reply_markup=get_menu_keyboard(), parse_mode="HTML")

async def start_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок діалогу встановлення меж ціни"""
    query = update.callback_query
    await query.answer()
    
    # Текст замінює головне меню, уникаючи захаращення чату
    await query.edit_message_text("📍 <b>Встановлення цілей</b>\n\nВведіть <b>мінімальну ціну</b> (або 0, якщо не потрібна):", parse_mode="HTML")
    return SET_LOWER

async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.replace(',', '.'))
        context.user_data['lower_price'] = val if val > 0 else None
        context.user_data['notified_lower'] = False
        
        try:
            await update.message.delete()  # Видаляємо цифру користувача
        except:
            pass
            
        # Замість відправки нового повідомлення, ми надсилаємо нове, але згодом його також перекриємо
        context.user_data['last_conv_msg'] = await update.message.reply_text(
            f"✅ Нижня межа: " + (f"${val:,.2f}" if val > 0 else "Вимкнено") + f"\n\nТепер введіть <b>максимальну ціну</b> (або 0):",
            parse_mode="HTML"
        )
        return SET_UPPER
    except ValueError:
        await update.message.reply_text("❌ Будь ласка, введіть коректне число! Спробуйте ще раз:")
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
            
        # Видаляємо проміжне повідомлення з кроку 1, якщо воно збереглось
        if 'last_conv_msg' in context.user_data:
            try:
                await context.user_data['last_conv_msg'].delete()
            except:
                pass
        
        lower = context.user_data.get('lower_price')
        await update.message.reply_text(
            f"🎯 <b>Цілі успішно оновлені!</b>\n\n"
            f"🔴 Мін: " + (f"${lower:,.2f}" if lower else "немає") + f"\n"
            f"🟢 Макс: " + (f"${val:,.2f}" if val > 0 else "немає") + f"\n\n"
            f"Бот автоматично надішле повідомлення у разі пробиття.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Головне меню", callback_data="back_from_chart")]]),
            parse_mode="HTML"
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Будь ласка, введіть коректне число. Спробуйте ще раз:")
        return SET_UPPER

async def handle_unknown_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видаляє випадковий текст від користувача поза меню та викликає свіже меню"""
    try:
        await update.message.delete()
    except:
        pass
    await start(update, context)

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Окремий хендлер розмови для встановлення меж цен
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_conversation, pattern="set_target")],
        states={
            SET_LOWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lower_price)],
            SET_UPPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_upper_price)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_messages))
    
    # Ініціалізація фонового моніторингу через post_init
    async def startup(app):
        asyncio.create_task(global_price_monitor(app))
        
    application.post_init = startup
    
    logger.info("Бот успішно запущений. Очікування оновлень...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
