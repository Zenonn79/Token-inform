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

last_notified_lower = {}
last_notified_upper = {}
global_price_history = {}

async def get_eth_price():
    """Отримує ціну ETH"""
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

def get_current_time():
    """Отримує поточний час"""
    return datetime.now(TZ)

def add_price_to_history(context, price):
    """Додає ціну до історії"""
    today = get_current_time().strftime('%Y-%m-%d')
    time_str = get_current_time().strftime('%H:%M')
    
    if 'price_history' not in context.user_data:
        context.user_data['price_history'] = {}
    
    if today not in context.user_data['price_history']:
        context.user_data['price_history'][today] = []
    
    context.user_data['price_history'][today].append((time_str, price))

def get_today_prices(context):
    """Отримує ціни за сьогодні"""
    if 'price_history' not in context.user_data:
        return []
    
    today = get_current_time().strftime('%Y-%m-%d')
    return context.user_data['price_history'].get(today, [])

def create_price_chart(prices_data):
    """Створює графік цін"""
    try:
        if not prices_data:
            return None
        
        times = [item[0] for item in prices_data]
        prices = [item[1] for item in prices_data]
        
        fig, ax = plt.subplots(figsize=(10, 5), facecolor='#2b2d31')
        ax.set_facecolor('#1e1f22')
        
        ax.plot(times, prices, color='#5865f2', linewidth=2.5, marker='o', markersize=6)
        ax.fill_between(range(len(prices)), prices, alpha=0.2, color='#5865f2')
        
        min_price = min(prices)
        max_price = max(prices)
        y_min = max(0, min_price - 100)
        y_max = max_price + 100
        ax.set_ylim(y_min, y_max)
        
        ax.set_xlabel('Час', color='#b5bac1', fontsize=10)
        ax.set_ylabel('Ціна (USD)', color='#b5bac1', fontsize=10)
        ax.set_title('📈 Історія цін Ефіра (сьогодні)', color='#ffffff', fontsize=12, fontweight='bold')
        
        ax.grid(True, alpha=0.2, color='#4f545c')
        ax.tick_params(colors='#b5bac1')
        
        ax.xaxis.set_major_locator(plt.MaxNLocator(8))
        plt.xticks(rotation=45, ha='right')
        
        plt.tight_layout()
        
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight', facecolor='#2b2d31')
        buffer.seek(0)
        plt.close()
        
        return buffer
    except Exception as e:
        logger.error(f"Помилка при створенні графіка: {e}")
        return None

async def global_price_monitor(application):
    """Глобальний моніторинг кожні 60 сек"""
    while True:
        try:
            await asyncio.sleep(60)
            
            price = await get_eth_price()
            
            if price:
                logger.info(f"ETH: ${price:,.2f}")
                
                # Проходимо по всім активним користувачам
                for user_id in list(last_notified_lower.keys()) + list(last_notified_upper.keys()):
                    try:
                        user_context = application.user_data.get(user_id)
                        if not user_context:
                            continue
                        
                        # ЗАПИСУЄМО ЦІНУ
                        add_price_to_history(user_context, price)
                        
                        lower = user_context.get('lower_price')
                        upper = user_context.get('upper_price')
                        
                        if not lower and not upper:
                            continue
                        
                        # СИГНАЛ НА НИЖНЮ ГРАНИЦЮ
                        if lower and price <= lower and not last_notified_lower.get(user_id, False):
                            await application.bot.send_message(
                                user_id,
                                f"🔴 <b>СИГНАЛ!</b>\n\n"
                                f"Ціна досягла {lower}!\n"
                                f"Поточна: ${price:,.2f}\n"
                                f"⏰ {get_current_time().strftime('%H:%M:%S')}",
                                parse_mode="HTML"
                            )
                            last_notified_lower[user_id] = True
                        elif price > lower * 1.05:
                            last_notified_lower[user_id] = False
                        
                        # СИГНАЛ НА ВЕРХНЮ ГРАНИЦЮ
                        if upper and price >= upper and not last_notified_upper.get(user_id, False):
                            await application.bot.send_message(
                                user_id,
                                f"🟢 <b>СИГНАЛ!</b>\n\n"
                                f"Ціна досягла {upper}!\n"
                                f"Поточна: ${price:,.2f}\n"
                                f"⏰ {get_current_time().strftime('%H:%M:%S')}",
                                parse_mode="HTML"
                            )
                            last_notified_upper[user_id] = True
                        elif price < upper * 0.95:
                            last_notified_upper[user_id] = False
                    
                    except Exception as e:
                        logger.error(f"Помилка користувача {user_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Помилка в моніторингу: {e}")

async def show_menu(message):
    """Відправляє меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("📈 Графік цін", callback_data="show_chart")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити", callback_data="stop")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        "🚀 <b>ETH Price Monitor</b>\n\n"
        "Слідкування за курсом Ефіра",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт"""
    try:
        await update.message.delete()
    except:
        pass
    
    await show_menu(update.message)

async def show_current_price(query, context):
    """Показує поточний курс"""
    if 'price_history' not in context.user_data:
        context.user_data['price_history'] = {}
    
    context.user_data['show_price_active'] = True
    price = await get_eth_price()
    
    if price:
        add_price_to_history(context, price)
        message = f"💰 <b>Поточний курс</b>\n\n<code>${price:,.2f}</code>\n\n⏰ {get_current_time().strftime('%H:%M:%S')}"
    else:
        message = "❌ Помилка при отриманні ціни"
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
    except:
        pass

async def show_chart(query, context):
    """Показує графік"""
    if 'price_history' not in context.user_data:
        context.user_data['price_history'] = {}
    
    context.user_data['show_price_active'] = False
    prices_data = get_today_prices(context)
    
    # Видаляємо старе меню
    try:
        await query.message.delete()
    except:
        pass
    
    if len(prices_data) < 2:
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            "📈 <b>Недостатньо даних</b>\n\n"
            "Потрібно мінімум 2 точки. Дивись курс кілька разів.",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        chart_buffer = create_price_chart(prices_data)
        
        if chart_buffer:
            message_text = (
                f"📈 <b>Графік (сьогодні)</b>\n\n"
                f"Дата: {get_current_time().strftime('%Y-%m-%d')}\n"
                f"Точок: {len(prices_data)}\n"
                f"Мін: ${min(p[1] for p in prices_data):,.2f}\n"
                f"Макс: ${max(p[1] for p in prices_data):,.2f}\n"
                f"Остання: ${prices_data[-1][1]:,.2f}"
            )
            
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            chart_buffer.seek(0)
            await query.message.reply_photo(
                photo=chart_buffer,
                caption=message_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_price":
        await show_current_price(query, context)
    elif query.data == "show_chart":
        await show_chart(query, context)
    elif query.data == "set_target":
        # Видаляємо меню
        try:
            await query.message.delete()
        except:
            pass
        
        await query.message.reply_text(
            "📍 <b>Встановити ціль</b>\n\n"
            "Введи <b>мінімальну ціну</b>:",
            parse_mode="HTML"
        )
        return SET_LOWER
    elif query.data == "show_targets":
        context.user_data['show_price_active'] = False
        
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        if not lower and not upper:
            msg = "❌ Цілі не встановлені"
        else:
            msg = f"📍 <b>Мої цілі</b>\n\n"
            if lower:
                msg += f"🔴 Мін: ${lower:,.2f}\n"
            if upper:
                msg += f"🟢 Макс: ${upper:,.2f}"
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="HTML")
        except:
            pass
    elif query.data == "stop":
        user_id = update.effective_user.id
        if user_id in last_notified_lower:
            del last_notified_lower[user_id]
        if user_id in last_notified_upper:
            del last_notified_upper[user_id]
        
        # Видаляємо меню
        try:
            await query.message.delete()
        except:
            pass
        
        await query.message.reply_text("⏹️ Моніторинг зупинено")
        
        # Показуємо нове меню
        await show_menu(query.message)
    elif query.data == "back":
        context.user_data['show_price_active'] = False
        
        # Видаляємо старе повідомлення
        try:
            await query.message.delete()
        except:
            pass
        
        # Показуємо нове меню
        await show_menu(query.message)

async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка нижної ціни"""
    try:
        lower_price = float(update.message.text)
        context.user_data['lower_price'] = lower_price
        
        # Видаляємо повідомлення користувача
        try:
            await update.message.delete()
        except:
            pass
        
        await update.message.reply_text(
            f"✅ Мін встановлена: ${lower_price:,.2f}\n\n"
            "Введи <b>максимальну ціну</b>:",
            parse_mode="HTML"
        )
        return SET_UPPER
    except ValueError:
        await update.message.reply_text("❌ Введи число! Приклад: 2500")
        return SET_LOWER

async def handle_upper_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка верхної ціни"""
    try:
        upper_price = float(update.message.text)
        context.user_data['upper_price'] = upper_price
        
        lower = context.user_data.get('lower_price')
        
        # Видаляємо повідомлення користувача
        try:
            await update.message.delete()
        except:
            pass
        
        user_id = update.effective_user.id
        last_notified_lower[user_id] = False
        last_notified_upper[user_id] = False
        
        await update.message.reply_text(
            f"✅ <b>Цілі встановлені!</b>\n\n"
            f"🔴 Мін: ${lower:,.2f}\n"
            f"🟢 Макс: ${upper_price:,.2f}\n\n"
            f"🔔 Моніторинг активний!"
        )
        
        keyboard = [[InlineKeyboardButton("🏠 Меню", callback_data="back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("Повертайся в меню", reply_markup=reply_markup)
        
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введи число! Приклад: 3000")
        return SET_UPPER

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматичний старт"""
    await start(update, context)

def main():
    """Запуск"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="set_target")],
        states={
            SET_LOWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lower_price)],
            SET_UPPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_upper_price)],
        },
        fallbacks=[],
    )
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    async def startup(application):
        asyncio.create_task(global_price_monitor(application))
    
    application.post_init = startup
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
