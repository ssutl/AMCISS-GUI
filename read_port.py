"""
AMCISS UDP Diagnostic Tool
==========================
Stand-alone receiver that prints every incoming UDP datagram on the
AMCISS port as both a short hex preview and a decoded summary of the
264-byte AMCISS packet.

Use this *instead of* ``main.py`` to verify that the firmware is
transmitting valid packets before bringing the full GUI up. It will
flag the two common failure modes seen in development:

  * wrong datagram size (struct packing or MTU issue), and
  * incorrect magic header (port collision with another sender).

Usage
-----
    python read_port.py [port]

The port argument defaults to 5005 and must match the port configured
in the main app's settings panel.
"""

import socket
import sys

from packet import decode_packet, PACKET_SIZE, raw_to_uh

HOST = '0.0.0.0'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5005
BUFSIZE = PACKET_SIZE + 64  # over-read so oversize packets are detected, not truncated

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.settimeout(1.0)
sock.bind((HOST, PORT))
print(f'AMCISS diagnostic — listening on UDP {HOST}:{PORT}')
print(f'Expected packet size: {PACKET_SIZE} bytes\n')

while True:
    try:
        data, addr = sock.recvfrom(BUFSIZE)
    except socket.timeout:
        continue

    # First 16 bytes as hex give us a quick eyeball check of the header.
    hex_str = ' '.join(f'{b:02X}' for b in data[:16]) + (' ...' if len(data) > 16 else '')
    print(f'[{addr[0]}:{addr[1]}]  len={len(data)}  header hex: {hex_str}')

    result = decode_packet(data)
    if result:
        seq, ts_ms, l_raw, rp_raw = result
        uh = raw_to_uh(l_raw)
        print(f'  ok   seq={seq}  ts={ts_ms} ms')
        print(f'  L    min={l_raw.min()} max={l_raw.max()} raw  '
              f'({uh.min():.2f}–{uh.max():.2f} µH)')
        print(f'  RP   min={rp_raw.min()} max={rp_raw.max()} raw')
    else:
        print(f'  WARN: not a valid {PACKET_SIZE}-byte AMCISS packet '
              f'(magic mismatch or wrong size)')
    print()
