# VARA Contact Lab — Windows VARA Modem Tester
<img width="1052" height="881" alt="image" src="https://github.com/user-attachments/assets/3bf2b094-cc04-49a8-91d8-75d1d5aee732" />

Automatically launch two VARA HF modems, establish a local audio contact, exchange and verify messages in both directions, and save separate labeled PCM WAV recordings. Sweep **BW500 → BW2300 → BW2750**, with a cancellable **10-second waiting period before each test contact**.

[Audio-sample demonstration video](https://www.youtube.com/watch?v=i3yrqm0G-74) · [Full step-by-step Windows guide](WINDOWS_STEP_BY_STEP.md)

## What you need

- Windows and VARA HF installed in both configured locations.
- Two independent installed virtual audio cables.
- Python 3.12 64-bit with its launcher to run/build from source.
- Internet access during dependency installation/building.

VARA, virtual audio drivers and recordings are not included.
## 1. Download and launch

1. Download this repository with **Code → Download ZIP**, then extract it completely to a local directory such as `C:\VARA-Tester`.
2. Install **Python 3.12 64-bit** with the Python launcher from [python.org](https://www.python.org/downloads/windows/).
3. Double-click **setup-windows.cmd** and wait for success.
4. Double-click **start-windows.cmd** to open the tester.

## 2. Set up the modems and audio once

Close VarAC, Winlink and other programs controlling either modem. Close both VARA instances before the first automatic launch, so selected ports/audio routes can be applied. Run each original modem once beforehand if it has not yet created its `VARA.ini`.

Defaults: (replace my callsign with yours)

| Station | Executable | Callsign | TCP command/data |
| --- | --- | --- | --- |
| 1 | `C:\VARA\VARA.exe` | N4EAC | 8300 / 8301 |
| 2 | `C:\VarAC\VARA\VARA.exe` | N4EAC-2 | 8310 / 8311 |

In **Modems & audio setup**, select your six endpoints. For the owner's Cable-A/Cable-B routing:

| Field | Device |
| --- | --- |
| S1 input | Cable-A OUT |
| S1 output | Cable-B In |
| S2 input | Cable-B OUT |
| S2 output | Cable-A In |
| Record station 1 output | Cable-B OUT, or Cable-B In loopback |
| Record station 2 output | Cable-A OUT, or Cable-A In loopback |

Choose actual names shown by your installed driver. Disable processing/feedback and keep unrelated audio off both routes. Settings are saved for later launches. The tester backs up each original INI before modifying selected ports/audio fields; already-running modems retain their current configuration.

## 3. Run the test

1. In **Contact & recording**, choose a capture folder, **BW500**, **1** contact and **300** seconds timeout per stage for the first check.
2. Leave the UTF-8 example message and **8192** payload bytes, or enter your own message. Text is repeated/padded to the requested byte count; the full actual payload is saved.
3. Click **Start full contact sweep**. The tester starts both modems, waits ten seconds before the contact, connects N4EAC to N4EAC-2, exchanges and verifies bytes in both directions, records both outputs, and disconnects.
4. Inspect the latest JSON: `status` must be `complete`; `peer_verification` and `reply_verification` must be `exact payload matched`.
5. Listen to both WAVs. Check capture diagnostics for silence, clipping and errors.
6. Select **All HF bandwidths** and run again for separate BW500, BW2300 and BW2750 contacts. Each contact gets its own ten-second wait. Stop cancels the wait/test and preserves available partial results.
7. For an exact binary case, select **Hex bytes** and enter `00 7F 80 FF`. Binary entries are sent exactly; payload-size padding applies only to UTF-8 mode.

## Outputs and speed labels

Each capture includes Station 1 `.wav`, Station 2 `.station2.wav`, exact payload/reply and received `.bin` files, message `.txt`, metadata `.json`, a full transcript `.csv`, and observed-rate `.rates.csv`.

`speed-coverage.json` lists observed and missing reference rates for the current sweep. [VARA_HF_REFERENCE_RATES.csv](VARA_HF_REFERENCE_RATES.csv) contains the historical reference table with source attribution.

**Bandwidth is not speed.** Original VARA adapts its rates; the documented interface does not guarantee every known rate will occur. Explicitly reported levels and reference-table candidates are kept separate. Rate timestamps are observations, not independently verified individual burst assignments. Unsupported BITRATE responses remain literal and unknown rates stay explicit.

## Build the Windows executable

Double-click **build.exe.bat**. It:

1. Detects a compatible 64-bit Python (3.12 preferred; 3.11/3.13 alternatives).
2. Creates/checks an isolated `.build-venv`.
3. Installs the declared dependencies and PyInstaller, checks imports and package consistency.
4. Runs the eleven automated tests.
5. Builds `dist\VARA-Modem-Tester.exe` and copies the setup guide and reference rates into `dist`.

The script stops on failure and leaves the console error visible. It does not install Python, original VARA or virtual audio drivers. `build-windows.cmd` calls the same build routine.

## Validation and troubleshooting

Eleven automated checks pass for the simulated exchanges, countdown/cancellation, configuration backups, dual recordings and rate coverage. Windows packaging, actual VARA launch/audio and interoperability still require a real Windows test.

For socket errors, check running instances, distinct ports and competing host applications. For connection failures, check both audio directions. For silent recordings, check the selected capture devices. See the [complete guide](WINDOWS_STEP_BY_STEP.md) for detailed recovery steps.

The tester does not implement radio CAT/serial PTT. Use a local audio bench setup. Keep licensed VARA installations and generated reference captures local; `.gitignore` excludes these files from this source repository.
