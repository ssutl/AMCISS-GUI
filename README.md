# AMCISS GUI

Data visualisation interface for the Adaptive Multichannel Induction Sorting System.

## Features
- **Live LDC trace plots** — single or multi-LDC overlay
- **Heatmap** — 64 LDCs vs time, colour = inductance value
- **UDP listener** — receives packets from STM32 over Ethernet
- **Dummy data generator** — for testing without hardware
- **Circular buffer** — user-adjustable duration (5–300s)
- **Packet validation** — magic header + CRC16 checksum

## Packet Format
```
[0xAA][0xBB]     Magic header        (2 bytes)
[seq_num]        uint16 sequence     (2 bytes)
[timestamp_ms]   uint32 milliseconds (4 bytes)
[ldc_0..63]      64 × float32 µH    (256 bytes)
[crc16]          CRC checksum        (2 bytes)
Total: 266 bytes
```

## Setup
```bash
pip install -r requirements.txt
python main.py
```

## Usage
1. **Dummy mode** — click "Start Dummy Data" to test the UI without hardware
2. **Live mode** — set UDP port/IP, click "Connect UDP", start transmitting from STM32
3. **LDC Traces tab** — select LDC number from spinner, add more via multi-select list
4. **Heatmap tab** — shows all 64 LDCs over time, metal objects appear as bright blobs

## Architecture
```
UDP socket (Thread 1) ──► DataBuffer (ring buffer) ──► QTimer (100ms) ──► UI update
DummyGenerator (Thread 2) ──► DataBuffer ──────────────────────────────────► UI update
```
