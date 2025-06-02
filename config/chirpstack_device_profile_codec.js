/**
 * Decode uplink function
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
      default:
        offset = view.byteLength; // Stop on unknown
        break;
    }
  }

  return { data: result };
}


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
