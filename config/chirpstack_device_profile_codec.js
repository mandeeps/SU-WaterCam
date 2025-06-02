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

  const schema = {
    '00-01': { type: 'uint32', name: 'timestamp' },
    '01-04': { type: 'uint32', name: 'emergency_status' },
    '01-05': { type: 'uint32', name: 'health_status' },
    '01-06': { type: 'uint32', name: 'movement_threshold' },
    '02-01': { type: 'uint32', name: 'battery_percent' },
    '03-01': { type: 'float3', name: 'tilt_roll_yaw' },
    '04-01': { type: 'float3', name: 'lat_lon_z' },
    '05-01': { type: 'float',  name: 'temperature_celsius' },
    '06-01': { type: 'uint32', name: 'relative_humidity_percent' },
    '07-17': { type: 'uint32', name: 'camera_flood_detected' },
    '07-27': { type: 'uint32', name: 'camera_flood_growing' },
    '08-18': { type: 'blob',   name: 'flood_bitmap_compressed' },
    '09-19': { type: 'uint32', name: 'status_area_threshold' },
    '09-29': { type: 'uint32', name: 'stage_threshold' },
    '09-39': { type: 'uint32', name: 'monitoring_frequency' },
    '09-49': { type: 'uint32', name: 'emergency_frequency' },
    '09-59': { type: 'uint32', name: 'neighborhood_emergency_frequency' },
  };

  const result = {};

  while (offset < view.byteLength) {
    if (offset + 2 > view.byteLength) break;

    const channel = view.getUint8(offset++);
    const type = view.getUint8(offset++);
    const key = `${channel.toString(16).padStart(2, '0')}-${type.toString(16).padStart(2, '0')}`;
    const def = schema[key];

    if (!def) {
      result[`unknown_${channel}_${type}`] = 'unrecognized field';
      break;
    }

    let value;

    if (def.type === 'uint32') {
      value = view.getUint32(offset, false); offset += 4;
    } else if (def.type === 'float') {
      value = view.getFloat32(offset, false); offset += 4;
    } else if (def.type === 'float3') {
      value = [
        view.getFloat32(offset, false),
        view.getFloat32(offset + 4, false),
        view.getFloat32(offset + 8, false)
      ];
      offset += 12;
    } else if (def.type === 'blob') {
      const len = view.getUint16(offset, false);
      offset += 2;
      const blob = [];
      for (let i = 0; i < len; i++) {
        blob.push(view.getUint8(offset++));
      }
      value = blob;
    }

    // Add both raw key and friendly name
    result[def.name] = value;
    result[key] = value;
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
