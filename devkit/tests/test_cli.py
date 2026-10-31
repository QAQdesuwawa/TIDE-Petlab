"""Run with the installed SDK Python; each CLI process starts outside the checkout."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DeveloperFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'new_project'
        self.call('new', 'my_model', '--output', str(self.project))

    def call(self, *args, ok=True):
        env = {k:v for k,v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')
               and not any(term in k.upper() for term in ('API_KEY', 'ACCESS_TOKEN'))}
        result = subprocess.run([sys.executable, '-B', '-m', 'tide_dev', *args],
            cwd=self.root, env=env, capture_output=True, text=True, encoding='utf8', timeout=30)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout+result.stderr)
        return json.loads(result.stdout if ok else result.stderr)

    def scenario(self):
        return json.loads((self.project/'scenario.json').read_text('utf8'))

    def save(self, scenario):
        (self.project/'scenario.json').write_text(json.dumps(scenario), encoding='utf8')

    def test_formal_run_and_restore(self):
        result = self.call('check', str(self.project))
        self.assertEqual(result['checked_events'], 1)
        output = self.root/'first.json'
        run = self.call('run', str(self.project), '--output', str(output))
        self.assertAlmostEqual(run['state']['affinity'], .85)
        self.assertTrue(run['receipts'][0]['event']['acceptance_fingerprint'])
        self.assertIn('receipt', run['receipts'][0]['assessment'])
        restored = self.call('run', str(self.project), '--restore', str(output))
        self.assertEqual(restored['state_fingerprint'], run['state_fingerprint'])
        self.assertFalse(list(self.project.rglob('__pycache__')))
        self.call('run', str(self.project), '--output', str(output), ok=False)

    def test_rename_schema_and_change_domain_math(self):
        definition = self.project/'my_model/definition.py'
        text = definition.read_text('utf8')
        scenario = json.dumps(self.scenario())
        for old, new in [('affinity_stimulus','signal'), ('conflict_stimulus','friction'),
                         ('affinity','bonding'), ('tension','strain'), ('expression_drive','drive'),
                         ('responsiveness','response'), ('restraint','barrier')]:
            text = text.replace(old,new)
            scenario = scenario.replace(old,new)
        text = text.replace('before["bonding"] + response * a', 'before["bonding"] + response * a / 2')
        definition.write_text(text, encoding='utf8')
        self.save(json.loads(scenario))
        result = self.call('run', str(self.project))
        self.assertEqual(set(result['state']), {'bonding','strain','drive'})
        self.assertAlmostEqual(result['state']['bonding'], .625)
        self.assertTrue(result['restored_same_state'])

    def test_reject_old_definition_binding(self):
        output = self.root/'old.json'
        self.call('run', str(self.project), '--output', str(output))
        definition = self.project/'my_model/definition.py'
        definition.write_text(definition.read_text('utf8').replace('"1.0.0"', '"2.0.0"'), encoding='utf8')
        error = self.call('run', str(self.project), '--restore', str(output), ok=False)
        self.assertIn('restore:', error['detail'])

    def test_add_axis_and_definition_fingerprint(self):
        output = self.root/'before.json'
        self.call('run', str(self.project), '--output', str(output))
        definition = self.project/'my_model/definition.py'
        text = definition.read_text('utf8').replace(
            'STATE_NAMES = ("affinity", "tension", "expression_drive")',
            'STATE_NAMES = ("affinity", "tension", "expression_drive", "energy")')
        text = text.replace('    return {\n', '    return {\n        "energy": before["energy"],\n', 1)
        definition.write_text(text, encoding='utf8')
        scenario = self.scenario()
        scenario['state']['energy'] = .6
        self.save(scenario)
        self.assertEqual(self.call('run', str(self.project))['state']['energy'], .6)
        self.call('run', str(self.project), '--restore', str(output), ok=False)

    def test_reject_invalid_measurement(self):
        scenario = self.scenario()
        scenario['events'][0]['measurement']['affinity_stimulus'] = 3
        self.save(scenario)
        self.assertIn('event:', self.call('check', str(self.project), ok=False)['detail'])

    def test_reject_missing_capability(self):
        source = self.project/'my_model/package.py'
        source.write_text(source.read_text('utf8').replace('            "advance_progress",\n',''), encoding='utf8')
        self.assertIn('registration:', self.call('check', str(self.project), ok=False)['detail'])

    def test_sequence_and_backwards_time(self):
        scenario = self.scenario()
        scenario['events'].append({'event_ref':'dev:signal:2','coordinate':1200,
                                  'measurement':{'affinity_stimulus':-.2,'conflict_stimulus':.1}})
        self.save(scenario)
        result = self.call('run', str(self.project))
        self.assertEqual(len(result['receipts']), 2)
        self.assertAlmostEqual(result['state']['affinity'], .7)
        scenario['events'][1]['coordinate'] = 900
        self.save(scenario)
        self.assertIn('progress:', self.call('check', str(self.project), ok=False)['detail'])

    def test_no_overwrite_and_invalid_name(self):
        before = (self.project/'scenario.json').read_bytes()
        self.call('new', 'my_model', '--output', str(self.project), ok=False)
        self.assertEqual(before, (self.project/'scenario.json').read_bytes())
        self.call('new', '../bad', '--output', str(self.root/'bad'), ok=False)
        self.assertFalse((self.root/'bad').exists())

    def test_reject_import_shadowing_and_windows_device_names(self):
        for name in ('json', 'typing', 'framework', 'ops', 'tide_dev', 'con', 'nul', 'com1'):
            path = self.root/('name-'+name)
            self.call('new', name, '--output', str(path), ok=False)
            self.assertFalse(path.exists(), name)

    def test_existing_factory_and_existing_algorithm(self):
        config = self.project/'tide.toml'
        text = config.read_text('utf8')
        config.write_text(text.replace('my_model:Package',
            'model_packages.minimal_affinity_v1:MinimalAffinityV1Package'), encoding='utf8')
        self.assertEqual(self.call('run', str(self.project))['package'], 'tide.minimal-affinity')
        config.write_text(text, encoding='utf8')
        # The user's existing algorithm stays independent of Runtime and package stages.
        (self.project/'existing_algorithm.py').write_text('def apply(value):\n    return value / 2\n', encoding='utf8')
        definition = self.project/'my_model/definition.py'
        code = definition.read_text('utf8').replace('PACKAGE_ID =', 'from existing_algorithm import apply\n\nPACKAGE_ID =', 1)
        code = code.replace('before["affinity"] + response * a', 'before["affinity"] + apply(response * a)')
        definition.write_text(code, encoding='utf8')
        self.assertAlmostEqual(self.call('run', str(self.project))['state']['affinity'], .625)

    def test_invalid_state_and_duplicate_event(self):
        scenario = self.scenario()
        scenario['state']['unexpected'] = .2
        self.save(scenario)
        self.assertIn('initialization:', self.call('check', str(self.project), ok=False)['detail'])
        scenario['state'].pop('unexpected')
        scenario['events'] *= 2
        self.save(scenario)
        self.assertIn('unique', self.call('check', str(self.project), ok=False)['detail'])


if __name__ == '__main__':unittest.main()
