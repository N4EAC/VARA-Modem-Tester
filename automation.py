"""Local Windows launch, persistent setup, and reference-rate coverage."""
import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

RATE_SOURCE = 'https://islandmagic.co/radiomail/help/classic/vara'
RATES = {
    'BW500': [18,41,61,88,177,270,441,588,705,884,1060,1286,1543],
    'BW2300': [18,41,82,175,270,363,549,735,922,2011,2682,3219,4025,4830,5872,7050],
    'BW2750': [18,41,82,175,270,363,549,735,922,1203,2423,3230,3877,4848,5817,7074,8489],
}

def settings_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'VARA-Modem-Tester' / 'settings.json'

def save_settings(config):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')

def load_settings():
    try:
        return json.loads(settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

def update_ini(path, changes):
    """Preserve unknown/license settings, create a backup, edit only selected fields."""
    if not path.exists():
        raise FileNotFoundError(f'{path} is missing. Run VARA once to create its configuration.')
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'cp1252')
    lines = text.splitlines()
    section = None
    remaining = dict(changes)
    result = []
    for line in lines:
        if line.strip().startswith('['):
            for (sec, key), value in list(remaining.items()):
                if sec == section:
                    result.append(f'{key}={value}')
                    del remaining[(sec, key)]
            section = line.strip().strip('[]')
        key = line.split('=', 1)[0].strip()
        if (section, key) in remaining:
            line = f'{key}={remaining.pop((section, key))}'
        result.append(line)
    for (sec, key), value in list(remaining.items()):
        if sec == section:
            result.append(f'{key}={value}')
            del remaining[(sec, key)]
    for sec in dict.fromkeys(sec for sec, key in remaining):
        result.append(f'[{sec}]')
        result.extend(f'{key}={value}' for (s, key), value in remaining.items() if s == sec)
    backup = path.with_name(path.name + '.tester-backup-' + str(time.time_ns()))
    shutil.copy2(path, backup)
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'cp1252'
    path.write_bytes(('\r\n'.join(result) + '\r\n').encode(encoding))
    return backup

def running_executable(exe):
    import psutil
    target = os.path.normcase(str(exe.resolve()))
    for process in psutil.process_iter(['exe']):
        try:
            actual = process.info['exe']
            if actual and os.path.normcase(str(Path(actual).resolve())) == target:
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return False

def launch_modems(config, stop, log):
    if os.name != 'nt':
        raise RuntimeError('Automatic VARA launch requires Windows.')
    routes = []
    for role, path_key, port_key, prefix, kiss in [
        ('Station 1', 'executable', 'port', 'station1', 8100),
        ('Station 2', 'peer_executable', 'peer_port', 'station2', 8110),
    ]:
        exe = Path(config[path_key])
        if not exe.is_file():
            raise FileNotFoundError(f'{role}: executable not found: {exe}')
        routes.append((role, exe, int(config[port_key]), prefix, kiss))
    ports = [p + offset for _, _, p, _, _ in routes for offset in (0, 1)]
    if len(set(ports)) != 4 or any(not 1 <= p <= 65535 for p in ports):
        raise ValueError('The two command/data port pairs must be distinct and valid.')
    if routes[0][1].resolve() == routes[1][1].resolve():
        raise ValueError('Use two separate VARA installations.')
    for role, exe, port, prefix, kiss in routes:
        if running_executable(exe):
            log(f'{role}: already running; retaining its current INI. Close it to apply changed routing/ports.')
            continue
        changes = {('Setup', 'TCP Command Port'): str(port), ('Setup', 'KISS Port'): str(kiss)}
        for direction in ('input', 'output'):
            name = config.get(prefix + '_' + direction)
            if name:
                # Original VARA stores Windows audio names in a 31-character field.
                changes[('Soundcard', direction.title() + ' Device Name')] = name[:31]
        backup = update_ini(exe.with_name('VARA.ini'), changes)
        log(f'{role}: backed up configuration to {backup}')
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        log(f'{role}: started {exe}')
    deadline = time.monotonic() + 45
    pending = set(ports)
    while pending:
        if stop.is_set():
            raise InterruptedError('Launch cancelled; VARA applications remain open.')
        for port in list(pending):
            try:
                with socket.create_connection(('127.0.0.1', port), .2):
                    pending.remove(port)
            except OSError:
                pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f'VARA ports not ready: {sorted(pending)}. Check setup or startup dialogs.')
        stop.wait(.3)
    log('Both modem TCP pairs are ready. Establishing the audio contact next.')


def rate_observations(transcript, bandwidth):
    observations = []
    for event in transcript:
        if event['kind'] != 'control_rx' or not event['value'].startswith('BITRATE'):
            continue
        literal = event['value']
        explicit = re.search(r'\((\d+)\)\s*(\d+)\s*BPS', literal, re.I)
        pairs = re.findall(r'\b(TX|RX)\s*[:=]?\s*(\d+)\s*(?:BPS)?', literal, re.I)
        if explicit:
            pairs = [('unspecified', explicit.group(2))]
        for direction, rate in pairs:
            rate = int(rate)
            candidate = RATES.get(bandwidth, [])
            observations.append(dict(event, direction=direction.upper(), bps=rate,
                reported_level=int(explicit.group(1)) if explicit else None,
                reference_level=candidate.index(rate)+1 if rate in candidate else None,
                label_basis='literal modem report; table match is a reference candidate',
                burst_assignment='not independently verified'))
    return observations


def write_coverage(results, folder):
    coverage = {'reference_source': RATE_SOURCE, 'scope': 'reported rates, not verified individual bursts', 'bandwidths': {}}
    for bandwidth, rates in RATES.items():
        observed = sorted({o['bps'] for r in results if r['requested_bandwidth'] == bandwidth
                           for o in r.get('rate_observations', [])})
        coverage['bandwidths'][bandwidth] = {
            'reference_levels': [{'level': i+1, 'bps': rate, 'observed': rate in observed} for i, rate in enumerate(rates)],
            'observed_bps': observed, 'unobserved_bps': [r for r in rates if r not in observed]}
    Path(folder, 'speed-coverage.json').write_text(json.dumps(coverage, indent=2), encoding='utf-8')
    return coverage


def wait_before_test(bandwidth, stop, log, seconds=10):
    """Leave both modems idle before each new bandwidth contact; Stop stays responsive."""
    for remaining in range(seconds, 0, -1):
        log(f'{bandwidth}: waiting {remaining}s before contact')
        if stop.wait(1):
            raise InterruptedError('Stopped during pre-test waiting period.')
