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
    """Глобальний моніторинг ціни кожні 10 секунд (з автооновленням активних екранів)"""
    global current_eth_price, global_price_history
    
    while True:
        try:
            price = await get_eth_price()
            now = get_current_time()
            
            if price:
                current_eth_price = price
                global_price_history.append((now, price))
                
                # Очищення старого кешу (залишаємо лише за останні 24 години)
                global_price_history = [item for item in global_price_history if (now - item[0]).total_seconds() < 86400]
                
                # Обробка користувачів (ліміти + «живе» оновлення екрана)
                for user_id in list(active_users):
                    user_data = application.user_data.get(user_id)
                    if not user_data:
                        continue
                    
                    # --- ЧАСТИНА 1: Живе оновлення екрану поточного курсу ---
                    active_price_msg_id = user_data.get('active_price_msg_id')
                    if active_price_msg_id:
                        try:
                            msg_text = f"💰 <b>Поточний курс ETH</b>\n\n<code>${price:,.2f}</code>\n\n⏰ Оновлено: {now.strftime('%H:%M:%S')}"
                            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
                            
                            await application.bot.edit_message_text(
                                chat_id=user_id,
                                message_id=active_price_msg_id,
                                text=msg_text,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode="HTML"
                            )
                        except Exception as edit_err:
                            # Якщо повідомлення вже видалене або текст абсолютно ідентичний — ігноруємо помилку
                            pass

                    # --- ЧАСТИНА 2: Перевірка лімітів користувачів ---
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
                        user_data['notified_lower'] = False
                        
                    # Перевірка верхньої межі
                    if upper and price >= upper and not user_data.get('notified_upper', False):
                        await application.bot.send_message(
                            user_id,
                            f"🟢 <b>СИГНАЛ! Ціна зросла!</b>\n\nЦільова: {upper}\nПоточна: <b>${price:,.2f}</b>",
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
        [InlineKeyboardButton("🛑 Скинути цілі", callback_data="stop")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт бота або повернення до головного меню"""
    user_id = update.effective_user.id
    active_users.add(user_id)
    context.user_data['active_price_msg_id'] = None  # Скидаємо трекер екрана
    
    if update.message:
        try:
            await update.message.delete()
        except:
            pass
        await update.message.reply_text(
            "🚀 <b>ETH Price Monitor</b>\n\nОпитування курсу відбувається кожні 10 секунд.",
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
        # Запам'ятовуємо ID цього повідомлення для монітора, щоб він міг його оновлювати
        context.user_data['active_price_msg_id'] = query.message.message_id

        display_price = current_eth_price
        display_time = get_current_time()

        if not display_price and global_price_history:
            display_time, display_price = global_price_history[-1]

        if display_price:
            msg = f"💰 <b>Поточний курс ETH</b>\n\n<code>${display_price:,.2f}</code>\n\n⏰ Оновлено: {display_time.strftime('%H:%M:%S')}"
        else:
            msg = "⏳ Зачекайте, завантажуються перші дані..."
            
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif query.data == "show_chart":
        context.user_data['active_price_msg_id'] = None  # Вийшли з екрана курсу
        chart_buffer = create_price_chart()
        if not chart_buffer:
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
            await query.edit_message_text("📈 <b>Недостатньо даних для графіка</b>\n\nЗачекайте кілька хвилин, поки назбирається історія (мін. 2 точки).", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
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
        context.user_data['active_price_msg_id'] = None  # Вийшли з екрана курсу
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        if not lower and not upper:
            msg = "❌ Сповіщення не налаштовані."
        else:
            msg = f"📍 <b>Ваші встановлені межі:</b>\n\n🔴 Мін: " + (f"${lower:,.2f}" if lower else "немає") + f"\n🟢 Макс: " + (f"${upper:,.2f}" if upper else "немає")
            
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif query.data == "stop":
        context.user_data['active_price_msg_id'] = None
        context.user_data['lower_price'] = None
        context.user_data['upper_price'] = None
        context.user_data['notified_lower'] = False
        context.user_data['notified_upper'] = False
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await query.edit_message_text("⏹️ Усі цілі видалено. Моніторинг меж вимкнено.", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "back":
        context.user_data['active_price_msg_id'] = None  # Скидаємо трекер, бо повернулися в корінь
        await query.edit_message_text("🚀 <b>ETH Price Monitor</b>\n\nОпитування курсу відбувається кожні 10 секунд.", reply_markup=get_menu_keyboard(), parse_mode="HTML")

    elif query.data == "back_from_chart":
        context.user_data['active_price_msg_id'] = None
        try:
            await query.message.delete()
        except:
            pass
        await query.message.reply_text("🚀 <b>ETH Price Monitor</b>", reply_markup=get_menu_keyboard(), parse_mode="HTML")

async def start_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок діалогу встановлення меж ціни"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['active_price_msg_id'] = None  # Вийшли з екрана курсу
    context.user_data['conv_menu_msg_id'] = query.message.message_id
    
    await query.edit_message_text("📍 <b>Встановлення цілей</b>\n\nВведіть <b>мінімальну ціну</b> (або 0, якщо не потрібна):", parse_mode="HTML")
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
        
        lower_status = f"${val:,.2f}" if val > 0 else "Вимкнено"
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
            f"🔴 Мін: " + (f"${lower:,.2f}" if lower else "немає") + f"\n"
            f"🟢 Макс: " + (f"${val:,.2f}" if val > 0 else "немає") + f"\n\n"
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
        fallbacks=[CommandHandler("start", start)],
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
