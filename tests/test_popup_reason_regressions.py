from pathlib import Path
from app.reason_analysis import fallback_reason

DASHBOARD = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'dashboard.html'

def test_khmer_distance_reason_is_verified():
    result = fallback_reason('ខ្ញុំចុះទៅផ្លូវឆ្ងាយ')
    assert result['is_reason'] is True
    assert result['category'] == 'travel_distance'

def test_meeting_with_dealer_is_reason():
    assert fallback_reason('Meeting With Dealer')['is_reason'] is True

def test_outlet_evidence_is_not_reason():
    assert fallback_reason('Dealer: SRP1\nOutlet Name: ABC')['is_reason'] is False

def test_outlet_popup_does_not_embed_fullscreen_broken_image():
    html = DASHBOARD.read_text(encoding='utf-8')
    assert 'className:"outlet-map-popup"' in html
    assert 'Open Telegram evidence' in html
    assert 'photo=x.image_preview?' not in html
    assert 'marker.on(\'click\',()=>{marker.openPopup()})' not in html

def test_employee_summary_shows_pending_and_position():
    html = DASHBOARD.read_text(encoding='utf-8')
    assert 'Pending AI/review:' in html
    assert 'Position: ${esc(profile.position' in html
    assert 'String(x.telegram_id||\'\').trim()===uid' in html
