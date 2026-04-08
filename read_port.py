"""
AMCISS UDP Diagnostic Tool
===========================
Listens on the same UDP port as the main app and prints every incoming packet
as raw hex + a decoded summary of the 136-byte AMCISS binary packet.

Run this INSTEAD of main.py to verify the hardware is transmitting correctly
before connecting the full GUI.

Usage:
    python read_port.py [port]

Default port: 5005  (must match the port set in the main app)
"""

import socket
import sys
from packet import decode_packet, PACKET_SIZE, raw_to_uh

HOST = '0.0.0.0'
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5005
BUFSIZE = PACKET_SIZE + 64

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

    hex_str = ' '.join(f'{b:02X}' for b in data[:16]) + (' ...' if len(data) > 16 else '')
    print(f'[{addr[0]}:{addr[1]}]  len={len(data)}  header hex: {hex_str}')

    result = decode_packet(data)
    if result:
        seq, ts_ms, raw = result
        uh = raw_to_uh(raw)
        print(f'  ok   seq={seq}  ts={ts_ms} ms')
        print(f'  LDC  min={raw.min()} max={raw.max()} raw  '
              f'({uh.min():.2f}–{uh.max():.2f} µH)')
    else:
        print(f'  WARN: not a valid {PACKET_SIZE}-byte AMCISS packet '
              f'(magic mismatch or wrong size)')
    print()
