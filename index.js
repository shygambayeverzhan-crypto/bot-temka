require('dotenv').config();
const { Telegraf, Markup } = require('telegraf');

const bot = new Telegraf(process.env.BOT_TOKEN || 'YOUR_BOT_TOKEN');

const activeSessions = new Map();
const pendingOrders = new Map();

bot.start((ctx) => {
    const username = ctx.from.username || ctx.from.first_name;
    ctx.reply(
        `👋 Привет, ${username}!\n✅ Вы авторизованы.\n\nВыберите действие:`,
        Markup.keyboard([
            ['💼 Мой баланс / Сессия', '📥 Внести депозит ($500)'],
            ['💳 Создать тестовый платеж', '🛑 Остановить трафик']
        ]).resize()
    );
});

bot.hears('📥 Внести депозит ($500)', (ctx) => {
    const userId = ctx.from.id;
    const initialDeposit = 500; 
    const cutPercent = 4;

    const traderCut = (initialDeposit * cutPercent) / 100;
    const workingBalance = initialDeposit - traderCut;

    activeSessions.set(userId, {
        initialDeposit,
        traderCut,
        workingBalance,
        currentBalance: workingBalance,
        isActive: true
    });

    ctx.reply(
        `🟢 Депозит активирован!\n\n` +
        `💵 Начальная сумма: $${initialDeposit}\n` +
        `👤 Доля трейдера (4%): -$${traderCut}\n` +
        `🚀 Рабочий баланс: $${workingBalance}`
    );
});

bot.hears('💳 Создать тестовый платеж', (ctx) => {
    const userId = ctx.from.id;
    const orderId = '18c938b5-9921-414a-b51a-95d5acce7f2d';
    const amount = 5500;

    pendingOrders.set(orderId, { userId, amount, status: 'pending' });

    ctx.reply(
        `💳 Заказ (пополнение баланса):\n` +
        `ID: \`${orderId}\`\n\n` +
        `Сумма: **${amount}.00 KZT**\n` +
        `Кошелёк: \`4400430250684138\`\n\n` +
        `⏳ Реквизиты актуальны 10 минут!\n` +
        `👉 Отправьте в чат скриншот (чек) оплаты.`,
        {
            parse_mode: 'Markdown',
            ...Markup.inlineKeyboard([
                [Markup.button.callback('✅ Подтвердить', `confirm_${orderId}`), Markup.button.callback('❌ Отменен', `cancel_${orderId}`)]
            ])
        }
    );
});

bot.on('photo', async (ctx) => {
    await ctx.reply(`📄 Чек получен и отправлен на проверку! Ожидайте подтверждения.`);
});

bot.action(/confirm_(.+)/, async (ctx) => {
    await ctx.editMessageText(`✅ Заказ подтвержден, баланс пополнен.`);
    await ctx.answerCbQuery('Успешно!');
});

bot.action(/cancel_(.+)/, async (ctx) => {
    await ctx.editMessageText(`❌ Заказ отменен.`);
    await ctx.answerCbQuery('Отменено.');
});

bot.hears('💼 Мой баланс / Сессия', (ctx) => {
    const userId = ctx.from.id;
    const session = activeSessions.get(userId);
    if (!session || !session.isActive) {
        return ctx.reply('⚠️ У вас нет активной сессии.');
    }
    ctx.reply(`📊 Рабочий остаток: $${session.currentBalance}`);
});

bot.hears('🛑 Остановить трафик', (ctx) => {
    const userId = ctx.from.id;
    const session = activeSessions.get(userId);
    if (session) {
        session.isActive = false;
        ctx.reply('🔴 Трафик остановлен.');
    }
});

bot.launch().then(() => {
    console.log('🤖 Бот с поддержкой чеков запущен!');
});

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
