# AMCISS GUI

Real-time visualisation of inductance readings from the STM32 over UDP.

---

## Setup & Run

```bash
git clone https://github.com/ssutl/AMCISS-GUI
cd amciss-gui
python -m venv venv

# Activate the virtual environment
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

Requires Python 3.10+.

---

## Changing the Port

The default port is **5005**. To change it, update the **UDP Port** field in the GUI settings panel before clicking **Connect UDP**.

> **Windows only:** run this once in an Administrator PowerShell to allow incoming UDP (replace 5005 with your port if different):
> ```powershell
> New-NetFirewallRule -DisplayName "AMCISS UDP" -Direction Inbound -Protocol UDP -LocalPort 5005 -Action Allow
> ```

---

## Packet Format

Send UDP packets to the PC's IP address on port **5005** (or whichever port is set in the GUI).

The packet always carries both L (inductance) and RP (parallel resistance) — configure the LDC1101 in **RP+L mode** so both are available each conversion cycle. If RP is unavailable, send zeros for the `rp[]` array.

Each packet must be **exactly 264 bytes**, matching this C struct:

```c
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];      // 0xAA, 0xBB  (required — packet is dropped if wrong)
    uint16_t seq;           // sequence number, wraps at 65535
    uint32_t timestamp_ms;  // HAL_GetTick()
    uint16_t ldc[64];       // raw L register values — ldc[0..31] = DCM0, ldc[32..63] = DCM1
    uint16_t rp[64];        // raw RP register values — rp[0..31] = DCM0, rp[32..63] = DCM1
} AMCISS_Packet_t;          // sizeof = 264
#pragma pack(pop)
```

Minimal send loop:

```c
AMCISS_Packet_t pkt;
pkt.magic[0]     = 0xAA;
pkt.magic[1]     = 0xBB;
pkt.seq          = seq++;
pkt.timestamp_ms = HAL_GetTick();
// fill pkt.ldc[0..63] with L register readings
// fill pkt.rp[0..63]  with RP register readings
udp_send((uint8_t*)&pkt, sizeof(pkt));
```

---

## Verifying the Connection

Before using the full GUI, run the diagnostic tool to confirm packets are arriving correctly:

```bash
python read_port.py
```

Prints a decoded summary of each packet received. Flags invalid magic or wrong size.

---

## Physical LDC Layout

The 64 sensing coils are split across **two identical PCBs** mounted vertically side by side. Each board carries 32 LDCs in a two-column arrangement:

- **Left column:** even LDC numbers
- **Right column:** odd LDC numbers
- Numbering counts upward from the bottom, with a gap in the middle of each board between the lower half (LDCs 1-16 on Board 1, 33-48 on Board 2) and the upper half (17-32 / 49-64). The gap is purely physical (mid-board electronics), not a hole in the data array.

### Board 1 — LDCs 1-32 (packet indices 0-31)

Reading the silkscreen top-to-bottom:

```
  Left  Right
   18    17     <- top of board
   20    19
   22    21
   24    23
   26    25
   28    27
   30    29
   32    31
  --gap (mid-board electronics)--
    2     1
    4     3
    6     5
    8     7
   10     9
   12    11
   14    13
   16    15     <- bottom of board
```

Vertical neighbours along the left column from bottom to top: 16, 14, 12, 10, 8, 6, 4, 2, 32, 30, 28, 26, 24, 22, 20, 18. The right column follows the same pattern with the corresponding odd numbers.

### Board 2 — LDCs 33-64 (packet indices 32-63)

Identical layout to Board 1, with every label shifted by 32:

```
  Left  Right
   50    49     <- top of board
   52    51
   54    53
   56    55
   58    57
   60    59
   62    61
   64    63
  --gap--
   34    33
   36    35
   38    37
   40    39
   42    41
   44    43
   46    45
   48    47     <- bottom of board
```

### Silkscreen-to-packet index

Silkscreen label `N` maps to packet index `N - 1` in both `ldc[]` and `rp[]`:

| Silkscreen LDC # | Board | Position             | Packet index |
| ---------------- | :---: | -------------------- | :----------: |
| 1                |   1   | bottom-right         |       0      |
| 2                |   1   | bottom-left          |       1      |
| 16               |   1   | bottom-left of base  |      15      |
| 17               |   1   | top-right of base    |      16      |
| 32               |   1   | top-left             |      31      |
| 33               |   2   | bottom-right         |      32      |
| 64               |   2   | top-left             |      63      |

### Heatmap column order

The heatmap reorders columns so the X axis reflects **physical left-to-right position across both boards**, not raw packet index. Walking left to right:

1. Board 1 left column, bottom-up: 16, 14, 12, 10, 8, 6, 4, 2, 32, 30, 28, 26, 24, 22, 20, 18
2. Board 1 right column, bottom-up: 15, 13, 11, 9, 7, 5, 3, 1, 31, 29, 27, 25, 23, 21, 19, 17
3. Board 2 left column, bottom-up: 48, 46, 44, 42, 40, 38, 36, 34, 64, 62, 60, 58, 56, 54, 52, 50
4. Board 2 right column, bottom-up: 47, 45, 43, 41, 39, 37, 35, 33, 63, 61, 59, 57, 55, 53, 51, 49

A dashed vertical line on the heatmap marks the boundary between Board 1 and Board 2 (physical column 31 / 32). Major X-axis ticks show the silkscreen LDC number sitting at that physical column.
