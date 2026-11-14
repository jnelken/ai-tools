import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('trigger', HERE / 'trigger.py')
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)


class TriggerTest(unittest.TestCase):
    def test_counts_channels_independently_and_deduplicates_events(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(trigger, 'ROOT', Path(tmp)), \
                patch.object(trigger.subprocess, 'Popen') as popen:
            for channel, event in [('eng', '1'), ('ops', '2'), ('eng', '1'),
                                   ('eng', '3'), ('ops', '4')]:
                trigger.record(channel, event)
            self.assertEqual(popen.call_count, 0)
            trigger.record('eng', '5')
            trigger.record('ops', '6')
            self.assertEqual([call.args[0][-1] for call in popen.call_args_list], ['eng', 'ops'])
            state = json.loads((Path(tmp) / 'trigger-state.json').read_text())
            self.assertEqual((state['eng']['count'], state['ops']['count']), (0, 0))

    def test_other_channels_are_rejected(self):
        with self.assertRaises(ValueError):
            trigger.record('other', 'event')


if __name__ == '__main__': unittest.main()
