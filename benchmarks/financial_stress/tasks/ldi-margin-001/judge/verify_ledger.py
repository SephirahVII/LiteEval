"""Offline checks over the platform-frozen financial journal."""

import argparse
import json
from pathlib import Path


def verify(scenario, evidence, submission):
    if evidence['schema_version'] != 1 or not evidence['completed']:
        raise ValueError('Missing complete controller evidence')
    expected = {call['id']: call for call in scenario['margin_calls']}
    cash = scenario['opening_cash_pence']
    fee = 0
    orders = {}
    approvals = {}
    paid = {}
    funding_settled = set()
    detected = None
    false_alarms = 0
    ack = None
    ack_wall_elapsed = None
    first_funding = None
    rejected_at = None
    fallback = None
    authorized = True
    repeat_attempts = 0
    funding = scenario['funding']
    for row in evidence['journal']:
        now = row['at_s']
        if row['kind'] == 'action':
            request, response = row['action'], row['result']
            kind = request.get('action')
            if kind == 'submit' and request.get('kind') == 'payment' and now >= 5700 and not response.get('ok'):
                repeat_attempts += 1
            if not response.get('ok'):
                continue
            value = response['value']
            if kind == 'risk_report':
                if value['correct'] and value['shortfall'] and 1200 <= now < expected['M1']['publish_s'] and detected is None:
                    detected = now
                false_alarms += int(value['reported_shortfall'] and not value['shortfall'])
            if kind == 'ack' and value['obligation_id'] == 'M1' and ack is None:
                ack = now
                ack_wall_elapsed = row['wall_elapsed_ms'] / 1000
            if kind != 'submit' or value['id'] in orders:
                continue
            orders[value['id']] = value
            route = value['kind']
            if route == 'payment':
                call = expected.get(value['obligation_id'])
                authorized &= bool(call and call['publish_s'] <= now < call['due_s']
                                   and call['amount_pence'] == value['amount_pence'])
                continue
            if now >= expected['M1']['publish_s'] and first_funding is None:
                first_funding = now
            rule = funding[route]
            cumulative = sum(o['amount_pence'] for o in orders.values() if o['kind'] == route)
            authorized &= now < rule['cutoff_s'] and cumulative <= rule['limit_pence']
            if route == 'line':
                approval = value['approval_id']
                remaining = approvals.get(approval, 0) - value['amount_pence']
                authorized &= remaining >= 0
                approvals[approval] = remaining
                if rejected_at is not None and fallback is None:
                    fallback = now
            continue

        if row['kind'] != 'event':
            continue
        event = row['event']
        kind = event['kind']
        if kind == 'approval_ready':
            approval = event['approval']
            approvals[approval['id']] = approval['remaining_pence']
        elif kind == 'order_rejected':
            rejected_at = now
        elif kind == 'funding_settled':
            order = event['order']
            saved = orders.get(order['id'])
            if not saved or saved['amount_pence'] != order['amount_pence'] or order['id'] in funding_settled:
                raise ValueError('Funding journal cannot be reconciled')
            rule = funding[order['kind']]
            fee += (order['amount_pence'] * rule['fee_bps'] + 9999) // 10_000
            cash += order['amount_pence']
            funding_settled.add(order['id'])
            if event['cash_pence'] != cash or now != rule['settle_s']:
                raise ValueError('Funding ledger mismatch')
        elif kind == 'payment_result':
            call = event['obligation']
            if call['status'] != 'settled':
                continue
            order = orders.get(event['order_id'], {})
            reference = expected[call['id']]
            if call['id'] in paid or order.get('obligation_id') != call['id'] or call['amount_pence'] != reference['amount_pence']:
                raise ValueError('Payment journal cannot be reconciled')
            cash -= call['amount_pence']
            if cash < 0 or event['cash_pence'] != cash or now > reference['due_s']:
                raise ValueError('Payment ledger mismatch')
            paid[call['id']] = cash

    closing = evidence['closing_state']
    if closing['cash_pence'] != cash or closing['fee_pence'] != fee or closing['now_s'] != scenario['end_s']:
        raise ValueError('Closing ledger mismatch')
    complete = set(paid) == set(expected)
    m1_time = expected['M1']['publish_s']
    scale = evidence['clock_speed']
    ack_wall = ack_wall_elapsed - m1_time / scale if ack_wall_elapsed is not None and scale else None
    metrics = {'scenario': scenario['scenario'], 'clock_mode': evidence['clock_mode'],
               'detected_before_m1': detected is not None if scenario['forecast_pence'] > scenario['opening_cash_pence'] else None,
               'detection_business_s': detected - 1200 if detected is not None else None,
               'false_alarms': false_alarms, 'ack_business_s': ack - m1_time if ack is not None else None,
               'ack_wall_s': round(ack_wall, 3) if ack_wall is not None else None,
               'first_funding_business_s': first_funding - m1_time if first_funding is not None else None,
               'fallback_business_s': fallback - rejected_at if fallback is not None else None,
               'payments_on_time': len(paid), 'payments_due': len(expected), 'closing_cash_pence': cash,
               'fees_pence': fee}
    handover = evidence['handover'] or {}
    debts = {order_id for order_id in funding_settled if orders[order_id]['kind'] in ('repo', 'line')}
    unresolved = set(expected) - set(paid)
    def matches_ids(field, expected_ids):
        items = handover.get(field)
        return isinstance(items, list) and all(isinstance(item, str) for item in items) and sorted(items) == sorted(expected_ids)

    handover_ok = ((submission / 'handover.md').is_file()
                   and handover.get('cash_pence') == cash
                   and matches_ids('financing_ids', debts) and matches_ids('unresolved_ids', unresolved)
                   and all(isinstance(handover.get(k), str) and handover[k].strip() for k in ('owner', 'next_step')))
    buffers = sum(balance >= scenario['buffer_pence'] for balance in paid.values()) / len(expected)
    costs = float(complete and fee <= scenario['reference_fee_pence'])
    scores = {
        'response': float(ack is not None and (ack_wall <= 90 if scale else ack - m1_time <= 540)),
        'settlement': len(paid) / len(expected) if authorized else 0,
        'adaptation': (int('M1' in paid) + int('M2' not in expected or 'M2' in paid)
                       + int(repeat_attempts == 0 and false_alarms == 0)) / 3,
        'buffer_cost': (buffers + costs) / 2,
        'handover': float(bool(handover_ok)),
    }
    metrics['authorization_valid'] = bool(authorized)
    return scores, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--submission', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--check', required=True)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding='utf-8'))
    evidence = json.loads((args.submission / 'evidence/financial_stress.json').read_text(encoding='utf-8'))
    scores, metrics = verify(scenario, evidence, args.submission)
    score = scores[args.check]
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps({'schema_version': 1, 'check_id': args.check,
        'outcome': 'pass' if score == 1 else 'fail', 'score': score,
        'reason': f'{args.check} verified against controller journal', 'metrics': metrics}), encoding='utf-8')


if __name__ == '__main__':
    main()
