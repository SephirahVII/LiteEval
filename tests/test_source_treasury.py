import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.source_treasury.authoring import sources,reconstruct,M
from benchmarks.source_treasury.build import build
from benchmarks.source_treasury.reference import execute
from benchmarks.source_treasury.verify import verify
from benchmarks.source_treasury.workflow.data_view import PublicView
from benchmarks.source_treasury.workflow.calculator import calculate
from liteeval.infrastructure.sources.package import DirectoryLoader


class SourceTreasuryTest(unittest.TestCase):
    def test_independent_sources_reconstruct_cash_and_csa_vm(self):
        answer=reconstruct(sources())
        self.assertEqual(answer,{'available_cash_minor':14*M,'forecast_by_csa':{'RHO':26*M,'TAU':12*M,'SIGMA':6*M,'OMEGA':0},'total_minor':44*M})
        opening=reconstruct(sources(),0)
        self.assertEqual(opening['total_minor'],8*M)

    def test_source_variants_have_full_score_feasible_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            for variant in ('calibration','retained'):
                for scenario in ('stress','quiet','hedged'):
                    package=build(Path(folder)/(variant+'-'+scenario),variant,scenario)
                    DirectoryLoader().load(package)
                    task=package/'tasks/treasury-duty'
                    files={p.relative_to(task/'environment/initial_workspace').as_posix():p.read_text(encoding='utf-8') for p in (task/'environment/initial_workspace').rglob('*') if p.is_file()}
                    fixture=json.loads((task/'workflow-fixtures/scenario.json').read_text(encoding='utf-8'))
                    truth=json.loads((task/'judge/ground_truth.json').read_text(encoding='utf-8'))
                    scores,metrics=verify(truth,execute(files,fixture))
                    self.assertTrue(all(scores[k]==1 for k in truth['weights']),(scenario,variant,scores,metrics))

    def test_sql_sees_only_published_data_and_cannot_write_or_read_files(self):
        view=PublicView(sources(),{'inbox/MB-025.csv':1200})
        with self.assertRaisesRegex(ValueError,'no such table'):
            view.query('SELECT * FROM inbox_MB_025',1199)
        self.assertEqual(view.query('SELECT delta_bp FROM inbox_MB_025',1200)['rows'],[[25]])
        for query in ("ATTACH DATABASE 'file.db' AS external",'DELETE FROM bank_opening',"SELECT load_extension('x')",'PRAGMA database_list'):
            with self.assertRaises(ValueError):
                view.query(query,1200)
        self.assertEqual(view.query("SELECT SUM(book_minor) FROM bank_opening WHERE entity='P' AND currency='GBP'",1200)['rows'],[[20*M]])

    def test_im_and_delivery_copies_do_not_create_another_vm_offset(self):
        public=sources()
        before=reconstruct(public)
        public['agreements/margin_collateral.csv']=public['agreements/margin_collateral.csv'].replace(',IM,400000000,',',IM,900000000,')
        self.assertEqual(reconstruct(public),before)

    def test_sql_work_is_bounded_and_catalog_has_no_name_collisions(self):
        view=PublicView(sources(),{})
        names=[t['table'] for t in view.catalog(1200)]
        self.assertEqual(len(names),len(set(names)))
        with self.assertRaisesRegex(ValueError,'interrupted'):
            view.query('WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM t) SELECT SUM(x) FROM t',1200)

    def test_zero_omission_and_late_snapshot_cash_do_not_create_format_penalties(self):
        with tempfile.TemporaryDirectory() as folder:
            package=build(Path(folder)/'case')
            task=package/'tasks/treasury-duty';public=task/'environment/initial_workspace'
            files={p.relative_to(public).as_posix():p.read_text(encoding='utf-8') for p in public.rglob('*') if p.is_file()}
            fixture=json.loads((task/'workflow-fixtures/scenario.json').read_text(encoding='utf-8'))
            truth=json.loads((task/'judge/ground_truth.json').read_text(encoding='utf-8'))
            evidence=execute(files,fixture)
            evidence['risk_reports'][0]['forecast_by_csa'].pop('OMEGA')
            self.assertEqual(verify(truth,evidence)[0]['risk_reconstruction'],1)
            report=evidence['risk_reports'][0]
            report.update(at_s=3600,as_of_s=3600,available_cash_minor=39*M)
            self.assertEqual(verify(truth,evidence)[0]['risk_reconstruction'],.5)

    def test_hedged_portfolio_does_not_have_the_same_cash_call_as_exposed_portfolio(self):
        self.assertEqual(reconstruct(sources(scenario='stress'))['total_minor'],44*M)
        self.assertEqual(reconstruct(sources(scenario='quiet'))['total_minor'],8*M)
        self.assertEqual(reconstruct(sources(scenario='hedged'))['total_minor'],8*M)

    def test_control_reduces_credit_for_unnecessary_actual_prefunding(self):
        with tempfile.TemporaryDirectory() as folder:
            package=build(Path(folder)/'quiet',scenario='quiet')
            task=package/'tasks/treasury-duty';public=task/'environment/initial_workspace'
            files={p.relative_to(public).as_posix():p.read_text(encoding='utf-8') for p in public.rglob('*') if p.is_file()}
            fixture=json.loads((task/'workflow-fixtures/scenario.json').read_text(encoding='utf-8'))
            truth=json.loads((task/'judge/ground_truth.json').read_text(encoding='utf-8'))
            good=verify(truth,execute(files,fixture))[0]
            # Same quiet environment, but a deliberately aggressive author policy.
            fixture['scenario']='stress'
            bad=verify(truth,execute(files,fixture))[0]
            self.assertEqual(good['funding_control'],1)
            self.assertEqual(bad['funding_control'],0)

    def test_calculator_is_precise_and_cannot_run_code(self):
        self.assertEqual(calculate('0.1 + 0.2'),{'value':'0.3'})
        self.assertEqual(calculate('-1800000000 + -60000000 * 25'),{'value':-3300000000})
        for text in ('__import__("os")','(1).__class__','2 ** 100000','1 / 0','1e100 * 1e100'):
            with self.assertRaises(ValueError):
                calculate(text)

    def test_risk_error_budget_is_shared_across_the_book(self):
        with tempfile.TemporaryDirectory() as folder:
            package=build(Path(folder)/'case');task=package/'tasks/treasury-duty';public=task/'environment/initial_workspace'
            files={p.relative_to(public).as_posix():p.read_text(encoding='utf-8') for p in public.rglob('*') if p.is_file()}
            fixture=json.loads((task/'workflow-fixtures/scenario.json').read_text(encoding='utf-8'))
            truth=json.loads((task/'judge/ground_truth.json').read_text(encoding='utf-8'))
            evidence=execute(files,fixture)
            # Four individually small errors consume one shared cash buffer.
            for key in evidence['risk_reports'][0]['forecast_by_csa']:
                evidence['risk_reports'][0]['forecast_by_csa'][key]+=M
            self.assertAlmostEqual(verify(truth,evidence)[0]['risk_reconstruction'],.36)


if __name__=='__main__':
    unittest.main()
