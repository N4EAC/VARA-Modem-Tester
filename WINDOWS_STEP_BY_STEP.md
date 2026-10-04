# VARA Contact Lab: Windows setup and automatic contact

## 1. Copy and install

1. Copy the entire project directory to Windows, for example `C:\VARA-Tester`. Extract completely if transferred as a ZIP.
2. Keep `vara_tester.py`, `automation.py`, `requirements.txt`, the scripts, and `tests` together. Omit any macOS `.venv` or `__pycache__` directories.
3. Install Python **3.12 64-bit** with the `py` launcher from https://www.python.org/downloads/windows/.
4. Double-click `setup-windows.cmd`. Wait for the setup-complete message.
5. Double-click `start-windows.cmd` to open the tester.

Alternatively, double-click **build.exe.bat** to detect a compatible 64-bit Python, create an isolated build environment, install/check dependencies, and build `dist\VARA-Modem-Tester.exe`. The build detects Python 3.12 first, then 3.11 or 3.13. If no compatible Python is installed, it gives installation instructions. Internet is required to obtain dependencies. The script does not install Python or audio drivers automatically.

## 2. Prepare the original modems once

The defaults match your installations:

| Station | Executable | Callsign | TCP command/data |
| --- | --- | --- | --- |
| 1 | `C:\VARA\VARA.exe` | `N4EAC` | 8300 / 8301 |
| 2 | `C:\VarAC\VARA\VARA.exe` | `N4EAC-2` | 8310 / 8311 |

1. Verify both executable paths exist.
2. Each installation must have its own `VARA.ini` beside its executable. If absent, run that VARA instance once, finish its initial setup, then close it.
3. Close both VARA instances before the tester's first automatic launch so it can apply your selected ports and audio devices.
4. Close VarAC, Winlink, EtherChat and other programs using either modem. The second executable is inside the VarAC directory, but **VarAC itself should remain closed** during the test.
5. Retain your registration settings in the original installations. They can limit attainable rates. The tester preserves unrelated INI settings and backs up the file before editing selected port/audio fields.
6. Keep the bench setup disconnected from RF. This routine exchanges real audio between two original VARA modems locally; it does not synthesize fake CONNECTED responses. CAT/serial radio PTT is not implemented.

## 3. Install and select two virtual audio cables

The tester can select installed devices and configure VARA to use them. It cannot create the driver endpoints; two independent virtual cables must already be installed.

| Direction | VARA output | VARA input | Tester recording |
| --- | --- | --- | --- |
| Station 1 → Station 2 | Cable 1 playback | Cable 1 recording | Cable 1 playback loopback or recording endpoint |
| Station 2 → Station 1 | Cable 2 playback | Cable 2 recording | Cable 2 playback loopback or recording endpoint |

Some products name the playback endpoint “Input” and the recording endpoint “Output”. Match cable identifiers and device directions, rather than those words alone.

1. Open **Modems & audio setup** in the tester.
2. Confirm the executable paths, callsigns and ports above.
3. Choose all six endpoints:
   - **S1 input:** Cable 2 recording.
   - **S1 output:** Cable 1 playback.
   - **S2 input:** Cable 1 recording.
   - **S2 output:** Cable 2 playback.
   - **Record station 1 output:** Cable 1 loopback or recording endpoint.
   - **Record station 2 output:** Cable 2 loopback or recording endpoint.
4. Use distinct recording devices for the two outputs. Do not mix both stations into a single cable.
5. Click **Refresh audio endpoints** after installing/changing devices, and select them again.
6. Disable enhancements, automatic gain, noise suppression and “Listen to this device” feedback routes. Keep unrelated audio off these cables.
7. Prefer compatible rates across paired endpoints; captures use the selected device's native rate and 16-bit PCM.
8. Click **Start modems only**. This validates the selected fields, backs up and updates INIs for stopped modems, starts both executables, and waits up to 45 seconds for their TCP ports.
9. Inspect the two VARA windows. Confirm selected devices and ports. Original VARA stores audio names in a short field; ambiguous/truncated names or a differing INI format may need adjustment in VARA's own sound-card settings.
10. Briefly use Station 1 TUNE to check Station 2's input meter, then turn it off. Repeat Station 2 → Station 1. Adjust levels to avoid clipping. Both TUNE buttons must be off before contact capture.

If a matching executable is already running, the tester retains its current configuration and attempts to reuse it. To change ports or routes, close that instance first and launch again. Started VARA applications remain open after the tester exits or Stop is pressed.

Audio selections and other tester settings are saved in `%LOCALAPPDATA%\VARA-Modem-Tester\settings.json`; they are restored next time. Audio devices are restored by name and resolved to current device IDs. Check selections after changing drivers.

### Your Cable-A / Cable-B selections

| Tester selection | Your device |
| --- | --- |
| S1 input | Cable-A OUT |
| S1 output | Cable-B In |
| S2 input | Cable-B OUT |
| S2 output | Cable-A In |
| Record station 1 output | Cable-B OUT (or Cable-B In loopback) |
| Record station 2 output | Cable-A OUT (or Cable-A In loopback) |

## 4. Run one complete contact

1. Open **Contact & recording**.
2. Choose a local capture folder.
3. For the first check, select **BW500**, **1** contact, timeout **300** seconds per stage.
4. Choose **UTF-8** and enter a message such as `N4EAC to N4EAC-2: VARA reference contact 001.`
5. Use **Payload bytes** = `8192` to provide enough data for adaptation. Text is repeated in complete UTF-8 units and padded with ASCII periods to this byte count. The full actual message is saved in the capture; Hex bytes mode sends exactly the entered bytes and ignores this size field.
6. Click **Start full contact sweep**. It starts missing modems automatically; no need to start them separately.
7. The tester configures the stations, opens listening mode, connects N4EAC to N4EAC-2, and waits for both actual CONNECTED responses.
8. It records both station outputs, sends the Station 1 payload, verifies it at Station 2, sends a similarly sized Station 2 reply, verifies it at Station 1, and requests disconnect after both queues drain.
9. Wait for `complete`. Use Stop to cancel; partial results are saved and modem applications stay open.
10. Inspect the latest JSON: `status` should be `complete`, and both `peer_verification` and `reply_verification` should be `exact payload matched`.
11. Inspect both WAVs and their diagnostics. A successful byte exchange does not prove you chose the correct recording devices. Check frames, peak, clipping, status flags and any audio warning.

The routine uses real VARA modulation/ARQ over the installed audio routes. A connection failure usually points to startup, port ownership, audio routing or level problems.

## 5. Sweep the HF bandwidths

1. After a successful BW500 contact, select **All HF bandwidths**.
2. Choose contacts per bandwidth and payload size. A larger payload gives adaptive VARA time to climb rates; it cannot guarantee every rate will occur.
3. Click **Start full contact sweep**. Before every contact, a cancellable **10-second countdown** lets the modems settle. Separate sessions run at BW500, BW2300 and BW2750. Bandwidth is never changed inside a live contact.
4. A failed contact stops the sweep. Inspect its transcript and fix the cause before retrying.
5. For a binary diagnostic, choose **Hex bytes**, enter `00 7F 80 FF`, and run a separate contact. Four bytes generally do not exercise high adaptive rates.
6. Inspect `speed-coverage.json`, which is refreshed for the current sweep. It lists the published reference speeds observed and not observed for each bandwidth. It does not accumulate older sweep results.

## 6. Rate and level labeling

The published historical HF reference table is included as `VARA_HF_REFERENCE_RATES.csv`, with source attribution. Its rate set may differ from your original VARA version.

- Requested BW500/BW2300/BW2750 appears in the capture name and metadata.
- Literal modem BITRATE reports are retained in JSON/CSV.
- Recognized numeric reports appear in `.rates.csv` with time, station, TX/RX direction, bps, any explicitly reported level, and a separate candidate level matched from the reference table.
- Filenames include observed bps values, or `rate-unknown` if unavailable.
- The tester attempts BITRATE after connecting and polls it during transfers if the initial query replies successfully. Unsupported queries are retained; they do not stop data transfer.
- No report is treated as proof of a particular waveform burst's level. Individual burst assignment remains unverified, and unknown values remain explicit.

**The software does not force or guarantee every known speed.** The original VARA TCP interface provided so far exposes bandwidth and adaptive rates, not a supported per-frame speed selector. Coverage reports expose the missing speeds instead of falsely labeling them as captured. Registration, channel quality, payload length, and modem version affect the observed rates.

## 7. Capture artifacts

Keep each group together:

| File | Meaning |
| --- | --- |
| `.wav` | Station 1 output through the complete contact |
| `.station2.wav` | Station 2 output, recorded independently |
| `.bin` | Exact Station 1 transmitted payload |
| `.peer-received.bin` | Station 2 received bytes |
| `.reply.bin` | Exact Station 2 reply |
| `.station1-received.bin` | Station 1 received bytes |
| `.txt` | Actual text/hex payload used |
| `.json` | Configuration, executable hashes, timestamps, both verification outcomes, version/rate reports and diagnostics |
| `.csv` | Full command/data transcript for both stations |
| `.rates.csv` | Parsed observed rates, explicitly reported levels and separate table candidates |
| `speed-coverage.json` | Current sweep's observed/missing reference rates |

Recording starts before CONNECT with lead-in silence, then continues through disconnect and trailing silence. The recordings start independently; the Station 2 start offset is stored. Timing is based on the host monotonic clock, not exact hardware sample synchronization. Original VARA logs/screenshots are not copied automatically; retain them manually if needed. Keep installations and reference captures local.

## 8. Troubleshooting

- **Python/setup failure:** verify `py -3.12 --version`, install/repair the 64-bit launcher, then rerun setup. Preserve the console error if dependency installation fails.
- **Build failure:** read the build console. It installs/checks dependencies before packaging. A reused `.build-venv` must belong to this Windows machine; delete that build environment and retry if copied from elsewhere. PyAudioWPatch must provide a wheel for your interpreter.
- **Missing VARA.ini:** open the original modem once, finish setup, then close it and retry.
- **Cannot update INI:** the user account needs write access to the installation folders. Backups remain beside each INI. Do not discard them until the setup is verified.
- **Ports not ready:** inspect the original modem windows for startup dialogs; close competing applications and confirm separate port pairs. Existing running modems retain their old port settings.
- **Connection timeout:** verify both audio directions, callsigns, input meters and device settings. Socket readiness does not prove an audio link exists.
- **WRONG at startup:** inspect the preceding command. This routine targets original VARA HF; required startup commands must be supported by your version.
- **Silent WAV despite successful transfer:** select the correct output loopback/virtual input; inspect both capture-device names and audio diagnostics.
- **Queue timeout:** the tester conservatively requires a positive BUFFER report followed by zero. Very brief omitted reports can cause a timeout; preserve the transcript to investigate.
- **Rates missing:** inspect literal BITRATE replies. Older or unsupported versions may supply no usable rate reports. Do not infer speed from socket throughput or bandwidth.
- **Low maximum rate:** inspect registration and channel quality. Increase the transfer length if appropriate, then examine actual reports; never assume all reference speeds occurred.

## Validation status

Eleven automated tests cover countdown/cancellation, two-way contacts, exact text/binary delivery, separate audio artifacts, INI backups, and reference-rate coverage. Actual Windows launch, packaged executable, original VARA interoperability and audio capture still require an end-to-end run on your machine.

## Demonstration and future downloads

[User-provided audio-sample demonstration](https://www.youtube.com/watch?v=i3yrqm0G-74).

The public repository initially contains source code and build instructions. A prebuilt Windows setup file and screenshots can be uploaded later by the owner. Until then, build locally with `build.exe.bat` or run from Python. A demonstration does not replace checking both current recording routes and byte-verification results.
