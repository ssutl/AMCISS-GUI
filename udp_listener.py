"""
UDP listener thread.
Receives packets, decodes them, pushes to DataBuffer.
Also supports a dummy data generator for testing without hardware.
"""

import socket
import threading
import time
import numpy as np
from packet import decode_packet, encode_packet, PACKET_SIZE
from buffer import DataBuffer


class UDPListener(threading.Thread):
    def __init__(self, buffer: DataBuffer, host: str = '0.0.0.0', port: int = 5005):
        super().__init__(daemon=True, name='UDPListener')
        self.buffer = buffer
        self.host = host
        self.port = port
        self._stop_event = threading.Event()
        self.packets_received = 0
        self.packets_invalid = 0

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            print(f'[UDP] Bind error: {e}')
            return

        print(f'[UDP] Listening on {self.host}:{self.port}')
        while not self._stop_event.is_set():
            try:
                raw, _ = sock.recvfrom(PACKET_SIZE + 64)  # small extra buffer
                result = decode_packet(raw)
                if result:
                    seq, ts, readings = result
                    self.buffer.push(seq, ts, readings)
                    self.packets_received += 1
                else:
                    self.packets_invalid += 1
            except socket.timeout:
                continue
            except Exception as e:
                print(f'[UDP] Error: {e}')
        sock.close()

    def stop(self):
        self._stop_event.set()


class DummyGenerator(threading.Thread):
    """Generates fake sensor data for UI testing without hardware."""
    def __init__(self, buffer: DataBuffer, rate_hz: float = 20.0):
        super().__init__(daemon=True, name='DummyGenerator')
        self.buffer = buffer
        self.rate_hz = rate_hz
        self._stop_event = threading.Event()
        self._seq = 0
        self._t0 = time.time()

    def run(self):
        interval = 1.0 / self.rate_hz
        print(f'[Dummy] Generating fake data at {self.rate_hz} Hz')
        while not self._stop_event.is_set():
            t_ms = int((time.time() - self._t0) * 1000)
            # Simulate a metal object passing over LDCs 20-30 at t~5s
            readings = np.random.normal(10.0, 0.05, 64).astype(np.float32)
            t_norm = (t_ms % 10000) / 10000.0  # 0..1 cycling every 10s
            center = int(t_norm * 64)
            for i in range(64):
                dist = abs(i - center)
                if dist < 6:
                    readings[i] += 2.5 * np.exp(-dist ** 2 / 8.0)
            self.buffer.push(self._seq, t_ms, readings)
            self._seq = (self._seq + 1) & 0xFFFF
            time.sleep(interval)

    def stop(self):
        self._stop_event.set()
