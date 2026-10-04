import json
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import threading
from pathlib import Path
from automation import update_ini, rate_observations, write_coverage, launch_modems, wait_before_test

class AutomationTests(unittest.TestCase):
    def test_ini_backup_and_preserve_unrelated_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,'VARA.ini')
            original=b'[Setup]\r\nTCP Command Port=8300\r\nOther=value\r\n[Soundcard]\r\nInput Device Name=Old\r\n'
            path.write_bytes(original)
            backup=update_ini(path,{('Setup','TCP Command Port'):'8310',('Setup','KISS Port'):'8110',
                                    ('Soundcard','Input Device Name'):'Cable 1'})
            self.assertEqual(backup.read_bytes(),original)
            text=path.read_text()
            self.assertIn('Other=value',text)
            self.assertIn('TCP Command Port=8310',text)
            self.assertIn('KISS Port=8110',text)
            self.assertIn('Input Device Name=Cable 1',text)

    def test_launch_configures_both_installations(self):
        with tempfile.TemporaryDirectory() as directory:
            one=Path(directory,'one'); two=Path(directory,'two')
            for folder in (one,two):
                folder.mkdir()
                (folder/'VARA.exe').write_bytes(b'fake executable')
                (folder/'VARA.ini').write_text('[Setup]\nTCP Command Port=8300\n[Soundcard]\n')
            config=dict(executable=str(one/'VARA.exe'),peer_executable=str(two/'VARA.exe'),
                        port=8300,peer_port=8310,station1_input='Cable 2',station1_output='Cable 1',
                        station2_input='Cable 1',station2_output='Cable 2')
            with patch('automation.os',SimpleNamespace(name='nt')), \
                 patch('automation.running_executable',return_value=False), \
                 patch('automation.subprocess.Popen') as spawn, \
                 patch('automation.socket.create_connection'):
                launch_modems(config,threading.Event(),lambda _:None)
                self.assertEqual(spawn.call_count,2)
                self.assertEqual(spawn.call_args_list[0].kwargs['cwd'],str(one))
            self.assertIn('TCP Command Port=8310',(two/'VARA.ini').read_text())
            self.assertIn('Output Device Name=Cable 2',(two/'VARA.ini').read_text())
            self.assertEqual(len(list(two.glob('*.tester-backup-*'))),1)

    def test_pre_test_wait_is_ten_seconds_and_cancellable(self):
        from unittest.mock import Mock
        stop=Mock()
        stop.wait.return_value=False
        log=Mock()
        wait_before_test('BW500',stop,log)
        self.assertEqual(stop.wait.call_count,10)
        self.assertTrue(all(c.args == (1,) for c in stop.wait.call_args_list))
        self.assertIn('10s',log.call_args_list[0].args[0])
        stop.reset_mock(); stop.wait.return_value=True
        with self.assertRaises(InterruptedError):
            wait_before_test('BW2300',stop,log)
        self.assertEqual(stop.wait.call_count,1)

    def test_rates_and_missing_coverage(self):
        events=[dict(kind='control_rx',value='BITRATE TX: 177 RX: 270',role='initiator',offset_seconds=1.5),
                dict(kind='control_rx',value='BITRATE (6) 270 BPS',role='peer',offset_seconds=2),
                dict(kind='control_rx',value='BITRATE unknown format',role='peer',offset_seconds=3)]
        observations=rate_observations(events,'BW500')
        self.assertEqual([o['bps'] for o in observations],[177,270,270])
        self.assertEqual(observations[0]['reference_level'],5)
        self.assertIsNone(observations[0]['reported_level'])
        self.assertEqual(observations[2]['reported_level'],6)
        with tempfile.TemporaryDirectory() as directory:
            result=write_coverage([dict(requested_bandwidth='BW500',rate_observations=observations)],directory)
            self.assertIn(18,result['bandwidths']['BW500']['unobserved_bps'])
            self.assertNotIn(177,result['bandwidths']['BW500']['unobserved_bps'])
            self.assertTrue(Path(directory,'speed-coverage.json').exists())

if __name__=='__main__': unittest.main()
