import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch, Mock
from trader.autopilot.cli import main, run_loop
from trader.autopilot.storage import InstanceLock


class CLITests(unittest.TestCase):
    def test_anchored_ten_second_loop_skips_missed_slots_and_stops(self):
        clock = [0.0]; calls = []
        class Stop:
            def is_set(self):
                return len(calls) == 3
            def wait(self, seconds):
                clock[0] += seconds
                return self.is_set()
        def tick():
            calls.append(clock[0])
            if len(calls) == 1:
                clock[0] += 23
        runner = Mock(pass_once=tick)
        run_loop(runner, Stop(), monotonic=lambda: clock[0])
        self.assertEqual(calls, [0, 30, 40])

    def test_second_instance_never_constructs_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            arguments = ['once', '--database', str(root/'db'), '--state', str(root/'state.json'),
                         '--radar', str(root/'radar.json'), '--snapshot', str(root/'snapshot.json')]
            with InstanceLock(root/'state.json.lock'), patch('trader.autopilot.cli.Runner') as runner:
                self.assertEqual(main(arguments), 1)
                runner.assert_not_called()

    def test_sigterm_sets_stop_and_handlers_are_restored(self):
        import signal
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            handlers = {}
            def register(sig, handler):
                handlers.setdefault(sig, []).append(handler)
            def loop(runner, stop):
                handlers[signal.SIGTERM][0](signal.SIGTERM, None)
                self.assertTrue(stop.is_set())
            args = ['run', '--database', str(root/'db'), '--state', str(root/'state'),
                    '--radar', str(root/'radar'), '--snapshot', str(root/'snapshot')]
            with patch('trader.autopilot.cli.Runner'), patch('trader.autopilot.cli.signal.signal', side_effect=register), \
                    patch('trader.autopilot.cli.run_loop', side_effect=loop):
                self.assertEqual(main(args), 0)
                self.assertEqual(len(handlers[signal.SIGTERM]), 2)


if __name__ == '__main__':
    unittest.main()
