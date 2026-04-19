# Winix Zero-S Monitor

RX-only UART monitor for reverse-engineering a Winix Zero-S air purifier debug/status stream.

## Current App

- Source: `winix_monitor_v27.py`
- Windows executable: `dist/WinixMonitor_v27.exe`
- UART default: `38400 8N1`
- Frame format: `F0 ID LEN ... CHK`

## Notes

Serial TX commands on the debug header were tested and did not control the purifier. Version 27 removes the experimental TX controls and treats the debug header as RX/status-only.

Known decoded values and protocol notes are in `winix_findings.md`.

## Run From Source

```bash
python winix_monitor_v27.py
```

Required Python packages:

- pyserial
- matplotlib
- tkinter

