#!/usr/bin/env python3
"""
AHT20 Data Flow Test
Tests the complete data flow from AHT20 sensor to LoRa transmission
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Unit tests for get_aht20() validation / retry logic ───────────────────

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


def _make_sensor(temp_sequence, rh_sequence):
    """Build a mock AHT20 sensor whose temperature/relative_humidity properties
    step through the provided sequences on successive reads."""
    sensor = MagicMock()
    type(sensor).temperature = PropertyMock(side_effect=temp_sequence)
    type(sensor).relative_humidity = PropertyMock(side_effect=rh_sequence)
    return sensor


def test_get_aht20_returns_valid_reading():
    """Normal path: sensor returns in-range values on first read."""
    import tools.aht20_temperature as mod
    sensor = _make_sensor([25.3], [52.0])
    with patch.object(mod, '_get_sensor', return_value=sensor):
        result = mod.get_aht20()
    assert result == {"temperature_celsius": 25.3, "relative_humidity": 52}


def test_get_aht20_rejects_startup_sentinel_and_retries():
    """First read returns -50.0 (hardware startup artifact); second returns valid."""
    import tools.aht20_temperature as mod
    sensor = _make_sensor([-50.0, 22.1], [0, 45])
    with patch.object(mod, '_get_sensor', return_value=sensor), \
         patch('time.sleep'):
        result = mod.get_aht20(retries=2, retry_delay=0)
    assert result == {"temperature_celsius": 22.1, "relative_humidity": 45}


def test_get_aht20_returns_empty_when_all_retries_fail():
    """All reads return out-of-range values → get_aht20 must return {}."""
    import tools.aht20_temperature as mod
    sensor = _make_sensor([-50.0, -50.0, -50.0], [0, 0, 0])
    with patch.object(mod, '_get_sensor', return_value=sensor), \
         patch('time.sleep'):
        result = mod.get_aht20(retries=2, retry_delay=0)
    assert result == {}


def test_get_aht20_returns_empty_when_sensor_unavailable():
    """_get_sensor() returns None (hardware missing) → get_aht20 must return {}."""
    import tools.aht20_temperature as mod
    with patch.object(mod, '_get_sensor', return_value=None):
        result = mod.get_aht20()
    assert result == {}


def test_get_aht20_returns_empty_on_exception():
    """sensor.temperature raises → get_aht20 must return {} without crashing."""
    import tools.aht20_temperature as mod
    sensor = MagicMock()
    type(sensor).temperature = PropertyMock(side_effect=OSError("I2C error"))
    with patch.object(mod, '_get_sensor', return_value=sensor), \
         patch('time.sleep'):
        result = mod.get_aht20(retries=0)
    assert result == {}


def test_get_aht20_rejects_above_range():
    """Values above 85°C are also invalid (sensor damage / runaway read)."""
    import tools.aht20_temperature as mod
    sensor = _make_sensor([90.0, 90.0, 90.0], [50, 50, 50])
    with patch.object(mod, '_get_sensor', return_value=sensor), \
         patch('time.sleep'):
        result = mod.get_aht20(retries=2, retry_delay=0)
    assert result == {}

def test_aht20_collection():
    """Test AHT20 data collection"""
    print("🌡️  Testing AHT20 Data Collection")
    print("=" * 50)
    
    try:
        from tools.aht20_temperature import get_aht20
        data = get_aht20()
        print(f"✅ AHT20 data collected: {data}")
        print(f"   Temperature: {data.get('temperature_celsius')}°C")
        print(f"   Humidity: {data.get('relative_humidity')}%")
        return data
    except Exception as e:
        print(f"❌ AHT20 collection failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_sensor_data_flow():
    """Test complete sensor data flow like in lora_token function"""
    print("\n🔄 Testing Complete Sensor Data Flow")
    print("=" * 50)
    
    # Simulate the data collection from lora_token function
    data = {}
    
    # Get IMU data
    try:
        from tools.bno055_imu import get_orientation
        imu_data = get_orientation()
        data.update(imu_data)
        print(f"✅ IMU data: {imu_data}")
    except Exception as e:
        print(f"⚠️ IMU data failed: {e}")
    
    # Get AHT20 data
    try:
        from tools.aht20_temperature import get_aht20
        aht20_data = get_aht20()
        data.update(aht20_data)
        print(f"✅ AHT20 data: {aht20_data}")
    except Exception as e:
        print(f"⚠️ AHT20 data failed: {e}")
    
    # Get GPS data
    try:
        from tools.get_gps import get_lat_lon_alt
        gps_data = get_lat_lon_alt()
        if gps_data:
            data.update(gps_data)
            print(f"✅ GPS data: {gps_data}")
        else:
            print("⚠️ GPS data: None")
    except Exception as e:
        print(f"⚠️ GPS data failed: {e}")
    
    # Add runtime parameters
    try:
        from tools.lora_runtime_integration import get_parameter
        data.update({
            'emergency_status': 0,
            'status_area_threshold': get_parameter('area_threshold', 10),
            'stage_threshold': get_parameter('stage_threshold', 50),
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'emergency_frequency': get_parameter('emergency_frequency', 5),
            'neighborhood_emergency_frequency': get_parameter('neighborhood_emergency_frequency', 30)
        })
        print(f"✅ Runtime parameters added")
    except Exception as e:
        print(f"⚠️ Runtime parameters failed: {e}")
    
    print(f"\n📊 Complete sensor data: {data}")
    print(f"   Total fields: {len(data)}")
    print(f"   Data types: {[(k, type(v).__name__) for k, v in data.items()]}")
    
    return data

def test_encoding(data):
    """Test data encoding"""
    print("\n🔧 Testing Data Encoding")
    print("=" * 50)
    
    try:
        from tools.lora_handler_concurrent import get_lora_handler
        handler = get_lora_handler()
        
        print(f"📤 Testing encoding with data: {data}")
        encoded = handler.compressed_encoding(data)
        print(f"✅ Encoding successful: {len(encoded)} bytes")
        print(f"   Encoded hex: {encoded.hex()}")
        
        return encoded
    except Exception as e:
        print(f"❌ Encoding failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_transmission(data):
    """Test data transmission"""
    print("\n📡 Testing Data Transmission")
    print("=" * 50)
    
    try:
        from tools.lora_handler_concurrent import get_lora_handler
        handler = get_lora_handler()
        
        print(f"📤 Testing transmission with data: {data}")
        success = handler.queue_transmit(data)
        
        if success:
            print("✅ Data queued for transmission successfully")
            handler.process_transmit_queue()
            print("✅ Transmission queue processed")
            return True
        else:
            print("❌ Failed to queue data for transmission")
            return False
            
    except Exception as e:
        print(f"❌ Transmission failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function"""
    print("🧪 AHT20 Data Flow Test")
    print("=" * 60)
    
    # Test 1: AHT20 collection
    aht20_data = test_aht20_collection()
    if not aht20_data:
        print("❌ AHT20 collection failed - stopping test")
        return False
    
    # Test 2: Complete sensor data flow
    sensor_data = test_sensor_data_flow()
    if not sensor_data:
        print("❌ Sensor data flow failed - stopping test")
        return False
    
    # Test 3: Data encoding
    encoded_data = test_encoding(sensor_data)
    if not encoded_data:
        print("❌ Data encoding failed - stopping test")
        return False
    
    # Test 4: Data transmission
    transmission_success = test_transmission(sensor_data)
    if not transmission_success:
        print("❌ Data transmission failed - stopping test")
        return False
    
    print("\n🎯 Test Summary:")
    print(f"   ✅ AHT20 data collected: {aht20_data}")
    print(f"   ✅ Complete sensor data: {len(sensor_data)} fields")
    print(f"   ✅ Data encoding: {len(encoded_data)} bytes")
    print(f"   ✅ Data transmission: {'SUCCESS' if transmission_success else 'FAILED'}")
    
    # Check if AHT20 data is in the final data
    if 'temperature_celsius' in sensor_data and 'relative_humidity' in sensor_data:
        print(f"   ✅ AHT20 data present in transmission: temp={sensor_data['temperature_celsius']}°C, humidity={sensor_data['relative_humidity']}%")
    else:
        print(f"   ❌ AHT20 data missing from transmission")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
