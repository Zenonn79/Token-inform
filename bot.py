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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Вітаю в ETH Price Monitor!\n\n"
        "Я буду слідкувати за курсом Ефіра і повідомлю тебе, коли він досягне твоїх цільових цін.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_price":
        await show_current_price(query, context)
    elif query.data == "set_target":
        await query.edit_message_text("📍 Встановити цільові ціни:\n\nВведи мінімальну ціну (нижня границя):")
        return SET_LOWER
    elif query.data == "show_targets":
        await show_targets(query, context)
    elif query.data == "stop_monitoring":
        context.user_data['monitoring'] = False
        await query.edit_message_text("⏹️ Моніторинг зупинено.")
        await send_main_menu(query, context)
    elif query.data == "back_menu":
        await send_main_menu(query, context)

async def show_current_price(query, context):
    """Показує поточний курс ETH"""
    price = await get_eth_price()
    
    if price:
        message = f"💰 Поточний курс Ефіра:\n\n${price:,.2f}\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Перевіряємо, чи перевищений нижній або верхній показник
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        if lower and price <= lower:
            message += f"\n\n🔴 СИГНАЛ: Ціна досягла нижнього показника ${lower}!"
        if upper and price >= upper:
            message += f"\n\n🟢 СИГНАЛ: Ціна досягла верхнього показника ${upper}!"
    else:
        message = "❌ Не вдалося отримати курс. Спробуй пізніше."
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

async def show_targets(query, context):
    """Показує встановлені цільові ціни"""
    lower = context.user_data.get('lower_price')
    upper = context.user_data.get('upper_price')
    
    if not lower and not upper:
        message = "📋 Ти ще не встановив цільові ціни.\n\nНатисни 'Встановити ціль', щоб додати їх."
    else:
        message = "📋 Твої цільові ціни:\n\n"
        if lower:
            message += f"📍 Нижня границя: ${lower:,.2f}\n"
        if upper:
            message += f"📍 Верхня границя: ${upper:,.2f}\n"
        message += "\n💡 Бот подасть сигнал, коли ціна досягне однієї з цих позначок."
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

async def send_main_menu(query, context):
    """Відправляє головне меню"""
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🏠 Головне меню:",
        reply_markup=reply_markup
    )

async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення мінімальної ціни"""
    try:
        lower_price = float(update.message.text)
        context.user_data['lower_price'] = lower_price
        
        await update.message.reply_text(
            f"✅ Нижня границя встановлена: ${lower_price:,.2f}\n\n"
            "Тепер введи верхню ціну (верхня границя):"
        )
        return SET_UPPER
    except ValueError:
        await update.message.reply_text("❌ Будь ласка, введи коректне число (наприклад: 2500 або 2500.50)")
        return SET_LOWER

async def handle_upper_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення максимальної ціни"""
    try:
        upper_price = float(update.message.text)
        context.user_data['upper_price'] = upper_price
        context.user_data['monitoring'] = True
        
        lower = context.user_data.get('lower_price')
        
        message = (
            f"✅ Цільові ціни встановлені:\n\n"
            f"📍 Нижня границя: ${lower:,.2f}\n"
            f"📍 Верхня границя: ${upper_price:,.2f}\n\n"
            f"🔔 Я буду сигналізувати, коли ціна Ефіра досягне однієї з цих позначок!"
        )
        
        # Запускаємо фоновий моніторинг
        context.application.create_task(
            monitor_price(update.effective_user.id, context)
        )
        
        keyboard = [
            [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
            [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
            [InlineKeyboardButton("📍 Змінити цілі", callback_data="set_target")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Будь ласка, введи коректне число (наприклад: 3000 або 3000.50)")
        return SET_UPPER

async def monitor_price(user_id, context: ContextTypes.DEFAULT_TYPE):
    """Фоновий моніторинг ціни з періодичною перевіркою"""
    check_interval = 300  # Перевіряємо кожні 5 хвилин
    last_notified_lower = False
    last_notified_upper = False
    
    while context.user_data.get('monitoring', True):
        try:
            price = await get_eth_price()
            lower = context.user_data.get('lower_price')
            upper = context.user_data.get('upper_price')
            
            if price:
                # Перевіряємо нижню границю
                if lower and price <= lower and not last_notified_lower:
                    await context.bot.send_message(
                        user_id,
                        f"🔴 СИГНАЛ! 🔴\n\n"
                        f"Курс Ефіра досягнув нижнього показника!\n\n"
                        f"💰 Поточна ціна: ${price:,.2f}\n"
                        f"📍 Твоя ціль: ${lower:,.2f}\n\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    last_notified_lower = True
                elif price > lower * 1.05:  # Якщо ціна повернулась вище, готуємо до нового сигналу
                    last_notified_lower = False
                
                # Перевіряємо верхню границю
                if upper and price >= upper and not last_notified_upper:
                    await context.bot.send_message(
                        user_id,
                        f"🟢 СИГНАЛ! 🟢\n\n"
                        f"Курс Ефіра досягнув верхнього показника!\n\n"
                        f"💰 Поточна ціна: ${price:,.2f}\n"
                        f"📍 Твоя ціль: ${upper:,.2f}\n\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    last_notified_upper = True
                elif price < upper * 0.95:  # Якщо ціна повернулась нижче, готуємо до нового сигналу
                    last_notified_upper = False
            
            # Чекаємо перед наступною перевіркою
            await asyncio.sleep(check_interval)
        except Exception as e:
            logger.error(f"Помилка в моніторингу: {e}")
            await asyncio.sleep(check_interval)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасовує операцію"""
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END

def main():
    """Запускає бот"""
    # Створюємо Application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # ConversationHandler для встановлення цін
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
    
    # Запускаємо бот
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
