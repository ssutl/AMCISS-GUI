# AMCISS GUI

Data visualisation interface for the **Adaptive Multichannel Induction Sorting System**.  
Receives live inductance readings from the STM32 embedded system over UDP and displays them as real-time traces and a heatmap.

---

## Requirements

- Python 3.10 or later
- Windows 10/11 (tested), Linux/macOS should also work

---

## Installation

```bash
git clone <repo-url>
cd amciss-gui
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / macOS
pip install -r requirements.txt
python main.py
```

---

## Connecting the Hardware

### 1. Network setup

Connect the STM32 board and PC to the same network (direct Ethernet cable or switch).  
The GUI listens on **all interfaces** (`0.0.0.0`) by default, so it will accept packets on any active adapter.

### 2. Windows Firewall

Windows silently blocks incoming UDP by default.  
Run this **once** in an Administrator PowerShell to open the port:

```powershell
New-NetFirewallRule -DisplayName "AMCISS UDP" -Direction Inbound -Protocol UDP -LocalPort 5005 -Action Allow
```

To remove the rule later:

```powershell
Remove-NetFirewallRule -DisplayName "AMCISS UDP"
```

### 3. STM32 configuration

Configure the firmware to send packets to:

| Parameter | Value |
|-----------|-------|
| Destination IP | PC's IP address on the shared network |
| Destination port | `5005` (default — adjustable in the GUI settings panel) |
| Protocol | UDP |
| Packet size | **136 bytes exactly** |

To find the PC's IP: run `ipconfig` in Command Prompt and look for the adapter connected to the STM32.

---

## Packet Format

Every UDP packet must be exactly **136 bytes**, matching the following C struct:

```c
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];      // 0xAA, 0xBB — used for validation
    uint16_t seq;           // packet sequence number, wraps at 65535
    uint32_t timestamp_ms;  // HAL_GetTick() — ms since boot
    uint16_t ldc[64];       // raw L register values
                            //   ldc[0..31]  = DCM0 channels
                            //   ldc[32..63] = DCM1 channels
} AMCISS_Packet_t;          // sizeof = 136
#pragma pack(pop)
```

**Field notes:**

- `magic` — if the first two bytes are not `0xAA 0xBB`, the GUI discards the packet.
- `seq` — used to count dropped packets (gaps in the sequence number).
- `ldc[]` — raw 16-bit ADC values. The GUI converts them to µH using a configurable scale factor: `µH = (raw / 65535) × scale_factor`.
- No CRC — the magic header is sufficient validation on a local network.

**Minimal firmware send loop:**

```c
AMCISS_Packet_t pkt;
pkt.magic[0] = 0xAA;
pkt.magic[1] = 0xBB;
pkt.seq      = seq++;
pkt.timestamp_ms = HAL_GetTick();
// fill pkt.ldc[] with your ADC readings...
udp_send((uint8_t*)&pkt, sizeof(pkt));  // must send exactly 136 bytes
```

---

## GUI Controls

| Control | Description |
|---------|-------------|
| UDP Port | Port to listen on (default 5005) |
| Host IP | Interface to bind (`0.0.0.0` = all) |
| Buffer (s) | Seconds of history to keep (5–300) |
| Refresh (ms) | UI update interval |
| Scale factor (µH) | Calibration: raw value 65535 maps to this inductance in µH |
| Belt velocity (m/s) | Used to convert the time axis to distance on the heatmap |
| Start Dummy Data | Simulates a metal object sweeping across the belt — use to test the UI without hardware |
| Connect UDP | Start/stop listening for hardware packets |
| Clear Buffer | Wipe all stored readings |
| Start Recording | Save all incoming data to a timestamped CSV in `recordings/` |

### Calibrating the scale factor

1. Connect a coil with a **known inductance** (e.g. 10 µH) to one LDC channel.
2. Start the GUI, click Connect UDP.
3. Read the raw value shown for that channel.
4. Set `scale factor = known_µH × 65535 / raw_value`.

### LDC Traces tab

- **LDC spinner** — primary channel to plot.
- **Multi-select list** — overlay additional channels on the same trace.

### Heatmap tab

- X axis = LDC index (0–63, position across the belt).
- Y axis = distance in metres, computed from elapsed time × belt velocity.
- A metal object passing the sensor array appears as a bright arc sweeping along the Y axis.

---

## Recorded CSV Format

Each row in a recording corresponds to one received packet:

```
timestamp_ms, seq, ldc0_raw, ldc1_raw, ..., ldc63_raw, ldc0_uh, ldc1_uh, ..., ldc63_uh
```

Files are saved to `recordings/amciss_YYYY-MM-DD_HH-MM-SS.csv`.

---

## Diagnostic Tool

Before opening the main GUI, use `read_port.py` to verify the hardware is transmitting correctly:

```bash
python read_port.py          # listens on port 5005
python read_port.py 6000     # custom port
```

For each packet received it prints the header bytes and a decoded summary (sequence number, timestamp, raw min/max, µH range). Invalid or wrongly-sized packets are flagged with a warning.

---

## Architecture

```
STM32 (UDP 136 B) ──► UDPListener thread ──► DataBuffer (ring buffer) ──► QTimer 100 ms ──► UI
DummyGenerator thread ──────────────────► DataBuffer ────────────────────────────────────► UI
                                              │
                                              └──► Recorder (CSV) when recording is active
```

| File | Responsibility |
|------|----------------|
| `main.py` | PyQt6 GUI — plots, settings panel, status bar |
| `buffer.py` | Thread-safe ring buffer; hooks into Recorder on each push |
| `packet.py` | 136-byte packet decoder, calibration conversion |
| `udp_listener.py` | UDP receive thread + dummy data generator |
| `recorder.py` | CSV recorder, writes raw + µH columns |
| `read_port.py` | Standalone diagnostic tool |
