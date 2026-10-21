import os

_REPO_ROOT = os.environ.get("WATERCAM_REPO", os.path.dirname(os.path.abspath(__file__)))

from tt_take_photos import flir, take_two_photos

# Import TickTalk decorators
from ticktalkpython.SQ import SQify, GRAPHify, STREAMify

# Import LoRa runtime integration
from tools.lora_runtime_integration import (
    get_runtime_manager, 
    get_parameter, 
    set_parameter, 
    register_callback,
    integrate_with_ticktalk
)

# Import LoRa handler
from tools.lora_handler_concurrent import get_lora_handler

# Import helper functions
from tools.wittypi_control import get_wittypi_status

@SQify
def wittypi_emergency_control(emergency_mode):
    """
    Control WittyPi schedule based on emergency mode status
    - Emergency ON: Clear shutdown schedule to prevent system shutdown
    - Emergency OFF: Regenerate and apply normal schedule

    Thin wrapper around tools.wittypi_control.apply_emergency_schedule() so
    the same logic is callable both as a graph node here and directly (via a
    plain local import) from contexts that cannot resolve a bare reference to
    this SQified function -- see lora_listener()'s on_emergency_mode_changed,
    which must call apply_emergency_schedule() directly for exactly this
    reason (a nested closure cannot resolve a sibling top-level @SQify
    function's name in TickTalk's isolated per-function exec runtime).
    """
    from tools.wittypi_control import apply_emergency_schedule
    return apply_emergency_schedule(emergency_mode)

@SQify
def create_sensor_tracker():
    """
    Create a sensor tracker to monitor value changes and only transmit when threshold is exceeded
    """
    try:
        # Initialize sensor tracker with previous values and change thresholds
        tracker = {
            'previous_values': {},
            'change_threshold': 0.05,  # 5% change threshold
            'transmission_history': {},
            'last_transmission': None
        }
        print("✅ Sensor tracker initialized with 5% change threshold")
        return tracker
    except Exception as e:
        print(f"⚠️ Failed to create sensor tracker: {e}")
        return None

@SQify
def check_sensor_changes(tracker, current_sensor_data):
    """
    Check if sensor values have changed by more than 5% from previous values
    Returns dict of sensors that need transmission
    """
    if not tracker or not current_sensor_data:
        return {}
    
    try:
        sensors_to_transmit = {}
        current_time = 'now'  # In real implementation, use datetime.now()
        
        for sensor_name, current_value in current_sensor_data.items():
            # Skip non-numeric values and special fields
            if not isinstance(current_value, (int, float)) or sensor_name in ['timestamp', 'error']:
                continue
            
            previous_value = tracker['previous_values'].get(sensor_name)
            
            if previous_value is None:
                # First time seeing this sensor - always transmit
                sensors_to_transmit[sensor_name] = {
                    'current_value': current_value,
                    'previous_value': None,
                    'change_percent': 100.0,
                    'reason': 'first_reading'
                }
                tracker['previous_values'][sensor_name] = current_value
            else:
                # Calculate percentage change
                if previous_value != 0:
                    change_percent = abs((current_value - previous_value) / previous_value)
                else:
                    change_percent = 100.0 if current_value != 0 else 0.0
                
                # Check if change exceeds 5% threshold
                if change_percent >= tracker['change_threshold']:
                    sensors_to_transmit[sensor_name] = {
                        'current_value': current_value,
                        'previous_value': previous_value,
                        'change_percent': change_percent * 100,
                        'reason': 'threshold_exceeded'
                    }
                    tracker['previous_values'][sensor_name] = current_value
                else:
                    # Update previous value even if not transmitting
                    tracker['previous_values'][sensor_name] = current_value
        
        # Log transmission decisions if any sensors qualify
        if sensors_to_transmit:
            print(f"📊 Sensors qualifying for transmission (5% threshold):")
            for sensor, details in sensors_to_transmit.items():
                print(f"   {sensor}: {details['previous_value']} → {details['current_value']} "
                      f"({details['change_percent']:.1f}% change)")
        else:
            print(f"📊 No sensors qualify for transmission (all changes < 5%)")
        
        return sensors_to_transmit
        
    except Exception as e:
        print(f"⚠️ Failed to check sensor changes: {e}")
        return {}

@SQify
def update_sensor_tracker(tracker, sensor_data, transmission_result):
    """
    Update sensor tracker with transmission results and maintain history
    """
    if not tracker:
        return
    
    try:
        current_time = 'now'  # In real implementation, use datetime.now()
        
        # Update transmission history
        tracker['last_transmission'] = current_time
        
        # Record which sensors were transmitted
        if transmission_result and 'transmitted_sensors' in transmission_result:
            for sensor_name in transmission_result['transmitted_sensors']:
                if sensor_name not in tracker['transmission_history']:
                    tracker['transmission_history'][sensor_name] = []
                
                tracker['transmission_history'][sensor_name].append({
                    'timestamp': current_time,
                    'value': sensor_data.get(sensor_name, 'unknown'),
                    'change_percent': transmission_result.get('change_percent', {}).get(sensor_name, 0)
                })
        
        print(f"✅ Sensor tracker updated with transmission results")
        
    except Exception as e:
        print(f"⚠️ Failed to update sensor tracker: {e}")
    
    return tracker

@SQify
def lora_token_with_tracker(bitmap, sensor_tracker, dirname):
    """
    Send this capture over LoRa: one sensor packet with the capture time and the
    fields that changed past the threshold since they were last sent, then the
    bitmap. If the mDot isn't joined, both are queued for store-and-forward.
    """

    from tools.lora_handler_concurrent import get_lora_handler, get_config_value, encode_compact_packet, COMPACT_PACKET_SIZE
    from tools import lora_store_forward as _lsf

    from tools.bno055_imu import get_orientation
    from tools.aht20_temperature import get_aht20
    from tools.get_gps import get_location_with_retry
    from tools.lora_runtime_integration import get_parameter
    # Helper functions are now imported at module level

    # LoRa transmission is always enabled
    # The capture time is the capture's ID on every transport (tools/capture_time.py).
    from tools.capture_time import capture_time, clock_unverified
    captured_at = capture_time(dirname)

    # Get sensor data
    try:
        data = get_orientation()
        data.update(get_aht20())
    except Exception as e:
        print(f"⚠️ Failed to get sensor data: {e}")
        data = {}
    
    # Always attempt to collect GPS data
    try:
        gps, packet = get_location_with_retry()
        if gps:
            data.update(gps)
            # The LoRa packet builder sends the position as lat_lon_z (04 01); the
            # tracker passes it on only when it moved 25 m (tools/sensor_changes.py)
            data['lat_lon_z'] = [gps['gps_lat'], gps['gps_lon'], gps.get('gps_alt') or 0.0]
    except Exception as e:
        print(f"⚠️ Failed to get GPS data: {e}")
        gps = {}
    # Moved from the mount position? (tools/gps_home.py) While it is, every packet
    # carries the alert and the best position available.
    try:
        from tools.gps_home import movement_fields
        move_fields, _ = movement_fields(gps)
        data.update(move_fields)
    except Exception as e:
        print(f"⚠️ GPS movement check failed: {e}")
    
    # Get WittyPi temperature and voltage diagnostics
    wittypi_data = None
    try:
        from tools.wittypi_control import get_wittypi_status
        wittypi_data = get_wittypi_status()
        if wittypi_data.get('status') == 'wittypi_data_retrieved':
            data.update({
                'wittypi_temperature': wittypi_data.get('temperature', 0.0),
                'wittypi_internal_voltage': wittypi_data.get('internal_voltage', 0.0),
                # Witty Pi input (the V50's 5 V): only the compact packet carries it
                'wittypi_vin': wittypi_data.get('battery_voltage')
            })
            print(f"WittyPi data: temp={wittypi_data.get('temperature', 0.0)}°C, internal={wittypi_data.get('internal_voltage', 0.0)}V")
        else:
            print(f"⚠️ WittyPi data unavailable: {wittypi_data.get('status', 'unknown')}")
    except Exception as e:
        print(f"⚠️ Failed to get WittyPi data: {e}")

    # Get battery state-of-charge from battery_manager (path varies by hardware)
    try:
        from tools.battery_manager import get_battery_status
        batt_status = get_battery_status(wittypi_data=wittypi_data)
        data['battery_percent'] = batt_status['battery_pct']
        data['battery_source'] = batt_status['battery_source']  # device-side diagnostic; not transmitted
        pct = batt_status['battery_pct']
        src = batt_status['battery_source']
        print(f"Battery: {pct}% ({src})" if pct is not None else f"Battery: unavailable ({src})")
    except Exception as e:
        print(f"⚠️ Failed to get battery status: {e}")
        data['battery_percent'] = None
        data['battery_source'] = 'unavailable'

    # Pi-side under-voltage/throttle flags -- the only supply reading taken on the
    # Pi side of the WittyPi and GPIO extension header (see tools/pi_power.py)
    from tools.pi_power import get_throttled
    data['pi_throttled'] = get_throttled()
    if data['pi_throttled'] is not None:
        print(f"Pi throttled: {data['pi_throttled']:#x}")
    else:
        print("⚠️ Pi throttled flags unavailable")

    # This capture's registration orientation check (0 ok, 1 shifted, ...); absent
    # when co-registration or the check failed, so the server keeps its last value.
    from tools.registration_orientation import status_code_for_capture
    registration_status = status_code_for_capture(dirname)
    if registration_status is not None:
        data['registration_status'] = registration_status

    # Water % of this capture's mask, and the flood flags when detection is on
    # (tools/flood_baseline.py); absent when there is no mask.
    try:
        from tools.flood_baseline import flood_fields
        data.update(flood_fields(dirname))
    except Exception as e:
        print(f"⚠️ Flood check failed: {e}")

    # Node software release and loaded model (tools/node_version.py); sent when
    # they change and in the periodic full send.
    from tools.node_version import uplink_fields
    data.update(uplink_fields())
    # Entries the store-and-forward queues dropped unsent (tools/drop_counter.py) and
    # whether this capture predates a verified clock (tools/capture_time.py).
    try:
        from tools import drop_counter
        data['sf_dropped'] = drop_counter.total()
        data['clock_unverified'] = 1 if clock_unverified(dirname) else 0
    except Exception as e:
        print(f"⚠️ Failed to read drop counter / clock flag: {e}")

    # Add runtime parameters to sensor data
    emergency_mode = get_parameter('emergency_mode', False)
    area_threshold = get_parameter('area_threshold', 10)
    stage_threshold = get_parameter('stage_threshold', 50)
    
    data.update({
        'emergency_status': 1 if emergency_mode else 0,
        'status_area_threshold': area_threshold,
        'stage_threshold': stage_threshold,
        'monitoring_frequency': get_parameter('monitoring_frequency', 60),
        'emergency_frequency': get_parameter('emergency_frequency', 5),
        'neighborhood_emergency_frequency': get_parameter('neighborhood_emergency_frequency', 30),
        'audio_recording_enabled': 1 if get_parameter('audio_recording_enabled', False) else 0,
    })

    # Embed capture timestamp so stored packets carry original collection time
    if 'timestamp' not in data:
        data['timestamp'] = int(captured_at)

    # The bitmap goes out over LoRa only when the scene changed, a flood is on,
    # or it is due for a refresh (tools/bitmap_policy.py); IP sends every one.
    send_bitmap = bool(bitmap)
    from tools import bitmap_policy, bitmap_fit
    # The capture's mask: a queued bitmap is compressed again from it when it
    # doesn't fit the room later (tools/bitmap_fit.py)
    try:
        from tools.flood_baseline import mask_path as _mask_path_for
        _mask = _mask_path_for(dirname) if dirname else None
    except Exception:
        _mask = None
    if bitmap:
        try:
            send_bitmap, bitmap_reason = bitmap_policy.lora_decision(data, captured_at, get_parameter)
        except Exception as e:
            send_bitmap, bitmap_reason = True, f"policy check failed: {e}"
        print(f"🗺️ LoRa bitmap: {'send' if send_bitmap else 'skip'} ({bitmap_reason})")

    # Check sensor changes using tracker if available (unless always_transmit_sensors is set)
    always_transmit_sensors = get_parameter('always_transmit_sensors', False)
    if always_transmit_sensors:
        print(f"📡 always_transmit_sensors=True: bypassing sensor change check")
    sensors_to_transmit = {}
    full_send = False
    if sensor_tracker and not always_transmit_sensors:
        try:
            # Compared with the values last sent, kept on disk: the tracker token
            # is a fresh copy every capture, and the Pi powers off between wakes.
            # Every lora_full_send_hours, all fields go out (LoRa is unconfirmed).
            from tools.sensor_changes import changed_fields, full_send_due
            full_send = full_send_due(get_parameter('lora_full_send_hours', 24))
            if full_send:
                print("📡 Periodic full send: all sensor fields this capture")
            sensors_to_transmit = changed_fields(
                data, threshold=sensor_tracker.get('change_threshold', 0.05), everything=full_send,
                angle_threshold_deg=get_parameter('lora_imu_threshold_deg', 2.0))
            print(f"📊 Sensor change check: {len(sensors_to_transmit)} sensors qualify for transmission")
        except Exception as e:
            print(f"⚠️ Failed to check sensor changes: {e}")
            sensors_to_transmit = {k: {'current_value': v, 'reason': 'tracker_failed'} for k, v in data.items()}

    if data.get('movement_threshold'):
        # A moved unit sends the alert and its position every time, whatever the tracker says
        for k in ('movement_threshold', 'lat_lon_z'):
            if k in data:
                sensors_to_transmit[k] = {'current_value': data[k], 'reason': 'moved from mount'}
    if data.get('camera_flood_detected') or data.get('camera_flood_growing'):
        # Likewise a flooded unit sends its flood flags and water % every time
        for k in ('camera_flood_detected', 'camera_flood_growing', 'camera_water_pct'):
            if k in data:
                sensors_to_transmit[k] = {'current_value': data[k], 'reason': 'flood'}

    daemon_down = None
    try:
        handler = get_lora_handler()
    except Exception as e:
        handler = None
        daemon_down = f"failed to get LoRa handler: {e}"
    if handler is None and daemon_down is None:
        # get_lora_handler() returns None when the LoRa daemon's socket is missing,
        # i.e. the daemon is not running; its contract requires callers to check
        # before dereferencing.
        from tools.lora_handler_concurrent import lora_unavailable_reason
        daemon_down = lora_unavailable_reason()

    # Store-and-forward: if the daemon is down or the mDot is not joined, queue
    # the payloads and skip transmission. The mDot rejoins autonomously (and
    # systemd restarts the daemon); a later cycle drains the backlog (trickle:
    # 2 packets/cycle) after sending its current data first.
    _sf_enabled = get_config_value('lora_sf_enabled', True)
    if daemon_down is None and _sf_enabled:
        try:
            _not_joined = not handler.is_joined()
        except Exception as e:
            print(f"⚠️ LoRa join state unknown ({e}): treating as not joined")
            _not_joined = True
    else:
        _not_joined = False
    if daemon_down is not None and not _sf_enabled:
        print(f"⚠️ LoRa unavailable ({daemon_down}) and store-and-forward is off: nothing sent this cycle")
        return bitmap
    if daemon_down is not None or _not_joined:
        # The packet encoding is pure, so it works without the daemon
        from tools.lora_handler_concurrent import _encode_compressed_packet
        _encode = handler.compressed_encoding if handler is not None else _encode_compressed_packet
        _sensor_hex = None
        _bitmap_hex = None
        try:
            # Always include 'timestamp' even when tracker filtering is active —
            # the sensor tracker explicitly excludes it from sensors_to_transmit,
            # but it must be in every stored packet so the gateway sees capture time.
            _sf_data = ({k: v for k, v in data.items()
                         if k in sensors_to_transmit or k == 'timestamp'}
                        if (sensor_tracker and sensors_to_transmit) else data)
            if always_transmit_sensors or not sensor_tracker or sensors_to_transmit:
                _sensor_hex = _encode(_sf_data).hex()
        except Exception as _e:
            print(f"⚠️ S&F: sensor encode failed: {_e}")
        try:
            # only a bitmap the policy would send now (tools/bitmap_policy.py)
            if send_bitmap:
                _bitmap_hex = _encode({'flood_bitmap_compressed': bitmap}).hex()
        except Exception as _e:
            print(f"⚠️ S&F: bitmap encode failed: {_e}")
        # A queued bitmap replaces older queued ones; the bitmap policy records
        # it only once it is transmitted (drain's on_bitmap_sent)
        _queued = (_sensor_hex or _bitmap_hex) and _lsf.enqueue(
            _sensor_hex, _bitmap_hex, captured_at,
            max_depth=get_config_value('lora_sf_max_depth', 96),
            max_age_days=get_config_value('lora_sf_max_age_days', 7),
            mask_path=_mask, bitmap_policy=bitmap_policy.queue_fields(data, dirname),
        )
        if _queued:
            if _sensor_hex:
                from tools.sensor_changes import mark_sent
                mark_sent(_sf_data, full=full_send)
        if daemon_down is not None:
            # Queued, not dropped: counted apart from sf_dropped (tools/drop_counter.py)
            from tools import drop_counter
            drop_counter.record_event('lora_daemon_unavailable')
            print(f"⚠️ LoRa unavailable ({daemon_down}) — data queued (depth: {_lsf.queue_depth()})")
        else:
            print(f"📦 LoRa not joined — data queued (depth: {_lsf.queue_depth()})")
        return bitmap

    # Transmit sensor data if: no tracker, always_transmit_sensors, or changes detected
    transmission_result = {'transmitted_sensors': [], 'change_percent': {}}

    # The mDot's payload limit right now: after a rejoin it is back at DR0 (11 B)
    # until ADR raises it, and the stored value lags. A packet that doesn't fit,
    # or fails, waits in the store-and-forward queue (drained below only once it
    # fits, so the gateway never sees sends that can't work), and a sensor
    # packet that doesn't fit is replaced by the 11-byte compact one.
    try:
        size_limit = handler.size_limit_now()
    except Exception as e:
        print(f"⚠️ Payload limit unknown ({e}): using the stored one")
        size_limit = handler.get_size_limit()
    print(f"📏 LoRa payload limit now: {size_limit} B")
    deferred = {}
    sensor_sent = False

    if always_transmit_sensors or not sensor_tracker or sensors_to_transmit:
        try:
            # Filter data to only include sensors that changed significantly
            if sensor_tracker and sensors_to_transmit:
                # The capture time goes in every sensor packet: it is the capture's ID.
                filtered_data = {k: v for k, v in data.items()
                                 if k in sensors_to_transmit or k == 'timestamp'}
                transmission_result['transmitted_sensors'] = list(sensors_to_transmit.keys())
                transmission_result['change_percent'] = {k: v.get('change_percent', 0) for k, v in sensors_to_transmit.items()}
                print(f"📡 Transmitting {len(filtered_data)} sensors with significant changes")
            else:
                filtered_data = data
                transmission_result['transmitted_sensors'] = list(data.keys())
                print(f"📡 Transmitting all sensor data (no tracker, always_transmit_sensors, or all sensors qualify)")
            
            sensor_packet = handler.compressed_encoding(filtered_data)
            if len(sensor_packet) <= size_limit:
                sensor_sent = handler.transmit(sensor_packet.hex())
                if not sensor_sent:
                    print("⚠️ Sensor packet not sent: queued for a later cycle")
            else:
                print(f"📏 Sensor packet {len(sensor_packet)} B > {size_limit} B limit: queued for later")
            if not sensor_sent:
                deferred['sensor_hex'] = sensor_packet.hex()
            # Sent or queued: either way the tracker's baseline moves on
            from tools.sensor_changes import mark_sent
            mark_sent(filtered_data, full=full_send)
            
        except Exception as e:
            print(f"⚠️ Failed to transmit sensor data: {e}")
    else:
        print(f"📊 No sensor changes detected - skipping sensor data transmission")

    # The full sensor packet didn't go out: send the key readings in 11 bytes
    # now. Before the bitmap, which the API pairs with the packet just before it.
    if 'sensor_hex' in deferred and size_limit >= COMPACT_PACKET_SIZE:
        try:
            compact = encode_compact_packet(data)
            if handler.transmit(compact.hex()):
                print(f"📡 Compact packet sent ({len(compact)} B): {compact.hex()}")
            else:
                print("⚠️ Compact packet not sent either")
        except Exception as e:
            print(f"⚠️ Compact packet failed: {e}")

    if not bitmap:
        print("[lora_token_with_tracker] No bitmap data to transmit")
    elif send_bitmap:
        try:
            # TLV-wrap as channel 0x08 type 0x18 so ChirpStack codec can decode it.
            # compressed_encoding({'flood_bitmap_compressed': bitmap}) produces:
            #   08 18 [len 2B BE] [bitmap bytes]
            # No capture time, to keep every byte for the bitmap: it goes out right
            # after the sensor packet (which carries the capture time), and the
            # API pairs the two by LoRaWAN frame counter.
            bitmap_packet = handler.compressed_encoding({'flood_bitmap_compressed': bitmap})
            _method2 = bitmap_fit.method2_allowed(get_parameter)
            if len(bitmap_packet) > size_limit and bitmap_fit.packet_budget(size_limit):
                # Compressed for a larger room (the stored limit lags, or a MAC
                # answer takes a few bytes now): compress again for the room now
                _refit = bitmap_fit.refit_packet(_mask, size_limit, handler.compressed_encoding, _method2)
                if _refit:
                    print(f"📏 Bitmap packet {len(bitmap_packet)} B > {size_limit} B limit: "
                          f"compressed again to {len(_refit)} B")
                    bitmap_packet = _refit
            if len(bitmap_packet) <= size_limit:
                print(f"[lora_token_with_tracker] Bitmap: {len(bitmap_packet) - bitmap_fit.TLV_OVERHEAD}B → TLV 08 18")
                if handler.transmit(bitmap_packet.hex()):
                    bitmap_policy.mark_sent(data, dirname, now=captured_at)
                    # Older bitmaps still queued would only show an older scene
                    _lsf.supersede_bitmaps(captured_at)
                else:
                    print("⚠️ Bitmap not sent: queued for a later cycle")
                    deferred['bitmap_hex'] = bitmap_packet.hex()
            else:
                # No room for any bitmap now (DR0): queue one with headroom for
                # MAC answers, since it goes out later at an unknown room
                _queue_max = bitmap_fit.queue_budget() + bitmap_fit.TLV_OVERHEAD
                if len(bitmap_packet) > _queue_max:
                    _small = bitmap_fit.compress_mask(_mask, bitmap_fit.queue_budget(), _method2)
                    if _small:
                        bitmap_packet = handler.compressed_encoding({'flood_bitmap_compressed': _small})
                print(f"📏 Bitmap packet {len(bitmap_packet)} B > {size_limit} B limit: queued for later")
                deferred['bitmap_hex'] = bitmap_packet.hex()
        except Exception as e:
            print(f"⚠️ Failed to transmit bitmap: {e}")

    # Whatever didn't go out waits for a cycle with room (and isn't lost)
    if deferred and _sf_enabled:
        # A queued bitmap replaces older queued ones; the bitmap policy records
        # it only once it is transmitted (drain's on_bitmap_sent below)
        if _lsf.enqueue(deferred.get('sensor_hex'), deferred.get('bitmap_hex'), captured_at,
                        max_depth=get_config_value('lora_sf_max_depth', 96),
                        max_age_days=get_config_value('lora_sf_max_age_days', 7),
                        mask_path=_mask, bitmap_policy=bitmap_policy.queue_fields(data, dirname)):
            print(f"📦 Queued {', '.join(k[:-4] for k in deferred)} for later (depth: {_lsf.queue_depth()})")


    # Store-and-forward: trickle-drain backlog after current data is sent
    if _sf_enabled:
        try:
            _drain = _lsf.drain(
                handler,
                max_packets=get_config_value('lora_sf_drain_per_cycle', 2),
                max_age_days=get_config_value('lora_sf_max_age_days', 7),
                size_limit=size_limit,
                # a queued bitmap too large for the room now is compressed again
                # from its capture's mask; one that goes out is recorded as sent
                refit=lambda rec, lim: bitmap_fit.refit_packet(
                    rec.get('mask_path'), lim, handler.compressed_encoding,
                    bitmap_fit.method2_allowed(get_parameter)),
                on_bitmap_sent=bitmap_policy.mark_sent_queued,
            )
            if _drain['drained']:
                print(f"📤 S&F: drained {_drain['drained']} queued packet(s)")
            if _drain['failed']:
                print(f"⚠️ S&F: drain stopped early (transmission failure)")
            if _drain.get('deferred'):
                print(f"📦 S&F: {_lsf.queue_depth()} queued, waiting for a payload limit above {size_limit} B")
        except Exception as _e:
            print(f"⚠️ S&F: drain error: {_e}")

    # Update sensor tracker with transmission results
    if sensor_tracker:
        try:
            # Call the function directly to avoid scope issues
            if not sensor_tracker:
                return bitmap
            
            current_time = 'now'  # In real implementation, use datetime.now()
            
            # Update transmission history
            sensor_tracker['last_transmission'] = current_time
            
            # Record which sensors were transmitted
            if transmission_result and 'transmitted_sensors' in transmission_result:
                for sensor_name in transmission_result['transmitted_sensors']:
                    if sensor_name not in sensor_tracker['transmission_history']:
                        sensor_tracker['transmission_history'][sensor_name] = []
                    
                    sensor_tracker['transmission_history'][sensor_name].append({
                        'timestamp': current_time,
                        'value': data.get(sensor_name, 'unknown'),
                        'change_percent': transmission_result.get('change_percent', {}).get(sensor_name, 0)
                    })
            
            print(f"✅ Sensor tracker updated with transmission results")
            
        except Exception as e:
            print(f"⚠️ Failed to update sensor tracker: {e}")
    
    return bitmap

@SQify
def create_workflow_data(monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result):
    """
    Create a structured workflow data object to reduce parameter complexity
    """
    from datetime import datetime
    try:
        return {
            'monitoring_params': monitoring_params,
            'dirname': dirname,
            'photo': photo,
            'lepton_file': lepton_file,
            'coreg_state': coreg_state,
            'seg_result': seg_result,
            'bitmap': bitmap,
            'lora_return': lora_return,
            'shutdown_result': shutdown_result,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Failed to create workflow data: {e}")
        return {'error': str(e)}

# get_wittypi_status moved to tools/wittypi_control.py

@SQify
def get_wittypi_schedule_config():
    """
    Get WittyPi schedule configuration from runtime parameters
    """
    try:
        from tools.lora_runtime_integration import get_parameter
        
        # Get schedule parameters with sensible defaults
        start_hour = get_parameter('wittypi_start_hour', 8)
        start_minute = get_parameter('wittypi_start_minute', 0)
        interval_length_minutes = get_parameter('wittypi_interval_minutes', 30)
        num_repetitions_per_day = get_parameter('wittypi_repetitions_per_day', 8)
        
        return {
            'start_hour': start_hour,
            'start_minute': start_minute,
            'interval_length_minutes': interval_length_minutes,
            'num_repetitions_per_day': num_repetitions_per_day
        }
        
    except Exception as e:
        # Return default values if runtime parameters fail
        return {
            'start_hour': 8,
            'start_minute': 0,
            'interval_length_minutes': 30,
            'num_repetitions_per_day': 8
        }

@SQify
def manual_wittypi_control(action, **kwargs):
    """
    Manual WittyPi control for testing and direct control
    Actions: 'clear_schedule', 'set_schedule', 'get_status', 'sync_time'
    """
    try:
        from tools.wittypi_control import clear_shutdown_time, set_schedule, get_data, sync_time
        
        if action == 'clear_schedule':
            clear_shutdown_time()
            return {
                'status': 'schedule_cleared',
                'action': 'clear_schedule',
                'message': 'WittyPi shutdown schedule cleared'
            }
            
        elif action == 'set_schedule':
            # Extract schedule parameters
            start_hour = kwargs.get('start_hour', 8)
            start_minute = kwargs.get('start_minute', 0)
            interval_length_minutes = kwargs.get('interval_length_minutes', 30)
            num_repetitions_per_day = kwargs.get('num_repetitions_per_day', 8)
            
            next_startup_time = set_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day)
            
            return {
                'status': 'schedule_set',
                'action': 'set_schedule',
                'next_startup': next_startup_time,
                'schedule': {
                    'start_hour': start_hour,
                    'start_minute': start_minute,
                    'interval_minutes': interval_length_minutes,
                    'repetitions_per_day': num_repetitions_per_day
                },
                'message': f'Schedule set, next startup: {next_startup_time}'
            }
            
        elif action == 'get_status':
            temperature, battery_voltage, internal_voltage, internal_current = get_data()
            return {
                'status': 'data_retrieved',
                'action': 'get_status',
                'temperature': temperature,
                'battery_voltage': battery_voltage,
                'internal_voltage': internal_voltage,
                'internal_current': internal_current
            }
            
        elif action == 'sync_time':
            sync_time()
            return {
                'status': 'time_synced',
                'action': 'sync_time',
                'message': 'WittyPi time synchronized with network'
            }
            
        else:
            return {
                'status': 'invalid_action',
                'error': f'Unknown action: {action}',
                'valid_actions': ['clear_schedule', 'set_schedule', 'get_status', 'sync_time']
            }
            
    except ImportError as e:
        return {
            'status': 'wittypi_unavailable',
            'error': f'Import error: {str(e)}',
            'message': 'WittyPi control functions not available'
        }
    except Exception as e:
        return {
            'status': 'control_failed',
            'error': str(e),
            'message': f'Failed to execute action: {action}'
        }

@SQify
def validate_configuration(trigger):
    """
    Validate that all required configuration parameters are properly set
    """
    from tools.lora_runtime_integration import get_parameter
    
    required_params = {
        'area_threshold': (10, 'default flood detection threshold'),
        'stage_threshold': (50, 'default stage height threshold'),
        'monitoring_frequency': (60, 'default monitoring interval'),
        'emergency_frequency': (5, 'default emergency monitoring interval'),
        # transmission_enabled and gps_enabled removed (always-on policy)
        
        'shutdown_iteration_limit': (3, 'default shutdown limit'),
        'auto_shutdown_enabled': (True, 'default auto-shutdown state')
    }
    
    validation_results = {}
    for param, (default_value, description) in required_params.items():
        try:
            value = get_parameter(param, default_value)
            validation_results[param] = {
                'value': value,
                'status': 'valid',
                'description': description
            }
        except Exception as e:
            validation_results[param] = {
                'value': default_value,
                'status': 'error',
                'error': str(e),
                'description': description
            }
    
    try:
        print("🔧 Configuration validation completed")
        return validation_results
    except Exception as e:
        print(f"⚠️ Failed to complete configuration validation: {e}")
        return {'validation_error': str(e)}

@SQify
def initialize_lora_integration(trigger):
    """
    Initialize LoRa runtime integration within TickTalk framework
    """
    from tools.lora_runtime_integration import integrate_with_ticktalk, get_runtime_manager
    
    try:
        integrate_with_ticktalk()
        runtime_manager = get_runtime_manager()
        print("✓ LoRa runtime integration initialized")
        return {'status': 'success', 'runtime_manager': 'initialized'}
    except ImportError as e:
        print(f"⚠️ LoRa runtime integration import error: {e}")
        return {'status': 'failed', 'error': f'Import error: {str(e)}'}
    except Exception as e:
        print(f"⚠️ LoRa runtime integration failed: {e}")
        return {'status': 'failed', 'error': str(e)}

@STREAMify
def get_time(trigger):
    from datetime import datetime
    from os import path, makedirs, environ
    repo_root = environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
    try:
        from tools.capture_time import write_capture_time
        now = datetime.now()
        directory = path.join(repo_root, "images", now.strftime('%Y%m%d-%H%M%S'))
        if not path.exists(directory):
            makedirs(directory)
        write_capture_time(directory, now.timestamp())
        return directory
    except Exception as e:
        print(f"Failed to create directory: {e}")
        return path.join(repo_root, "images", "fallback")

@SQify
def record_audio(trigger, directory):
    """Start this iteration's audio segment from the USB microphone, if enabled.

    One WAV segment per iteration (same directory photos/thermal go into),
    so a hard WittyPi power-cut loses at most the in-progress segment rather
    than the whole wake window. See tools/audio_recorder.py.

    Also makes sure USB is on: usb-power-off.service cuts it early in boot and
    usb-power-on.timer restores it 8 s after boot; this covers a timer that
    didn't run. See tools/usb_power.py.
    """
    from tools.audio_recorder import get_audio_recorder
    from tools.lora_runtime_integration import get_parameter
    from tools.usb_power import usb_power_on, wait_for_usb_mic

    usb_power_on()
    # Photos: past data_retention_days (0, the default, keeps them), or the oldest
    # when the card is 80 % full
    # (tools/data_retention.py). The capture starting now is left alone.
    try:
        from tools.data_retention import run as photo_retention
        photo_retention(current_dir=directory)
    except Exception as e:
        print(f"⚠️ Photo retention failed: {e}")
    if not get_parameter('audio_recording_enabled', False):
        return "disabled"
    wait_for_usb_mic()

    retention_days = get_parameter('data_retention_days', 0)
    recorder = get_audio_recorder(retention_days=retention_days)
    if recorder.start_segment(directory):
        return "recording"
    return "unavailable"

@SQify
def coregistration(dirname, lepton_state, photo_state):
    from tools.coreg_multiple import coreg
    from tools.lock_utils import power_exclusive
    from tools.lock_utils import flock_deadline
    print(f"\n running coreg on {dirname}\n")
    try:
        # a hung previous holder must not stall the graph (and shutdown) forever
        with flock_deadline("/tmp/watercam_coreg.lock", 300), \
                power_exclusive("coregistration", 120):
            filepath = coreg(dirname)
        print(f"\n {filepath} images registered \n")
    except Exception as e:
        print(f"⚠️ Failed to run coregistration: {e}")
        return False
    # Flag, don't re-solve: coreg() keeps using the cached transform even after the
    # node has turned. tools/registration_orientation.py explains why.
    try:
        from tools.bno055_imu import get_pose
        from tools.lora_runtime_integration import get_parameter
        from tools.registration_orientation import check, describe, write_capture_flag
        result = check(
            dirname, get_pose(),
            tilt_threshold_deg=get_parameter('coreg_tilt_threshold_deg', 5.0),
            rotation_threshold_deg=get_parameter('coreg_rotation_threshold_deg', 15.0))
        write_capture_flag(dirname, result)
        print(describe(result))
    except Exception as e:
        print(f"⚠️ Registration orientation check failed: {e}")
    return True

@SQify
def segformer(filepath, coreg_state): # operate on coregistered image file
    import os
    import subprocess

    from tools.coreg_multiple import config as coreg_config
    from tools.lock_utils import flock_deadline, power_exclusive
    from tools.segformer_client import segformer_via_daemon

    # The fallback cold-loads the model (~40 s); give it room but not forever, since
    # every later step, call_shutdown included, waits for this one.
    FALLBACK_TIMEOUT_S = 240

    # Serve color_preserved_5_band.tiff, not final_5_band.tiff. The two files
    # are not the same image: co-registration writes final_5_band in OpenCV's
    # native BGR at a squashed 512x512, while color_preserved reverses the
    # optical channels to true RGB and keeps the native 4:3 frame. The annotator
    # trains on color_preserved (see photo_processing/export_dataset.py), so
    # serving final_5_band fed the model swapped red and blue at a distorted
    # aspect ratio.
    #
    # The mask is named after the input. It has to be: the subprocess fallback
    # below passes only the TIFF, and segment_tiff_5band.py names its output
    # `<input stem>_segmentation.png`. While this said final_5_band_segmentation
    # the daemon path wrote that name and the fallback wrote
    # color_preserved_5_band_segmentation, then returned the name it had not
    # written.
    tiff_path = filepath + "/" + coreg_config.MODEL_INPUT_TIFF
    output_path = filepath + "/" + coreg_config.SEGMENTATION_PNG
    socket_path = "/run/segformer/segformer.sock"

    if not coreg_state or not os.path.exists(tiff_path):
        # Without a co-registered TIFF both paths below are a slow, certain failure.
        print(f"⚠️ Skipping segmentation: no co-registered image ({tiff_path})")
        return None

    try:
        # Prefer the persistent daemon — no cold-start, model stays loaded.
        # Not while a cellular upload runs: together they pull the Pi under-voltage.
        if os.path.exists(socket_path):
            with power_exclusive("segmentation", 120):
                ok = segformer_via_daemon(tiff_path, output_path, socket_path)
            if ok:
                return output_path
            print("⚠️ Daemon call failed, falling back to subprocess")

        # Legacy fallback: spawn a fresh Python process each cycle.
        segformer_location = os.environ.get("SEGFORMER_DIR", "/home/pi/segformer_5band")
        segformer_python = os.environ.get("SEGFORMER_PYTHON", "/home/pi/miniforge3/envs/5band/bin/python")
        segformer_coreg = os.path.join(segformer_location, "segment_tiff_5band.py")
        with flock_deadline("/tmp/watercam_segformer.lock", FALLBACK_TIMEOUT_S), \
                power_exclusive("segmentation", 120):
            subprocess.run(
                [segformer_python, segformer_coreg, tiff_path],
                cwd=segformer_location, timeout=FALLBACK_TIMEOUT_S,
            )
        return output_path if os.path.exists(output_path) else None
    except Exception as e:
        print(f"⚠️ Failed to run segmentation: {e}")
        return None

@SQify
def call_shutdown(state):
    import sys
    from subprocess import call
    from tools.lora_runtime_integration import get_runtime_manager

    import time

    new_count = 1
    auto_shutdown_enabled = True
    shutdown_limit = 3
    emergency_mode = False
    emergency_since = None
    emergency_max_hours = 24

    try:
        result = get_runtime_manager().atomic_increment_iteration_count()
        new_count = result['iteration_count']
        auto_shutdown_enabled = result['auto_shutdown_enabled']
        shutdown_limit = result['shutdown_iteration_limit']
        emergency_mode = result['emergency_mode']
        emergency_since = result.get('emergency_since')
        emergency_max_hours = result.get('emergency_max_hours', emergency_max_hours)
        print(f"\n Iteration: {new_count} \n")
    except Exception as e:
        print(f"⚠️ Failed to update iteration count: {e}")

    # Emergency mode keeps the unit awake and moves the Witty Pi shutdown alarm
    # to a backstop an hour after its end (tools/wittypi_control.py), so a stray
    # or forgotten command can't run the battery flat. End it after
    # emergency_max_hours (0 = never).
    try:
        expired = (emergency_mode and emergency_since and float(emergency_max_hours) > 0
                   and time.time() - float(emergency_since) > float(emergency_max_hours) * 3600)
    except (TypeError, ValueError):
        expired = False
    if expired:
        print(f"⏱️ Emergency mode has run {emergency_max_hours} h: ending it and restoring the schedule")
        try:
            get_runtime_manager().set_parameter('emergency_mode', False)
        except Exception as e:
            print(f"⚠️ Failed to clear emergency_mode: {e}")
        try:
            # directly, not through the update callback, which only prints in this process
            from tools.wittypi_control import apply_emergency_schedule
            apply_emergency_schedule(False)
        except Exception as e:
            print(f"⚠️ Failed to restore the Witty Pi schedule: {e}")
        emergency_mode = False

    if emergency_mode:
        # Keep the hardware backstop in step with emergency mode's end (a resend
        # restarts it): if this run hangs, the Witty Pi still powers the unit down
        try:
            from tools.wittypi_control import arm_emergency_backstop
            arm_emergency_backstop(emergency_since, emergency_max_hours)
        except Exception as e:
            print(f"⚠️ Could not set the emergency shutdown backstop: {e}")
        print("🚨 EMERGENCY MODE ACTIVE - Ignoring shutdown limit, continuing data collection")
        return "emergency_mode_active"

    print(f"🔍 Shutdown check: count={new_count}, limit={shutdown_limit}, enabled={auto_shutdown_enabled}")

    if auto_shutdown_enabled and new_count >= shutdown_limit:
        # The monitoring work is done and the connection is up: pick up any
        # command sent during the cycle before powering off. One may say not to
        # (emergency on, a remote access session) -- tools/last_downlink_check.py
        try:
            from tools.last_downlink_check import check as last_downlink_check
            last = last_downlink_check()
        except Exception as e:
            print(f"⚠️ Last downlink check failed: {e}")
            last = {}
        if last.get("keep_awake"):
            print(f"📬 Not shutting down: {last.get('why')} (set by the last downlink check)")
            return "kept_awake_by_downlink"
        print(f"\n🚨 SHUTDOWN TRIGGERED: {new_count} iterations >= {shutdown_limit} limit\n")
        try:
            # Finalize the in-progress audio segment so its WAV header is
            # written correctly before the OS goes down.
            from tools.audio_recorder import stop_recording_if_active
            stop_recording_if_active()
        except Exception as e:
            print(f"⚠️ Failed to finalize audio recording before shutdown: {e}")
        # Graceful OS shutdown via doas (configured in /etc/doas.conf for user pi).
        # Check that it worked: if doas is missing or misconfigured, try sudo
        # rather than fall through to a restart loop that captures until the
        # Witty Pi cuts power. runrtm.py keeps the count across restarts within
        # one boot, so a restarted runtime asks for shutdown again after a cycle.
        for cmd in (["doas", "/usr/sbin/shutdown", "-h", "now"],
                    ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"]):
            try:
                rc = call(cmd, timeout=30)
            except Exception as e:
                rc = e
            if rc == 0:
                break
            print(f"⚠️ {' '.join(cmd[:2])} shutdown failed: {rc}")
        else:
            print("🚨 Could not shut down: the Witty Pi will cut power at the end of the window")
            # Exit with an error: systemd restarts the runtime (Restart=on-failure),
            # and the next cycle asks for shutdown again.
            sys.exit("shutdown")
        # The OS is going down: end the runtime with status 0. The execute
        # process re-raises this from the SQ's worker thread and exits 0, and
        # the ensemble (ticktalkpython/Ensemble.py) treats that as a requested
        # stop, so runrtm.py exits without a ProcessError traceback.
        sys.exit(0)
    else:
        print(f"✅ Continuing: count={new_count} < limit={shutdown_limit} or shutdown disabled")

    return "continue"

@SQify
def flir_planb(dummy):
    from tools.lepton_reset_gpiozero import reset
    print("\n reset lepton \n")
    try:
        reset()
    except Exception as e:
        print(f"⚠️ Failed to reset lepton: {e}")

@SQify
def compress_bitmap(segmented_file):
    from tools.compress_segmented import compress_image

    # Query the mDot's stored payload limit (updated by +TXS: responses).
    # Bitmap is sent TLV-wrapped (08 18 [2B len] = 4B overhead) so the
    # bitmap itself may be up to lora_limit - 4 bytes.
    # Fall back to 238 B (SF7/500kHz: 242 − 4).
    #
    # Deliberately uses the FULL reported limit, not the full limit minus
    # margin for LoRaWAN MAC command overhead (FOpts, up to 15B) -- maximizing
    # bitmap resolution is the priority; an occasional "Tx buffer filled by
    # MAC Commands, send data again" from a pending MAC command colliding
    # with a near-limit payload is handled by transmit()'s retry loop
    # thoroughly draining the MAC queue before retrying the same packet
    # (tools/lora_handler_concurrent.py's _flush_mac_commands(), looped up to
    # MAC_FLUSH_MAX_ROUNDS times), not by permanently shrinking every bitmap
    # to reserve headroom that's usually unused. That holds for a bitmap sent
    # now; one that has to be queued keeps headroom (below), and
    # lora_token_with_tracker compresses again for the room at send time.
    max_bitmap_bytes = 238
    try:
        from tools.lora_handler_concurrent import get_size_limit
        lora_limit = get_size_limit()
        max_bitmap_bytes = lora_limit - 4
        print(f"[compress_bitmap] mDot limit {lora_limit}B → max bitmap {max_bitmap_bytes}B (TLV overhead 4B)")
    except Exception as e:
        print(f"[compress_bitmap] Could not query mDot limit ({e}), using {max_bitmap_bytes}B default")

    if max_bitmap_bytes < 32:
        # Too small for any bitmap at this data rate (DR0: 11 B): compress for
        # the store-and-forward queue instead, with headroom for MAC command
        # answers (226 B, tools/bitmap_fit.py), since it goes out later at an
        # unknown room; draining compresses it again if it still doesn't fit.
        from tools.bitmap_fit import queue_budget
        max_bitmap_bytes = queue_budget()
        print(f"[compress_bitmap] mDot limit {lora_limit}B too small for a bitmap now: compressing at {max_bitmap_bytes}B to queue")

    # Method 2 (lossless context model, up to 640 px) is the default; the API and
    # dashboard must decode it (docs/BITMAP_DECODING_SPEC.md). bitmap_method2_enabled
    # false goes back to methods 0/1 up to 160 px.
    method2 = False
    try:
        from tools.lora_runtime_integration import get_parameter
        method2 = get_parameter('bitmap_method2_enabled', True) is not False
    except Exception as e:
        print(f"[compress_bitmap] Could not read bitmap_method2_enabled ({e}): methods 0/1 only")

    print(f"Compressing {segmented_file} for transmission (max {max_bitmap_bytes}B{', method 2 allowed' if method2 else ''})")
    try:
        bitmap_dict = compress_image(segmented_file, max_bytes=max_bitmap_bytes, method2=method2)
        if bitmap_dict.get('error'):
            # Not a binary water mask (tools/compress_segmented.py mask_format_error)
            print(f"⚠️ Not sending {segmented_file}: {bitmap_dict['error']}")
            return b''
        if not bitmap_dict.get('success'):
            print(f"⚠️ Could not compress {segmented_file} to fit within {max_bitmap_bytes}B (SF too high?)")
            return b''
        print(f"Compressed {segmented_file}: {bitmap_dict['total_size']}B at {bitmap_dict['width']}×{bitmap_dict['height']}px")
        return bitmap_dict['compressed_data']
    except Exception as e:
        print(f"⚠️ Failed to compress image: {e}")
        return b''

@SQify
def lora_listener():
    """
    LoRa listener that processes incoming commands and returns current status
    This function is called by TickTalk and handles one iteration of monitoring
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter, get_runtime_manager
    from tools.lora_handler_concurrent import get_lora_handler
    
    # Get the LoRa handler (listening is already started by runtime integration)
    handler = get_lora_handler()
    
    # Register callbacks for parameter changes (only once)
    def on_emergency_mode_changed(value, old_value):
        # Call tools.wittypi_control.apply_emergency_schedule() directly via
        # a local import, NOT the @SQify-wrapped wittypi_emergency_control()
        # graph node -- a nested closure inside an SQified function cannot
        # resolve a bare reference to another top-level SQified function in
        # TickTalk's isolated per-function exec runtime (confirmed via a
        # minimal repro: NameError every time). wittypi_emergency_control(True)
        # here would always raise NameError, silently caught below, meaning
        # this safety-critical WittyPi-schedule side effect never actually
        # ran in production. apply_emergency_schedule is a plain
        # tools/-importable function, which resolves correctly the same way
        # every other local import in this file already does.
        from tools.wittypi_control import apply_emergency_schedule
        if value:
            print("🚨 EMERGENCY MODE ACTIVATED via LoRa command!")
            try:
                wittypi_result = apply_emergency_schedule(True)
                print(f"📋 WittyPi Control: {wittypi_result.get('message', 'Unknown status')}")
            except Exception as e:
                print(f"⚠️ Failed to control WittyPi during emergency: {e}")
        else:
            print("✅ Emergency mode deactivated via LoRa command")
            try:
                wittypi_result = apply_emergency_schedule(False)
                print(f"📋 WittyPi Control: {wittypi_result.get('message', 'Unknown status')}")
            except Exception as e:
                print(f"⚠️ Failed to control WittyPi during emergency deactivation: {e}")
    
    def on_monitoring_frequency_changed(value, old_value):
        print(f"⏰ Monitoring frequency updated via LoRa: {old_value} → {value} minutes")
    
    def on_area_threshold_changed(value, old_value):
        print(f"🌊 Area threshold updated via LoRa: {old_value} → {value}%")
    
    # Get runtime manager and register callbacks
    try:
        runtime_manager = get_runtime_manager()
        runtime_manager.register_update_callback('emergency_mode', on_emergency_mode_changed)
        runtime_manager.register_update_callback('monitoring_frequency', on_monitoring_frequency_changed)
        runtime_manager.register_update_callback('area_threshold', on_area_threshold_changed)
    except Exception as e:
        print(f"Warning: Could not register LoRa callbacks: {e}")
    
    # Get current configuration (automatically updated by incoming messages)
    try:
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        emergency_mode = get_parameter('emergency_mode', False)
        debug_mode = get_parameter('debug_mode', False)
    except Exception as e:
        print(f"⚠️ Failed to get LoRa parameters: {e}")
        # Use default values
        area_threshold = 10
        monitoring_freq = 60
        emergency_mode = False
        debug_mode = False
    
    # Process any queued transmissions
    if handler:
        handler.process_transmit_queue()
    
    # Log current status if in debug mode
    if debug_mode:
        print(f"📡 LoRa Status ({datetime.now().strftime('%H:%M:%S')}): "
              f"Area={area_threshold}%, Freq={monitoring_freq}min, "
              f"Emergency={emergency_mode}, Transmit=True")
    
    # Return current status for TickTalk integration
    return {
        'area_threshold': area_threshold,
        'monitoring_frequency': monitoring_freq,
        'emergency_mode': emergency_mode,
        'transmission_enabled': True,
        'debug_mode': debug_mode,
        'timestamp': datetime.now().isoformat()
    }

@SQify
def adaptive_monitoring():
    """
    Adaptive monitoring function that adjusts behavior based on runtime parameters
    This can be called from the main workflow to check if conditions have changed
    """
    from tools.lora_runtime_integration import get_parameter
    
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        stage_threshold = get_parameter('stage_threshold', 50)
        
        print(f"🔍 Adaptive monitoring: Emergency={emergency_mode}, "
              f"Area={area_threshold}%, Stage={stage_threshold}cm")
        
        # Return monitoring parameters for use in main workflow
        return {
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'stage_threshold': stage_threshold,
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'photo_interval': get_parameter('photo_interval', 60)
        }
    except Exception as e:
        print(f"⚠️ Failed to get adaptive monitoring parameters: {e}")
        # Return default values
        return {
            'emergency_mode': False,
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'photo_interval': 60
        }

@SQify
def emergency_workflow(trigger, monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result):
    """
    Emergency mode workflow with continuous operation and bypassed shutdown
    """
    print("🚨 Emergency mode detected - bypassing shutdown limits, continuing data collection")
    
    # Photos, lepton data, coregistration, segmentation, compression, LoRa transmission, and shutdown check are now passed as parameters from GRAPH level
    
    # LoRa monitoring and parameter management
    from tools.lora_runtime_integration import get_parameter, set_parameter
    
    # Get current parameters directly
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        
        # Force emergency mode parameters during emergency
        if emergency_mode:
            # Set faster monitoring frequency for emergency
            set_parameter('monitoring_frequency', 5)  # 5 minutes instead of 60
            set_parameter('emergency_frequency', 1)   # 1 minute for critical monitoring
            print("🚨 Emergency parameters set: Fast monitoring (5min), continuous transmission")
        
        # Return comprehensive status with emergency indicators
        return {
            'status': 'emergency_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': get_parameter('monitoring_frequency', 5),  # Use updated value
            'emergency_frequency': get_parameter('emergency_frequency', 1),   # Use updated value
            'bitmap_compressed': True,
            'lora_transmitted': True,
            'photos_captured': True,
            'lepton_data_captured': True,
            'shutdown_bypassed': True,
            'emergency_priority': 'high'
        }
    except Exception as e:
        print(f"⚠️ Failed to get emergency workflow parameters: {e}")
        return {
            'status': 'emergency_workflow_error',
            'error': str(e),
            'dirname': dirname
        }

@SQify
def normal_workflow(trigger, workflow_data):
    """
    Normal mode workflow with standard timing
    """
    print("📸 Normal mode - using standard workflow timing")
    
    # Extract data from workflow_data structure
    dirname = workflow_data['dirname']
    monitoring_params = workflow_data['monitoring_params']
    
    # LoRa monitoring and parameter management
    from tools.lora_runtime_integration import get_parameter
    
    # Get current parameters directly
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        
        # Return comprehensive status
        return {
            'status': 'normal_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': monitoring_freq,
            'bitmap_compressed': True,
            'lora_transmitted': True,
            'photos_captured': True,
            'lepton_data_captured': True,
            'workflow_data': workflow_data
        }
    except Exception as e:
        print(f"⚠️ Failed to get normal workflow parameters: {e}")
        return {
            'status': 'normal_workflow_error',
            'error': str(e),
            'dirname': dirname,
            'workflow_data': workflow_data
        }

@SQify
def emergency_status_monitor(trigger):
    """
    Monitor and report emergency status for system visibility
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter
    
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        emergency_freq = get_parameter('emergency_frequency', 5)
        
        if emergency_mode:
            print(f"🚨 EMERGENCY STATUS ({datetime.now().strftime('%H:%M:%S')}):")
            print(f"   - Emergency Mode: ACTIVE")
            print(f"   - Monitoring Frequency: {monitoring_freq} minutes")
            print(f"   - Emergency Frequency: {emergency_freq} minutes")
            print(f"   - Transmission: ENABLED")
            print(f"   - Shutdown: BYPASSED (continuing operation)")
        else:
            print(f"✅ Normal Operation ({datetime.now().strftime('%H:%M:%S')}):")
            print(f"   - Emergency Mode: INACTIVE")
            print(f"   - Monitoring Frequency: {monitoring_freq} minutes")
            print(f"   - Transmission: ENABLED")
        
        return {
            'emergency_mode': emergency_mode,
            'monitoring_frequency': monitoring_freq,
            'emergency_frequency': emergency_freq,
            'timestamp': datetime.now().isoformat(),
            'status': 'emergency_active' if emergency_mode else 'normal_operation'
        }
    except Exception as e:
        print(f"⚠️ Failed to monitor emergency status: {e}")
        return {'error': str(e), 'timestamp': datetime.now().isoformat()}

@SQify
def lora_parameter_monitor(trigger):
    """
    TickTalk SQ function that monitors and processes LoRa parameter updates
    This function runs periodically and checks for parameter changes
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter, sync_lora_parameters
    from tools.lora_handler_concurrent import get_lora_handler
    
    # Sync runtime parameters with LoRa config to ensure consistency
    try:
        sync_lora_parameters()
    except Exception as e:
        print(f"⚠️ Failed to sync LoRa parameters: {e}")
        # Continue with current parameters
    
    # Get current parameters
    try:
        current_params = {
            'emergency_mode': get_parameter('emergency_mode', False),
            'area_threshold': get_parameter('area_threshold', 10),
            'stage_threshold': get_parameter('stage_threshold', 50),
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'emergency_frequency': get_parameter('emergency_frequency', 5),
            'debug_mode': get_parameter('debug_mode', False),
            
            'photo_interval': get_parameter('photo_interval', 60),
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Failed to get LoRa parameters: {e}")
        # Use default values
        current_params = {
            'emergency_mode': False,
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'emergency_frequency': 5,
            'debug_mode': False,
            
            'photo_interval': 60,
            'timestamp': datetime.now().isoformat()
        }
    
    # Process any queued LoRa transmissions
    try:
        handler = get_lora_handler()
        if handler:
            handler.process_transmit_queue()
    except Exception as e:
        print(f"⚠️ Failed to process LoRa transmissions: {e}")
    
    # Log current status if in debug mode
    if current_params['debug_mode']:
        print(f"📡 LoRa Parameter Monitor ({datetime.now().strftime('%H:%M:%S')}): "
              f"Emergency={current_params['emergency_mode']}, "
              f"Area={current_params['area_threshold']}%, "
              f"Transmit=True")
    
    return current_params

@SQify
def lora_emergency_handler(trigger, lora_status):
    """
    Handle emergency mode changes detected via LoRa
    This function is triggered when emergency mode changes
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter
    
    if lora_status and lora_status.get('emergency_mode', False):
        print("🚨 EMERGENCY MODE DETECTED - Taking immediate action!")
        
        # Could trigger immediate photo capture, increased monitoring, etc.
        # For now, just log the emergency state
        try:
            emergency_actions = {
                'emergency_triggered': True,
                'timestamp': datetime.now().isoformat(),
                'actions_taken': ['emergency_logging', 'increased_monitoring'],
                'monitoring_frequency': get_parameter('emergency_frequency', 5)
            }
            return emergency_actions
        except Exception as e:
            print(f"⚠️ Failed to create emergency actions: {e}")
            return {'emergency_triggered': True, 'status': 'error', 'error': str(e)}
    else:
        return {'emergency_triggered': False, 'status': 'normal_operation'}

@SQify
def lora_configuration_manager(trigger, current_config):
    """
    Manage LoRa configuration updates and apply them to the system
    This function handles configuration changes from LoRa commands
    """
    from datetime import datetime
    
    if not current_config:
        return {'status': 'no_config_provided'}
    
    # Check for significant configuration changes
    changes = []
    
    # Monitor for emergency mode changes
    if current_config.get('emergency_mode', False):
        changes.append('emergency_mode_activated')
    
    # Monitor for transmission enable/disable
    # transmission_enabled removed (always-on policy)
    
    # Monitor for debug mode
    if current_config.get('debug_mode', False):
        changes.append('debug_mode_enabled')
    
    # Apply configuration changes to system behavior
    if changes:
        # Could trigger system reconfiguration here
        # For example, adjust monitoring intervals, enable/disable features, etc.
        
        try:
            return {
                'status': 'configuration_updated',
                'changes': changes,
                'timestamp': datetime.now().isoformat(),
                'new_config': current_config
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    
    return {'status': 'no_changes', 'config': current_config}

@SQify
def emergency_mode_logger(trigger, emergency_mode, shutdown_bypassed):
    """
    Log emergency mode status for system visibility
    This function handles logging that cannot be done in GRAPH functions
    """
    if emergency_mode and shutdown_bypassed:
        # This function can be called from the main workflow to log emergency status
        return {
            'status': 'emergency_mode_logged',
            'emergency_mode': True,
            'shutdown_bypassed': True,
            'message': 'EMERGENCY MODE: Continuing data collection and transmission'
        }
    return {
        'status': 'normal_operation',
        'emergency_mode': emergency_mode,
        'shutdown_bypassed': shutdown_bypassed
    }

@SQify
def get_emergency_mode_status(trigger):
    """
    Get emergency mode status - wrapper for get_parameter that can be called from GRAPH functions
    """
    from tools.lora_runtime_integration import get_parameter
    emergency_mode = get_parameter('emergency_mode', False)
    return {
        'emergency_mode': emergency_mode,
        'status': 'emergency_active' if emergency_mode else 'normal_operation'
    }


@SQify
def check_lora_availability(trigger):
    """
    Check if LoRa functionality is available
    """
    try:
        from tools.lora_runtime_integration import get_runtime_manager
        runtime = get_runtime_manager()
        return runtime.is_lora_available()
    except Exception as e:
        return False

@SQify
def handle_emergency_mode_logic(trigger, emergency_status, shutdown_result):
    """
    Handle emergency mode logic - consolidated wrapper to avoid dependency issues
    """
    # Extract emergency mode status
    if isinstance(emergency_status, dict):
        emergency_mode = emergency_status.get('emergency_mode', False)
    else:
        emergency_mode = False
    
    # Extract shutdown bypassed status
    if isinstance(shutdown_result, dict):
        shutdown_bypassed = shutdown_result.get('shutdown_bypassed', False)
    else:
        shutdown_bypassed = False
    
    # Assume LoRa is available for now to avoid dependency issues
    # In a real implementation, this would check LoRa availability
    lora_available = True
    
    if emergency_mode and shutdown_bypassed:
        # Emergency mode activated - LoRa transmission is always enabled
        try:
            # Simple logging without external function dependency
            print(f"🚨 EMERGENCY MODE LOG: emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}")
            
            return {
                'emergency_mode': True,
                'shutdown_bypassed': True,
                'action_taken': 'emergency_mode_activated',
                'lora_available': True
            }
        except Exception as e:
            # If LoRa integration fails, still activate emergency mode
            print(f"⚠️ EMERGENCY MODE LOG (LoRa failed): emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}, error={e}")
            
            return {
                'emergency_mode': True,
                'shutdown_bypassed': True,
                'action_taken': 'emergency_mode_activated_no_lora',
                'lora_available': False,
                'error': str(e)
            }
    else:
        print(f"ℹ️ EMERGENCY MODE LOG: emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}")
        return {
            'emergency_mode': emergency_mode,
            'shutdown_bypassed': shutdown_bypassed,
            'action_taken': 'no_action_needed',
            'lora_available': lora_available
        }

@SQify
def process_workflow_by_emergency_mode(trigger, emergency_logic_result, workflow_data):
    """
    Process workflow based on emergency mode - wrapper to avoid conditional statements in GRAPH functions
    """
    # Extract emergency mode status
    emergency_mode = emergency_logic_result.get('emergency_mode', False)
    
    if emergency_mode:
        # Emergency mode - return emergency status
        print("🚨 Emergency workflow placeholder - emergency mode active")
        return {
            'status': 'emergency_workflow_placeholder',
            'emergency_mode': True,
            'message': 'Emergency mode active - continuing data collection',
            'workflow_data': workflow_data,
            'timestamp': 'now'
        }
    else:
        # Normal mode - return normal status
        print("ℹ️ Normal workflow placeholder - normal operation")
        return {
            'status': 'normal_workflow_placeholder',
            'emergency_mode': False,
            'message': 'Normal operation - standard workflow',
            'workflow_data': workflow_data,
            'timestamp': 'now'
        }

@SQify
def get_sensor_tracking_stats(tracker):
    """
    Get statistics about sensor tracking and transmission patterns
    """
    if not tracker:
        return {'error': 'No tracker available'}
    
    try:
        stats = {
            'total_sensors_tracked': len(tracker['previous_values']),
            'sensors_with_history': len(tracker['transmission_history']),
            'last_transmission': tracker['last_transmission'],
            'change_threshold': tracker['change_threshold'] * 100,  # Convert to percentage
            'sensor_details': {}
        }
        
        # Add details for each tracked sensor
        for sensor_name in tracker['previous_values']:
            current_value = tracker['previous_values'][sensor_name]
            transmission_count = len(tracker['transmission_history'].get(sensor_name, []))
            
            stats['sensor_details'][sensor_name] = {
                'current_value': current_value,
                'transmission_count': transmission_count,
                'last_transmitted': tracker['transmission_history'].get(sensor_name, [{}])[-1].get('timestamp') if tracker['transmission_history'].get(sensor_name) else None
            }
        
        return stats
        
    except Exception as e:
        print(f"⚠️ Failed to get sensor tracking stats: {e}")
        return {'error': str(e)}

@SQify
def reset_sensor_tracker(tracker):
    """
    Reset sensor tracker to clear all previous values and history
    Useful for system restart or manual reset
    """
    if not tracker:
        return {'error': 'No tracker available'}
    
    try:
        tracker['previous_values'] = {}
        tracker['transmission_history'] = {}
        tracker['last_transmission'] = None
        
        print("✅ Sensor tracker reset - all previous values cleared")
        return {'status': 'reset_successful'}
        
    except Exception as e:
        print(f"⚠️ Failed to reset sensor tracker: {e}")
        return {'error': str(e)}

@SQify
def log_sensor_tracking_stats(sensor_tracker):
    """
    Log sensor tracking statistics - wrapper to avoid print() in GRAPH functions
    """
    if not sensor_tracker:
        return {'status': 'no_tracker', 'message': 'No sensor tracker available'}
    
    try:
        # Define the logic inline to avoid dependency on get_sensor_tracking_stats function
        if not sensor_tracker:
            tracking_stats = {'error': 'No tracker available'}
        else:
            try:
                stats = {
                    'total_sensors_tracked': len(sensor_tracker.get('previous_values', {})),
                    'sensors_with_history': len(sensor_tracker.get('transmission_history', {})),
                    'last_transmission': sensor_tracker.get('last_transmission'),
                    'change_threshold': sensor_tracker.get('change_threshold', 0.05) * 100,  # Convert to percentage
                    'sensor_details': {}
                }
                
                # Add details for each tracked sensor
                for sensor_name in sensor_tracker.get('previous_values', {}):
                    current_value = sensor_tracker['previous_values'][sensor_name]
                    transmission_count = len(sensor_tracker.get('transmission_history', {}).get(sensor_name, []))
                    
                    stats['sensor_details'][sensor_name] = {
                        'current_value': current_value,
                        'transmission_count': transmission_count,
                        'last_transmitted': sensor_tracker.get('transmission_history', {}).get(sensor_name, [{}])[-1].get('timestamp') if sensor_tracker.get('transmission_history', {}).get(sensor_name) else None
                    }
                
                tracking_stats = stats
                
            except Exception as e:
                print(f"⚠️ Failed to get sensor tracking stats: {e}")
                tracking_stats = {'error': str(e)}
        
        total_sensors = tracking_stats.get('total_sensors_tracked', 0)
        print(f"📊 Sensor tracking stats: {total_sensors} sensors tracked")
        
        return {
            'status': 'logged_successfully',
            'total_sensors': total_sensors,
            'stats': tracking_stats
        }
    except Exception as e:
        print(f"⚠️ Failed to log sensor tracking stats: {e}")
        return {'status': 'error', 'error': str(e)}

@SQify
def ip_uplink_transmit(bitmap, _sensor_tracker, dirname):
    """Send a subset of sensor readings and the flood bitmap to the FastAPI server over IP.

    Encodes the following channels as channel-coded hex blocks and POSTs to
    /ip/uplink: device_ts, battery_pct (from battery_manager; source varies by
    available hardware — ADS1115 D+, INA260, or WittyPi output), Pi throttled flags, GPS lat/lon, temperature,
    humidity, flood_detect (inferred from bitmap), flood_bitmap, and the five
    status-report parameters.  IMU data is not included.

    Failed transmissions are persisted to data/ip_uplink_pending/ and retried
    on the next wake cycle (store-and-forward).  On a successful wake, queued
    entries are drained oldest-first before the live reading is sent.

    Disabled by default — set ip_upload.enabled=true in runtime_config.json to
    activate.  Runs after the LoRa path in the wake cycle, and the shutdown
    waits for it.  Transport order LoRa -> WiFi -> cellular:
    - ip_upload.only_if_lora_unavailable (default true) skips IP while the
      mDot is joined.
    - ip_upload.cellular_connection (default "Quectel") is the NetworkManager
      connection brought up when the server can't be reached otherwise.  Once
      up it stays up for the session, but is taken down as soon as LoRa joins
      or WiFi reconnects.  Only a connection with autoconnect off is managed;
      one that autoconnects is left alone.
    Readings delivered over IP are removed from the LoRa store-and-forward
    queue, so they aren't sent twice once LoRa joins.

    IP fallback (tools/ip_fallback.py): this capture goes over IP even with
    ip_upload.enabled false or LoRa healthy when the server asked for it
    (downlink 17 97 NN, one upload per wake) or when no bitmap was delivered for
    ip_fallback_after_hours while LoRa holds bitmaps back. A cellular connection
    brought up for it goes down again afterwards unless IP upload is enabled.

    After a reading is delivered, any pending command is collected: the uplink
    asks for it (want_command) and the reply carries it, or, from a server that
    predates that, a poll of /ip/downlink fetches it. The wake-start poll
    (ip_downlink_poll_and_apply) runs before the uplink and never brings up
    cellular, so on a unit without WiFi it has no route; this is where such a
    unit gets its commands. It also picks up commands queued during the wake.

    Returns a status dict (never raises) so a failure here never stops the main
    workflow.
    """
    import struct
    import time as _time
    from tools.transmit_ip import (IPTransmitter, cellular_status, cellular_up,
                                   cellular_down, handle_downlink_reply, reply_command,
                                   wifi_connected, wifi_in_range)
    from tools import lora_store_forward as _lsf
    from tools.lora_runtime_integration import get_parameter, set_parameter

    tx = IPTransmitter()

    # IP fallback: the server asked for an upload, or LoRa has held bitmaps back too long
    fallback, fallback_source, boot_id = False, "", ""
    try:
        from tools import ip_fallback
        from tools.config_io import current_boot_id
        boot_id = current_boot_id() or ""
        fallback, fallback_source, why = ip_fallback.plan(
            have_bitmap=bool(bitmap), boot_id=boot_id,
            lora_pending_bitmaps=_lsf.pending_bitmaps(),
            after_hours=get_parameter('ip_fallback_after_hours', ip_fallback.DEFAULT_AFTER_HOURS),
            min_interval_hours=get_parameter('ip_fallback_min_interval_hours',
                                             ip_fallback.DEFAULT_MIN_INTERVAL_HOURS))
        if fallback and not tx.configured:
            fallback = False
            why += "; no ip_upload.server_url set, so not sending"
        print(f"📶 IP fallback: {'UPLOAD this capture: ' if fallback else ''}{why}")
    except Exception as e:
        print(f"⚠️ IP fallback check failed: {e}")

    if not tx.enabled and not fallback:
        print("📡 IP uplink disabled (ip_upload.enabled=false) — skipping")
        tx.close()
        return {"status": "disabled", "success": False}

    if fallback:
        try:
            from tools.wifi_policy import ensure_on
            ensure_on()     # WiFi may be off while LoRa looks healthy
        except Exception as e:
            print(f"⚠️ IP fallback: WiFi radio check failed: {e}")

    cell = tx.cellular_connection
    cell_status = cellular_status(cell) if cell else {"exists": False, "active": False, "autoconnect": False}
    on_demand = cell_status["exists"] and not cell_status["autoconnect"]
    cellular_active = on_demand and cell_status["active"]

    if tx.only_if_lora_unavailable and not fallback:
        # Joined isn't enough: the saved session says joined with no gateway in
        # range. Skip IP only when a link check this boot showed LoRa works.
        try:
            from tools.lora_health import assess
            health = assess()
            lora_joined = bool(health.get("healthy"))
            if not lora_joined:
                print(f"📡 LoRa not healthy ({health.get('reason')}) — sending over IP too")
        except Exception as e:
            print(f"⚠️ IP uplink: LoRa health unknown ({e}) — sending over IP")
            lora_joined = False
        moved = False
        if lora_joined:
            try:
                from tools.gps_home import load as _gps_home
                moved = bool(_gps_home().get("moved"))
            except Exception:
                moved = False
        if lora_joined and moved:
            print("🚨 Unit moved from its mount position — sending over IP as well as LoRa")
        elif lora_joined:
            if cellular_active:
                cellular_down(cell)
                print(f"📶 LoRa is back — {cell} taken down")
            print("📡 LoRa is healthy — IP uplink not needed this cycle")
            tx.close()
            return {"status": "skipped_lora_joined", "success": False}

    cellular_started = False
    delivered = False
    result = {}
    # Cellular transmission and heavy CPU work take turns (tools/lock_utils.py
    # power_exclusive): held from bringing cellular up, or from here if it is
    # already up, until this upload is done.
    from tools.lock_utils import PowerHold
    power = PowerHold()
    if cellular_active:
        power.acquire("cellular upload", 60)
    try:
        # ── Collect sensor data ────────────────────────────────────────────────
        # Sensor collection is done BEFORE the reachability check so that a
        # reading can be queued to disk when the server is temporarily down.
        # IMU orientation is not transmitted (no 03 01 channel encoded below).
        data = {}
        # The capture time, same as the LoRa packet's: the server drops
        # whichever copy of a capture arrives second.
        from tools.capture_time import capture_time
        ts_now = capture_time(dirname)

        try:
            from tools.aht20_temperature import get_aht20
            data.update(get_aht20())
        except Exception as e:
            print(f"⚠️ IP uplink: AHT20 unavailable: {e}")

        move_info = {}
        try:
            from tools.get_gps import get_location_with_retry, last_fix
            gps, _ = get_location_with_retry()
            if gps:
                data.update(gps)
                data['gps_fix_age_s'] = int(last_fix.get("age_s") or 0)
            from tools.gps_home import movement_fields
            move_fields, move_info = movement_fields(gps)
            data['movement_threshold'] = move_fields['movement_threshold']
        except Exception as e:
            print(f"⚠️ IP uplink: GPS unavailable: {e}")

        try:
            from tools.battery_manager import get_battery_status
            batt_status = get_battery_status()
            data['battery_percent'] = batt_status['battery_pct']
            data['battery_source'] = batt_status['battery_source']
        except Exception as e:
            print(f"⚠️ IP uplink: battery manager unavailable: {e}")
            data['battery_percent'] = None
            data['battery_source'] = 'unavailable'

        from tools.pi_power import get_throttled
        data['pi_throttled'] = get_throttled()
        from tools.registration_orientation import status_code_for_capture
        data['registration_status'] = status_code_for_capture(dirname)
        from tools.node_version import uplink_fields
        data.update(uplink_fields())
        try:
            from tools import drop_counter
            from tools.capture_time import clock_unverified
            data['sf_dropped'] = drop_counter.total()
            data['clock_unverified'] = 1 if clock_unverified(dirname) else 0
        except Exception as e:
            print(f"⚠️ IP uplink: drop counter / clock flag unavailable: {e}")
        flood = {}
        try:
            from tools.flood_baseline import flood_fields
            flood = flood_fields(dirname)
        except Exception as e:
            print(f"⚠️ IP uplink: flood check failed: {e}")

        data['area_threshold']                    = get_parameter('area_threshold', 10)
        data['stage_threshold']                   = get_parameter('stage_threshold', 50)
        data['monitoring_frequency']              = get_parameter('monitoring_frequency', 60)
        data['emergency_frequency']               = get_parameter('emergency_frequency', 5)
        data['neighborhood_emergency_frequency']  = get_parameter('neighborhood_emergency_frequency', 30)
        data['audio_recording_enabled']           = 1 if get_parameter('audio_recording_enabled', False) else 0

        # ── Build channel list ─────────────────────────────────────────────────
        channels = []

        # 00 01 — device timestamp (8-byte uint64)
        channels.append({"code": "00 01", "payload_hex": struct.pack(">Q", ts_now).hex()})

        # 02 01 — battery percent from battery_manager (source varies by hardware); omitted when unavailable
        batt_pct = data.get('battery_percent')
        if batt_pct is not None:
            channels.append({"code": "02 01", "payload_hex": struct.pack(">I", batt_pct).hex()})

        # 01 07 — raw Pi throttled register (uint32, vcgencmd layout); omitted when unreadable
        pi_throttled = data.get('pi_throttled')
        if pi_throttled is not None:
            channels.append({"code": "01 07", "payload_hex": struct.pack(">I", pi_throttled).hex()})
        # 01 08 — registration orientation check (tools/registration_orientation.py); omitted when absent
        registration_status = data.get('registration_status')
        if registration_status is not None:
            channels.append({"code": "01 08", "payload_hex": struct.pack(">I", registration_status).hex()})
        # 01 09 / 01 0A / 01 0B — software release, commit and model (tools/node_version.py)
        for code, key in (("01 09", "software_version"), ("01 0A", "software_commit"),
                          ("01 0B", "model_id")):
            if data.get(key) is not None:
                channels.append({"code": code, "payload_hex": struct.pack(">I", data[key]).hex()})
        # 01 0C — store-and-forward entries dropped unsent (tools/drop_counter.py), u32
        # 01 0D — 1 if this capture was taken before the clock was verified, u8
        if data.get('sf_dropped') is not None:
            channels.append({"code": "01 0C", "payload_hex": struct.pack(">I", min(data['sf_dropped'], 0xFFFFFFFF)).hex()})
        if data.get('clock_unverified') is not None:
            channels.append({"code": "01 0D", "payload_hex": struct.pack(">B", 1 if data['clock_unverified'] else 0).hex()})

        # 04 01 — GPS block (lat int32 microdeg, lon int32 microdeg)
        lat = data.get('gps_lat')
        lon = data.get('gps_lon')
        if lat is not None and lon is not None:
            try:
                channels.append({
                    "code": "04 01",
                    "payload_hex": struct.pack(">ii",
                        int(round(lat * 1_000_000)),
                        int(round(lon * 1_000_000)),
                    ).hex(),
                })
            except struct.error as e:
                print(f"⚠️ IP uplink: GPS encoding error (lat={lat}, lon={lon}): {e}")
            # 04 02 — age of that fix in seconds: 0 live, more when it's the saved
            # fix (tools/get_gps.py), so the server can tell a stale position
            channels.append({"code": "04 02",
                             "payload_hex": struct.pack(">I", max(0, min(data.get('gps_fix_age_s', 0), 0xFFFFFFFF))).hex()})
        # 01 06 — moved from the mount position (tools/gps_home.py), and 04 03 how far, in metres
        if data.get('movement_threshold') is not None:
            channels.append({"code": "01 06", "payload_hex": struct.pack(">I", 1 if data['movement_threshold'] else 0).hex()})
        if move_info.get("distance_m") is not None:
            channels.append({"code": "04 03",
                             "payload_hex": struct.pack(">I", int(min(move_info["distance_m"], 0xFFFFFFFF))).hex()})

        # 05 01 — temperature (int16, value × 100)
        temp = data.get('temperature_celsius')
        if temp is not None:
            try:
                channels.append({"code": "05 01",
                                  "payload_hex": struct.pack(">h", int(round(temp * 100))).hex()})
            except struct.error as e:
                print(f"⚠️ IP uplink: temperature encoding error (temp={temp}): {e}")

        # 06 01 — humidity (uint8, 0–100 %)
        hum = data.get('relative_humidity')
        if hum is not None:
            channels.append({"code": "06 01",
                             "payload_hex": struct.pack(">B", max(0, min(100, int(round(hum))))).hex()})

        # 07 17 / 07 27 — flood detected / growing (tools/flood_baseline.py), only
        # when detection is on and a baseline is set; 07 37 — water % of the mask
        # (uint8, 0-100) whenever the capture has one
        for code, key in (("07 17", "camera_flood_detected"), ("07 27", "camera_flood_growing")):
            if key in flood:
                channels.append({"code": code, "payload_hex": struct.pack(">I", flood[key]).hex()})
        if flood.get('camera_water_pct') is not None:
            channels.append({"code": "07 37",
                             "payload_hex": struct.pack(">B", flood['camera_water_pct']).hex()})

        # 08 18 — flood bitmap (variable length). Over IP the mask goes at its
        # full resolution, lossless (methods 0/1); the LoRa-sized bitmap is the
        # fallback when there is no mask or it compresses to more than
        # FULL_RES_MAX_BYTES (tools/compress_segmented.py).
        ip_bitmap = bitmap
        try:
            from tools.flood_baseline import mask_path
            from tools.compress_segmented import compress_full_resolution
            _mask = mask_path(dirname) if dirname else None
            _full = compress_full_resolution(_mask) if _mask else None
            if _full:
                ip_bitmap = _full
                print(f"📡 IP uplink: full-resolution bitmap {len(_full)} B")
            elif _mask:
                print("⚠️ IP uplink: full-resolution bitmap too large or not a mask — sending the LoRa bitmap")
        except Exception as e:
            print(f"⚠️ IP uplink: full-resolution bitmap failed ({e}) — sending the LoRa bitmap")
        if ip_bitmap:
            channels.append({"code": "08 18", "payload_hex": bytes(ip_bitmap).hex()})

        # 09 xx — status reports; clamped to uint32 range so struct.pack never raises
        _u32 = lambda v: max(0, min(0xFFFFFFFF, int(v)))
        channels.append({"code": "09 19",
                         "payload_hex": struct.pack(">I", _u32(data['area_threshold'])).hex()})
        # stage_threshold is reserved: reported, but nothing on the node uses it
        channels.append({"code": "09 29",
                         "payload_hex": struct.pack(">I", _u32(data['stage_threshold'])).hex()})
        channels.append({"code": "09 39",
                         "payload_hex": struct.pack(">I", _u32(data['monitoring_frequency'])).hex()})
        channels.append({"code": "09 49",
                         "payload_hex": struct.pack(">I", _u32(data['emergency_frequency'])).hex()})
        channels.append({"code": "09 59",
                         "payload_hex": struct.pack(">I",
                             _u32(data['neighborhood_emergency_frequency'])).hex()})
        channels.append({"code": "09 69",
                         "payload_hex": struct.pack(">I",
                             _u32(data['audio_recording_enabled'])).hex()})

        # ── Reachability check (after data is ready so we can queue on failure) ─
        # WiFi first; cellular only as the last resort.  A cellular session
        # left up by an earlier cycle gives way as soon as WiFi is back.
        if cellular_active and wifi_connected():
            cellular_down(cell)
            cellular_active = False
            print(f"📶 WiFi is back — {cell} taken down")
        reachable = tx.is_reachable(timeout_s=5)
        if (not reachable and on_demand and not cellular_active and tx.wifi_grace_s > 0
                and (wifi_connected() or wifi_in_range())):
            # A saved WiFi network is in range but the server isn't reachable over
            # it yet. At boot a weak signal can take a minute to authenticate (006
            # sat at -76 dBm and joined 66 s after boot), so wait a bounded time
            # before spending cellular data. Out of range of every saved network
            # this is skipped, so a cellular-only unit loses no on-time.
            print(f"📶 WiFi in range but the server isn't reachable yet — "
                  f"waiting up to {tx.wifi_grace_s} s before {cell}")
            reachable = tx.wait_reachable(tx.wifi_grace_s)
            if reachable:
                print("📶 Server reachable over WiFi — cellular not needed")
        if not reachable and on_demand and not cellular_active:
            print(f"📶 IP uplink: server unreachable — bringing up {cell}")
            power.acquire("cellular upload", 60)
            cellular_started = cellular_up(cell, tx.cellular_connect_timeout_s)
            # Tailscale needs a few seconds to find a path over the new link. Wait
            # for it locally rather than probing /health over cellular; the POST
            # below, with its retries, is the reachability check.
            reachable = cellular_started and tx.wait_tailnet(30)
            if cellular_started and not reachable:
                cellular_down(cell)   # no use to us, and Tailscale would keep using data
                cellular_started = False
        if not reachable:
            print(f"⚠️ IP uplink: server unreachable at {tx.server_url} — queuing reading")
            tx._enqueue(channels, ts_now)
            return {"status": "queued", "success": False, "channels_queued": len(channels)}

        # ── Drain any previously queued readings ───────────────────────────────
        drain = tx._drain_queue()
        delivered_ts = list(drain.get("drained_ts", []))
        if drain["drained"]:
            print(f"✅ IP uplink: drained {drain['drained']} queued reading(s)")
        if drain["failed"]:
            if delivered_ts:
                _lsf.remove_delivered(delivered_ts)
            print(f"⚠️ IP uplink: drain failed on {drain['failed_file']} — queuing live reading")
            tx._enqueue(channels, ts_now)
            if cellular_started and not delivered_ts:
                cellular_down(cell)   # never reached the server: stop Tailscale using data
            return {
                "status": "queued_drain_failed",
                "success": False,
                "drained": drain["drained"],
                "channels_queued": len(channels),
            }

        # ── Transmit live reading ──────────────────────────────────────────────
        # The reply carries any pending command (want_command), saving a poll
        result = tx.send_uplink(channels, device_ts=ts_now, want_command=True,
                                ip_fallback=fallback)
        delivered = result["success"]
        if delivered and (bitmap or fallback):
            try:
                if bitmap:
                    ip_fallback.record_delivery("ip")
                if fallback:
                    left = ip_fallback.uploaded(fallback_source, boot_id)
                    print(f"📶 IP fallback upload delivered ({fallback_source}"
                          + (f", {left} requested wake(s) left)" if fallback_source == "request" else ")"))
            except Exception as e:
                print(f"⚠️ IP fallback: delivery not recorded: {e}")

        if result["success"]:
            print(f"✅ IP uplink OK: {len(channels)} channels sent "
                  f"(attempt {result.get('attempts', '?')})")
            delivered_ts.append(ts_now)
            try:
                from tools.transmit_ip import correct_clock
                offset = correct_clock(result)
                if offset is not None:
                    print(f"🕒 Clock was {abs(offset):.0f} s {'behind' if offset > 0 else 'ahead of'} the server's; corrected")
            except Exception as e:
                print(f"⚠️ Clock check failed: {e}")
        if delivered_ts:
            # Delivered over IP: don't send the same captures again over LoRa.
            removed = _lsf.remove_delivered(delivered_ts)
            if removed:
                print(f"📦 S&F: removed {removed} LoRa entr{'y' if removed == 1 else 'ies'} already delivered over IP")
        if not result["success"]:
            print(f"⚠️ IP uplink failed after {result.get('attempts', '?')} attempt(s): "
                  f"{result.get('error', 'unknown error')} — queuing reading")
            tx._enqueue(channels, ts_now)
            if cellular_started and not delivered_ts:
                cellular_down(cell)   # never reached the server: stop Tailscale using data
            if tx.fallback_to_lora:
                print("📡 LoRa fallback is enabled — data may also be sent via the LoRa path")

        return {
            "status": "ok" if result["success"] else "queued",
            "success": result["success"],
            "channels_sent": len(channels) if result["success"] else 0,
            "drained": drain["drained"],
            "result": result,
        }
    except Exception as exc:
        print(f"⚠️ IP uplink: unexpected error: {exc}")
        return {"status": "error", "success": False, "error": str(exc)}
    finally:
        if delivered:
            # The server is reachable now; collect any command the wake-start
            # poll missed (see the docstring). The uplink reply carries it; a
            # server too old to put it there gets polled instead.
            try:
                carried = reply_command(result)
                if carried is not None:
                    handle_downlink_reply(carried, set_param_fn=set_parameter,
                                          label="IP downlink in uplink reply")
                else:
                    handle_downlink_reply(tx.poll_downlink(), set_param_fn=set_parameter,
                                          label="IP downlink after uplink")
            except Exception as exc:
                print(f"⚠️ IP downlink after uplink failed: {exc}")
        if fallback and cellular_started and not tx.enabled:
            cellular_down(cell)     # brought up only for the fallback upload
            print(f"📶 IP fallback done — {cell} taken down")
        tx.close()
        power.release()


@SQify
def ip_downlink_poll_and_apply(_lora_init):
    """Poll /ip/downlink/{device_id} for queued server commands and apply them.

    The server queues parameter-update commands when a human operator (or weather
    automation) sends a downlink via the dashboard.  This function retrieves the
    oldest pending command, decodes the parts list, and applies each recognised
    parameter change to the runtime config via set_parameter().

    Runs once at the start of each wake cycle (after LoRa init) so the device
    picks up any commands that arrived while it was sleeping. It doesn't bring
    up cellular; ip_uplink_transmit polls again once a reading is delivered.

    Disabled by default — requires ip_upload.enabled=true in runtime_config.json.
    """
    from tools.transmit_ip import IPTransmitter, handle_downlink_reply
    from tools.lora_runtime_integration import set_parameter

    # Is LoRa really working here (a link check the network must answer)? If so
    # WiFi stays off for this wake, as cellular does (tools/lora_health.py,
    # tools/wifi_policy.py).
    try:
        from tools import lora_health, wifi_policy
        wifi_policy.apply(lora_health.assess())
    except Exception as e:
        print(f"⚠️ LoRa health / WiFi policy failed: {e}")

    tx = IPTransmitter()

    if not tx.enabled:
        tx.close()
        return {"status": "disabled"}

    if not tx.is_reachable(timeout_s=2):
        tx.close()
        print("⚠️ IP downlink poll skipped: server unreachable — polling again after the IP uplink")
        return {"status": "unreachable"}

    poll_result = tx.poll_downlink()
    tx.close()

    return handle_downlink_reply(poll_result, set_param_fn=set_parameter)


@GRAPHify
def ttmain(trigger):
    # Import TTClock for the main workflow
    from ticktalkpython.Clock import TTClock
    
    with TTClock.root() as root_clock:
        
        # Initialize LoRa integration first
        lora_init = initialize_lora_integration(trigger)

        # Poll for queued server commands over IP and apply them before the
        # capture cycle begins, so any threshold/frequency changes take effect
        # this iteration.  No-ops silently when ip_upload.enabled=false.
        ip_downlink = ip_downlink_poll_and_apply(lora_init)

        # Validate configuration
        config_validation = validate_configuration(trigger)
        
        # Get adaptive monitoring parameters
        monitoring_params = adaptive_monitoring()
        
        # Call get_time as STREAM function to get directory name
        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000, TTStartOnArrival=True)

        # Start this iteration's audio segment (USB microphone), if enabled
        audio_status = record_audio(trigger, dirname)

        # Take photos and capture lepton data at GRAPH level
        from tt_take_photos import flir, take_two_photos
        photo = take_two_photos(trigger, dirname)
        deadline_time = READ_TTCLOCK(token, TTClock=root_clock) + 30_000_000
        
        lepton_file = flir(dirname)
        lepton = TTFinishByOtherwise(lepton_file, TTTimeDeadline=deadline_time, TTPlanB=TTSingleRunTimeout(flir_planb(token), TTTimeout=3_000_000), TTWillContinue=False) 
        
        #lepton_file = TTFinishByOtherwise(
        #    flir(dirname, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000),
        #    TTTimeDeadline=deadline_time,
        #    TTPlanB=TTSingleRunTimeout(flir_planb(token), TTTimeout=10_000_000),
        #    TTWillContinue=True
        #)
        
        # Coregistration and segmentation at GRAPH level
        coreg_state = coregistration(dirname, lepton_file, photo)
        seg_result = segformer(dirname, coreg_state)
        
        # Compression and LoRa transmission at GRAPH level
        bitmap = compress_bitmap(seg_result)
        
        # Create sensor tracker for monitoring value changes
        sensor_tracker = create_sensor_tracker()
        
        # Use sensor tracker for intelligent LoRa transmission
        lora_return = lora_token_with_tracker(bitmap, sensor_tracker, dirname)

        # IP uplink — sends the same sensor snapshot + bitmap to the FastAPI
        # server over WiFi/cellular.  Takes lora_return (the bitmap) so it runs
        # after LoRa, and the shutdown waits for it so an upload is never cut off.
        # No-ops silently when disabled; a failure here does not affect LoRa.
        ip_return = ip_uplink_transmit(lora_return, sensor_tracker, dirname)

        # Shutdown check at GRAPH level
        shutdown_result = call_shutdown(ip_return)
        
        # Create workflow data structure
        workflow_data = create_workflow_data(monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result)
        
        # Check if emergency mode is active and bypass shutdown if needed
        emergency_status = get_emergency_mode_status(trigger)
        emergency_logic_result = handle_emergency_mode_logic(trigger, emergency_status, shutdown_result)
        
        # Process the data based on emergency mode
        result = process_workflow_by_emergency_mode(trigger, emergency_logic_result, workflow_data)
        
        # Get sensor tracking statistics for monitoring
        log_result = log_sensor_tracking_stats(sensor_tracker)
        
        # Ensure constant LoRa monitoring for incoming messages
        lora_status = lora_listener(TTPersistent=True)
        
        return result

# Test function removed - not needed for main application
