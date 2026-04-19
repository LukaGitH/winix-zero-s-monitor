# Winix Zero-S UART reverse-engineering notes

## Transport / framing
- UART: **38400 baud, 8N1**
- Frame format: `F0 ID LEN ... CHK`
- Checksum: **8-bit sum of all previous bytes in the frame**

## Packet IDs seen
- `0x10` - main state / mode / command-like fields
- `0x11` - processed AQ / UI / indicator-like fields
- `0x13` - ambient light path
- `0x14` - motor / fan feedback path
- `0x15` - mostly static config-like frame

---

## Known fields

### `0x10`
- `0x10[4]` -> **power state**
  - `0` = off
  - `1` = on

- `0x10[7]` -> **Plasma state**
  - `0x01` / `1` = plasma off
  - `0x61` / `97` = plasma on
  - confirmed from the plasma button capture

- `0x10[12]` -> **fan mode code**
  - `1` = auto
  - `2` = speed 1
  - `3` = speed 2
  - `4` = speed 3
  - `5` = max
  - `0` = sleep

### `0x11`
- `0x11[12]` -> **Particle filtered**
  - rises and falls cleanly with smoke / flux events
  - currently the best particulate-like signal on the bus

- `0x11[14]` -> **AQ LED state**
  - `1` = blue
  - `2` = orange
  - `3` = red

### `0x13`
- `0x13[4]` -> **Ambient light filtered**
  - reacts to shadows / flashlight
  - byte-sized, 0..255-like behavior

- `0x13[7]` -> **Ambient light raw**
  - reacts faster and more analog-like to light changes
  - byte-sized, can appear to wrap or roll over under very strong light

### `0x14`
- `0x14[7]`, `0x14[8:10]` -> **motor feedback raw candidate**
  - useful for fan ramp / feedback observation
  - shown as motor feedback converted to m3/h
  - motor feedback is not a true monotonic counter; unwrap accumulation was wrong because the value must go back down when switching from max to sleep
  - current app candidate is an instantaneous 18-bit value: `(0x14[7] & 0x03) << 16 | 0x14[8:10]`
  - in `purifier_fan.csv`, this ramps to about `220332` at max and then falls again when speed is reduced
  - max motor feedback is around `220000`, though the value can go past that
  - WINIX ZERO-S CADR is `410 m3/h`, so app scales `220000` motor feedback to `410 m3/h`
  - when power is off, app displays airflow as `0.0 m3/h` even if the last motor counter remains nonzero

---

## Strong candidates / not yet proven

### Fan speed / motor
- Max speed is mapped as `0x10[12] == 5`.
- Sleep is mapped as `0x10[12] == 0`.
- Motor feedback needs validation as an instantaneous wider value, not an accumulated unwrapped counter.
- Motor feedback airflow is currently a linear estimate from `0..220000` raw to `0..410 m3/h`, clipped at `410 m3/h`.

---

## Recommended capture methods

### To map fan speeds
Use stable holds:
- auto
- speed 1
- speed 2
- speed 3
- max
- sleep

Keep everything else fixed:
- same plasma state
- no smoke
- no lighting changes
- wait for motor feedback to settle at each speed

## UI guidance
- Every new app update should create the next version file, unless a specific existing version is requested.

### Known section
Should show:
- power
- fan mode / speed
- Particle filtered
- AQ LED state
- ambient light filtered
- ambient light raw
- plasma state
- motor feedback raw
- motor feedback m3/h

### Graph section
Should show:
- Particle filtered
- Ambient light
- Ambient light filtered
- Motor feedback m3/h, default off, fixed `0..420 m3/h` scale
- Graph window can be changed in `30 s` steps with `-30s` / `+30s`
- Ambient light is plotted on the same right-side scale as motor feedback

### Debug section
Should show:
- last frame ID
- frame count
- checksum errors

### TX / command status
- Experimental TX was added in `winix_monitor_v26.py` and removed again in `winix_monitor_v27.py`.
- State-shaped `0x10` frames sent at `38400 8N1` from RealTerm/app did not control the purifier.
- The debug header should be treated as RX/status-only until a separate command parser is proven.
- C545 WiFi-module control examples use a different `115200` ASCII `*ICT*...` protocol and do not apply to this no-WiFi unit.
- Practical control path is external button emulation, with UART RX used for feedback.
