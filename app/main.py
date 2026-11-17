import logging
import time
import hashlib
from sqlalchemy.exc import SQLAlchemyError
from app.diagnostics import database_message
from logging.handlers import RotatingFileHandler
from app import db
from app.config import ROOT,DATA,TOKEN,TEST_MODE,PURGE_TEST_DATA_ON_STARTUP
from app.imports import import_roster,import_plan,import_dealer_locations,purge_excluded_roster,purge_removed_users
from app.warehouse import import_locations
from app.bot import create_bot
from app.al_reconciliation import apply_reconciliation

def main():
    (ROOT/'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[logging.StreamHandler(),RotatingFileHandler(ROOT/'logs'/'bot.log',maxBytes=5_000_000,backupCount=3,encoding='utf-8')],format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # HTTP request URLs contain the Telegram token; suppress routine HTTP logging.
    logging.getLogger('httpx').setLevel(logging.WARNING)
    # APScheduler's two INFO lines every five seconds are normal heartbeat
    # noise, not errors. Keep warnings and failures visible.
    logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
    if not TOKEN or TOKEN=='PUT_NEW_BOT_TOKEN_HERE':raise SystemExit('Set TELEGRAM_BOT_TOKEN in .env first.')
    for attempt in range(1,4):
        try:
            db.connect()
            break
        except SQLAlchemyError as exc:
            message=database_message(exc,db.database_url())
            logging.error('%s (attempt %s/3)',message,attempt)
            if attempt==3:raise SystemExit(1) from None
            time.sleep(2)
    try:db.acquire_lock()
    except RuntimeError as exc:raise SystemExit(str(exc)) from None
    try:
        with db.session() as s:
            if not TEST_MODE and PURGE_TEST_DATA_ON_STARTUP:
                removed=db.purge_test_data(s)
                logging.info('Production test-data cleanup: %s',removed)
            # Always remove rows written by older releases. This must not
            # depend on the bundled workbook hash changing.
            purge_excluded_roster(s)
            removed_users=purge_removed_users(s)
            if any(value for key,value in removed_users.items() if key!='source_event_ids'):
                logging.info('Permanent user cleanup: %s',removed_users)
            roster_path=DATA/'List_SO.xlsx';roster_hash=hashlib.sha256(roster_path.read_bytes()).hexdigest()
            logging.info('Startup configuration: checking roster, plan and location masters')
            if db.state(s,'bundled_roster',{}).get('sha256')!=roster_hash:
                count,so,controllers,controller_groups=import_roster(s,roster_path)
                db.put(s,'bundled_roster',{'sha256':roster_hash,'rows':count,'so':so,'controllers':controllers,'controller_groups':controller_groups})
            repaired=apply_reconciliation(s,DATA/'morning_talk_al_reconciliation_2026-11-11.csv')
            if repaired:logging.info('Historical AL reconciliation: %s verified matched employees',repaired)
            plan_path=DATA/'Plan_SO.xlsx';plan_hash=hashlib.sha256(plan_path.read_bytes()).hexdigest()
            if db.state(s,'bundled_plan',{}).get('sha256')!=plan_hash:
                rows=import_plan(s,plan_path,0,plan_path.name);db.put(s,'bundled_plan',{'sha256':plan_hash,'rows':rows})
            location_path=DATA/'Location_All.xlsx';location_hash=hashlib.sha256(location_path.read_bytes()).hexdigest()
            if db.state(s,'bundled_location',{}).get('sha256')!=location_hash:
                rows,_=import_locations(s,location_path,location_path.name);db.put(s,'bundled_location',{'sha256':location_hash,'rows':rows})
            dealer_csv=DATA/'DEALERS_STATION_All_Location.csv'
            dealer_path=dealer_csv if dealer_csv.exists() else DATA/'Dealer Location With sales coverage.xlsx'
            dealer_hash=hashlib.sha256(dealer_path.read_bytes()).hexdigest()
            if db.state(s,'bundled_dealer_locations',{}).get('sha256')!=dealer_hash:
                source_rows,dealers=import_dealer_locations(s,dealer_path)
                db.put(s,'bundled_dealer_locations',{'sha256':dealer_hash,'source_rows':source_rows,'dealers':dealers})
            # Never block Telegram polling on the large Bronze-Silver-Gold
            # refresh. post_init schedules it in a worker thread after the bot
            # is already able to answer commands.
            logging.info('Startup configuration committed; warehouse refresh is deferred')
        logging.info('SO bot ready: concurrent update workers=128, isolation=per-chat, test_mode=%s',TEST_MODE)
        create_bot().run_polling(allowed_updates=['message','edited_message','chat_member'],
                                 drop_pending_updates=False,bootstrap_retries=-1)
    finally:db.release_lock()

if __name__=='__main__':main()
