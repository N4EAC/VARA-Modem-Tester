"""Windows VARA capture tester. Protocol core also runs without audio dependencies."""
import csv
import hashlib
import json
import queue
import re
import socket
import struct
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from automation import launch_modems, load_settings, save_settings, rate_observations, write_coverage, wait_before_test


def call(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{3,7}(?:-(?:[1-9]|1[0-5]|T|R))?', value):
        raise ValueError('Use a 3–7 character callsign with optional valid SSID.')
    return value


class Modem:
    def __init__(self, host, port, log=lambda _: None):
        self.log = log
        self.events = []
        self.transcript = []
        self.received = bytearray()
        self.condition = threading.Condition()
        self.command_lock = threading.Lock()
        self.connected = False
        self.error = None
        self.closed = False
        self.cmd = socket.create_connection((host, port), 5)
        try:
            self.data = socket.create_connection((host, port + 1), 5)
        except Exception:
            self.cmd.close()
            raise
        self.cmd.settimeout(0.25)
        self.data.settimeout(0.25)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        self.data_reader = threading.Thread(target=self.read_data, daemon=True)
        self.data_reader.start()

    def read(self):
        pending = b''
        try:
            while not self.closed:
                try:
                    part = self.cmd.recv(4096)
                except socket.timeout:
                    continue
                if not part:
                    raise ConnectionError('Modem command socket closed.')
                pending += part
                if len(pending) > 65536:
                    raise ConnectionError('Oversized modem response.')
                while b'\r' in pending:
                    line, pending = pending.split(b'\r', 1)
                    line = line.decode('ascii', errors='replace').strip()
                    if not line:
                        continue
                    with self.condition:
                        now = time.monotonic()
                        self.events.append((now, line))
                        self.transcript.append((now, 'control_rx', line))
                        if line.startswith('CONNECTED '):
                            self.connected = True
                        elif line == 'DISCONNECTED':
                            self.connected = False
                        self.condition.notify_all()
                    self.log('< ' + line)
        except Exception as exc:
            if not self.closed:
                with self.condition:
                    self.error = exc
                    self.condition.notify_all()

    def read_data(self):
        try:
            while not self.closed:
                try:
                    data = self.data.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError('Modem data socket closed.')
                with self.condition:
                    self.received.extend(data)
                    self.transcript.append((time.monotonic(), 'data_rx', data.hex()))
                    self.condition.notify_all()
        except Exception as exc:
            if not self.closed:
                with self.condition:
                    self.error = exc
                    self.condition.notify_all()

    def write_data(self, payload):
        # Log the attempt separately: sendall may fail after a partial write.
        with self.condition:
            self.transcript.append((time.monotonic(), 'data_tx_attempt', payload.hex()))
        self.data.sendall(payload)
        with self.condition:
            self.transcript.append((time.monotonic(), 'data_tx_complete', payload.hex()))

    def wait_received(self, payload, timeout, stop):
        deadline = time.monotonic() + timeout
        with self.condition:
            while len(self.received) < len(payload):
                if stop.is_set():
                    raise InterruptedError('Stopped by user.')
                if self.error:
                    raise self.error
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out verifying payload at peer.')
                self.condition.wait(0.1)
            if bytes(self.received) != payload:
                raise ValueError('Peer received bytes differ from the exact payload.')

    def observe(self, command, stop):
        start = self.mark()
        self.send(command)
        # VERSION and BITRATE may reply with information instead of OK.
        self.wait(lambda line: line == 'OK' or line.startswith(command + ' '),
                  start, 5, stop)

    def send(self, command):
        if '\r' in command or '\n' in command:
            raise ValueError('Invalid command.')
        with self.condition:
            self.transcript.append((time.monotonic(), 'control_tx', command))
        self.log('> ' + command)
        with self.command_lock:
            self.cmd.sendall(command.encode('ascii') + b'\r')

    def mark(self):
        with self.condition:
            return len(self.events)

    def wait(self, predicate, start, timeout, stop, reject_wrong=True):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if stop.is_set():
                    raise InterruptedError('Stopped by user.')
                if self.error:
                    raise self.error
                for _, line in self.events[start:]:
                    if line == 'WRONG' and reject_wrong:
                        raise RuntimeError('VARA rejected a command (WRONG).')
                    if predicate(line):
                        return line
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for VARA; capture preserved.')
                self.condition.wait(0.1)

    def configure(self, command, stop):
        start = self.mark()
        self.send(command)
        self.wait(lambda line: line == 'OK', start, 5, stop)

    def close(self):
        self.closed = True
        for sock in (self.cmd, self.data):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.reader.join(1)
        self.data_reader.join(1)


class Recorder:
    def __init__(self, path, device):
        import pyaudiowpatch as pa
        self.pa = pa.PyAudio()
        self.stream = None
        self.wav = None
        self.status = []
        self.frames = 0
        self.peak = 0
        self.clipped = 0
        try:
            info = self.pa.get_device_info_by_index(device)
            self.info = dict(info)
            self.rate = int(info['defaultSampleRate'])
            self.channels = int(info['maxInputChannels'])
            self.wav = wave.open(str(path), 'wb')
            self.wav.setnchannels(self.channels)
            self.wav.setsampwidth(2)
            self.wav.setframerate(self.rate)
            def callback(data, frames, timing, status):
                if status:
                    self.status.append(int(status))
                self.wav.writeframesraw(data)
                self.frames += frames
                values = struct.unpack('<' + 'h' * (len(data) // 2), data)
                if values:
                    self.peak = max(self.peak, max(abs(x) for x in values))
                    self.clipped += sum(abs(x) >= 32767 for x in values)
                return (None, pa.paContinue)
            self.started = time.monotonic()
            self.stream = self.pa.open(format=pa.paInt16, channels=self.channels,
                                      rate=self.rate, input=True, input_device_index=device,
                                      frames_per_buffer=1024, stream_callback=callback)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        if self.wav:
            self.wav.close()
            self.wav = None
        self.pa.terminate()


def embed_info(path, message, speed):
    """Append standard RIFF INFO tags without altering PCM samples."""
    def chunk(tag, value):
        data = value.encode('utf-8') + b'\0'
        return tag + struct.pack('<I', len(data)) + data + b'\0' * (len(data) % 2)
    content = b'INFO' + chunk(b'INAM', 'VARA test: ' + speed) + chunk(b'ICMT', message)
    with path.open('r+b') as f:
        f.seek(0, 2)
        f.write(b'LIST' + struct.pack('<I', len(content)) + content)
        size = f.tell()
        f.seek(4)
        f.write(struct.pack('<I', size - 8))


def parse_payload(message, mode='UTF-8'):
    if mode == 'Hex bytes':
        try:
            payload = bytes.fromhex(message)
        except ValueError as exc:
            raise ValueError('Enter hexadecimal byte pairs, e.g. 00 7F 80 FF.') from exc
    else:
        payload = message.encode('utf-8')
    if not payload or len(payload) > 1024 * 1024:
        raise ValueError('Payload must contain 1 to 1,048,576 bytes.')
    return payload


def executable_info(path):
    if not path:
        return {'path': None, 'sha256': None, 'status': 'not supplied'}
    exe = Path(path).expanduser().resolve()
    h = hashlib.sha256()
    with exe.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return {'path': str(exe), 'sha256': h.hexdigest()}


def initialize(modem, callsign, bandwidth, stop):
    modem.configure('MYCALL ' + callsign, stop)
    if bandwidth != 'FM / SAT (unchanged)':
        modem.configure(bandwidth, stop)
    for command in ['CHAT ON', 'P2P SESSION']:
        modem.configure(command, stop)
    modem.observe('VERSION', stop)
    modem.configure('LISTEN ON', stop)


def run_test(config, device, message, stop, log, recorder_factory=Recorder):
    payload = parse_payload(message, config.get('payload_mode', 'UTF-8'))
    source, destination = call(config['source']), call(config['destination'])
    out = Path(config['output']).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    label = re.sub(r'[^A-Za-z0-9_-]+', '_', message[:32]).strip('_') or 'message'
    base = out / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + config['bandwidth'].split()[0]
                  + '_' + label + '_' + uuid.uuid4().hex[:6])
    wav = base.with_suffix('.wav')
    metadata = {'message': message, 'encoding': config.get('payload_mode', 'UTF-8'),
                'payload_hex': payload.hex(), 'bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(), 'configuration': config,
                'started_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'failed', 'speed': 'unknown', 'actual_speed_level': 'unknown',
                'requested_bandwidth': config['bandwidth'], 'completion': 'not confirmed',
                'peer_verification': 'not enabled'}
    modem = peer = recorder = peer_recorder = None
    poll_stop = threading.Event()
    poller = None
    start = time.monotonic()
    try:
        metadata['executable'] = executable_info(config.get('executable', ''))
        metadata['peer_executable'] = executable_info(config.get('peer_executable', ''))
        modem = Modem(config['host'], int(config['port']), log)
        if config.get('peer_port'):
            if (config.get('peer_host', '127.0.0.1'), int(config['peer_port'])) == (config['host'], int(config['port'])):
                raise ValueError('Peer must use a separate modem and port pair.')
            peer = Modem(config.get('peer_host', '127.0.0.1'), int(config['peer_port']),
                         lambda line: log('Peer ' + line))
            initialize(peer, destination, config['bandwidth'], stop)
        initialize(modem, source, config['bandwidth'], stop)
        recorder = recorder_factory(wav, device)
        start = recorder.started
        if peer and config.get('peer_capture_device') is not None:
            peer_recorder = recorder_factory(base.with_suffix('.station2.wav'), int(config['peer_capture_device']))
        if stop.wait(0.5):
            raise InterruptedError('Stopped by user.')
        mark = modem.mark()
        peer_mark = peer.mark() if peer else 0
        modem.send('CONNECT ' + source + ' ' + destination)
        modem.wait(lambda line: line.startswith('CONNECTED '), mark, config['timeout'], stop)
        if peer and config.get('full_contact'):
            peer.wait(lambda line: line.startswith('CONNECTED '), peer_mark, config['timeout'], stop)
        # Observation only. Rejection or lack of reply is recorded, not treated as support.
        try:
            modem.observe('BITRATE', stop)
            metadata['bitrate_query'] = 'response received; see transcript'
        except (RuntimeError, TimeoutError) as exc:
            metadata['bitrate_query'] = str(exc)
        if config.get('full_contact'):
            def poll_rates():
                while not poll_stop.wait(1):
                    for client in (modem, peer):
                        if client:
                            try:
                                client.send('BITRATE')
                            except OSError:
                                return
            # Stop polling if the initial query was rejected or timed out.
            if metadata.get('bitrate_query') == 'response received; see transcript':
                poller = threading.Thread(target=poll_rates, daemon=True)
                poller.start()
        if stop.is_set():
            raise InterruptedError('Stopped by user.')
        sent = time.monotonic()
        mark = modem.mark()
        modem.write_data(payload)
        metadata['payload_offset_seconds'] = sent - start
        modem.wait(lambda line: bool(re.fullmatch(r'BUFFER [1-9][0-9]*', line)), mark,
                   config['timeout'], stop, reject_wrong=False)
        with modem.condition:
            queued = next(i for i in range(mark, len(modem.events))
                          if re.fullmatch(r'BUFFER [1-9][0-9]*', modem.events[i][1]))
        modem.wait(lambda line: line == 'BUFFER 0' or line == 'DISCONNECTED', queued + 1,
                   config['timeout'], stop, reject_wrong=False)
        if not modem.connected:
            raise ConnectionError('Link disconnected before completion could be confirmed.')
        metadata['queue_drained_offset_seconds'] = time.monotonic() - start
        metadata['completion'] = 'VARA reported transmit buffer drained after nonzero queue'
        if peer:
            peer.wait_received(payload, config['timeout'], stop)
            metadata['peer_verification'] = 'exact payload matched'
            metadata['peer_verified_offset_seconds'] = time.monotonic() - start
        if peer and config.get('full_contact'):
            reply_unit = (destination + ' to ' + source + ': received ' + str(len(payload)) + ' bytes; full contact reply.\r\n').encode('ascii')
            reply = (reply_unit * (len(payload) // len(reply_unit) + 1))[:max(len(payload), len(reply_unit))]
            metadata['reply_payload_hex'] = reply.hex()
            metadata['reply_offset_seconds'] = time.monotonic() - start
            reply_mark = peer.mark()
            peer.write_data(reply)
            peer.wait(lambda line: bool(re.fullmatch(r'BUFFER [1-9][0-9]*', line)),
                      reply_mark, config['timeout'], stop, reject_wrong=False)
            with peer.condition:
                reply_queued = next(i for i in range(reply_mark, len(peer.events))
                    if re.fullmatch(r'BUFFER [1-9][0-9]*', peer.events[i][1]))
            peer.wait(lambda line: line == 'BUFFER 0', reply_queued+1,
                      config['timeout'], stop, reject_wrong=False)
            modem.wait_received(reply, config['timeout'], stop)
            metadata['reply_verification'] = 'exact payload matched'
            metadata['reply_verified_offset_seconds'] = time.monotonic() - start
        poll_stop.set()
        if poller:
            poller.join(2)
        mark = modem.mark()
        modem.send('DISCONNECT')
        modem.wait(lambda line: line == 'DISCONNECTED', mark, config['timeout'], stop, reject_wrong=False)
        if stop.wait(1):
            raise InterruptedError('Stopped by user.')
        metadata['status'] = 'complete'
    except Exception as exc:
        metadata['error'] = str(exc)
        if isinstance(exc, InterruptedError):
            metadata['status'] = 'cancelled'
        log(str(exc))
    finally:
        poll_stop.set()
        if poller:
            poller.join(2)
        for client in (modem, peer):
            if client:
                if client.connected or metadata['status'] != 'complete':
                    try:
                        client.send('ABORT')
                    except OSError:
                        pass
                client.close()
        if recorder:
            try:
                recorder.close()
            except Exception as exc:
                metadata['audio_close_error'] = str(exc)
                metadata['status'] = 'failed'
            metadata['audio'] = {'device': recorder.info, 'sample_rate': recorder.rate,
                                 'channels': recorder.channels, 'channel_mapping': config.get('channel_mapping', 'unspecified'),
                                 'direction': 'initiator output', 'frames': recorder.frames,
                                 'peak': recorder.peak, 'clipped_samples': recorder.clipped,
                                 'callback_status_flags': recorder.status,
                                 'timing_basis': 'host monotonic estimate; not hardware-synchronized'}
            if not recorder.frames or recorder.peak == 0 or recorder.status:
                metadata['audio_warning'] = 'Audio is empty, silent, or has capture status errors.'
        if peer_recorder:
            try:
                peer_recorder.close()
                metadata['peer_audio'] = {'device': peer_recorder.info, 'sample_rate': peer_recorder.rate,
                    'channels': peer_recorder.channels, 'frames': peer_recorder.frames,
                    'peak': peer_recorder.peak, 'clipped_samples': peer_recorder.clipped,
                    'callback_status_flags': peer_recorder.status,
                    'start_offset_seconds': peer_recorder.started - start, 'direction': 'station2 output'}
                if not peer_recorder.frames or peer_recorder.peak == 0 or peer_recorder.status:
                    metadata['peer_audio_warning'] = 'Audio is empty, silent, or has capture status errors.'
            except Exception as exc:
                metadata['peer_audio_error'] = str(exc)
                metadata['status'] = 'failed'
        events = [] if not modem else modem.events
        speeds = [line for _, line in events if line.startswith('BITRATE')]
        metadata['version_reports'] = [line for _, line in events if line.startswith('VERSION')]
        metadata['speed_reports'] = speeds
        metadata['speed'] = ' | '.join(dict.fromkeys(speeds)) or 'unknown (no BITRATE report)'
        metadata['events'] = [{'offset_seconds': round(t - start, 6), 'response': line}
                              for t, line in events]
        timeline = []
        for role, client in [('initiator', modem), ('peer', peer)]:
            if client:
                timeline.extend({'offset_seconds': round(t - start, 6), 'role': role,
                                 'kind': kind, 'value': value}
                                for t, kind, value in client.transcript)
        timeline.sort(key=lambda event: event['offset_seconds'])
        metadata['transcript'] = timeline
        metadata['rate_observations'] = rate_observations(timeline, config['bandwidth'])
        if peer:
            if metadata['peer_verification'] == 'exact payload matched' and bytes(peer.received) != payload:
                metadata['peer_verification'] = 'mismatch: extra or changed bytes before socket close'
                metadata['status'] = 'failed'
            if config.get('full_contact') and metadata.get('reply_payload_hex') and bytes(modem.received).hex() != metadata['reply_payload_hex']:
                metadata['reply_verification'] = 'mismatch at socket close'
                metadata['status'] = 'failed'
            metadata['peer_received_hex'] = bytes(peer.received).hex()
            metadata['peer_version_reports'] = [line for _, line in peer.events if line.startswith('VERSION')]
            metadata['peer_received_bytes'] = len(peer.received)
        # Rates are preserved literally; do not assign a burst level from an unverified response format.
        observed_rates = sorted({o['bps'] for o in metadata['rate_observations']})
        suffix = ('_observed-' + '-'.join(map(str, observed_rates)) + 'bps') if observed_rates else '_rate-unknown'
        final = base.with_name(base.name + suffix)
        metadata['label_note'] = 'Observed rates are timestamped reports; individual burst assignment remains unverified.'
        peer_wav = base.with_suffix('.station2.wav')
        if peer_wav.exists():
            try:
                embed_info(peer_wav, 'Station 2 reply to: ' + message, config['bandwidth'] + '; literal rate reports in JSON')
                peer_wav.rename(final.with_suffix('.station2.wav'))
                metadata['peer_wav'] = final.with_suffix('.station2.wav').name
            except Exception as exc:
                metadata['peer_wav_error'] = str(exc)
                metadata['peer_wav'] = peer_wav.name
                metadata['status'] = 'failed'
        if wav.exists():
            try:
                embed_info(wav, message, config['bandwidth'] + '; ' + metadata['speed'] + '; level unknown')
                wav.rename(final.with_suffix('.wav'))
                metadata['wav'] = final.with_suffix('.wav').name
            except Exception as exc:
                metadata['wav_error'] = str(exc)
                metadata['wav'] = wav.name
                metadata['status'] = 'failed'
        if modem and config.get('full_contact'):
            final.with_suffix('.station1-received.bin').write_bytes(modem.received)
        if peer:
            final.with_suffix('.peer-received.bin').write_bytes(peer.received)
        if metadata.get('reply_payload_hex'):
            final.with_suffix('.reply.bin').write_bytes(bytes.fromhex(metadata['reply_payload_hex']))
        final.with_suffix('.bin').write_bytes(payload)
        with final.with_suffix('.rates.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['offset_seconds','role','direction','bps','reported_level','reference_level','literal_response'])
            writer.writerows((o['offset_seconds'],o['role'],o['direction'],o['bps'],o['reported_level'],o['reference_level'],o['value']) for o in metadata['rate_observations'])
        final.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        final.with_suffix('.txt').write_text(message, encoding='utf-8')
        with final.with_suffix('.csv').open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['offset_seconds', 'role', 'kind', 'value'])
            writer.writerows((e['offset_seconds'], e['role'], e['kind'], e['value']) for e in timeline)
        log('Saved ' + str(final) + ' — ' + metadata['status'])
    return metadata


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    root = tk.Tk()
    root.title('VARA Contact Lab — N4EAC ↔ N4EAC-2')
    root.geometry('1050x850')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TButton', padding=8)
    style.configure('Title.TLabel', font=('Segoe UI', 21, 'bold'))
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='VARA CONTACT LAB', style='Title.TLabel').pack(anchor='w')
    ttk.Label(frame, text='Start both modems · Connect N4EAC ↔ N4EAC-2 · Exchange & verify · Record both outputs').pack(anchor='w', pady=(0,10))
    saved = load_settings()
    fields = {}
    defaults = {'host':'127.0.0.1', 'port':'8300', 'peer_host':'127.0.0.1', 'peer_port':'8310',
        'source':'N4EAC', 'destination':'N4EAC-2', 'executable':r'C:\VARA\VARA.exe',
        'peer_executable':r'C:\VarAC\VARA\VARA.exe', 'timeout':'300', 'repeat':'1',
        'output':str(Path.home() / 'Documents' / 'VARA Captures'), 'registration':'unknown',
        'channel_mapping':'Station 1 and Station 2 in separate WAVs', 'notes':'Local two-modem contact',
        'payload_mode':'UTF-8', 'bandwidth':'All HF bandwidths', 'payload_size':'8192'}
    for key, value in defaults.items():
        fields[key] = tk.StringVar(value=str(saved.get(key, value)))
    tabs = ttk.Notebook(frame)
    tabs.pack(fill='x')
    contact = ttk.Frame(tabs, padding=12)
    setup = ttk.Frame(tabs, padding=12)
    tabs.add(contact, text='Contact & recording')
    tabs.add(setup, text='Modems & audio setup')
    def entry(parent, row, label, key):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=3)
        ttk.Entry(parent, textvariable=fields[key], width=65).grid(row=row, column=1, sticky='ew', pady=3)
        parent.columnconfigure(1, weight=1)
    for row, (label, key) in enumerate([('Station 1 executable','executable'),('Station 2 executable','peer_executable'),
        ('Station 1 callsign','source'),('Station 2 callsign','destination'),('Station 1 command port','port'),
        ('Station 2 command port','peer_port')]):
        entry(setup, row, label, key)
    ttk.Label(setup, text='Two virtual cables required: S1 output → S2 input; S2 output → S1 input.').grid(row=6,column=0,columnspan=2,sticky='w',pady=8)
    audio_boxes = {}
    device_list = []
    for row, (key, label) in enumerate([('station1_input','S1 input (Cable 2 recording)'),
        ('station1_output','S1 output (Cable 1 playback)'),('station2_input','S2 input (Cable 1 recording)'),
        ('station2_output','S2 output (Cable 2 playback)'),('capture1','Record station 1 output'),('capture2','Record station 2 output')],7):
        ttk.Label(setup,text=label).grid(row=row,column=0,sticky='w')
        box = ttk.Combobox(setup,state='readonly',width=68)
        box.grid(row=row,column=1,sticky='ew',pady=2)
        audio_boxes[key] = box
    def refresh():
        try:
            import pyaudiowpatch as pa
            with pa.PyAudio() as audio:
                device_list[:] = [audio.get_device_info_by_index(i) for i in range(audio.get_device_count())]
            for key, box in audio_boxes.items():
                is_output = key.endswith('_output')
                infos = [d for d in device_list if d['maxOutputChannels' if is_output else 'maxInputChannels'] > 0]
                box.devices = infos
                box['values'] = [f"{int(d['index'])}: {d['name']}" for d in infos]
                match = next((i for i,d in enumerate(infos) if d['name'] == saved.get(key)), None)
                if match is not None:
                    box.current(match)
            status.set('Choose the six audio endpoints once in Modems & audio setup. Settings are saved.')
        except Exception as exc:
            messagebox.showerror('Audio dependency', str(exc) + '\nRun setup-windows.cmd or build.exe.bat.')
    ttk.Button(setup,text='Refresh audio endpoints',command=refresh).grid(row=13,column=1,sticky='w',pady=5)
    ttk.Label(setup,text='Close VARA before changing routes. INI files are backed up; running instances retain current settings.').grid(row=14,column=0,columnspan=2,sticky='w')
    for row, (label,key) in enumerate([('Capture folder','output'),('Timeout per stage (s)','timeout'),
        ('Contacts per bandwidth','repeat'),('Payload bytes (UTF-8 repeat/pad)','payload_size')]):
        entry(contact,row,label,key)
    ttk.Button(contact,text='Browse',command=lambda: choose_folder()).grid(row=0,column=2)
    def choose_folder():
        folder = filedialog.askdirectory()
        if folder: fields['output'].set(folder)
    ttk.Label(contact,text='Bandwidth sweep').grid(row=4,column=0,sticky='w')
    ttk.Combobox(contact,textvariable=fields['bandwidth'],values=['All HF bandwidths','BW500','BW2300','BW2750'],state='readonly').grid(row=4,column=1,sticky='ew')
    ttk.Combobox(contact,textvariable=fields['payload_mode'],values=['UTF-8','Hex bytes'],state='readonly').grid(row=5,column=0,sticky='w',pady=6)
    ttk.Label(contact,text='Longer payloads give VARA time to adapt; they do not force specific rates.').grid(row=5,column=1,sticky='w')
    payload_box = tk.Text(contact,height=4,font=('Consolas',11))
    payload_box.grid(row=6,column=0,columnspan=3,sticky='ew')
    payload_box.insert('1.0',saved.get('message','N4EAC to N4EAC-2: VARA reference contact 001.\r\n'))
    ttk.Label(contact,text='Text is repeated to the requested byte count. Hex payloads are sent exactly as entered.').grid(row=7,column=0,columnspan=3,sticky='w')
    messages = queue.Queue()
    stop = threading.Event()
    worker = None
    closing = False
    status = tk.StringVar(value='Ready for audio setup')
    ttk.Label(frame,textvariable=status,font=('Segoe UI',11,'bold')).pack(anchor='w',pady=10)
    controls = ttk.Frame(frame)
    controls.pack(fill='x')
    logbox = tk.Text(frame,height=12,state='disabled',font=('Consolas',10))
    logbox.pack(fill='both',expand=True,pady=8)
    def config_from_ui():
        config = {k:v.get().strip() for k,v in fields.items()}
        config['port'],config['peer_port'] = int(config['port']),int(config['peer_port'])
        config['timeout'] = float(config['timeout'])
        config['repeat'] = int(config['repeat'])
        if not 1 <= config['repeat'] <= 100 or not 1 <= config['timeout'] <= 3600:
            raise ValueError('Use 1–100 contacts and a 1–3600 second timeout.')
        call(config['source']); call(config['destination'])
        for key,box in audio_boxes.items():
            if box.current() < 0:
                tabs.select(setup)
                raise ValueError('Select ' + key + ' in the Modems & audio setup tab.')
            info = box.devices[box.current()]
            config[key] = info['name']
            if key.startswith('capture'):
                config[key + '_device'] = int(info['index'])
        if config['capture1_device'] == config['capture2_device']:
            raise ValueError('Record the two directions using distinct capture devices.')
        config['peer_capture_device'] = config['capture2_device']
        config['full_contact'] = True
        config['message'] = payload_box.get('1.0','end-1c')
        raw = parse_payload(config['message'],config['payload_mode'])
        if config['payload_mode'] == 'UTF-8':
            size = int(config['payload_size'])
            if not 1 <= size <= 1048576:
                raise ValueError('Payload byte count must be 1–1048576.')
            # Repeat complete text units, then ASCII pad: retain valid UTF-8 boundaries.
            units,remainder = divmod(size,len(raw))
            if not units:
                raise ValueError('Payload byte count must fit at least one full message.')
            config['test_message'] = config['message'] * units + '.' * remainder
        else:
            config['test_message'] = config['message']
        save_settings({k:v for k,v in config.items() if k != 'test_message'})
        return config
    def start(launch_only=False):
        nonlocal worker
        try: config = config_from_ui()
        except Exception as exc:
            messagebox.showerror('Setup',str(exc)); return
        stop.clear()
        run_button['state'] = launch_button['state'] = 'disabled'
        status.set('Starting modems…')
        def work():
            results = []
            try:
                launch_modems(config,stop,messages.put)
                if launch_only:
                    messages.put('Modems ready. Click Start full contact sweep to record.')
                    return
                bands = list(['BW500','BW2300','BW2750']) if config['bandwidth']=='All HF bandwidths' else [config['bandwidth']]
                for band in bands:
                    for i in range(config['repeat']):
                        if stop.is_set(): return
                        messages.put(f'CONTACT {band} {i+1}/{config["repeat"]}: connect, exchange, verify, disconnect')
                        wait_before_test(band, stop, messages.put, seconds=10)
                        case = dict(config,bandwidth=band,pre_test_wait_seconds=10)
                        result = run_test(case,config['capture1_device'],config['test_message'],stop,messages.put)
                        results.append(result)
                        write_coverage(results,config['output'])
                        if result['status'] != 'complete': return
                messages.put('Sweep complete. speed-coverage.json lists observed and unobserved reference rates.')
            except Exception as exc:
                messages.put('Error: ' + str(exc))
            finally:
                messages.put(None)
        worker = threading.Thread(target=work,daemon=True)
        worker.start()
    run_button = ttk.Button(controls,text='Start full contact sweep',command=start)
    run_button.pack(side='left')
    launch_button = ttk.Button(controls,text='Start modems only',command=lambda:start(True))
    launch_button.pack(side='left',padx=8)
    ttk.Button(controls,text='Stop',command=stop.set).pack(side='left')
    def close():
        nonlocal closing
        closing=True; stop.set()
    root.protocol('WM_DELETE_WINDOW',close)
    def poll():
        while not messages.empty():
            item=messages.get()
            if item is None:
                run_button['state']=launch_button['state']='normal'
            else:
                status.set(item)
                logbox['state']='normal'; logbox.insert('end',item+'\n'); logbox.see('end'); logbox['state']='disabled'
        if closing and (worker is None or not worker.is_alive()):
            root.destroy(); return
        root.after(100,poll)
    root.after(100,refresh)
    poll()
    root.mainloop()


if __name__ == '__main__':
    main()
