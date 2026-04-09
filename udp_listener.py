"""
UDP listener thread.
Receives packets, decodes them, pushes raw uint16 L and RP readings to DataBuffer.
Also supports a dummy data generator for testing without hardware.
"""

import socket
import threading
import time
import numpy as np
from packet import decode_packet, encode_packet, PACKET_SIZE
from buffer import DataBuffer

DEFAULT_PORT = 5005


class UDPListener(threading.Thread):
    def __init__(self, buffer: DataBuffer, host: str = '0.0.0.0', port: int = DEFAULT_PORT):
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
                raw, _ = sock.recvfrom(PACKET_SIZE + 64)
                result = decode_packet(raw)
                if result:
                    seq, ts, l_readings, rp_readings = result
                    self.buffer.push(seq, ts, l_readings, rp_readings)
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
    """
    Generates fake raw uint16 sensor data for UI testing without hardware.
    Simulates a metal object sweeping across all 64 LDCs periodically.
    L increases and RP decreases where the metal object is detected.
    """
    def __init__(self, buffer: DataBuffer, rate_hz: float = 50.0):
        super().__init__(daemon=True, name='DummyGenerator')
        self.buffer = buffer
        self.rate_hz = rate_hz
        self._stop_event = threading.Event()
        self._seq = 0
        self._t0 = time.time()

    def run(self):
        interval = 1.0 / self.rate_hz
        print(f'[Dummy] Generating fake data at {self.rate_hz} Hz')

        L_BASELINE   = int(0.4  * 65535)
        L_PEAK_DELTA = int(0.25 * 65535)   # metal raises L reading by 25%

        RP_BASELINE   = int(0.6  * 65535)
        RP_DIP_DELTA  = int(0.3  * 65535)  # metal lowers RP reading by 30%

        while not self._stop_event.is_set():
            t_ms = int((time.time() - self._t0) * 1000)

            l_readings  = np.full(64, L_BASELINE,  dtype=np.uint16)
            rp_readings = np.full(64, RP_BASELINE, dtype=np.uint16)

            # Simulate metal object sweeping across belt every 8s
            t_norm = (t_ms % 8000) / 8000.0
            center = t_norm * 64
            for i in range(64):
                dist = abs(i - center)
                if dist < 8:
                    weight = np.exp(-dist ** 2 / 10.0)
                    l_readings[i]  = min(65535, L_BASELINE  + int(L_PEAK_DELTA  * weight))
                    rp_readings[i] = max(0,     RP_BASELINE - int(RP_DIP_DELTA  * weight))

            # Add noise
            noise = np.random.randint(-200, 200, 64)
            l_readings  = np.clip(l_readings.astype(np.int32)  + noise, 0, 65535).astype(np.uint16)
            rp_readings = np.clip(rp_readings.astype(np.int32) + noise, 0, 65535).astype(np.uint16)

            self.buffer.push(self._seq, t_ms, l_readings, rp_readings)
            self._seq = (self._seq + 1) & 0xFFFF
            time.sleep(interval)

    def stop(self):
        self._stop_event.set()
