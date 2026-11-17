from app.rules import morning_talk_full_day_leave_entries


def test_full_day_variants_and_half_day_exclusion():
    report = '''Morning Talk
Date:11.11.2026
*Attain
1. Bou Thary (SS): A/L
2. Phon Piseth (SO): AL
3. BO Vannoy (SO): No (AL)
4. Keng Kimhong (SO): AL Half Morning
5. No Check In (SO): No
6. Phallai LY (SS): AL
1. Compititor Activity'''
    assert [entry['name'] for entry in morning_talk_full_day_leave_entries(report)] == [
        'Bou Thary', 'Phon Piseth', 'BO Vannoy', 'Phallai LY'
    ]


def test_explicit_id_is_retained():
    assert morning_talk_full_day_leave_entries('Morning Talk\n1.ID:90021882-Eol Phearun (SA): AL') == [
        {'employee_id': '90021882', 'name': 'Eol Phearun'}
    ]
