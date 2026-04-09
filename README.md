# AMCISS GUI

Real-time visualisation of inductance readings from the STM32 over UDP.

---

## Setup & Run

```bash
git clone <repo-url>
cd amciss-gui
python -m venv venv
venv\Scripts\activate
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

Each packet must be **exactly 136 bytes**, matching this C struct:

```c
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];      // 0xAA, 0xBB  (required — packet is dropped if wrong)
    uint16_t seq;           // sequence number, wraps at 65535
    uint32_t timestamp_ms;  // HAL_GetTick()
    uint16_t ldc[64];       // raw uint16 readings — ldc[0..31] = DCM0, ldc[32..63] = DCM1
} AMCISS_Packet_t;          // sizeof = 136
#pragma pack(pop)
```

Minimal send loop:

```c
AMCISS_Packet_t pkt;
pkt.magic[0]     = 0xAA;
pkt.magic[1]     = 0xBB;
pkt.seq          = seq++;
pkt.timestamp_ms = HAL_GetTick();
// fill pkt.ldc[0..63] with ADC readings
udp_send((uint8_t*)&pkt, sizeof(pkt));
```

---

## Verifying the Connection

Before using the full GUI, run the diagnostic tool to confirm packets are arriving correctly:

```bash
python read_port.py
```

Prints a decoded summary of each packet received. Flags invalid magic or wrong size.
