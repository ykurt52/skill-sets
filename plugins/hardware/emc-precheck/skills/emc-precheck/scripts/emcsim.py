#!/usr/bin/env python
"""Pulse simulation of one port: surge, burst or ESD generator into the protection circuit.

Usage:
  python emcsim.py verify [--out DIR]
  python emcsim.py run CASE.json [--out DIR]

verify   simulates the three generators into their calibration loads and
         checks the waveforms against the tolerances in generators.py.
run      simulates one case and writes <name>.json, <name>.md and <name>.html
         next to the case file (or into --out).

A case file:
{
  "name": "j1-otg-vb-surge",
  "generator": {"type": "surge", "level_v": 500, "coupling": "io_line", "polarity": "+"},
  "netlist": ["R14 port pin 1k", "Cpin pin 0 5p"],
  "clamps": [{"name": "D2", "a": "port", "b": "0", "vbr": 6.0, "vc": 18.0, "ipp": 24.0,
              "cj_pf": 350, "bidirectional": true}],
  "measure": [{"node": "pin", "label": "U3.17 IO13", "max_v": 3.6, "min_v": -0.3}]
}
The generator drives node "port" against node "0". Types: surge (couplings
direct, line_to_line, line_to_ground, io_line), burst (direct, power_line,
clamp with "clamp_pf"), esd (contact discharge, no coupling).
"""
import argparse
import html
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generators  # noqa: E402
import spicerun  # noqa: E402


# ---------------------------------------------------------------- simulation
def simulate(lines, vectors, step, stop, work_dir):
    """Run a transient analysis and return {vector: [values]} plus 'time'."""
    out_path = os.path.join(work_dir, 'out.txt').replace('\\', '/')
    deck_path = os.path.join(work_dir, 'deck.cir')
    deck = ['* emcsim'] + lines + [
        '.tran %.6g %.6g uic' % (step, stop),
        '.control', 'set wr_singlescale', 'run', 'wrdata %s %s' % (out_path, ' '.join(vectors)),
        '.endc', '.end', '']
    with open(deck_path, 'w', encoding='ascii') as handle:
        handle.write('\n'.join(deck))
    if os.path.exists(out_path):
        os.remove(out_path)
    _ok, messages = spicerun.run(deck_path)
    if not os.path.exists(out_path):
        problems = [m for m in messages if re.search(r'error|fatal|unknown|no such', m, re.IGNORECASE)]
        raise RuntimeError('simulation produced no data: ' + ' | '.join(problems[:6] or messages[-6:]))
    columns = spicerun.read_table(out_path)
    return dict(zip(['time'] + vectors, columns)), deck


def crossing(times, values, level, rising, start=0):
    for index in range(max(start, 1), len(values)):
        before, after = values[index - 1], values[index]
        if (rising and before < level <= after) or (not rising and before > level >= after):
            ratio = (level - before) / (after - before)
            return times[index - 1] + ratio * (times[index] - times[index - 1])
    return None


def value_at(times, values, moment):
    for index in range(1, len(times)):
        if times[index] >= moment:
            ratio = (moment - times[index - 1]) / (times[index] - times[index - 1])
            return values[index - 1] + ratio * (values[index] - values[index - 1])
    return values[-1]


def shape(times, values, low=0.1, peak_index=None):
    """Peak, edge time between low and 90 %, and width at half height."""
    if peak_index is None:
        peak_index = values.index(max(values))
    peak = values[peak_index]
    t_low = crossing(times, values, low * peak, True)
    t_high = crossing(times, values, 0.9 * peak, True)
    t_up = crossing(times, values, 0.5 * peak, True)
    t_down = crossing(times, values, 0.5 * peak, False, peak_index)
    return dict(peak=peak, edge=(t_high - t_low) if None not in (t_low, t_high) else None,
                width=(t_down - t_up) if None not in (t_up, t_down) else None, t_low=t_low)


def within(measured, spec):
    nominal = spec[0]
    minus, plus = (spec[1], spec[1]) if len(spec) == 2 else (-spec[1], spec[2])
    return nominal * (1 - abs(minus)) <= measured <= nominal * (1 + plus)


# ---------------------------------------------------------------- verify
def verify(work_dir):
    rows = []

    def row(name, measured, spec, unit, scale=1.0):
        low = spec[0] * (1 - abs(spec[1]))
        high = spec[0] * (1 + (spec[1] if len(spec) == 2 else spec[2]))
        rows.append((name, measured * scale, spec[0] * scale, low * scale, high * scale, unit,
                     within(measured, spec)))

    surge, burst, esd = generators.SURGE, generators.BURST, generators.ESD
    step, stop = generators.DEFAULT_TIMES['surge']
    level = 1000.0
    data, _deck = simulate(generators.surge_lines(level) + ['Rload port 0 10k'], ['v(port)'], step, stop, work_dir)
    opened = shape(data['time'], data['v(port)'], low=0.3)
    row('surge open circuit front time', 1.67 * opened['edge'], surge['front_open'], 'us', 1e6)
    row('surge open circuit duration', opened['width'], surge['duration_open'], 'us', 1e6)
    row('surge open circuit peak', opened['peak'], (level, 0.10), 'V')
    data, _deck = simulate(generators.surge_lines(level) + ['Vshort port 0 0'], ['i(vshort)'], step, stop, work_dir)
    current = data['i(vshort)']
    shorted = shape(data['time'], current, low=0.1)
    row('surge short circuit front time', 1.25 * shorted['edge'], surge['front_short'], 'us', 1e6)
    row('surge short circuit duration', 1.18 * shorted['width'], surge['duration_short'], 'us', 1e6)
    row('surge peak voltage / peak current', opened['peak'] / shorted['peak'], surge['impedance'], 'ohm')
    undershoot = -min(current) / shorted['peak']
    rows.append(('surge short circuit undershoot', undershoot * 100, None, 0.0, surge['undershoot_max'] * 100, '%',
                 undershoot <= surge['undershoot_max']))

    step, stop = generators.DEFAULT_TIMES['burst']
    for load, width_spec, ratio_spec in ((50, burst['width_50'], burst['peak_ratio_50']),
                                         (1000, burst['width_1000'], burst['peak_ratio_1000'])):
        data, _deck = simulate(generators.burst_lines(level) + ['Rload port 0 %d' % load], ['v(port)'], step, stop,
                               work_dir)
        pulse = shape(data['time'], data['v(port)'], low=0.1)
        row('burst rise time into %d ohm' % load, pulse['edge'], burst['rise'], 'ns', 1e9)
        row('burst pulse width into %d ohm' % load, pulse['width'], width_spec, 'ns', 1e9)
        row('burst peak / set voltage into %d ohm' % load, pulse['peak'] / level, ratio_spec, '')

    step, stop = generators.DEFAULT_TIMES['esd']
    kilovolt = 4.0
    data, _deck = simulate(generators.esd_lines(kilovolt * 1000) + ['Vshort port 0 0'], ['i(vshort)'], step, stop,
                           work_dir)
    times, current = data['time'], data['i(vshort)']
    first_index = next(i for i in range(1, len(current) - 1) if current[i] >= current[i + 1] and current[i] > 1.0)
    first = shape(times, current, low=0.1, peak_index=first_index)
    reference = first['t_low']
    row('ESD first peak per kV', first['peak'] / kilovolt, esd['first_peak_per_kv'], 'A')
    row('ESD rise time', first['edge'], esd['rise'], 'ns', 1e9)
    row('ESD current at 30 ns per kV', value_at(times, current, reference + 30e-9) / kilovolt,
        esd['at_30ns_per_kv'], 'A')
    row('ESD current at 60 ns per kV', value_at(times, current, reference + 60e-9) / kilovolt,
        esd['at_60ns_per_kv'], 'A')
    second = max(value for moment, value in zip(times, current) if reference + 10e-9 <= moment <= reference + 40e-9)
    row('ESD second peak per kV', second / kilovolt, esd['second_peak_per_kv'], 'A')
    return rows


def verify_report(rows):
    lines = ['# Generator check', '', '| Quantity | Simulated | Nominal | Allowed | Result |',
             '| --- | --- | --- | --- | --- |']
    for name, measured, nominal, low, high, unit, good in rows:
        lines.append('| %s | %.4g %s | %s | %.4g to %.4g %s | %s |' % (
            name, measured, unit, '%.4g %s' % (nominal, unit) if nominal is not None else '', low, high, unit,
            'ok' if good else 'OUT OF TOLERANCE'))
    lines += ['', 'Sources: surge %s; burst %s; ESD %s.' % (
        generators.SURGE['source'], generators.BURST['source'], generators.ESD['source'])]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- case
def clamp_lines(clamp):
    """A clamp as diodes with breakdown, built from datasheet values. Returns (lines, sense source)."""
    name = re.sub(r'\W', '_', clamp['name'])
    breakdown, clamping, peak_current = float(clamp['vbr']), float(clamp['vc']), float(clamp['ipp'])
    if clamping <= breakdown or peak_current <= 0:
        raise ValueError('clamp %s: vc must be above vbr and ipp above zero' % clamp['name'])
    resistance = (clamping - breakdown) / peak_current
    capacitance = float(clamp.get('cj_pf', 0.0)) * 1e-12
    test_current = float(clamp.get('it_ma', 1.0)) * 1e-3
    sense = 'vs_%s' % name.lower()
    lines = ['* clamp %s: vbr %g V, vc %g V at %g A' % (clamp['name'], breakdown, clamping, peak_current),
             'Vs_%s %s %s_x 0' % (name, clamp['a'], name)]
    if clamp.get('bidirectional'):
        lines += ['D%s_1 %s_m %s_x DM_%s' % (name, name, name, name),
                  'D%s_2 %s_m %s DM_%s' % (name, name, clamp['b'], name),
                  '.model DM_%s D(BV=%.6g IBV=%.6g RS=%.6g CJO=%.6g)' % (
                      name, breakdown, test_current, resistance / 2, capacitance * 2)]
    else:
        lines += ['D%s_1 %s %s_x DM_%s' % (name, clamp['b'], name, name),
                  '.model DM_%s D(BV=%.6g IBV=%.6g RS=%.6g CJO=%.6g)' % (
                      name, breakdown, test_current, resistance, capacitance)]
    return lines, sense


def run_case(case, work_dir):
    generator = case['generator']
    kind = generator['type']
    level = float(generator['level_v']) * (-1.0 if generator.get('polarity', '+') == '-' else 1.0)
    coupling = generator.get('coupling', 'direct')
    if kind == 'surge':
        lines = generators.surge_lines(level, coupling)
    elif kind == 'burst':
        lines = generators.burst_lines(level, coupling, clamp_pf=generator.get('clamp_pf'))
    elif kind == 'esd':
        lines = generators.esd_lines(level)
    else:
        raise ValueError('generator type must be surge, burst or esd')
    step, stop = generators.DEFAULT_TIMES[kind]
    step, stop = float(case.get('step_s', step)), float(case.get('stop_s', stop))
    lines += ['* circuit under test'] + list(case.get('netlist', []))
    vectors = ['v(port)']
    clamps = []
    for clamp in case.get('clamps', []):
        clamp_part, sense = clamp_lines(clamp)
        lines += clamp_part
        nodes = [node for node in (clamp['a'], clamp['b']) if node != '0']
        vectors += ['i(%s)' % sense] + ['v(%s)' % node for node in nodes if 'v(%s)' % node not in vectors]
        clamps.append((clamp, sense))
    for item in case.get('measure', []):
        vector = 'v(%s)' % item['node'] if 'node' in item else 'i(%s)' % item['current'].lower()
        if vector not in vectors:
            vectors.append(vector)
    data, deck = simulate(lines, vectors, step, stop, work_dir)
    times = data['time']

    results, traces = [], []
    for item in case.get('measure', []):
        vector = 'v(%s)' % item['node'] if 'node' in item else 'i(%s)' % item['current'].lower()
        values = data[vector]
        unit = 'V' if 'node' in item else 'A'
        high, low = max(values), min(values)
        limit_high, limit_low = item.get('max_v', item.get('max_a')), item.get('min_v', item.get('min_a'))
        over = (limit_high is not None and high > limit_high) or (limit_low is not None and low < limit_low)
        results.append(dict(label=item.get('label', vector), vector=vector, unit=unit, max=high, min=low,
                            limit_max=limit_high, limit_min=limit_low,
                            verdict='exceeds the limit' if over else
                            'within the limit' if (limit_high is not None or limit_low is not None) else 'no limit given'))
        traces.append((item.get('label', vector), unit, values))
    for clamp, sense in clamps:
        current = data['i(%s)' % sense]
        va = data['v(%s)' % clamp['a']] if clamp['a'] != '0' else [0.0] * len(times)
        vb = data['v(%s)' % clamp['b']] if clamp['b'] != '0' else [0.0] * len(times)
        voltage = [a - b for a, b in zip(va, vb)]
        energy = sum((voltage[i] * current[i] + voltage[i - 1] * current[i - 1]) / 2 * (times[i] - times[i - 1])
                     for i in range(1, len(times)))
        peak = max(abs(value) for value in current)
        rated = float(clamp['ipp'])
        results.append(dict(label='clamp %s current' % clamp['name'], vector='i(%s)' % sense, unit='A',
                            max=max(current), min=min(current), limit_max=rated if kind == 'surge' else None,
                            limit_min=-rated if kind == 'surge' else None, energy_j=energy,
                            peak_voltage=max(abs(value) for value in voltage),
                            verdict=('exceeds the rated peak pulse current' if peak > rated else
                                     'within the rated peak pulse current') if kind == 'surge'
                            else 'rated current is given for a surge pulse, not compared'))
        traces.append(('clamp %s current' % clamp['name'], 'A', current))
    traces.insert(0, ('port', 'V', data['v(port)']))
    return dict(name=case.get('name', 'case'), generator=generator, results=results,
                port_peak=max(data['v(port)'], key=abs)), times, traces, deck


def case_report(outcome):
    generator = outcome['generator']
    lines = ['# Pulse simulation: %s' % outcome['name'], '',
             '- generator: %s, %g V%s, polarity %s' % (
                 generator['type'], generator['level_v'],
                 ', coupling %s' % generator['coupling'] if generator.get('coupling') else '',
                 generator.get('polarity', '+')),
             '- voltage at the port: %.4g V peak' % outcome['port_peak'], '',
             '| Quantity | Maximum | Minimum | Limit | Result |', '| --- | --- | --- | --- | --- |']
    for item in outcome['results']:
        limit = ' / '.join('%g' % value for value in (item['limit_min'], item['limit_max']) if value is not None)
        extra = ''
        if 'energy_j' in item:
            extra = ' (%.3g J absorbed, %.3g V across it)' % (item['energy_j'], item['peak_voltage'])
        lines.append('| %s | %.4g %s | %.4g %s | %s | %s%s |' % (
            item['label'], item['max'], item['unit'], item['min'], item['unit'], limit, item['verdict'], extra))
    lines += ['', 'The clamp models are straight lines through the datasheet breakdown and clamping points. '
              'Treat the numbers as estimates.']
    return '\n'.join(lines) + '\n'


def plot_page(title, times, traces):
    """Small HTML page with one plot per trace."""
    width, height, pad = 720, 190, 46
    stride = max(1, len(times) // 1400)
    span = times[-1] - times[0] or 1.0
    scale, unit = (1e9, 'ns') if span < 5e-6 else (1e6, 'us')
    blocks = []
    for label, quantity, values in traces:
        low, high = min(values), max(values)
        if high - low < 1e-12:
            high, low = high + 1.0, low - 1.0
        points = ' '.join('%.1f,%.1f' % (
            pad + (times[i] - times[0]) / span * (width - pad - 10),
            10 + (high - values[i]) / (high - low) * (height - 40))
            for i in range(0, len(times), stride))
        zero = 10 + (high - 0.0) / (high - low) * (height - 40) if low < 0 < high else None
        blocks.append(
            '<h2>%s</h2><svg viewBox="0 0 %d %d" role="img">%s<polyline class="trace" points="%s"/>'
            '<text x="4" y="18">%.4g %s</text><text x="4" y="%d">%.4g %s</text>'
            '<text x="%d" y="%d">0</text><text x="%d" y="%d" text-anchor="end">%.4g %s</text></svg>' % (
                html.escape(label), width, height,
                '<line class="zero" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>' % (pad, zero, width - 10, zero)
                if zero is not None else '',
                points, high, quantity, height - 34, low, quantity, pad, height - 8, width - 10, height - 8,
                span * scale, unit))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>%s</title><style>'
            ':root{--bg:#f4f5f7;--panel:#fff;--text:#1c2330;--muted:#5d6878;--line:#d5d9e0;--trace:#1f6fb2}'
            '@media (prefers-color-scheme: dark){:root{--bg:#12161c;--panel:#1b2129;--text:#e4e8ee;'
            '--muted:#9aa5b5;--line:#313a47;--trace:#62b0ee}}'
            'body{margin:0;padding:16px;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif}'
            'h1{font-size:17px;margin:0 0 12px}h2{font-size:13px;margin:14px 0 4px;color:var(--muted)}'
            'svg{width:100%%;max-width:900px;height:auto;background:var(--panel);border:1px solid var(--line);'
            'border-radius:8px;display:block}.trace{fill:none;stroke:var(--trace);stroke-width:1.5}'
            '.zero{stroke:var(--line);stroke-width:1}text{font-size:11px;fill:var(--muted)}'
            '</style></head><body><h1>%s</h1>%s</body></html>' % (
                html.escape(title), html.escape(title), ''.join(blocks)))


def main():
    parser = argparse.ArgumentParser(description='Surge, burst and ESD pulse simulation of one port.')
    parser.add_argument('command', choices=['verify', 'run'])
    parser.add_argument('case', nargs='?')
    parser.add_argument('--out')
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    work_dir = tempfile.mkdtemp(prefix='emcsim_')
    try:
        if args.command == 'verify':
            rows = verify(work_dir)
            text = verify_report(rows)
            if args.out:
                os.makedirs(args.out, exist_ok=True)
                with open(os.path.join(args.out, 'generator-check.md'), 'w', encoding='utf-8') as handle:
                    handle.write(text)
            print(text)
            sys.exit(0 if all(row[-1] for row in rows) else 1)
        if not args.case:
            sys.exit('run needs a case file')
        with open(args.case, encoding='utf-8') as handle:
            case = json.load(handle)
        outcome, times, traces, deck = run_case(case, work_dir)
    except (RuntimeError, ValueError, KeyError) as error:
        sys.exit('emcsim: %s' % error)
    out_dir = args.out or os.path.dirname(os.path.abspath(args.case))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, re.sub(r'[^\w.-]', '_', outcome['name']))
    with open(base + '.json', 'w', encoding='utf-8') as handle:
        json.dump(dict(outcome, deck=deck), handle, ensure_ascii=False, indent=1)
    text = case_report(outcome)
    with open(base + '.md', 'w', encoding='utf-8') as handle:
        handle.write(text)
    with open(base + '.html', 'w', encoding='utf-8') as handle:
        handle.write(plot_page('Pulse simulation: %s' % outcome['name'], times, traces))
    print(text)
    print('Written to %s' % out_dir)


if __name__ == '__main__':
    main()
