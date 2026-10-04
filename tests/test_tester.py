import socket
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from vara_tester import Modem, call, parse_payload, executable_info, run_test


class FakeModem:
    def __enter__(self):
        for _ in range(100):
            self.commands = socket.socket()
            self.commands.bind(('127.0.0.1', 0))
            self.port = self.commands.getsockname()[1]
            self.data = socket.socket()
            try:
                self.data.bind(('127.0.0.1', self.port + 1))
                break
            except OSError:
                self.commands.close(); self.data.close()
        else:
            raise RuntimeError('Cannot allocate adjacent ports')
        self.commands.listen(); self.data.listen()
        self.received = b''
        self.lines = []
        self.relay = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        return self

    def serve(self):
        with self.commands.accept()[0] as cmd, self.data.accept()[0] as data:
            self.data_conn = data
            self.cmd_conn = cmd
            def receive():
                try:
                    while True:
                        block = data.recv(65536)
                        if not block:
                            return
                        self.received += block
                        if self.relay:
                            self.relay.data_conn.sendall(block)
                        cmd.sendall(b'BUFFER 100\rBITRATE TX 500 RX 175\rBUFFER 0\r')
                except OSError:
                    return
            threading.Thread(target=receive, daemon=True).start()
            pending = b''
            while True:
                part = cmd.recv(1024)
                if not part:
                    return
                pending += part
                while b'\r' in pending:
                    line, pending = pending.split(b'\r', 1)
                    self.lines.append(line.decode())
                    if line.startswith(b'CONNECT '):
                        # Fragmented responses exercise stream framing.
                        cmd.sendall(b'CONNE')
                        connected = b'CONNECTED ' + line.split(b' ',1)[1] + b'\r'
                        cmd.sendall(connected[5:] + b'\nBUFFER 0\r')
                        if self.relay:
                            self.relay.cmd_conn.sendall(connected)
                    elif line == b'VERSION':
                        cmd.sendall(b'VERSION VARA HF simulated\r')
                    elif line == b'BITRATE':
                        cmd.sendall(b'WRONG\r')
                    elif line == b'DISCONNECT':
                        cmd.sendall(b'DISCONNECTED\r')
                    elif line == b'ABORT':
                        return
                    else:
                        cmd.sendall(b'OK\r')

    def __exit__(self, *args):
        self.commands.close(); self.data.close()
        self.thread.join(2)


class FakeRecorder:
    def __init__(self, path, device):
        self.started = time.monotonic()
        self.rate = 48000; self.channels = 1; self.frames = 480
        self.peak = 100; self.clipped = 0; self.status = []; self.info = {'name': 'fake'}
        with wave.open(str(path), 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(48000)
            f.writeframes(b'\x64\x00' * 480)
    def close(self):
        pass


class Tests(unittest.TestCase):
    def test_call_validation(self):
        self.assertEqual(call('test1-15'), 'TEST1-15')
        for value in ['AA', 'TEST1-16', 'TEST1\rABORT', 'X Y Z']:
            with self.assertRaises(ValueError): call(value)

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder, FakeModem() as fake:
            config = dict(host='127.0.0.1', port=fake.port, source='TEST1',
                          destination='TEST2', bandwidth='BW2300', timeout=2, output=folder)
            message = 'Exact payload\nwith UTF-8: é'
            result = run_test(config, 0, message, threading.Event(), lambda _: None, FakeRecorder)
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(fake.received, message.encode('utf-8'))
            self.assertIn('TX 500', result['speed'])
            path = Path(folder) / result['wav']
            with wave.open(str(path)) as f:
                self.assertEqual(f.getnframes(), 480)
            self.assertIn(message.encode('utf-8'), path.read_bytes())
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 1)
            self.assertEqual(len(list(Path(folder).glob('*.csv'))), 2)

    def test_cancel_preserves_artifacts(self):
        with tempfile.TemporaryDirectory() as folder, FakeModem() as fake:
            stop = threading.Event(); stop.set()
            config = dict(host='127.0.0.1', port=fake.port, source='TEST1',
                          destination='TEST2', bandwidth='BW2300', timeout=2, output=folder)
            result = run_test(config, 0, 'message', stop, lambda _: None, FakeRecorder)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 1)

    def test_binary_verified_at_peer(self):
        with tempfile.TemporaryDirectory() as folder, FakeModem() as fake, FakeModem() as peer:
            fake.relay = peer
            exe = Path(folder) / 'modem.exe'
            exe.write_bytes(b'executable fixture')
            config = dict(host='127.0.0.1', port=fake.port, source='TEST1',
                          destination='TEST2', bandwidth='BW500', timeout=2, output=folder,
                          peer_host='127.0.0.1', peer_port=peer.port, payload_mode='Hex bytes',
                          executable=str(exe))
            result = run_test(config, 0, '00 7F 80 FF', threading.Event(), lambda _: None, FakeRecorder)
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(result['peer_verification'], 'exact payload matched')
            self.assertEqual(result['peer_received_hex'], '007f80ff')
            self.assertEqual(result['actual_speed_level'], 'unknown')
            self.assertEqual(len(result['executable']['sha256']), 64)
            self.assertEqual(fake.lines[:6], ['MYCALL TEST1', 'BW500', 'CHAT ON',
                                              'P2P SESSION', 'VERSION', 'LISTEN ON'])
            self.assertIn('WRONG', result['bitrate_query'])
            kinds = [e['kind'] for e in result['transcript']]
            self.assertIn('data_rx', kinds)
            self.assertIn('data_tx_complete', kinds)
            self.assertEqual(next(Path(folder).glob('*.peer-received.bin')).read_bytes(), bytes.fromhex('007f80ff'))

    def test_full_contact_both_audio_directions(self):
        with tempfile.TemporaryDirectory() as folder, FakeModem() as first, FakeModem() as second:
            first.relay = second; second.relay = first
            config = dict(host='127.0.0.1',port=first.port,source='N4EAC',destination='N4EAC-2',
                          bandwidth='BW500',timeout=2,output=folder,peer_host='127.0.0.1',
                          peer_port=second.port,full_contact=True,peer_capture_device=1)
            result = run_test(config,0,'Contact payload ' * 30,threading.Event(),lambda _:None,FakeRecorder)
            self.assertEqual(result['status'],'complete',result)
            self.assertEqual(result['reply_verification'],'exact payload matched')
            self.assertTrue((Path(folder)/result['peer_wav']).exists())
            self.assertEqual(first.received.hex(),result['payload_hex'])
            self.assertEqual(second.received.hex(),result['reply_payload_hex'])
            self.assertEqual({o['bps'] for o in result['rate_observations']},{500,175})

    def test_invalid_hex(self):
        with self.assertRaises(ValueError):
            parse_payload('00 zz', 'Hex bytes')

    def test_timeout(self):
        with FakeModem() as fake:
            modem = Modem('127.0.0.1', fake.port)
            try:
                with self.assertRaises(TimeoutError):
                    modem.wait(lambda s: s == 'NEVER', 0, .1, threading.Event())
            finally:
                modem.close()


if __name__ == '__main__': unittest.main()
