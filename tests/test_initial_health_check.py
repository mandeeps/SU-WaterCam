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
    # what witty_pi_4 returns when i2cget fails
    dead = {'wittypi_temperature_c': -273.15, 'wittypi_battery_voltage_v': 0.0,
            'wittypi_internal_voltage_v': 0.0, 'wittypi_internal_current_a': 0.0}
    r = hc.evaluate_health(45.0, dead, GPS, IMU)
    assert r['failures'] == ['wittypi_unavailable']


def test_low_input_voltage_reported():
    r = hc.evaluate_health(45.0, dict(GOOD_WITTYPI, wittypi_battery_voltage_v=4.49), GPS, IMU)
    assert r['failures'] == ['wittypi_input_voltage_low_4.49V']


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
