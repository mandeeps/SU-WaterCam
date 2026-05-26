#!/usr/bin/env python3
"""
Escapes the mDot auto-join loop and disables AUTO_OTA mode.
Switches NJM from 2 (Auto OTA) to 1 (OTA) so the mDot waits for an
explicit AT+JOIN rather than joining automatically on every boot.
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial required: pip3 install pyserial")
    sys.exit(1)

DEFAULT_PORT  = '/dev/ttyAMA5'
DEFAULT_BAUD  = 115200
GUARD_TIME    = 1.1   # seconds of silence required before and after +++
READ_TIMEOUT  = 2.0
MAX_ATTEMPTS  = 8


def send(s, cmd, delay=0.3):
    s.reset_input_buffer()
    s.write((cmd + '\r\n').encode())
    time.sleep(delay)
    return s.read(256).decode(errors='replace').strip()


def expect_ok(s, cmd, label=None):
    resp = send(s, cmd)
    tag  = label or cmd
    if 'OK' in resp:
        print(f"  {tag}: OK")
    else:
        print(f"  {tag}: unexpected response: {resp!r}")
        return False
    return True


def escape_command_mode(s):
    print(f"Escaping auto-join loop (up to {MAX_ATTEMPTS} attempts)...")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"  [{attempt}/{MAX_ATTEMPTS}] guard → +++ → guard ...", end=' ', flush=True)
        s.reset_input_buffer()
        time.sleep(GUARD_TIME)
        s.write(b'+++')
        time.sleep(GUARD_TIME)
        resp = s.read(64).decode(errors='replace').strip()
        if 'OK' in resp:
            print("command mode active")
            return True
        print(f"no response ({resp!r})")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Escape mDot auto-join loop and disable AUTO_OTA mode"
    )
    parser.add_argument('--port', default=DEFAULT_PORT,
                        help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument('--baud', type=int, default=DEFAULT_BAUD,
                        help=f"Baud rate (default: {DEFAULT_BAUD})")
    args = parser.parse_args()

    print(f"Connecting to mDot on {args.port} at {args.baud} baud")
    try:
        s = serial.Serial(args.port, args.baud, timeout=READ_TIMEOUT)
    except serial.SerialException as e:
        print(f"Cannot open port: {e}")
        sys.exit(1)

    with s:
        if not escape_command_mode(s):
            print("\nFailed to reach command mode.")
            print("Check serial connection or try power-cycling the mDot.")
            sys.exit(1)

        print("\nReading current state...")
        njm  = send(s, 'AT+NJM?')
        njs  = send(s, 'AT+NJS?')
        freq = send(s, 'AT+TXF?')
        dr   = send(s, 'AT+TXDR?')
        print(f"  NJM  = {njm}")
        print(f"  NJS  = {njs}  (0=not joined, 1=joined)")
        print(f"  FREQ = {freq}")
        print(f"  DR   = {dr}")

        print("\nDisabling auto-join (NJM=1, manual OTA)...")
        if not expect_ok(s, 'AT+NJM=1', 'AT+NJM=1'):
            sys.exit(1)

        resp = send(s, 'AT+NJM?')
        if '1' not in resp:
            print(f"NJM verify failed: {resp!r}")
            sys.exit(1)
        print(f"  NJM confirmed: {resp}")

        print("\nSaving to flash...")
        expect_ok(s, 'AT&W', 'AT&W')

        print("\nmDot is in command mode. Auto-join is disabled.")
        print("Next steps:")
        print("  AT+JOIN              - join manually (OTAA)")
        print("  AT+NJM=0             - switch to ABP/manual mode")
        print("  AT+NJM?              - verify join mode")


if __name__ == '__main__':
    main()
