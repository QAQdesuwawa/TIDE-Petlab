"""Run independently: python -B -m unittest examples.test_petlab_scenarios."""
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch


class PetLabScenarios(unittest.TestCase):
    def test_cases_complete_real_pipeline_without_network_or_private_inputs(self):
        from examples.petlab_scenarios import run
        with tempfile.TemporaryDirectory(prefix='petlab-cases-') as tmp:
            target=Path(tmp)/'run'
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')):
                report=run(target)
            self.assertTrue(report['passed'])
            self.assertEqual(report['network_requests'],0)
            self.assertEqual({x['id'] for x in report['cases']},{'keys','names','updated_location','speech_is_not_execution'})
            self.assertTrue(all(x['checkpoint_resume_no_extra_calls'] for x in report['cases']))
            self.assertTrue(all(x['duplicate_publication_no_extra_events'] for x in report['cases']))
            self.assertTrue(report['failed_return_stops_without_resend'])
            self.assertTrue(report['source_scope_isolated'])
            self.assertTrue(report['invalid_inputs_rejected'])
            self.assertTrue((target/'report.json').is_file())
            for row in report['cases']:
                self.assertTrue((target/row['source']).is_file())
                for query in row['queries']:
                    self.assertTrue(query['delivered_imported_ids'])
                    self.assertTrue(query['reported_used_imported_ids'])
            with self.assertRaises(FileExistsError):run(target)

    def test_fixture_writer_cannot_answer_from_undelivered_source(self):
        from examples.petlab_scenarios import OfflineFixture,load_cases
        fixture=OfflineFixture(load_cases()[0])
        with self.assertRaisesRegex(ValueError,'delivered'):
            fixture.writer('相关记忆（按发生顺序）：[]')

if __name__=='__main__':unittest.main()
