"""The debug-status reply must reach the mDot as hex.

LoRaHandler.decode() passes handle_debug_status_request()['data'] to
queue_binary_transmit(), which treats any str as a hex payload and sends it
unchanged. The report is compact JSON, so it has to be hex-encoded first.
"""
from unittest import mock

from tools import lora_debug_integration as dbg

REPORT = '{"ts":"2026-09-30T19:30:00","host":"ufo-01-0","up":1.5,"cpu_t":48.2}'


def _generated(text=REPORT):
    return {'status': 'debug_status_generated', 'timestamp': '2026-09-30T19:30:00',
            'lora_formatted': text, 'lora_size_bytes': len(text.encode())}


def test_success_payload_is_hex_of_the_json_report():
    with mock.patch('tools.debug_status_command.handle_debug_status_command', return_value=_generated()):
        r = dbg.handle_debug_status_request()
    assert r['status'] == 'success'
    int(r['data'], 16)                                   # all hex digits
    assert bytes.fromhex(r['data']).decode() == REPORT
    assert r['size_bytes'] == len(REPORT)


def test_report_failure_is_passed_through_as_error():
    failed = {'status': 'debug_status_failed', 'error': 'boom', 'timestamp': 't'}
    with mock.patch('tools.debug_status_command.handle_debug_status_command', return_value=failed):
        r = dbg.handle_debug_status_request()
    assert r == {'command': 'debug_status_response', 'timestamp': 't', 'status': 'error', 'error': 'boom'}


def test_import_failure_is_an_error_not_a_crash():
    # e.g. psutil missing from the venv: debug_status_command imports it at module level
    with mock.patch.dict('sys.modules', {'tools.debug_status_command': None}):
        r = dbg.handle_debug_status_request()
    assert r['status'] == 'error'


def test_process_debug_command_only_answers_type_50_command_01():
    with mock.patch.object(dbg, 'handle_debug_status_request', return_value={'status': 'success'}) as h:
        assert dbg.process_debug_command('50011') == {'status': 'success'}
        assert dbg.process_debug_command('21001') is None
    h.assert_called_once()
