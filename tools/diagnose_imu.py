#!/usr/bin/env python3
"""
IMU Diagnostic Script
Diagnoses BNO055 IMU issues and provides calibration guidance
"""

import sys
import time
import board
import adafruit_bno055

def diagnose_imu():
    """Diagnose IMU status and calibration"""
    print("🔍 BNO055 IMU Diagnostic")
    print("=" * 50)
    
    try:
        # Initialize I2C and sensor
        i2c = board.I2C()
        sensor = adafruit_bno055.BNO055_I2C(i2c)
        
        print("✅ BNO055 sensor initialized")
        
        # Check calibration status
        print("\n📊 Calibration Status:")
        cal_status = sensor.calibration_status
        print(f"   System: {cal_status[0]}/3")
        print(f"   Gyro:   {cal_status[1]}/3") 
        print(f"   Accel:  {cal_status[2]}/3")
        print(f"   Mag:    {cal_status[3]}/3")
        
        # Check if sensor is calibrated.
        # accel=3 is unreachable on hardware with antenna-blocked side faces;
        # accel>=2 with gyro=3 and mag=3 is the expected fully-calibrated state.
        sys_ok  = cal_status[0] >= 2
        gyro_ok = cal_status[1] == 3
        accel_ok= cal_status[2] >= 2
        mag_ok  = cal_status[3] == 3
        if gyro_ok and accel_ok and mag_ok:
            print("✅ Sensor is fully calibrated")
        else:
            print("⚠️  Sensor needs calibration!")
            print("\n🔧 Calibration Instructions:")
            print("   1. Place sensor on flat, stable surface")
            print("   2. Rotate sensor slowly in figure-8 pattern")
            print("   3. Move sensor in all directions (pitch, roll, yaw)")
            print("   4. Wait for all calibration values to reach 3/3")
            print("   5. Keep sensor still for 10 seconds after calibration")
        
        # Check current orientation values
        print("\n📐 Current Orientation Values:")
        euler = sensor.euler
        print(f"   Euler angles: {euler}")
        
        if euler and all(abs(x) > 0.001 for x in euler):
            print("✅ Euler angles are non-zero (sensor working)")
        else:
            print("❌ Euler angles are zero or None (sensor not calibrated)")
        
        # Check other sensor values
        print("\n🌡️  Other Sensor Values:")
        print(f"   Temperature: {sensor.temperature}°C")
        print(f"   Acceleration: {sensor.acceleration}")
        print(f"   Gyroscope: {sensor.gyro}")
        print(f"   Magnetometer: {sensor.magnetic}")
        
        # Test get_orientation function
        print("\n🧪 Testing get_orientation() function:")
        from tools.bno055_imu import get_orientation
        orientation_data = get_orientation()
        print(f"   Returned data: {orientation_data}")
        
        if orientation_data.get('tilt_roll_yaw') and all(abs(x) > 0.001 for x in orientation_data['tilt_roll_yaw']):
            print("✅ get_orientation() working correctly")
        else:
            print("❌ get_orientation() returning zeros - calibration needed")
            
        return cal_status
        
    except Exception as e:
        print(f"❌ Error initializing IMU: {e}")
        import traceback
        traceback.print_exc()
        return None

def monitor_calibration():
    """Monitor calibration progress in real-time"""
    print("\n🔄 Monitoring Calibration Progress...")
    print("   Move the sensor in all directions to calibrate")
    print("   Press Ctrl+C to stop monitoring")
    print("=" * 50)
    
    try:
        i2c = board.I2C()
        sensor = adafruit_bno055.BNO055_I2C(i2c)
        
        while True:
            cal_status = sensor.calibration_status
            euler = sensor.euler
            
            print(f"\rCalibration: Sys={cal_status[0]}/3 Gyro={cal_status[1]}/3 Accel={cal_status[2]}/3 Mag={cal_status[3]}/3 | Euler: {euler}", end="")
            
            gyro_ok_  = cal_status[1] == 3
            accel_ok_ = cal_status[2] >= 2
            mag_ok_   = cal_status[3] == 3
            if gyro_ok_ and accel_ok_ and mag_ok_:
                print(f"\n✅ Calibration complete! Final Euler: {euler}")
                break
                
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        print(f"\n\n⏹️  Monitoring stopped")
    except Exception as e:
        print(f"\n❌ Error during monitoring: {e}")

def main():
    """Main diagnostic function"""
    print("BNO055 IMU Diagnostic Tool")
    print("=" * 50)
    
    # Run initial diagnosis
    cal_status = diagnose_imu()
    
    gyro_ok  = cal_status[1] == 3
    accel_ok = cal_status[2] >= 2
    mag_ok   = cal_status[3] == 3
    if cal_status and not (gyro_ok and accel_ok and mag_ok):
        print("\n" + "=" * 50)
        response = input("Would you like to monitor calibration progress? (y/n): ")
        if response.lower() == 'y':
            monitor_calibration()
    
    print("\n🎯 Summary:")
    print("   - If Euler angles are (0,0,0), the sensor needs calibration")
    print("   - Calibration requires moving the sensor in all directions")
    print("   - Once calibrated, the sensor will provide accurate orientation data")
    print("   - The sensor must remain calibrated for accurate readings")

if __name__ == "__main__":
    main()
