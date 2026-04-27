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
    Generates fake sensor data simulating rocks/metal randomly landing
    on the belt at different LDC positions, passing over the array,
    then disappearing. Uses encode_packet/decode_packet so the full
    packet pipeline is exercised.
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
        L_PEAK_DELTA = int(0.25 * 65535)
        RP_BASELINE  = int(0.6  * 65535)
        RP_DIP_DELTA = int(0.3  * 65535)

        # Active rocks: list of (center_ldc, width, start_ms, duration_ms, strength)
        active_rocks = []
        next_rock_ms = 0  # spawn first rock immediately

        while not self._stop_event.is_set():
            try:
                t_ms = int((time.time() - self._t0) * 1000)

                # Spawn new rocks at random intervals
                if t_ms >= next_rock_ms:
                    center = np.random.randint(2, 62)
                    width = np.random.uniform(2, 6)
                    duration = np.random.randint(800, 2500)
                    strength = np.random.uniform(0.5, 1.0)
                    active_rocks.append((center, width, t_ms, duration, strength))
                    next_rock_ms = t_ms + np.random.randint(300, 1500)

                # Remove expired rocks
                active_rocks = [r for r in active_rocks if t_ms < r[2] + r[3]]

                # Build readings
                l_readings  = np.full(64, L_BASELINE,  dtype=np.uint16)
                rp_readings = np.full(64, RP_BASELINE, dtype=np.uint16)

                for center, width, start_ms, duration, strength in active_rocks:
                    elapsed = t_ms - start_ms
                    # Intensity ramps up then down over the rock's pass
                    intensity = np.sin((elapsed / duration) * np.pi) * strength
                    for i in range(64):
                        dist = abs(i - center)
                        if dist < width * 3:
                            weight = np.exp(-dist ** 2 / (2.0 * width)) * intensity
                            l_val = L_BASELINE + int(L_PEAK_DELTA * weight)
                            rp_val = RP_BASELINE - int(RP_DIP_DELTA * weight)
                            # Multiple rocks can overlap — take the strongest signal
                            l_readings[i]  = max(l_readings[i],  min(65535, l_val))
                            rp_readings[i] = min(rp_readings[i], max(0, rp_val))

                # Add noise
                noise = np.random.randint(-200, 200, 64)
                l_readings  = np.clip(l_readings.astype(np.int32)  + noise, 0, 65535).astype(np.uint16)
                rp_readings = np.clip(rp_readings.astype(np.int32) + noise, 0, 65535).astype(np.uint16)

                # Go through the real packet pipeline
                raw = encode_packet(self._seq, t_ms, l_readings, rp_readings)
                result = decode_packet(raw)
                if result:
                    seq, ts, l_dec, rp_dec = result
                    self.buffer.push(seq, ts, l_dec, rp_dec)
                self._seq = (self._seq + 1) & 0xFFFF
            except Exception as e:
                print(f'[Dummy] loop error: {e}')
            time.sleep(interval)

    def stop(self):
        self._stop_event.set()
