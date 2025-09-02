/**
 * Decode uplink function
 * 
 * Data Format:
 * Channel 00 Type 01: Timestamp (UNIX)
 * Channel 01 Type 04: Emergency status (0/1)
 * Channel 01 Type 05: Health status (0/1) 
 * Channel 01 Type 06: Movement threshold (0/1)
 * Channel 02 Type 01: Battery percent
 * Channel 03 Type 01: Tilt/roll/yaw (3x float32)
 * Channel 04 Type 01: Lat/lon/z coordinates (3x float32)
 * Channel 05 Type 01: Temperature in Celsius (float32)
 * Channel 06 Type 01: Relative humidity percent
 * Channel 07 Type 17: Camera flood detected (0/1)
 * Channel 07 Type 27: Camera flood growing (0/1)
 * Channel 08 Type 18: Flood bitmap compressed binary
 * Channel 09 Type 19: Status area threshold %
 * Channel 09 Type 29: Stage threshold %
 * Channel 09 Type 39: Monitoring frequency (minutes)
 * Channel 09 Type 49: Emergency frequency (minutes)
 * Channel 09 Type 59: Neighborhood emergency frequency (minutes)
 * Channel 0A Type 01: WittyPi temperature (float32, Celsius)
 * Channel 0A Type 02: WittyPi battery voltage (float32, Volts)
 * Channel 0A Type 03: WittyPi internal voltage (float32, Volts)
 * Channel 0B Type 01: Debug status message (JSON string)
 * 
 * @param {object} input
 * @param {number[]} input.bytes Byte array containing the uplink payload, e.g. [255, 230, 255, 0]
 * @param {number} input.fPort Uplink fPort.
 * @param {Record<string, string>} input.variables Object containing the configured device variables.
 * 
 * @returns {{data: object}} Object representing the decoded payload.
 */
function decodeUplink(input) {
  const bytes = input.bytes;
  const view = new DataView(Uint8Array.from(bytes).buffer);
  let offset = 0;

  const result = {};

  while (offset < view.byteLength) {
    const channel = view.getUint8(offset++);
    const type = view.getUint8(offset++);
    const key = `${channel.toString(16)}-${type.toString(16)}`;

    switch (key) {
      case '0-1':
        result.timestamp = view.getUint32(offset, false);
        offset += 4;
        break;
      case '1-4':
        result.emergency_status = view.getUint8(offset++);
        break;
      case '1-5':
        result.health_status = view.getUint8(offset++);
        break;
      case '1-6':
        result.movement_threshold = view.getUint8(offset++);
        break;
      case '2-1':
        result.battery_percent = view.getUint8(offset++);
        break;
      case '3-1':
        result.tilt_roll_yaw = [
          view.getFloat32(offset, false),
          view.getFloat32(offset + 4, false),
          view.getFloat32(offset + 8, false)
        ];
        offset += 12;
        break;
      case '4-1':
        result.lat_lon_z = [
          view.getFloat32(offset, false),
          view.getFloat32(offset + 4, false),
          view.getFloat32(offset + 8, false)
        ];
        offset += 12;
        break;
      case '5-1':
        result.temperature_celsius = view.getFloat32(offset, false);
        offset += 4;
        break;
      case '6-1':
        result.relative_humidity = view.getUint8(offset++);
        break;
      case '7-17':
        result.camera_flood_detected = view.getUint8(offset++);
        break;
      case '7-27':
        result.camera_flood_growing = view.getUint8(offset++);
        break;
      case '8-18': {
        const len = view.getUint16(offset, false);
        offset += 2;
        const bitmap = [];
        for (let i = 0; i < len; i++) bitmap.push(view.getUint8(offset++));
        result.flood_bitmap_compressed = bitmap;
        break;
      }
      case '9-19':
        result.status_area_threshold = view.getUint8(offset++);
        break;
      case '9-29':
        result.stage_threshold = view.getUint8(offset++);
        break;
      case '9-39':
        result.monitoring_frequency = view.getUint16(offset, false);
        offset += 2;
        break;
      case '9-49':
        result.emergency_frequency = view.getUint16(offset, false);
        offset += 2;
        break;
      case '9-59':
        result.neighborhood_emergency_frequency = view.getUint16(offset, false);
        offset += 2;
        break;
      case 'a-1':
        result.wittypi_temperature = view.getFloat32(offset, false);
        offset += 4;
        break;
      case 'a-2':
        result.wittypi_battery_voltage = view.getFloat32(offset, false);
        offset += 4;
        break;
      case 'a-3':
        result.wittypi_internal_voltage = view.getFloat32(offset, false);
        offset += 4;
        break;
      case 'b-1': {
        // Debug status message (JSON string)
        const len = view.getUint16(offset, false);
        offset += 2;
        const debugBytes = [];
        for (let i = 0; i < len; i++) {
          debugBytes.push(view.getUint8(offset++));
        }
        const debugJson = String.fromCharCode(...debugBytes);
        try {
          result.debug_status = JSON.parse(debugJson);
        } catch (e) {
          result.debug_status_raw = debugJson;
          result.debug_status_error = 'Failed to parse JSON';
        }
        break;
      }
      default:
        offset = view.byteLength; // Stop on unknown
        break;
    }
  }

  return { data: result };
}

/**
 * Example decoded output:
 * {
 *   "data": {
 *     "timestamp": 1748892908,
 *     "emergency_status": 0,
 *     "health_status": 1,
 *     "battery_percent": 85,
 *     "temperature_celsius": 23.5,
 *     "relative_humidity": 55,
 *     "wittypi_temperature": 30.625,
 *     "wittypi_battery_voltage": 4.73,
 *     "wittypi_internal_voltage": 5.2
 *   }
 * }
 * 
 * Example debug status output:
 * {
 *   "data": {
 *     "debug_status": {
 *       "ts": "2025-09-02T14:08:43",
 *       "host": "aurora",
 *       "up": 22.4,
 *       "cpu_t": 64.0,
 *       "cpu_p": 8.5,
 *       "mem_p": 56.8,
 *       "disk_p": 100.0,
 *       "load": 2.33,
 *       "em": false,
 *       "tx": true,
 *       "lora": false,
 *       "wp": false
 *     }
 *   }
 * }
 * 
 * Battery Status Guide:
 * - Battery Voltage 4.5V-5.0V: Excellent (90-100%)
 * - Battery Voltage 4.0V-4.5V: Good (70-90%)
 * - Battery Voltage 3.7V-4.0V: Fair (50-70%)
 * - Battery Voltage 3.3V-3.7V: Poor (20-50%)
 * - Battery Voltage <3.3V: Critical (<20%)
 * 
 * Debug Status Field Guide:
 * - ts: Timestamp (ISO format, truncated to seconds)
 * - host: Hostname (truncated to 8 characters)
 * - up: System uptime in hours
 * - cpu_t: CPU temperature in Celsius
 * - cpu_p: CPU usage percentage
 * - mem_p: Memory usage percentage
 * - disk_p: Disk usage percentage
 * - load: System load average (1 minute)
 * - em: Emergency mode status (boolean)
 * - tx: Transmission enabled status (boolean)
 * - lora: LoRa handler availability (boolean)
 * - wp: WittyPi availability (boolean)
 */


/**
 * Encode downlink function.
 * 
 * @param {object} input
 * @param {object} input.data Object representing the payload that must be encoded.
 * @param {Record<string, string>} input.variables Object containing the configured device variables.
 * 
 * @returns {{bytes: number[]}} Byte array containing the downlink payload.
 */
function encodeDownlink(input) {
  return {
    // bytes: [225, 230, 255, 0]
  };
}
