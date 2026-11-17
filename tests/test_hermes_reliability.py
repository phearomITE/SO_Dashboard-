import json
from unittest.mock import patch
from app import reason_analysis as ra

class FakeResponse:
    def __init__(self, body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,*args):return json.dumps(self.body).encode()

def test_hermes_immediate_reason_for_khmer_long_distance():
    with patch.object(ra,'urlopen',return_value=FakeResponse({'response':json.dumps({'is_reason':True,'summary_en':'The employee reports travelling a long distance.','summary_km':'បានបញ្ជាក់ថាត្រូវធ្វើដំណើរផ្លូវឆ្ងាយ។','category':'travel'})})):
        result=ra.analyze_reason('ខ្ញុំចុះទៅផ្លូវឆ្ងាយ')
    assert result['ai_status']=='completed'
    assert result['method'].startswith('ollama:')
    assert result['is_reason'] is True

def test_hermes_offline_is_visible_and_not_fabricated():
    with patch.object(ra,'urlopen',side_effect=OSError('Connection refused')):
        with patch('time.sleep'):
            result=ra.analyze_reason('Followed up with the team')
    assert result['is_reason'] is None
    assert result['ai_status']=='unavailable'
    assert 'Connection refused' in result['ai_error']

def test_outlet_evidence_is_not_sent_to_hermes():
    with patch.object(ra,'urlopen') as call:
        result=ra.analyze_reason('Dealer: SRP1\nOutlet Name: test\nPOSM:')
    assert result['is_reason'] is False
    call.assert_not_called()

def test_health_detects_model_missing():
    with patch.object(ra,'urlopen',return_value=FakeResponse({'models':[{'name':'llama3:latest'}]})):
        health=ra.ollama_health()
    assert health['reachable'] and not health['ready']


def test_hermes_requires_actual_khmer_summary():
    body={'response':json.dumps({'is_reason':True,'summary_en':'No signal','summary_km':'No signal','category':'connectivity'})}
    with patch.object(ra,'urlopen',return_value=FakeResponse(body)),patch('time.sleep'):
        result=ra.analyze_reason('នៅតំបន់ដែលអត់សេវាអ៊ីនធឺណិត')
    assert result['ai_status']=='unavailable'
    assert result['summary_km']

def test_hermes_can_leave_ambiguous_text_for_review():
    body={'response':json.dumps({'is_reason':None,'summary_en':'Working at the depot; the cause of the missed report is unclear.','summary_km':'កំពុងធ្វើការនៅដេប៉ូ ប៉ុន្តែមិនទាន់ច្បាស់ពីមូលហេតុខកខានរាយការណ៍។','category':'review'})}
    with patch.object(ra,'urlopen',return_value=FakeResponse(body)):
        result=ra.analyze_reason('កំពុងធ្វើការនៅដេប៉ូ')
    assert result['is_reason'] is None
    assert result['ai_status']=='completed'

def test_short_khmer_reason_is_not_rejected_as_empty():
    assert ra.fallback_reason('ឈឺ')['is_reason'] is True
    assert ra.fallback_reason('អត់សេវា')['is_reason'] is True
    assert ra.fallback_reason('សួស្តី')['is_reason'] is False
