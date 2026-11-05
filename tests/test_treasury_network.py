import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.treasury_network.build import build
from benchmarks.treasury_network.reference import execute
from benchmarks.treasury_network.verify import verify
from benchmarks.treasury_network.workflow.engine import Treasury, Rejected, M
from benchmarks.treasury_network.workflow.controller import display
from liteeval.infrastructure.sources.package import DirectoryLoader


class TreasuryNetworkTest(unittest.TestCase):
    def test_each_level_has_a_full_score_executable_path(self):
        with tempfile.TemporaryDirectory() as root:
            for level in range(1,9):
                package=build(Path(root)/str(level),level)
                DirectoryLoader().load(package)
                truth=json.loads((package/'tasks/treasury-duty/judge/ground_truth.json').read_text())
                evidence=execute(level)
                scores,metrics=verify(truth,evidence)
                self.assertEqual(set(scores.values()),{1.0},(level,scores,metrics))
                self.assertEqual(evidence['repo_used'],0)

    def test_retained_values_have_their_own_feasible_paths(self):
        with tempfile.TemporaryDirectory() as root:
            for level in range(1,9):
                package=build(Path(root)/str(level),level,'retained')
                truth=json.loads((package/'tasks/treasury-duty/judge/ground_truth.json').read_text())
                scores,metrics=verify(truth,execute(level,'retained'))
                self.assertEqual(set(scores.values()),{1.0},(level,scores,metrics))

    def test_buffer_deducts_prior_unpaid_margin_arrears(self):
        d=Treasury({'level':4})
        d.advance(10800)
        truth={'buffer_minor':500000000,'reference_fee_minor':4377600,'expected_obligations':['M1','M2','U1']}
        scores,metrics=verify(truth,d.export())
        self.assertEqual(scores['buffer'],0)
        self.assertTrue(all(v<0 for v in metrics['GBP_buffers_minor']))

    def test_failed_fx_releases_reservation_without_credit(self):
        d=Treasury({'level':3})
        q=d.act({'action':'quote','route':'fx'})
        d.act({'action':'submit','route':'fx','amount':7*M,'quote_id':q['id'],'key':'fx'})
        self.assertEqual(d.available('P-USD'),5*M)
        d.advance(3600)
        self.assertEqual(d.available('P-USD'),12*M)
        self.assertEqual(d.available('P-GBP'),14*M)

    def test_repo_quote_exposes_cash_capacity_without_spurious_fx_rate(self):
        d=Treasury({'level':2})
        d.advance(2160)
        quote=d.act({'action':'quote','route':'repo'})
        self.assertEqual(quote['maximum_cash_minor'],204*M//10)
        self.assertNotIn('rate_num',quote)

    def test_stale_payment_is_not_silently_amended(self):
        d=Treasury({'level':2})
        d.advance(1800)
        d.act({'action':'submit','route':'payment','amount':40*M,'key':'m1','obligation_id':'M1','version':1,'account':'P-GBP','ssi_version':'SSI1'})
        d.advance(2280)
        with self.assertRaises(Rejected):
            d.act({'action':'submit','route':'payment','amount':40*M,'key':'m1-old','obligation_id':'M1','version':1,'account':'P-GBP','ssi_version':'SSI1'})
        d.advance(4200)
        self.assertEqual(d.obligations['M1']['status'],'missed')

    def test_frozen_package_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'package'
            build(path,1)
            with self.assertRaises(ValueError):
                build(path,1)

    def test_display_does_not_change_machine_values(self):
        result=display({'amount':4000000000,'due_s':4200})
        self.assertEqual(result['amount'],4000000000)
        self.assertEqual(result['amount_major_units'],'40000000.00')
        self.assertEqual(result['due_s_clock'],'09:40:00')

    def test_idle_policy_does_not_earn_joint_funding_points(self):
        with tempfile.TemporaryDirectory() as root:
            package=build(Path(root)/'package',7)
            truth=json.loads((package/'tasks/treasury-duty/judge/ground_truth.json').read_text(encoding='utf-8'))
            d=Treasury({'level':7});d.advance(10800)
            scores,_=verify(truth,d.export())
            self.assertEqual(sum(scores[k]*weight for k,weight in truth['weights'].items()),0)

    def test_bill_disposal_and_repo_share_the_same_lot(self):
        d=Treasury({'level':7});d.advance(2160)
        quote=d.act({'action':'quote','route':'repo'})
        d.act({'action':'submit','route':'repo','amount':380000000,'quote_id':quote['id'],'key':'repo-bills',
               'collateral':[{'lot_id':'T1','market_value_minor':4*M}]})
        sale=d.act({'action':'quote','route':'sale'})
        with self.assertRaisesRegex(Rejected,'pledged'):
            d.act({'action':'submit','route':'sale','amount':M,'quote_id':sale['id'],'key':'sale-pledged'})

    def test_foreign_owned_and_unsettled_assets_are_not_eligible(self):
        for lot in ('G4','G5','G6','G7','X1'):
            d=Treasury({'level':7});d.advance(2160)
            quote=d.act({'action':'quote','route':'repo'})
            with self.assertRaisesRegex(Rejected,'not eligible'):
                d.act({'action':'submit','route':'repo','amount':M,'quote_id':quote['id'],'key':'ineligible',
                       'collateral':[{'lot_id':lot,'market_value_minor':2*M}]})

    def test_late_suspense_allocation_does_not_schedule_events_in_the_past(self):
        d=Treasury({'level':7});d.advance(2160)
        quote=d.act({'action':'quote','route':'repo'})
        order=d.act({'action':'submit','route':'repo','amount':M,'quote_id':quote['id'],'key':'repo',
                     'collateral':[{'lot_id':'G1','market_value_minor':2*M}]})
        d.advance(9200)
        self.assertTrue(d.orders[order['id']]['defaulted'])
        d.act({'action':'reconcile','order_id':order['id'],'account':'P-GBP','reference':'CR-'+order['id']})
        self.assertTrue(all(at>=d.now for at,_,_,_ in d.queue))

    def test_unissued_collateral_call_is_not_scored_as_a_missed_payment(self):
        with tempfile.TemporaryDirectory() as root:
            package=build(Path(root)/'package',7)
            truth=json.loads((package/'tasks/treasury-duty/judge/ground_truth.json').read_text(encoding='utf-8'))
            d=Treasury({'level':7});d.advance(3600)
            d.act({'action':'submit','route':'payment','amount':5*M,'key':'usd','obligation_id':'U1','version':1,'account':'P-USD','ssi_version':'SSI2'})
            d.act({'action':'submit','route':'payment','amount':8*M,'key':'gbp','obligation_id':'M2','version':1,'account':'P-GBP','ssi_version':'SSI2'})
            d.act({'action':'submit','route':'redeem','amount':8*M,'key':'mmf'})
            d.advance(10800)
            scores,_=verify(truth,d.export())
            self.assertAlmostEqual(scores['payments'],2/3)

    def test_future_cash_can_cover_a_timely_queued_repayment(self):
        evidence=execute(8)
        self.assertTrue(all(o['status']=='repaid' for o in evidence['orders'] if o['route'] in ('repo','line')))
        repayment_times=[r['at_s'] for r in evidence['journal'] if r['kind']=='action' and r['action'].get('route')=='repay']
        self.assertTrue(all(at<7500 for at in repayment_times))
        first=next(r for r in evidence['journal'] if r['kind']=='action' and r['action'].get('route')=='repay')
        cash=20*M+sum(r['delta'] for r in evidence['journal'] if r['kind']=='cash' and r['account']=='P-GBP' and r['at_s']<=first['at_s'])
        self.assertLess(cash-6*M,first['action']['amount'])

    def test_malformed_collateral_is_a_rejected_action_not_an_engine_crash(self):
        d=Treasury({'level':8});d.advance(2160)
        quote=d.act({'action':'quote','route':'repo'})
        with self.assertRaisesRegex(Rejected,'objects'):
            d.act({'action':'submit','route':'repo','amount':M,'quote_id':quote['id'],'key':'bad',
                   'collateral':['G1']})


if __name__=='__main__':
    unittest.main()
