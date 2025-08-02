import sqlite3
import json
import requests
import datetime
import pytz
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler
)

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
TELEGRAM_TOKEN = '7722520417:AAGrS1tD_rlfZ58uwNEKswfL6wQTPw276pY'
API_ENDPOINT = 'https://dashboard.gensyn.ai/api/v1/peer'
DB_FILE = 'bot_database.db'
HOURLY_STATS_FILE = 'previous_stats.json'
DAILY_STATS_FILE = 'daily_summary_stats.json'

# --- FOOTER CONFIGURATION ---
FOOTER_TEXT = "\n\n\nMade with ❤️ by <a href='https://t.me/md_alfaaz'>clownyy</a>"

# --- STATE MANAGEMENT ---
AWAITING_PEER_FOR_ADD, AWAITING_PEER_FOR_REMOVE = range(2)

# --- DATABASE HELPER FUNCTIONS ---

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            identifier TEXT,
            FOREIGN KEY(chat_id) REFERENCES users(chat_id)
        )
    ''')
    conn.commit()
    conn.close()

def add_user_to_db(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def remove_user_from_db(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE chat_id = ?", (chat_id,))
    cursor.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def add_to_watchlist_db(chat_id, identifier):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO watchlist (chat_id, identifier) VALUES (?, ?)", (chat_id, identifier))
    conn.commit()
    conn.close()

def remove_from_watchlist_db(chat_id, identifier):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE chat_id = ? AND identifier = ?", (chat_id, identifier))
    conn.commit()
    conn.close()

def get_user_watchlist(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT identifier FROM watchlist WHERE chat_id = ?", (chat_id,))
    items = [row[0] for row in cursor.fetchall()]
    conn.close()
    return items

def get_all_watchlists():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, identifier FROM watchlist")
    all_watchlists = {}
    for chat_id, identifier in cursor.fetchall():
        if chat_id not in all_watchlists: all_watchlists[chat_id] = []
        all_watchlists[chat_id].append(identifier)
    conn.close()
    return all_watchlists

# --- STATS PERSISTENCE ---
def load_stats_from_file(filename):
    try:
        with open(filename, 'r') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}
def save_stats_to_file(stats_dict, filename):
    with open(filename, 'w') as f: json.dump(stats_dict, f, indent=4)

# --- HELPER & FORMATTING FUNCTIONS ---
def up(n): return f"🔺+{n}" if n > 0 else (f"🔻{n}" if n < 0 else "➖")
def fetch_peer_data(query_params: dict) -> dict | None:
    try:
        response = requests.get(API_ENDPOINT, params=query_params, timeout=10)
        if response.status_code != 200: return None
        data = response.json()
        if isinstance(data, list): return data[0] if data else None
        elif isinstance(data, dict) and data: return data
        else: return None
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error(f"API request or JSON parsing failed: {e}"); return None
def format_peer_message(data: dict, reward_change: int = 0, wins_change: int = 0, is_top: bool = False, is_daily: bool = False) -> str:
    if not data: return "Peer data could not be retrieved."
    peer_id, peer_name = data.get('peerId', 'N/A'), data.get('peerName', 'N/A')
    rewards, wins = data.get('reward', 0), data.get('score', 0)
    online_status = "🟢 Online" if data.get('online', False) else "🔴 Offline"
    peer_id_display = f"<code>{peer_id[:6]}...{peer_id[-4:]}</code>" if len(peer_id) > 10 else f"<code>{peer_id}</code>"
    reward_change_str = f" <code>{up(reward_change)}</code>" if reward_change != 0 else ""
    wins_change_str = f" <code>{up(wins_change)}</code>" if wins_change != 0 else ""
    top_label = "👑 <b>Daily Top Performer!</b>\n" if is_top and is_daily else ("👑 <b>Top Performer!</b>\n" if is_top else "")
    message_lines = [
        f"{top_label}🪪 <b>Peer ID:</b> {peer_id_display}", f"📝 <b>Name:</b> {peer_name}",
        f"💰 <b>Rewards:</b> {rewards}{reward_change_str}", f"🏆 <b>Wins:</b> {wins}{wins_change_str}",
        f"{online_status}"
    ]
    return "\n".join(message_lines)
def build_main_menu():
    keyboard = [[InlineKeyboardButton("📊 Status", callback_data='status'), InlineKeyboardButton("📋 View Tracking List", callback_data='list')], [InlineKeyboardButton("➕ Add Peer(s)", callback_data='add'), InlineKeyboardButton("➖ Remove Peer(s)", callback_data='remove')]]
    return InlineKeyboardMarkup(keyboard)

# --- COMMANDS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} started the bot.")
    add_user_to_db(update.effective_chat.id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Welcome! \n\nUse the menu below to manage your tracking list.",
        reply_markup=build_main_menu()
    )
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} stopped the bot.")
    remove_user_from_db(update.effective_chat.id)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Your tracking list has been cleared.")
async def list_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_watchlist = get_user_watchlist(update.effective_chat.id)
    message = "Your watchlist is currently empty."
    if user_watchlist:
        message = "📋 <b>Your Tracking List</b>\n" + "\n".join([f"• <code>{item}</code>" for item in user_watchlist])
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode='HTML')
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_watchlist = get_user_watchlist(update.effective_chat.id)
    if not user_watchlist:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Your tracking list is empty."); return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Fetching live status...")
    peer_data_list, top_performer_id, max_overall_wins = [], None, -1
    for identifier in user_watchlist:
        data = fetch_peer_data({'id': identifier} if identifier.startswith('Qm') else {'name': identifier})
        if data:
            peer_data_list.append((identifier, data))
            if data.get('score', 0) > max_overall_wins: max_overall_wins, top_performer_id = data.get('score', 0), data.get('peerId')
        else: peer_data_list.append((identifier, None))
    status_messages = []
    for identifier, data in peer_data_list:
        if data: status_messages.append(format_peer_message(data, is_top=(data.get('peerId') == top_performer_id and max_overall_wins > 0)))
        else: status_messages.append(f"<b>{identifier[:20]}</b>\nCould not be found.")
    summary_line = f"\n\n<b>Overall Totals:</b>\n💰 Rewards: {sum(d.get('reward', 0) for i, d in peer_data_list if d)} | 🏆 Wins: {sum(d.get('score', 0) for i, d in peer_data_list if d)}"
    full_report = "📊 <b>Live Node Status</b>\n\n" + "\n\n—————————————————\n\n".join(status_messages) + summary_line + FOOTER_TEXT
    await context.bot.send_message(chat_id=update.effective_chat.id, text=full_report, parse_mode='HTML')

# --- CONVERSATION HANDLERS ---
async def add_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Please send the peer names or IDs to add, <b>each on a new line</b>.\n\nSend /cancel to stop.", parse_mode='HTML')
    return AWAITING_PEER_FOR_ADD
async def receive_peer_to_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id, identifiers = update.effective_chat.id, [line.strip() for line in update.message.text.splitlines() if line.strip()]
    if not identifiers:
        await context.bot.send_message(chat_id=chat_id, text="No peers provided. Action canceled."); return ConversationHandler.END
    user_watchlist, added_peers, failed_peers, existing_peers = get_user_watchlist(chat_id), [], [], []
    await context.bot.send_message(chat_id=chat_id, text=f"Processing {len(identifiers)} peer(s)...")
    for identifier in identifiers:
        if identifier in user_watchlist: existing_peers.append(identifier); continue
        if fetch_peer_data({'id': identifier} if identifier.startswith('Qm') else {'name': identifier}):
            add_to_watchlist_db(chat_id, identifier); added_peers.append(identifier)
        else: failed_peers.append(identifier)
    summary_message = "--- <b>Add Results</b> ---\n"
    if added_peers: summary_message += "\n✅ <b>Added:</b>\n" + "\n".join([f"• <code>{p}</code>" for p in added_peers])
    if existing_peers: summary_message += "\n\n☑️ <b>Already in Watchlist:</b>\n" + "\n".join([f"• <code>{p}</code>" for p in existing_peers])
    if failed_peers: summary_message += "\n\n❌ <b>Failed (Not Found):</b>\n" + "\n".join([f"• <code>{p}</code>" for p in failed_peers])
    await context.bot.send_message(chat_id=chat_id, text=summary_message, parse_mode='HTML'); return ConversationHandler.END
async def remove_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Please send the peer names or IDs to remove, <b>each on a new line</b>.\n\nSend /cancel to stop.", parse_mode='HTML')
    return AWAITING_PEER_FOR_REMOVE
async def receive_peer_to_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id, identifiers = update.effective_chat.id, [line.strip() for line in update.message.text.splitlines() if line.strip()]
    if not identifiers:
        await context.bot.send_message(chat_id=chat_id, text="No peers provided. Action canceled."); return ConversationHandler.END
    user_watchlist, removed_peers, not_found_peers = get_user_watchlist(chat_id), [], []
    for identifier in identifiers:
        if identifier in user_watchlist: remove_from_watchlist_db(chat_id, identifier); removed_peers.append(identifier)
        else: not_found_peers.append(identifier)
    summary_message = "--- <b>Remove Results</b> ---\n"
    if removed_peers: summary_message += "\n🗑️ <b>Removed:</b>\n" + "\n".join([f"• <code>{p}</code>" for p in removed_peers])
    if not_found_peers: summary_message += "\n\n❌ <b>Not in Watchlist:</b>\n" + "\n".join([f"• <code>{p}</code>" for p in not_found_peers])
    await context.bot.send_message(chat_id=chat_id, text=summary_message, parse_mode='HTML'); return ConversationHandler.END
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Action canceled."); return ConversationHandler.END
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    logger.info(f"User {query.from_user.id} pressed button: {query.data}")
    command = query.data
    if command == 'status': await status_command(update, context)
    elif command == 'list': await list_watchlist(update, context)
    elif command == 'add': await add_command_entry(update, context)
    elif command == 'remove': await remove_command_entry(update, context)

# --- SCHEDULED JOBS ---
async def send_hourly_updates(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting hourly update job...")
    previous_stats, all_watchlists = load_stats_from_file(HOURLY_STATS_FILE), get_all_watchlists()
    all_peers = set(p for w in all_watchlists.values() for p in w)
    if not all_peers: logger.info("Hourly update: No peers in any watchlist. Skipping."); return
    current_peer_data, top_performer_id, max_overall_wins = {}, None, -1
    for identifier in all_peers:
        data = fetch_peer_data({'id': identifier} if identifier.startswith('Qm') else {'name': identifier})
        if data:
            current_rewards, current_wins = data.get('reward', 0), data.get('score', 0)
            last_stats = previous_stats.get(identifier, {'rewards': current_rewards, 'wins': current_wins})
            current_peer_data[identifier] = {'data': data, 'reward_change': current_rewards - last_stats.get('rewards', 0), 'wins_change': current_wins - last_stats.get('wins', 0)}
            if current_wins > max_overall_wins: max_overall_wins, top_performer_id = current_wins, identifier
    for chat_id, watchlist in all_watchlists.items():
        report_items = []
        for identifier in watchlist:
            peer_info = current_peer_data.get(identifier)
            if peer_info and (peer_info['reward_change'] != 0 or peer_info['wins_change'] != 0):
                is_top = (identifier == top_performer_id and max_overall_wins > 0)
                report_items.append(format_peer_message(peer_info['data'], peer_info['reward_change'], peer_info['wins_change'], is_top=is_top))
        if report_items:
            full_report = "📈<b>Hourly Peer Update!</b>\n\n" + "\n\n—————————————————\n\n".join(report_items)
            try: await context.bot.send_message(chat_id=chat_id, text=full_report, parse_mode='HTML')
            except Exception as e: logger.error(f"Failed to send hourly update to {chat_id}: {e}")
    new_stats_to_save = {iden: {'rewards': p_info['data'].get('reward', 0), 'wins': p_info['data'].get('score', 0)} for iden, p_info in current_peer_data.items()}
    save_stats_to_file(new_stats_to_save, HOURLY_STATS_FILE)
    logger.info("Hourly update job finished.")

async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Starting daily summary job...")
    last_24h_stats, all_watchlists = load_stats_from_file(DAILY_STATS_FILE), get_all_watchlists()
    all_peers = set(p for w in all_watchlists.values() for p in w)
    if not all_peers: logger.info("Daily summary: No peers in any watchlist. Skipping."); return
    current_peer_data, top_performer_id, max_daily_wins_change = {}, None, 0
    for identifier in all_peers:
        data = fetch_peer_data({'id': identifier} if identifier.startswith('Qm') else {'name': identifier})
        if data:
            current_rewards, current_wins = data.get('reward', 0), data.get('score', 0)
            last_stats = last_24h_stats.get(identifier, {'rewards': current_rewards, 'wins': current_wins})
            current_peer_data[identifier] = {'data': data, 'reward_change': current_rewards - last_stats.get('rewards', 0), 'wins_change': current_wins - last_stats.get('wins', 0)}
            if current_peer_data[identifier]['wins_change'] > max_daily_wins_change: max_daily_wins_change, top_performer_id = current_peer_data[identifier]['wins_change'], identifier
    for chat_id, watchlist in all_watchlists.items():
        report_items = []
        total_reward_change, total_wins_change = 0, 0
        for identifier in watchlist:
            peer_info = current_peer_data.get(identifier)
            if peer_info:
                total_reward_change += peer_info['reward_change']
                total_wins_change += peer_info['wins_change']
                if peer_info['reward_change'] > 0 or peer_info['wins_change'] > 0:
                    is_top = (identifier == top_performer_id and max_daily_wins_change > 0)
                    report_items.append(format_peer_message(peer_info['data'], peer_info['reward_change'], peer_info['wins_change'], is_top=is_top, is_daily=True))
        if report_items:
            summary_line = f"\n\n<b>24h Change Totals:</b>\n💰 Rewards: {up(total_reward_change)} | 🏆 Wins: {up(total_wins_change)}"
            full_report = "📈 <b>24-Hour Summary Report!</b>\n\n" + "\n\n—————————————————\n\n".join(report_items) + summary_line + FOOTER_TEXT
            try: await context.bot.send_message(chat_id=chat_id, text=full_report, parse_mode='HTML')
            except Exception as e: logger.error(f"Failed to send daily summary to {chat_id}: {e}")
    new_stats_to_save = {iden: {'rewards': p_info['data'].get('reward', 0), 'wins': p_info['data'].get('score', 0)} for iden, p_info in current_peer_data.items()}
    save_stats_to_file(new_stats_to_save, DAILY_STATS_FILE)
    logger.info("Daily summary job finished.")

# --- MAIN BOT SETUP ---
def main():
    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    add_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command_entry), CallbackQueryHandler(add_command_entry, pattern='^add$')],
        states={AWAITING_PEER_FOR_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_peer_to_add)]},
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    remove_handler = ConversationHandler(
        entry_points=[CommandHandler("remove", remove_command_entry), CallbackQueryHandler(remove_command_entry, pattern='^remove$')],
        states={AWAITING_PEER_FOR_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_peer_to_remove)]},
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )
    application.add_handler(add_handler)
    application.add_handler(remove_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("list", list_watchlist))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    job_queue = application.job_queue
    job_queue.run_repeating(send_hourly_updates, interval=3600, first=10)
    job_queue.run_daily(send_daily_summary, time=datetime.time(hour=9, minute=0, tzinfo=pytz.timezone('Asia/Kolkata')))
    
    logger.info("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()
