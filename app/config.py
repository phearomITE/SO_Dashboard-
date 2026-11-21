from pathlib import Path
import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', encoding='utf-8-sig')
TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'Asia/Phnom_Penh'))
ADMINS = {int(x) for x in os.getenv('GLOBAL_ADMIN_IDS','5400514256').split(',') if x.strip()}
CENTRAL_ALERT_CHAT_ID = int(os.getenv('CENTRAL_ALERT_CHAT_ID','0') or 0)
CENTRAL_ALERT_TOPIC_ID = int(os.getenv('CENTRAL_ALERT_TOPIC_ID','0') or 0)
CENTRAL_ALERT_TOPIC_IDS = {
    'morning_talk':int(os.getenv('CENTRAL_TOPIC_MORNING_TALK_ID','0') or 0),
    'wrong_plan':int(os.getenv('CENTRAL_TOPIC_WRONG_PLAN_ID','0') or 0),
    'over_10km':int(os.getenv('CENTRAL_TOPIC_OVER_10KM_ID','0') or 0),
    'user_al':int(os.getenv('CENTRAL_TOPIC_USER_AL_ID','0') or 0),
    'distant_dealer':int(os.getenv('CENTRAL_TOPIC_DISTANT_DEALER_ID','0') or 0),
    'user_in_wrong_group':int(os.getenv('CENTRAL_TOPIC_USER_IN_WRONG_GROUP_ID','0') or 0),
    'new_user_join':int(os.getenv('CENTRAL_TOPIC_NEW_USER_JOIN_ID','0') or 0),
    'missing_report':int(os.getenv('CENTRAL_TOPIC_MISSING_REPORT_ID','0') or 0),
    'parasol':int(os.getenv('CENTRAL_TOPIC_PARASOL_ID','0') or 0),
    'banner_report':int(os.getenv('CENTRAL_TOPIC_BANNER_REPORT_ID','0') or 0),
}
AUTO_RECONCILE_DEALER = os.getenv('AUTO_RECONCILE_DEALER','true').lower() == 'true'
WORK_DAYS = {int(x) for x in os.getenv('WORK_DAYS','0,1,2,3,4').split(',')}
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN','')
TEST_MODE = os.getenv('TEST_MODE','false').lower() == 'true'
TEST_GROUP_TITLE = os.getenv('TEST_GROUP_TITLE','Test_SO')
PURGE_TEST_DATA_ON_STARTUP = os.getenv('PURGE_TEST_DATA_ON_STARTUP','true').lower() == 'true'
EXCLUDED_TEST_CHAT_IDS = {
    int(x.strip()) for x in os.getenv('EXCLUDED_TEST_CHAT_IDS','-1004303041907').split(',')
    if x.strip()
}
SETTLE = float(os.getenv('ALERT_SETTLE_SECONDS','2'))
ALERT_INTERVAL_MINUTES = max(5,min(60,int(os.getenv('ALERT_INTERVAL_MINUTES','15'))))
if ALERT_INTERVAL_MINUTES % 5:ALERT_INTERVAL_MINUTES=15
REVERSE_GEOCODING_ENABLED = os.getenv('REVERSE_GEOCODING_ENABLED','true').lower() == 'true'
REVERSE_GEOCODING_URL = os.getenv('REVERSE_GEOCODING_URL','https://nominatim.openstreetmap.org/reverse')
REVERSE_GEOCODING_USER_AGENT = os.getenv('REVERSE_GEOCODING_USER_AGENT','SO-Alert-Bot/1.0 (local business reporting bot)')
ROUTING_ENABLED = os.getenv('ROUTING_ENABLED','true').lower() == 'true'
ROUTING_URL = os.getenv('ROUTING_URL','https://router.project-osrm.org').rstrip('/')
ROUTING_USER_AGENT = os.getenv('ROUTING_USER_AGENT','SO-Alert-Bot/1.0 (local business reporting bot)')
ROUTING_ALERT_KM = float(os.getenv('ROUTING_ALERT_KM','10'))
ROUTING_TIMEOUT_SECONDS = float(os.getenv('ROUTING_TIMEOUT_SECONDS','20'))
WAREHOUSE_ENABLED = os.getenv('WAREHOUSE_ENABLED','true').lower() == 'true'
WAREHOUSE_INTERVAL_MINUTES = max(1,int(os.getenv('WAREHOUSE_INTERVAL_MINUTES','5')))
WAREHOUSE_INITIAL_DELAY_SECONDS = max(10,int(os.getenv('WAREHOUSE_INITIAL_DELAY_SECONDS','30')))
DATA = ROOT / 'data'
