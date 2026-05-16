"""
UDP listener and dummy-data generator
=====================================
Two background threads that produce LDC scans for the GUI:

  * :class:`UDPListener` — binds a UDP socket, decodes every incoming
    264-byte AMCISS packet, and pushes the raw uint16 L and RP arrays
    into the shared :class:`DataBuffer`.

  * :class:`DummyGenerator` — synthesises fake "rocks passing over the
    belt" scans for development and demos when no hardware is present.
    It builds full packets with :func:`encode_packet` and decodes them
    again with :func:`decode_packet`, so the test path exercises the
    same codec the real listener uses.

Both threads are daemons and expose a :meth:`stop` method that signals
their loop to exit on the next iteration.
"""

import socket
import threading
import time

import numpy as np

from buffer import DataBuffer
from packet import decode_packet, encode_packet, PACKET_SIZE

DEFAULT_PORT = 5005


class UDPListener(threading.Thread):
    """Background thread that receives UDP packets from the STM32."""

    def __init__(self, buffer: DataBuffer, host: str = '0.0.0.0',
                 port: int = DEFAULT_PORT):
        """
        Args:
            buffer: Shared ring buffer to push decoded scans into.
            host: Interface to bind on. ``0.0.0.0`` accepts packets on
                every local interface.
            port: UDP port to bind. Must match the firmware's
                destination port.
        """
        super().__init__(daemon=True, name='UDPListener')
        self.buffer = buffer
        self.host = host
        self.port = port
        self._stop_event = threading.Event()
        self.packets_received = 0
        self.packets_invalid = 0

    def run(self) -> None:
        """Receive loop. Exits when :meth:`stop` is called."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Short timeout so the stop event is checked roughly once a second
        # even when no traffic is arriving.
        sock.settimeout(1.0)
        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            print(f'[UDP] Bind error: {e}')
            return

        print(f'[UDP] Listening on {self.host}:{self.port}')
        while not self._stop_event.is_set():
            try:
                # Read slightly more than PACKET_SIZE so we can recognise
                # (and reject) oversize datagrams rather than truncating.
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

    def stop(self) -> None:
        """Signal the receive loop to exit."""
        self._stop_event.set()


class DummyGenerator(threading.Thread):
    """
    Synthetic data source for offline development.

    Simulates discrete "rocks" (or metal fragments) landing on the
    conveyor belt at random LDC positions, ramping a Gaussian-shaped
    response up and down as they pass through the sensing window, then
    disappearing. Multiple rocks can be in flight at once and overlap.

    The synthesised readings are packed into a real AMCISS packet via
    :func:`encode_packet` and then re-decoded — this guarantees that
    the dummy path exercises the same byte-level codec the GUI uses
    for hardware traffic.
    """

    def __init__(self, buffer: DataBuffer, rate_hz: float = 50.0):
        """
        Args:
            buffer: Shared ring buffer to push synthesised scans into.
            rate_hz: Packets per second. ~50 Hz approximates the LDC1101's
                RP+L conversion rate at typical settings.
        """
        super().__init__(daemon=True, name='DummyGenerator')
        self.buffer = buffer
        self.rate_hz = rate_hz
        self._stop_event = threading.Event()
        self._seq = 0
        self._t0 = time.time()

    def run(self) -> None:
        """Generation loop. Exits when :meth:`stop` is called."""
        interval = 1.0 / self.rate_hz
        print(f'[Dummy] Generating fake data at {self.rate_hz} Hz')

        # Baselines and peak deltas are expressed as fractions of full
        # scale (uint16) so the simulated data sits comfortably inside
        # the codec's value range.
        L_BASELINE   = int(0.4 * 65535)
        L_PEAK_DELTA = int(0.25 * 65535)
        RP_BASELINE  = int(0.6 * 65535)
        RP_DIP_DELTA = int(0.3 * 65535)

        # Each entry: (center_ldc, width, start_ms, duration_ms, strength)
        active_rocks: list[tuple[int, float, int, int, float]] = []

        # Hold off spawning the first rock for ~1.5 s so the heatmap can
        # capture a clean "no metal" baseline before any signal arrives.
        next_rock_ms = 1500

        while not self._stop_event.is_set():
            try:
                t_ms = int((time.time() - self._t0) * 1000)

                # Spawn a new rock at random intervals.
                if t_ms >= next_rock_ms:
                    center = np.random.randint(2, 62)
                    width = np.random.uniform(2, 6)
                    duration = np.random.randint(800, 2500)
                    strength = np.random.uniform(0.5, 1.0)
                    active_rocks.append((center, width, t_ms, duration, strength))
                    next_rock_ms = t_ms + np.random.randint(300, 1500)

                # Retire rocks whose duration has elapsed.
                active_rocks = [r for r in active_rocks if t_ms < r[2] + r[3]]

                # Start each scan at the no-metal baseline.
                l_readings  = np.full(64, L_BASELINE,  dtype=np.uint16)
                rp_readings = np.full(64, RP_BASELINE, dtype=np.uint16)

                # Superimpose each active rock's contribution.
                for center, width, start_ms, duration, strength in active_rocks:
                    elapsed = t_ms - start_ms
                    # Smooth half-sine envelope: 0 → strength → 0 across
                    # the rock's lifetime, modelling its passage through
                    # the array.
                    intensity = np.sin((elapsed / duration) * np.pi) * strength
                    for i in range(64):
                        dist = abs(i - center)
                        if dist < width * 3:
                            # Gaussian spatial profile centred on `center`.
                            weight = np.exp(-dist ** 2 / (2.0 * width)) * intensity
                            l_val  = L_BASELINE + int(L_PEAK_DELTA * weight)
                            rp_val = RP_BASELINE - int(RP_DIP_DELTA * weight)
                            # When rocks overlap, take the strongest L
                            # response and the deepest RP dip per channel.
                            l_readings[i]  = max(l_readings[i],  min(65535, l_val))
                            rp_readings[i] = min(rp_readings[i], max(0,    rp_val))

                # Sprinkle a small amount of uniform noise so the
                # baseline isn't perfectly flat.
                noise = np.random.randint(-200, 200, 64)
                l_readings  = np.clip(l_readings.astype(np.int32)  + noise, 0, 65535).astype(np.uint16)
                rp_readings = np.clip(rp_readings.astype(np.int32) + noise, 0, 65535).astype(np.uint16)

                # Round-trip through the real codec so any bug in the
                # encode/decode path surfaces immediately in dev.
                raw = encode_packet(self._seq, t_ms, l_readings, rp_readings)
                result = decode_packet(raw)
                if result:
                    seq, ts, l_dec, rp_dec = result
                    self.buffer.push(seq, ts, l_dec, rp_dec)
                self._seq = (self._seq + 1) & 0xFFFF
            except Exception as e:
                print(f'[Dummy] loop error: {e}')
            time.sleep(interval)

    def stop(self) -> None:
        """Signal the generation loop to exit."""
        self._stop_event.set()
