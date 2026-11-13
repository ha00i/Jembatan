"""Iteration 1: SQLite workflow foundation, safe API, persistence and idempotence."""

import concurrent.futures
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workflow

SHA = 'a' * 40


class WorkflowStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = workflow.WorkflowStore(self.temp.name)
        self.app = SimpleNamespace(
            repo_dir=lambda r: Path('/tmp/' + r.replace('/', '__')),
            docker_control=SimpleNamespace(valid_branch=lambda name: isinstance(name, str) and name == 'dev'),
            gh=Mock(side_effect=lambda url: (
                {'number': 12, 'state': 'open'} if url.endswith('/issues/12') else
                {'default_branch': 'dev'} if url.endswith('/repos/ha00i/Vortex-ERP') else
                {'commit': {'sha': SHA}})))

    def test_full_workflow_start_state_and_audit(self):
        result = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        self.assertEqual(result['run']['phase'], 'brainstorm')
        self.assertEqual(result['run']['status'], 'awaiting_chatgpt')
        self.assertEqual(result['run']['mode'], 'penuh')
        self.assertEqual(result['run']['base_branch'], 'dev')
        self.assertEqual(result['run']['base_sha'], SHA)
        self.assertIsNone(result['run']['head_sha'])
        self.assertEqual(result['events'][0]['event'], 'workflow_started')
        self.assertEqual(result['events'][0]['payload']['base_sha'], SHA)
        self.assertTrue(result['gate']['blocked'])
        self.assertFalse(result['gate']['can_advance'])
        self.assertFalse(result['gate']['qa_verified'])
        self.assertEqual(result['artifacts'], [])
        self.assertEqual(result['iterations']['brainstorm'], 0)
        self.assertFalse(result['reused'])

    def test_quick_starts_pending_worktree_and_never_claims_baseline(self):
        result = self.store.start('ha00i/Vortex-ERP', 12, 'cepat', 'sekaligus', 'dev', SHA)
        self.assertEqual(result['run']['phase'], 'worktree')
        self.assertEqual(result['run']['status'], 'pending')
        self.assertIn('belum tersedia', result['gate']['reason'])
        self.assertFalse(result['gate']['merge_allowed'])

    def test_retry_is_idempotent_after_reopening_store(self):
        first = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        restarted = workflow.WorkflowStore(self.temp.name)
        second = restarted.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        self.assertTrue(second['reused'])
        self.assertEqual(first['run']['id'], second['run']['id'])
        self.assertEqual(len(second['events']), 1)
        self.assertEqual(second['run']['created_at'], first['run']['created_at'])
        self.assertTrue((Path(self.temp.name) / 'workflow.db').is_file())

    def test_restarting_does_not_retarget_existing_snapshot(self):
        first = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        second = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'staging', 'b' * 40)
        self.assertEqual(first['run']['base_sha'], second['run']['base_sha'])
        self.assertEqual(second['run']['base_branch'], 'dev')

    def test_different_mode_is_rejected_and_no_second_event(self):
        self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'sekaligus', 'dev', SHA)
        with self.assertRaisesRegex(workflow.WorkflowError, 'mode berbeda'):
            self.store.start('ha00i/Vortex-ERP', 12, 'cepat', 'sekaligus', 'dev', SHA)
        self.assertEqual(len(self.store.state('ha00i/Vortex-ERP', 12)['events']), 1)

    def test_independent_repositories_and_issues_have_isolated_state(self):
        one = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'sekaligus', 'dev', SHA)
        two = self.store.start('ha00i/Mabc', 12, 'cepat', 'sekaligus', 'main', SHA)
        self.assertNotEqual(one['run']['id'], two['run']['id'])
        with self.assertRaisesRegex(workflow.WorkflowError, 'belum dimulai'):
            self.store.state('ha00i/Vortex-ERP', 13)
        self.assertEqual(self.store.state('ha00i/Mabc', 12)['run']['mode'], 'cepat')

    def test_two_threads_starting_same_issue_only_create_one_row_and_event(self):
        def task(_):
            return self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(task, range(24)))
        self.assertEqual(len({x['run']['id'] for x in results}), 1)
        self.assertEqual(sum(not x['reused'] for x in results), 1)
        self.assertEqual(len(self.store.state('ha00i/Vortex-ERP', 12)['events']), 1)

    def test_two_store_instances_share_db_lock_without_duplicate_issue(self):
        other = workflow.WorkflowStore(self.temp.name)
        def task(i):
            return (self.store if i % 2 else other).start('ha00i/Vortex-ERP', 12,
                                                          'penuh', 'sekaligus', 'dev', SHA)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(task, range(30)))
        self.assertEqual(len({x['run']['id'] for x in results}), 1)

    def test_database_enforces_unique_active_issue_and_immutable_events(self):
        state = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        db = sqlite3.connect(self.store.db_path)
        try:
            run_id = state['run']['id']
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE workflow_events SET event=? WHERE run_id=?', ('false', run_id))
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('DELETE FROM workflow_events WHERE run_id=?', (run_id,))
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO workflow_runs '
                           '(id,repo,issue,mode,execution,phase,status,base_branch,base_sha,created_at,updated_at) '
                           'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           ('duplicate','ha00i/Vortex-ERP',12,'penuh','per_tugas',
                            'brainstorm','pending','dev',SHA,'now','now'))
        finally:
            db.close()

    def test_audit_rejects_raw_logs_or_token_fields(self):
        state = self.store.start('ha00i/Vortex-ERP', 12, 'penuh', 'per_tugas', 'dev', SHA)
        db = sqlite3.connect(self.store.db_path)
        try:
            with self.assertRaises(workflow.WorkflowError):
                self.store._event(db, state['run']['id'], 'user', 'test', 'brainstorm', None,
                                  {'raw_log': 'github_pat_should_not_be_stored'})
        finally:
            db.close()
        self.assertNotIn('github_pat', json.dumps(self.store.state('ha00i/Vortex-ERP', 12)))

    def test_validation_rejects_bad_names_modes_sha_and_issue(self):
        for repo, issue, mode, execution, branch, sha in [
            ('../evil', 1, 'penuh', 'per_tugas', 'main', SHA),
            ('owner/..', 1, 'penuh', 'per_tugas', 'main', SHA),
            ('owner/name', True, 'penuh', 'per_tugas', 'main', SHA),
            ('owner/name', 1, 'lupa', 'per_tugas', 'main', SHA),
            ('owner/name', 1, 'cepat', 'per_tugas', 'main', SHA),
            ('owner/name', 1, 'penuh', 'per_tugas', 'main', 'not-sha')]:
            with self.subTest(repo=repo, mode=mode, execution=execution):
                with self.assertRaises(workflow.WorkflowError):
                    self.store.start(repo, issue, mode, execution, branch, sha)

    def test_route_validates_github_issue_and_default_sha(self):
        with patch.object(workflow, '_store', return_value=self.store):
            data = workflow.start_route(self.app, {'repo': 'ha00i/Vortex-ERP',
                                                    'issue': 12, 'mode': 'penuh',
                                                    'execution': 'sekaligus'})
            state = workflow.state_route(self.app, {'repo': 'ha00i/Vortex-ERP', 'issue': '12'})
        self.assertEqual(state['run']['id'], data['run']['id'])
        self.assertEqual(data['run']['base_branch'], 'dev')
        self.assertIn('/repos/ha00i/Vortex-ERP/issues/12', [c.args[0] for c in self.app.gh.call_args_list])

    def test_route_rejects_pull_request_without_making_workflow(self):
        self.app.gh = Mock(return_value={'pull_request': {'url': 'some-pr'}})
        with patch.object(workflow, '_store', return_value=self.store):
            with self.assertRaisesRegex(workflow.WorkflowError, 'PR'):
                workflow.start_route(self.app, {'repo': 'ha00i/Vortex-ERP', 'issue': 12,
                                                'mode': 'penuh', 'execution': 'sekaligus'})
        self.assertFalse(self.store.db_path.exists())

    def test_route_rejects_extra_fields_without_external_calls(self):
        with self.assertRaisesRegex(workflow.WorkflowError, 'tidak dikenal'):
            workflow.start_route(self.app, {'repo': 'ha00i/Vortex-ERP', 'issue': 12,
                                            'mode': 'cepat', 'token': 'secret'})
        self.app.gh.assert_not_called()

    def test_route_rejects_invalid_git_branch_and_sha(self):
        self.app.gh = Mock(side_effect=[{'number': 12}, {'default_branch': '../evil'}])
        with self.assertRaisesRegex(workflow.WorkflowError, 'Branch default'):
            workflow.start_route(self.app, {'repo': 'ha00i/Vortex-ERP', 'issue': 12,
                                            'mode': 'cepat'})
        self.app.gh = Mock(side_effect=[{'number': 12}, {'default_branch': 'dev'},
                                        {'commit': {'sha': 'bad'}}])
        with self.assertRaisesRegex(workflow.WorkflowError, 'SHA branch default'):
            workflow.start_route(self.app, {'repo': 'ha00i/Vortex-ERP', 'issue': 12,
                                            'mode': 'cepat'})


if __name__ == '__main__':
    unittest.main()
