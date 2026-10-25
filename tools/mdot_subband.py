#!/home/pi/SU-WaterCam/venv/bin/python
"""Set the mDot's US915 frequency sub-band to the one our gateway listens on.

Our gateway uses sub-band 1 (channels 0-7 plus 500 kHz channel 64; after a
join the network's channel mask reads 000100000000000000FF). A new mDot comes
with sub-band 0, "all 64 channels", so only about one join request in eight
reaches the gateway and a join can take 15 minutes. With the right sub-band it
joins in seconds (005, 2026-10-14: 6.6 s).

It also turns off the sub-band search (AT+JR=0). With join retries above 0, an
mDot that keeps failing to join (out of range, or at the edge of coverage)
moves to the next sub-band after that many tries, and so on round all eight:
006 drifted from 1 to 8 in a day out of range, and to 3 at its site
(2026-10-24). On another sub-band it can't join our gateway even when back in
range. With 0 it keeps trying on the sub-band set here.

    python3 tools/mdot_subband.py               # set sub-band 1 and no sub-band search if needed, save
    python3 tools/mdot_subband.py --check       # only report
    python3 tools/mdot_subband.py --sub-band 2  # a site whose gateway uses another
    python3 tools/mdot_subband.py --from-mask   # take it from the joined network's channel mask
    python3 tools/mdot_subband.py --rejoin      # then join once to prove it
    python3 tools/mdot_subband.py --fixed-dr 1  # also turn ADR off and send at DR1 (SF9)
    python3 tools/mdot_subband.py --adr         # also turn ADR back on

A fixed data rate is for a site at the edge of coverage. There the network's ADR
can raise the data rate past what the link carries: 006's frames arrived at SF9
with an SNR of -10 to -12 dB (SF9 decodes to about -12.5), and ADR still moved it
to SF8 (about -10 dB) on 2026-10-25, so more frames were lost. With ADR off the
mDot keeps the data rate set here. The US915 125 kHz data rates are DR0 (SF10,
11-byte payloads) to DR3 (SF7); a lower one reaches further but carries less, and
the node splits its packets to fit (tools/lora_handler_concurrent.py).

It stops lora_daemon (which owns the port) and starts it again. Settings are
saved with AT&W, which saves every current setting, so the data-rate and ADR
settings are printed first. Joining spends a DevNonce: --rejoin only when
needed. Nothing changes if the sub-band and join retries are already right.
"""
import argparse
import re
import subprocess
import sys
import time

try:
    import serial
except ImportError:
    serial = None

GATEWAY_SUB_BAND = 1          # our gateway (MultiTech Conduit, US915)
DEFAULT_PORT = '/dev/ttyAMA5'
BAUD = 115200
GUARD_TIME = 1.2              # silence before and after +++


def sub_band_from_mask(chm: str):
    """The sub-band (1-8) an AT+CHM? answer enables, or None.

    The answer is five 16-bit words, highest first: channels 64-71 (500 kHz),
    then 48-63, 32-47, 16-31, 0-15. A sub-band is eight 125 kHz channels, 8n-8
    to 8n-1, plus 500 kHz channel 63+n.
    """
    chm = chm.strip()
    if not re.fullmatch(r'[0-9A-Fa-f]{20}', chm):
        return None
    words = [int(chm[i:i + 4], 16) for i in range(0, 20, 4)]
    narrow = words[4] | words[3] << 16 | words[2] << 32 | words[1] << 48
    for n in range(1, 9):
        if narrow == 0xFF << (8 * (n - 1)):
            return n
    return None


class MDot:
    def __init__(self, port: str):
        self.s = serial.Serial(port, BAUD, timeout=0.05)

    def at(self, cmd: str, wait: float = 4) -> str:
        """Send a command; the answer lines between the echo and OK/ERROR."""
        self.s.reset_input_buffer()
        self.s.write(cmd.encode() + b'\r\n')
        buf, end = b'', time.time() + wait
        while time.time() < end and b'\r\nOK' not in buf and b'ERROR' not in buf:
            buf += self.s.read(512)
        lines = [l.strip() for l in buf.decode(errors='replace').replace('\r', '').split('\n')]
        return '\n'.join(l for l in lines if l and l != cmd)

    def escape(self) -> None:
        for _ in range(2):
            time.sleep(GUARD_TIME)
            self.s.write(b'+++')
            time.sleep(GUARD_TIME)
        self.s.write(b'\r\nAT\r\n')
        time.sleep(0.4)
        self.s.reset_input_buffer()

    def command_mode(self, timeout: float = 300) -> bool:
        """An OK at the prompt. A mDot joining after a reset ignores the port;
        +++ gets through during a long back-off ("Waiting N s before ...")."""
        if self.at('AT', 2).endswith('OK'):
            return True
        self.escape()
        if self.at('AT', 2).endswith('OK'):
            return True
        print('mDot busy joining: waiting for a back-off to send +++')
        buf, end = b'', time.time() + timeout
        while time.time() < end:
            buf += self.s.read(256)
            m = re.search(rb'Waiting (\d+) s before', buf)
            if m:
                buf = b''
                if int(m.group(1)) >= 5:
                    self.escape()
                    if self.at('AT', 2).endswith('OK'):
                        return True
            if b'oined network' in buf and self.at('AT', 2).endswith('OK'):
                return True
        return False


def changes_needed(fsb: str, join_retries: str, want: int, adr: str = None, tx_dr: str = None,
                   fixed_dr: int = None, adr_on: bool = False) -> list:
    """The AT commands that set sub-band `want` and no sub-band search; [] if both are right.

    With fixed_dr also ADR off and that data rate; with adr_on, ADR on. `adr` and
    `tx_dr` are the AT+ADR? and AT+TXDR? answers ('1, 64, 32', 'DR1 - SF9BW125').
    """
    cmds = []
    if fsb != str(want):
        cmds.append(f'AT+FSB={want}')
    if join_retries != '0':
        cmds.append('AT+JR=0')
    adr_enabled = (adr or '').split(',')[0].strip()
    if fixed_dr is not None:
        if adr_enabled != '0':
            cmds.append('AT+ADR=0')
        if data_rate(tx_dr) != fixed_dr:
            cmds.append(f'AT+TXDR={fixed_dr}')
    elif adr_on and adr_enabled != '1':
        cmds.append('AT+ADR=1')
    return cmds


def data_rate(answer: str):
    """'DR1 - SF9BW125' -> 1, or None."""
    m = re.match(r'DR(\d+)', (answer or '').strip())
    return int(m.group(1)) if m else None


def value(answer: str) -> str:
    """'1\\nOK' -> '1'."""
    return answer.split('\n')[0] if answer.endswith('OK') else ''


def daemon(action: str) -> None:
    subprocess.run(['sudo', 'systemctl', action, 'lora_daemon'], check=False)


def daemon_active() -> bool:
    return subprocess.run(['systemctl', 'is-active', '--quiet', 'lora_daemon']).returncode == 0


def run(a) -> int:
    m = MDot(a.port)
    if not m.command_mode():
        print('No AT prompt: the mDot stayed in its join loop')
        return 1
    fsb, joined, chm = value(m.at('AT+FSB?')), value(m.at('AT+NJS?')), value(m.at('AT+CHM?'))
    jr = value(m.at('AT+JR?'))
    print(f"mDot {value(m.at('AT+DI?'))}: sub-band {fsb or '?'}, joined {joined or '?'}, channel mask {chm or '?'}, "
          f"sub-band search (join retries) {jr or '?'}")
    tx_dr, adr = value(m.at('AT+TXDR?')), value(m.at('AT+ADR?'))
    print(f"  data rate {tx_dr or '?'}, ADR {adr or '?'}")

    want = a.sub_band
    if a.from_mask:
        want = sub_band_from_mask(chm) if joined == '1' else None
        if want is None:
            print('--from-mask: the mDot must be joined and its mask enable exactly one sub-band')
            return 1
        print(f"  the network's channel mask is sub-band {want}")
    dr_args = dict(fixed_dr=a.fixed_dr, adr_on=a.adr)
    cmds = changes_needed(fsb, jr, want, adr, tx_dr, **dr_args)
    if a.check:
        print('OK' if not cmds else f'sub-band {fsb} (wanted {want}), join retries {jr} (wanted 0); '
                                    f"would send {', '.join(cmds)}")
        return 0 if not cmds else 2

    if not cmds:
        print(f'Sub-band already {want} with no sub-band search: nothing to change')
    else:
        if not all(m.at(c).endswith('OK') for c in cmds) or not m.at('AT&W').endswith('OK'):
            print(f"Could not set and save {', '.join(cmds)}")
            return 1
        now_fsb, now_jr = value(m.at('AT+FSB?')), value(m.at('AT+JR?'))
        now_dr, now_adr = value(m.at('AT+TXDR?')), value(m.at('AT+ADR?'))
        print(f'Sub-band {fsb} -> {now_fsb}, join retries {jr} -> {now_jr}, '
              f'data rate {tx_dr} -> {now_dr}, ADR {adr} -> {now_adr}, saved')
        if changes_needed(now_fsb, now_jr, want, now_adr, now_dr, **dr_args):
            return 1

    if a.rejoin:
        t = time.time()
        answer = m.at('AT+JOIN', 90)
        print(f"Join: {'joined' if answer.endswith('OK') else 'FAILED: ' + answer.splitlines()[-1]} "
              f'in {time.time() - t:.1f} s')
        if not answer.endswith('OK'):
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--sub-band', type=int, default=GATEWAY_SUB_BAND, choices=range(1, 9),
                    help=f'US915 sub-band 1-8 (default {GATEWAY_SUB_BAND}, our gateway)')
    ap.add_argument('--from-mask', action='store_true',
                    help="use the sub-band in the channel mask the network sent (after a join "
                         "and an uplink: until then the mask follows the mDot's own setting)")
    ap.add_argument('--check', action='store_true', help='report only; exit 2 if it differs')
    ap.add_argument('--rejoin', action='store_true', help='join once afterwards (spends a DevNonce)')
    dr = ap.add_mutually_exclusive_group()
    dr.add_argument('--fixed-dr', type=int, choices=range(0, 4),
                    help='turn ADR off and send at this data rate (US915 DR0-DR3, SF10-SF7)')
    dr.add_argument('--adr', action='store_true', help='turn ADR (back) on')
    ap.add_argument('--port', default=DEFAULT_PORT)
    a = ap.parse_args()
    if serial is None:
        print('pyserial required (the node venv has it)')
        return 1
    was_running = daemon_active()
    if was_running:
        daemon('stop')
    try:
        return run(a)
    finally:
        if was_running:
            daemon('start')


if __name__ == '__main__':
    sys.exit(main())
