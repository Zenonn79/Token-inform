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
    """Команда /start - видаляє старі повідомлення і показує меню"""
    # Видаляємо старе повідомлення якщо воно було
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id - 1
        )
    except:
        pass
    
    keyboard = [
        [InlineKeyboardButton("📊 Показати курс", callback_data="show_price")],
        [InlineKeyboardButton("📍 Встановити ціль", callback_data="set_target")],
        [InlineKeyboardButton("⚙️ Мої цілі", callback_data="show_targets")],
        [InlineKeyboardButton("🛑 Зупинити моніторинг", callback_data="stop_monitoring")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 <b>Вітаю в ETH Price Monitor!</b>\n\n"
        "Я буду слідкувати за курсом Ефіра і повідомлю тебе, коли він досягне твоїх цільових цін.",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def auto_update_price(query, context: ContextTypes.DEFAULT_TYPE):
    """Автоматично оновлює ціну кожні 5 секунд"""
    try:
        message_id = query.message.message_id
        chat_id = query.message.chat_id
        
        while context.user_data.get('show_price_active', False):
            await asyncio.sleep(5)
            
            # Перевіряємо, чи ще користувач у екрані показу ціни
            if not context.user_data.get('show_price_active'):
                break
            
            try:
                price = await get_eth_price()
                
                if price:
                    lower = context.user_data.get('lower_price')
                    upper = context.user_data.get('upper_price')
                    
                    # Основне повідомлення
                    message = f"<b>💰 Поточний курс Ефіра</b>\n\n"
                    message += f"<code>${price:,.2f}</code>\n\n"
                    message += f"<i>⏰ {datetime.now().strftime('%H:%M:%S')}</i>\n"
                    message += "🔄 <i>(оновлюється кожні 5 сек)</i>"
                    
                    # Додаємо сигнали
                    if lower and price <= lower:
                        message += f"\n\n🔴 <b>СИГНАЛ!</b>\n"
                        message += f"Ціна досягла нижнього показника ${lower}"
                    if upper and price >= upper:
                        message += f"\n\n🟢 <b>СИГНАЛ!</b>\n"
                        message += f"Ціна досягла верхнього показника ${upper}"
                else:
                    message = "❌ Не вдалося отримати курс. Спробуй пізніше."
                
                keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Редагуємо повідомлення
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Помилка при оновленні ціни: {e}")
                break
    except Exception as e:
        logger.error(f"Помилка в auto_update_price: {e}")

async def show_current_price(query, context):
    """Показує поточний курс ETH з автооновленням"""
    # Зупиняємо попереднє оновлення, якщо воно було
    context.user_data['show_price_active'] = True
    
    price = await get_eth_price()
    
    if price:
        lower = context.user_data.get('lower_price')
        upper = context.user_data.get('upper_price')
        
        # Основне повідомлення
        message = f"<b>💰 Поточний курс Ефіра</b>\n\n"
        message += f"<code>${price:,.2f}</code>\n\n"
        message += f"<i>⏰ {datetime.now().strftime('%H:%M:%S')}</i>\n"
        message += "🔄 <i>(оновлюється кожні 5 сек)</i>"
        
        # Додаємо сигнали
        if lower and price <= lower:
            message += f"\n\n🔴 <b>СИГНАЛ!</b>\n"
            message += f"Ціна досягла нижнього показника ${lower}"
        if upper and price >= upper:
            message += f"\n\n🟢 <b>СИГНАЛ!</b>\n"
            message += f"Ціна досягла верхнього показника ${upper}"
    else:
        message = "❌ Не вдалося отримати курс. Спробуй пізніше."
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Помилка при оновленні повідомлення: {e}")
    
    # Запускаємо автооновлення кожні 5 секунд
    context.application.create_task(
        auto_update_price(query, context)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "show_price":
        await show_current_price(query, context)
    elif query.data == "set_target":
        # Зупиняємо оновлення ціни
        context.user_data['show_price_active'] = False
        
        await query.edit_message_text(
            "📍 <b>Встановити цільові ціни</b>\n\n"
            "Введи <b>мінімальну ціну</b> (нижня границя):",
            parse_mode="HTML"
        )
        return SET_LOWER
    elif query.data == "show_targets":
        # Зупиняємо оновлення ціни
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
        # Зупиняємо оновлення ціни
        context.user_data['show_price_active'] = False
        
        await send_main_menu(query, context)

async def show_targets(query, context):
    """Показує встановлені цільові ціни"""
    lower = context.user_data.get('lower_price')
    upper = context.user_data.get('upper_price')
    
    if not lower and not upper:
        message = "📋 <b>Твої цільові ціни</b>\n\n"
        message += "Ти ще не встановив цільові ціни.\n\n"
        message += "Натисни <b>'📍 Встановити ціль'</b>, щоб додати їх."
    else:
        message = "📋 <b>Твої цільові ціни</b>\n\n"
        if lower:
            message += f"📍 <b>Нижня границя:</b> <code>${lower:,.2f}</code>\n"
        if upper:
            message += f"📍 <b>Верхня границя:</b> <code>${upper:,.2f}</code>\n"
        message += f"\n💡 Бот подасть сигнал, коли ціна досягне однієї з цих позначок."
    
    keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")

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
        "🏠 <b>Головне меню</b>",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def handle_lower_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє введення мінімальної ціни"""
    try:
        lower_price = float(update.message.text)
        context.user_data['lower_price'] = lower_price
        
        await update.message.reply_text(
            f"✅ <b>Нижня границя встановлена:</b> <code>${lower_price:,.2f}</code>\n\n"
            "Тепер введи верхню ціну <b>(верхня границя):</b>",
            parse_mode="HTML"
        )
        return SET_UPPER
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Помилка!</b>\n\n"
            "Будь ласка, введи коректне число\n"
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
        
        message = (
            f"✅ <b>Цільові ціни встановлені!</b>\n\n"
            f"📍 <b>Нижня границя:</b> <code>${lower:,.2f}</code>\n"
            f"📍 <b>Верхня границя:</b> <code>${upper_price:,.2f}</code>\n\n"
            f"🔔 <b>Моніторинг активний!</b>\n"
            f"Я буду сигналізувати, коли ціна Ефіра досягне однієї з цих позначок."
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
        
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Помилка!</b>\n\n"
            "Будь ласка, введи коректне число\n"
            "<i>Приклад: 3000 або 3000.50</i>",
            parse_mode="HTML"
        )
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
                        f"🔴 <b>СИГНАЛ!</b> 🔴\n\n"
                        f"<b>Курс Ефіра досягнув нижнього показника!</b>\n\n"
                        f"💰 <b>Поточна ціна:</b> <code>${price:,.2f}</code>\n"
                        f"📍 <b>Твоя ціль:</b> <code>${lower:,.2f}</code>\n\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="HTML"
                    )
                    last_notified_lower = True
                elif price > lower * 1.05:  # Якщо ціна повернулась вище, готуємо до нового сигналу
                    last_notified_lower = False
                
                # Перевіряємо верхню границю
                if upper and price >= upper and not last_notified_upper:
                    await context.bot.send_message(
                        user_id,
                        f"🟢 <b>СИГНАЛ!</b> 🟢\n\n"
                        f"<b>Курс Ефіра досягнув верхнього показника!</b>\n\n"
                        f"💰 <b>Поточна ціна:</b> <code>${price:,.2f}</code>\n"
                        f"📍 <b>Твоя ціль:</b> <code>${upper:,.2f}</code>\n\n"
                        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="HTML"
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
