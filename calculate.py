"""Small exhaustive scheduling calculation, Python standard library only.

Not a product solver: a few jobs, one operator, positive durations, manual input.
Enumerate printer assignments and all precedence-compatible operator orders.
For each order place operations as early as possible inside operator windows.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import platform
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def slot(earliest, duration, windows, horizon):
    for left, right in windows:
        start = max(earliest, left)
        if start + duration <= min(right, horizon):
            return start
    return None


def enumerate_plans(data, jobs, fixed=None, deadlines=None):
    """Full enumeration, no time limit; empty result means infeasible in this model."""
    fixed, deadlines = fixed or {}, deadlines or {}
    candidates = [([j['printer']] if j['kind'] == 'accepted'
                   else [fixed[j['id']]] if j['id'] in fixed
                   else sorted(j['print_minutes'])) for j in jobs]
    stats = {'assignments': 0, 'complete_orders': 0, 'states': 0}
    plans = []
    for values in itertools.product(*candidates):
        stats['assignments'] += 1
        assignment = dict(zip((j['id'] for j in jobs), values))
        predecessor = {}
        for printer in data['printers']:
            queue = sorted((j for j in jobs if assignment[j['id']] == printer),
                           key=lambda j: (j['kind'] == 'new', j['rank'], j['id']))
            for previous, current in zip(queue, queue[1:]):
                predecessor[current['id']] = previous['id']

        def walk(now, schedule, order):
            stats['states'] += 1
            if len(order) == 2 * len(jobs):
                stats['complete_orders'] += 1
                new = [schedule[j['id']]['unload'][1] for j in jobs if j['kind'] == 'new']
                ready = max(new) if new else None
                plans.append({'schedule': deepcopy(schedule), 'operator_order': order,
                              'new_ready': ready})
                return
            for j in jobs:
                name = j['id']
                printer = assignment[name]
                if name not in schedule:
                    prev = predecessor.get(name)
                    if prev and (prev not in schedule or 'unload' not in schedule[prev]):
                        continue
                    earliest = max(now, data['printers'][printer]['available_from'])
                    if prev:
                        earliest = max(earliest, schedule[prev]['unload'][1])
                    start = slot(earliest, j['setup'], data['operator_windows'], data['horizon'])
                    if start is None:
                        continue
                    end = start + j['setup']
                    print_end = end + j['print_minutes'][printer]
                    if print_end > data['horizon']:
                        continue
                    schedule[name] = {'printer': printer, 'setup': [start, end],
                                      'print': [end, print_end]}
                    walk(end, schedule, order + [f'{name}:setup'])
                    del schedule[name]
                elif 'unload' not in schedule[name]:
                    start = slot(max(now, schedule[name]['print'][1]), j['unload'],
                                 data['operator_windows'], data['horizon'])
                    if start is None:
                        continue
                    end = start + j['unload']
                    if end > deadlines.get(name, data['horizon']):
                        continue
                    schedule[name]['unload'] = [start, end]
                    walk(end, schedule, order + [f'{name}:unload'])
                    del schedule[name]['unload']

        walk(0, {}, [])
    return plans, stats


def validate(data, jobs, plan, deadlines=None):
    """Independent interval checks; no calls to enumerator, predecessor or slot."""
    errors, operator, printer_intervals = [], [], {}
    schedule = plan['schedule']
    if set(schedule) != {j['id'] for j in jobs}:
        return ['jobs_incomplete']
    for j in jobs:
        name = j['id']
        row = schedule[name]
        printer = row['printer']
        if printer not in j['print_minutes'] or (j['kind'] == 'accepted' and printer != j['printer']):
            errors.append('printer_incompatible')
            continue
        setup, printing, unload = (row[k] for k in ('setup', 'print', 'unload'))
        if (setup[1] - setup[0] != j['setup'] or
                printing[1] - printing[0] != j['print_minutes'][printer] or
                unload[1] - unload[0] != j['unload']):
            errors.append('duration')
        if setup[1] != printing[0] or printing[1] > unload[0]:
            errors.append('precedence')
        if setup[0] < data['printers'][printer]['available_from']:
            errors.append('initial_availability')
        if setup[0] < 0 or unload[1] > data['horizon']:
            errors.append('horizon')
        if unload[1] > (deadlines or {}).get(name, data['horizon']):
            errors.append('accepted_deadline')
        for kind, interval in (('setup', setup), ('unload', unload)):
            if not any(a <= interval[0] and interval[1] <= b
                       for a, b in data['operator_windows']):
                errors.append('operator_calendar')
            operator.append((interval[0], interval[1], name, kind))
        printer_intervals.setdefault(printer, []).append((setup[0], unload[1], name))
    for first, second in itertools.combinations(operator, 2):
        if max(first[0], second[0]) < min(first[1], second[1]):
            errors.append('operator_overlap')
    for intervals in printer_intervals.values():
        for first, second in itertools.combinations(intervals, 2):
            if max(first[0], second[0]) < min(first[1], second[1]):
                errors.append('printer_holding_overlap')
    for printer in data['printers']:
        expected = sorted((j for j in jobs if schedule[j['id']]['printer'] == printer),
                          key=lambda j: (j['kind'] == 'new', j['rank'], j['id']))
        for first, second in zip(expected, expected[1:]):
            if schedule[first['id']]['unload'][1] > schedule[second['id']]['setup'][0]:
                errors.append('queue_order')
    new_ends = [schedule[j['id']]['unload'][1] for j in jobs if j['kind'] == 'new']
    if plan['new_ready'] != (max(new_ends) if new_ends else None):
        errors.append('premature_or_wrong_ready')
    return sorted(set(errors))


def choose(jobs, plans):
    def key(plan):
        ends = tuple(plan['schedule'][j['id']]['unload'][1] for j in jobs if j['kind'] == 'accepted')
        objective = plan['new_ready'] if plan['new_ready'] is not None else max(ends, default=0)
        print_ends = sum(row['print'][1] for row in plan['schedule'].values())
        return objective, ends, print_ends, tuple(plan['operator_order'])
    return min(plans, key=key) if plans else None


def run():
    started = datetime.now(timezone.utc).isoformat()
    tic = time.perf_counter()
    data = json.loads((ROOT / 'parameters.json').read_text(encoding='utf-8'))
    results = {'data_kind': 'synthetic_assumptions', 'cases': [], 'checks': []}
    for case in data['cases']:
        jobs = case['jobs']
        accepted = [j for j in jobs if j['kind'] == 'accepted']
        old_plans, old_stats = enumerate_plans(data, accepted)
        old = choose(accepted, old_plans)
        assert old is not None
        deadlines = {name: row['unload'][1] for name, row in old['schedule'].items()}
        plans, stats = enumerate_plans(data, jobs, deadlines=deadlines)
        # Validate every feasible complete plan, not only the chosen winner.
        for plan in plans:
            assert not validate(data, jobs, plan, deadlines), validate(data, jobs, plan, deadlines)
        for plan in old_plans:
            assert not validate(data, accepted, plan)
        best = choose(jobs, plans)
        assert best is not None
        new_job, = [j for j in jobs if j['kind'] == 'new']
        # Defined comparison: first printer released by old plan, then exact
        # scheduling for that fixed assignment under the same deadline limits.
        release = {p: max([data['printers'][p]['available_from']] +
                          [r['unload'][1] for r in old['schedule'].values() if r['printer'] == p])
                   for p in new_job['print_minutes']}
        first_free = min(release, key=lambda p: (release[p], p))
        forced = [p for p in plans if p['schedule'][new_job['id']]['printer'] == first_free]
        baseline = choose(jobs, forced)
        assert baseline is not None
        loose_plans, loose_stats = enumerate_plans(data, jobs)
        for plan in loose_plans:
            assert not validate(data, jobs, plan)
        loose = choose(jobs, loose_plans)
        row = {'id': case['id'], 'old_deadlines': deadlines, 'old_plan': old,
               'protected_best': best, 'first_free_printer': first_free,
               'first_free_plan': baseline, 'unprotected_best': loose,
               'unprotected_old_shifts': {name: loose['schedule'][name]['unload'][1] - end
                                          for name, end in deadlines.items()},
               'improvement_minutes_in_this_example': baseline['new_ready'] - best['new_ready'],
               'enumeration': {'old': old_stats, 'protected': stats, 'unprotected': loose_stats},
               'checked_plans': len(old_plans) + len(plans) + len(loose_plans)}
        results['cases'].append(row)

    main, protection = results['cases']
    # Hand-calculated oracle for this fixture, independent of search choices.
    assert main['old_deadlines'] == {'A': 380, 'B': 90}
    assert main['protected_best']['schedule']['C']['printer'] == 'P1'
    assert main['protected_best']['new_ready'] == 460
    assert main['first_free_printer'] == 'P2'
    assert main['first_free_plan']['new_ready'] == 1450
    assert main['first_free_plan']['schedule']['C']['print'][1] == 700
    assert main['improvement_minutes_in_this_example'] == 990
    assert protection['protected_best']['new_ready'] == 450
    assert protection['unprotected_best']['new_ready'] == 430
    assert protection['unprotected_old_shifts'] == {'A': 10, 'B': -10}
    assert any(x > 0 for x in protection['unprotected_old_shifts'].values())
    results['checks'].append({'id': 'hand_calculated_choice_and_night', 'passed': True})

    case = data['cases'][1]
    bad = deepcopy(protection['unprotected_best'])
    errors = validate(data, case['jobs'], bad, protection['old_deadlines'])
    assert 'accepted_deadline' in errors
    results['checks'].append({'id': 'reject_delayed_accepted_job', 'passed': True, 'errors': errors})

    base = main['protected_best']
    jobs = data['cases'][0]['jobs']
    mutations = []
    bad = deepcopy(base)
    bad['new_ready'] -= 1
    mutations.append(('wrong_ready', bad, 'premature_or_wrong_ready'))
    bad = deepcopy(base)
    bad['schedule']['B']['setup'] = deepcopy(bad['schedule']['A']['setup'])
    mutations.append(('double_booked_operator', bad, 'operator_overlap'))
    bad = deepcopy(base)
    bad['schedule']['C']['setup'] = [235, 245]
    mutations.append(('setup_crossing_lunch', bad, 'operator_calendar'))
    bad = deepcopy(base)
    # Start C when A has finished printing but is still on P1 awaiting removal.
    bad['schedule']['C']['setup'] = [370, 380]
    mutations.append(('printer_reused_before_removal', bad, 'printer_holding_overlap'))
    for name, bad, expected_error in mutations:
        errors = validate(data, jobs, bad, main['old_deadlines'])
        assert expected_error in errors, (name, errors)
        results['checks'].append({'id': name, 'passed': True, 'errors': errors})

    boundary = deepcopy(data)
    boundary['horizon'] = 100
    impossible, stats = enumerate_plans(boundary, jobs, deadlines=main['old_deadlines'])
    assert not impossible
    results['checks'].append({'id': 'infeasible_within_100_minute_horizon', 'passed': True,
                              'enumeration': stats, 'scope': 'only the specified model and horizon'})
    # Positive calendar check at the lunch boundary, not just rejecting a mutation.
    boundary = deepcopy(data)
    boundary['printers']['P1']['available_from'] = 235
    lone = [{'id': 'X', 'kind': 'new', 'rank': 0, 'print_minutes': {'P1': 10},
             'setup': 10, 'unload': 10}]
    lunch_plans, stats = enumerate_plans(boundary, lone)
    lunch = choose(lone, lunch_plans)
    assert lunch['schedule']['X']['setup'] == [300, 310]
    assert lunch['new_ready'] == 330
    assert not validate(boundary, lone, lunch)
    results['checks'].append({'id': 'wait_for_lunch_to_end', 'passed': True,
                              'plan': lunch, 'enumeration': stats})

    results['timing'] = {'started_utc': started,
                         'calculation_and_checks_seconds': time.perf_counter() - tic,
                         'note': 'Includes enumeration and checks; excludes reading/writing result files and author work.'}
    results['environment'] = {'python': platform.python_version(), 'dependencies': 'standard library only',
                              'ortools_run': False}
    results['input_sha256'] = hashlib.sha256((ROOT / 'parameters.json').read_bytes()).hexdigest()
    (ROOT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'cases': [{r['id']: {'protected_ready': r['protected_best']['new_ready'],
                                        'first_free_ready': r['first_free_plan']['new_ready'],
                                        'unprotected_ready': r['unprotected_best']['new_ready'],
                                        'old_shifts_unprotected': r['unprotected_old_shifts'],
                                        'checked_plans': r['checked_plans']}}
                               for r in results['cases']],
                      'checks_passed': len(results['checks']), 'timing': results['timing']}, ensure_ascii=False))


if __name__ == '__main__':
    run()
