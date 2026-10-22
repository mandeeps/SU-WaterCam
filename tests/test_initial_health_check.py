"""initial_health_check.py prints its verdict and reasons, and reports an unreadable
Witty Pi as unavailable rather than as 0 V."""
import tools.initial_health_check as hc

GOOD_WITTYPI = {'wittypi_temperature_c': 30.5, 'wittypi_battery_voltage_v': 4.9,
                'wittypi_internal_voltage_v': 5.0, 'wittypi_internal_current_a': 0.7}
GPS = {'gps_lat': 1.0, 'gps_lon': 2.0}
IMU = {'tilt_roll_yaw': [0, 0, 0]}


def test_all_good():
    r = hc.evaluate_health(45.0, GOOD_WITTYPI, GPS, IMU)
    assert r['status'] == 'ok' and r['failures'] == []


def test_unreadable_wittypi_is_unavailable_not_low_voltage():
    # read_wittypi_voltages() leaves out readings the board didn't give (None)
    r = hc.evaluate_health(45.0, {}, GPS, IMU)
    assert r['failures'] == ['wittypi_unavailable']


def test_missing_output_voltage_is_unavailable_not_low():
    partial = {'wittypi_temperature_c': 30.5}
    r = hc.evaluate_health(45.0, partial, GPS, IMU)
    assert r['failures'] == ['internal_voltage_unavailable']


def test_read_wittypi_voltages_skips_none(monkeypatch):
    import tools.wittypi_control as wc
    monkeypatch.setattr(wc, 'get_data', lambda: (None, None, 4.9, None))
    assert hc.read_wittypi_voltages() == {'wittypi_internal_voltage_v': 4.9}
    monkeypatch.setattr(wc, 'get_data', lambda: (None, None, None, None))
    assert hc.read_wittypi_voltages() == {}


def test_input_voltage_is_reported_but_never_fails():
    # the Witty Pi's input reading is 0.4-0.5 V low on several boards
    for vin in (4.49, 4.2, 0.0):
        assert hc.evaluate_health(45.0, dict(GOOD_WITTYPI, wittypi_battery_voltage_v=vin), GPS, IMU)['failures'] == []


def test_output_voltage_threshold_is_4_7_v():
    assert hc.evaluate_health(45.0, dict(GOOD_WITTYPI, wittypi_internal_voltage_v=4.7), GPS, IMU)['failures'] == []
    r = hc.evaluate_health(45.0, dict(GOOD_WITTYPI, wittypi_internal_voltage_v=4.65), GPS, IMU)
    assert r['failures'] == ['internal_voltage_low_4.65V']


def test_main_prints_reasons_and_skips_alert(monkeypatch, capsys):
    monkeypatch.setattr(hc, 'read_cpu_temperature_c', lambda: 45.0)
    monkeypatch.setattr(hc, 'read_wittypi_voltages', lambda: GOOD_WITTYPI)
    monkeypatch.setattr(hc, 'read_gps_location', lambda: {})
    monkeypatch.setattr(hc, 'read_imu_orientation', lambda: IMU)
    sent = []
    monkeypatch.setattr(hc, 'send_lora_alert', lambda: sent.append(1) or True)

    assert hc.main(['--no-alert']) == 1
    out = capsys.readouterr().out
    assert 'Health check: FAIL' in out and 'FAIL  gps_unavailable' in out and 'cpu_temp_c' in out
    assert sent == []

    assert hc.main([]) == 1
    assert sent == [1] and 'LoRa alert queued' in capsys.readouterr().out


def test_main_ok_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(hc, 'read_cpu_temperature_c', lambda: 45.0)
    monkeypatch.setattr(hc, 'read_wittypi_voltages', lambda: GOOD_WITTYPI)
    monkeypatch.setattr(hc, 'read_gps_location', lambda: GPS)
    monkeypatch.setattr(hc, 'read_imu_orientation', lambda: IMU)
    assert hc.main([]) == 0
    assert 'Health check: OK' in capsys.readouterr().out
