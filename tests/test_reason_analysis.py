from app.reason_analysis import analyze_reason, fallback_reason


def test_no_network_reply_is_reason():
    result = fallback_reason('ចុះ attached ជាមួយបងPSEតំបន់អត់សេវ៉ា')
    assert result['is_reason'] is True
    assert result['category'] == 'connectivity'


def test_fieldwork_reply_is_reason():
    result = fallback_reason('កំពង់បាញ់Banner នឹងចរចារម៉ូយអោយជួយយកទឹក')
    assert result['is_reason'] is True
    assert result['category'] == 'field_work'


def test_outlet_form_and_location_are_not_reasons():
    for message in ('Dealer: SRP1\nOutlet Name: ABC\nPOSM:',
                    'https://maps.google.com/maps?q=13.341883,104.000851'):
        assert fallback_reason(message)['is_reason'] is False


def test_unexplained_reply_is_reviewed_not_invented():
    assert fallback_reason('Followed up with the team')['is_reason'] is None


def test_analyze_reason_uses_rules_without_hermes():
    result = analyze_reason('ខ្ញុំចុះទៅផ្លូវឆ្ងាយ')
    assert result['is_reason'] is True
    assert result['category'] == 'travel_distance'
    assert result['method'] == 'rules'
    assert result['ai_status'] == 'rules'
    assert result['summary_km']
    assert result['summary_en']
