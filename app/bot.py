from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,ContextTypes
from .config import settings
from .db import db
from .service import automation_enabled,set_automation,transaction_details

def admin(u): return u.effective_user and u.effective_user.id in settings.admin_ids
async def deny(u): await u.effective_message.reply_text('Unauthorized.')
def menu(): return InlineKeyboardMarkup([[InlineKeyboardButton('Senders',callback_data='senders'),InlineKeyboardButton('Recipients',callback_data='recipients')],[InlineKeyboardButton('Profiles',callback_data='profiles'),InlineKeyboardButton('Transactions',callback_data='transactions')],[InlineKeyboardButton('Held',callback_data='held'),InlineKeyboardButton('Reports',callback_data='reports')],[InlineKeyboardButton('Emergency STOP' if automation_enabled() else 'RESUME',callback_data='stop' if automation_enabled() else 'resume')]])
async def start(u,c):
    if not admin(u):return await deny(u)
    await u.message.reply_text('Airtm Payout Manager\n\nTelegram is the admin console. Use /dashboard.',reply_markup=menu())
async def dashboard(u,c):
    if not admin(u):return await deny(u)
    with db() as x:
        total=x.execute("SELECT COUNT(*) n FROM transactions").fetchone()['n']; held=x.execute("SELECT COUNT(*) n FROM transactions WHERE status='HELD'").fetchone()['n']; done=x.execute("SELECT COUNT(*) n FROM transactions WHERE status='COMPLETED'").fetchone()['n']; failed=x.execute("SELECT COUNT(*) n FROM transactions WHERE status='FAILED'").fetchone()['n']; senders=x.execute("SELECT COUNT(*) n FROM senders WHERE active=1").fetchone()['n']; recipients=x.execute("SELECT COUNT(*) n FROM recipients WHERE active=1").fetchone()['n']; incoming=x.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) n FROM transactions").fetchone()['n']
    state='RUNNING' if automation_enabled() else 'STOPPED'
    await u.message.reply_text(f'AIRTM PAYOUT MANAGER\n\nSystem: {state}\nTransactions: {total}\nCompleted: {done}\nHeld: {held}\nFailed: {failed}\nActive senders: {senders}\nActive recipients: {recipients}\nRecorded incoming: ${incoming:.2f}',reply_markup=menu())
async def status(u,c):
    if not admin(u):return await deny(u)
    await u.message.reply_text('System: '+('RUNNING' if automation_enabled() else 'STOPPED')+f'\nDry-run: {settings.dry_run}')
async def stop(u,c):
    if not admin(u):return await deny(u)
    set_automation(False,u.effective_user.id); await u.message.reply_text('EMERGENCY STOP ACTIVE. Incoming records remain enabled; new payouts are blocked.',reply_markup=menu())
async def resume(u,c):
    if not admin(u):return await deny(u)
    set_automation(True,u.effective_user.id); await u.message.reply_text('Automation resumed.',reply_markup=menu())
async def held(u,c):
    if not admin(u):return await deny(u)
    with db() as x: rows=x.execute("SELECT id,external_id,sender_email,amount,status FROM transactions WHERE status='HELD' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:return await u.message.reply_text('No held transactions.')
    await u.message.reply_text('\n'.join(f"#{r['id']} | {r['sender_email']} | ${r['amount']} | {r['external_id']}" for r in rows))
async def transactions(u,c):
    if not admin(u):return await deny(u)
    with db() as x: rows=x.execute("SELECT id,external_id,sender_email,amount,status FROM transactions ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:return await u.message.reply_text('No transactions yet.')
    await u.message.reply_text('\n'.join(f"#{r['id']} | {r['status']} | ${r['amount']} | {r['sender_email']}" for r in rows))
async def callback(u,c):
    if not admin(u):return await u.callback_query.answer('Unauthorized')
    q=u.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    d=q.data
    if d=='stop': set_automation(False,q.from_user.id); return await q.edit_message_text('EMERGENCY STOP ACTIVE.',reply_markup=menu())
    if d=='resume': set_automation(True,q.from_user.id); return await q.edit_message_text('Automation resumed.',reply_markup=menu())
    if d=='held':
        with db() as x:
            rows=x.execute("SELECT id,external_id,sender_email,amount,status FROM transactions WHERE status='HELD' ORDER BY id DESC LIMIT 20").fetchall()
        if not rows:
            return await q.edit_message_text('No held transactions.',reply_markup=menu())
        return await q.edit_message_text(
            '\n'.join(f"#{r['id']} | {r['sender_email']} | ${r['amount']} | {r['external_id']}" for r in rows),
            reply_markup=menu()
        )
    if d=='transactions':
        with db() as x:
            rows=x.execute("SELECT id,external_id,sender_email,amount,status FROM transactions ORDER BY id DESC LIMIT 20").fetchall()
        if not rows:
            return await q.edit_message_text('No transactions yet.',reply_markup=menu())
        return await q.edit_message_text(
            '\n'.join(f"#{r['id']} | {r['status']} | ${r['amount']} | {r['sender_email']}" for r in rows),
            reply_markup=menu()
        )
    await q.edit_message_text(f'{d.title()} module is wired into the backend. CRUD commands are the next interface layer.',reply_markup=menu())
def build_bot():
    if not settings.telegram_bot_token: raise RuntimeError('TELEGRAM_BOT_TOKEN is required')
    a=Application.builder().token(settings.telegram_bot_token).build(); a.add_handler(CommandHandler('start',start)); a.add_handler(CommandHandler('dashboard',dashboard)); a.add_handler(CommandHandler('status',status)); a.add_handler(CommandHandler('stop',stop)); a.add_handler(CommandHandler('resume',resume)); a.add_handler(CommandHandler('held',held)); a.add_handler(CommandHandler('transactions',transactions)); a.add_handler(CallbackQueryHandler(callback)); return a
