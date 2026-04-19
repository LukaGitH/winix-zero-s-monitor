# Winix Zero-S Monitor v27

RX-only UART monitor for observing the debug/status stream from a Winix Zero-S air purifier.

This repository intentionally publishes only the current v27 source and the matching Windows executable.

## Files

- `winix_monitor_v27.py` - Python source for the monitor.
- `dist/WinixMonitor_v27.exe` - Windows executable build of v27.

## What It Shows

- Power state
- Fan mode / speed
- Particle filtered value
- AQ LED state
- Ambient light filtered and raw values
- Plasma state
- Motor feedback raw value
- Estimated motor feedback in m3/h
- Last frame ID, frame count, and checksum errors

## Connection

- UART: `38400 8N1`
- Frame format: `F0 ID LEN ... CHK`
- Checksum: 8-bit sum of the previous bytes in the frame

Version 27 treats the debug header as RX/status-only. Earlier TX command experiments did not control the purifier, so transmit controls were removed.

## Run the Windows App

Download or open:

```text
dist/WinixMonitor_v27.exe
```

Select the serial port, keep the baud rate at `38400`, and click `Connect`.

## Run From Source

```bash
python winix_monitor_v27.py
```

Python dependencies:

- `pyserial`
- `matplotlib`
- `tkinter`

Install the Python packages with:

```bash
python -m pip install pyserial matplotlib
```

`tkinter` is included with many Python installs. On Linux it may need to be installed through the system package manager.
