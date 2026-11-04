import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('check_touch_probe', Path(__file__).resolve().parents[1] / 'scripts/check_touch_probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def callback():
    fields = dict(identifier=17, radiusX=5, radiusY=5, force=0, rotationAngle=0)
    touch = {**fields, 'propertyReads': {
        **{name: {'values': fields.copy()} for name in ('direct', 'indexedForward', 'indexedReverse')},
        'prototypeGetters': {key: {'values': value} for key, value in fields.items()}}}
    events = [
        dict(type='pointerdown', isTrusted=True, timeStamp=100, pointerType='touch', pointerId=17, width=10, height=10, pressure=0),
        dict(type='touchstart', isTrusted=True, timeStamp=100, changedTouches=[touch]),
        dict(type='pointerup', isTrusted=True, timeStamp=200, pointerType='touch', pointerId=17, width=0, height=0, pressure=0),
        dict(type='touchend', isTrusted=True, timeStamp=200, changedTouches=[copy.deepcopy(touch)]),
        dict(type='click', isTrusted=True, timeStamp=205),
    ]
    return {'observation': {'type': 'click', 'isTrusted': True, 'events': events}}


class TouchProbeTests(unittest.TestCase):
    def test_calibrated_tap_and_all_readers(self):
        result = probe.check_probe(callback(), radius=5, require_property_reads=True)
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['property_read_paths_verified'])
        self.assertEqual(result['observed']['contact_milliseconds'], 100)

    def test_historical_field_mixup_fails(self):
        data = callback()
        touch = data['observation']['events'][1]['changedTouches'][0]
        touch.update(radiusY=0, force=5)
        result = probe.check_probe(data, radius=5)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any('touchstart.radiusY' in e for e in result['errors']))
        self.assertTrue(any('touchstart.force' in e for e in result['errors']))

    def test_release_dimensions_fail_independently(self):
        data = callback()
        data['observation']['events'][2].update(width=10, height=10)
        result = probe.check_probe(data, radius=5)
        self.assertEqual(len(result['errors']), 2)
        self.assertTrue(all('pointerup' in e for e in result['errors']))

    def test_missing_duplicate_untrusted_cancelled_and_reordered_events(self):
        for change in ('missing', 'duplicate', 'untrusted', 'cancelled', 'reordered'):
            with self.subTest(change=change):
                data = callback()
                events = data['observation']['events']
                if change == 'missing': events.pop(2)
                if change == 'duplicate': events.append(copy.deepcopy(events[0]))
                if change == 'untrusted': events[0]['isTrusted'] = False
                if change == 'cancelled': events.append({'type': 'touchcancel'})
                if change == 'reordered': events[0], events[1] = events[1], events[0]
                self.assertEqual(probe.check_probe(data, radius=5)['status'], 'failed')

    def test_older_recorder_is_explicitly_not_verified(self):
        data = callback()
        for i in (1, 3): del data['observation']['events'][i]['changedTouches'][0]['propertyReads']
        result = probe.check_probe(data, radius=5)
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['property_read_paths_verified'])
        self.assertEqual(probe.check_probe(data, radius=5, require_property_reads=True)['status'], 'failed')

    def test_reader_error_or_disagreement_fails(self):
        for value in ({'error': 'getter failed'}, {'values': 99}):
            data = callback()
            data['observation']['events'][1]['changedTouches'][0]['propertyReads']['prototypeGetters']['radiusX'] = value
            self.assertEqual(probe.check_probe(data, radius=5)['status'], 'failed')

    def test_numbers_ids_and_timestamps_are_checked(self):
        for key, value in [('width', True), ('width', float('nan')), ('pointerId', True), ('timeStamp', -1)]:
            data = callback()
            data['observation']['events'][2][key] = value
            self.assertEqual(probe.check_probe(data, radius=5)['status'], 'failed')
        for radius in (0, -1, True, float('inf'), 10**1000):
            with self.assertRaises(ValueError): probe.check_probe(callback(), radius=radius)

    def test_malformed_documents_fail_without_claiming_success(self):
        for value in (None, [], {}, {'observation': {'events': [None, 5]}}):
            self.assertEqual(probe.check_probe(value, radius=5)['status'], 'failed')

    def test_cli_status_and_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps(callback()))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(probe.main([str(path), '--radius', '5', '--require-property-reads']), 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'passed')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main([str(path), '--radius', '7']), 1)
            for invalid in ('NaN', 'Infinity', '1e999'):
                path.write_text('{"value":' + invalid + '}')
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    probe.main([str(path), '--radius', '5'])
                self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
