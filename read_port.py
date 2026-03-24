"""
Simple UDP reader — 192.168.0.123:5000
Prints raw 9-byte packets as they arrive.
"""

import socket

HOST = '0.0.0.0'
PORT = 25
BUFSIZE = 1472  # larger than 9 so we catch oversized packets too

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.settimeout(1.0)
sock.bind((HOST, PORT))
print(f'Listening on UDP {HOST}:{PORT} ...\n')

while True:
    try:
        data, addr = sock.recvfrom(BUFSIZE)
        
    except socket.timeout:
        continue
    hex_str = ' '.join(f'{b:02X}' for b in data)
    dec_str = ' '.join(f'{b:3d}' for b in data)
    print(f'[{addr[0]}:{addr[1]}]  len={len(data)}  hex: {hex_str}')
    print(f'                         dec: {dec_str}')
