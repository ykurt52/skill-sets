#!/usr/bin/env python
"""Fit the component values of the surge combination wave generator.

The surge standard gives the circuit (storage capacitor, two shaping
resistors, matching resistor, rise time inductor) and the waveforms it must
deliver, not the component values. This tool searches for values that meet
the waveform definitions:

  open circuit voltage    front 1.2 us, duration 50 us
  short circuit current   front 8 us, duration 20 us
  peak voltage / peak current = 2 ohm
  undershoot of the current below 30 % of its peak

All five nominal values together give slightly more undershoot than
allowed, so the search trades a little of the time tolerances for it.

Run it to reproduce the constants used in generators.py:
  python fit_cwg.py
"""
import math

TARGET = dict(front_oc=1.2e-6, duration_oc=50e-6, front_sc=8e-6, duration_sc=20e-6, impedance=2.0)
START = dict(cc=6e-6, rs1=25.0, rm=1.0, lr=10e-6, rs2=20.0)
UNDERSHOOT_GOAL = 0.27
STEP = 10e-9
STOP = 160e-6


def simulate(values, short):
    """Unit charge on cc, switch closes at t = 0. Returns (times, output voltage or current)."""
    cc, rs1, rm, lr, rs2 = (values[key] for key in ('cc', 'rs1', 'rm', 'lr', 'rs2'))
    load = 0.0 if short else rs2
    vc, il = 1.0, 0.0

    def slope(vc_now, il_now):
        return (-(vc_now / rs1 + il_now) / cc, (vc_now - il_now * (rm + load)) / lr)

    times, output = [], []
    for index in range(int(STOP / STEP)):
        times.append(index * STEP)
        output.append(il if short else il * rs2)
        k1 = slope(vc, il)
        k2 = slope(vc + STEP / 2 * k1[0], il + STEP / 2 * k1[1])
        k3 = slope(vc + STEP / 2 * k2[0], il + STEP / 2 * k2[1])
        k4 = slope(vc + STEP * k3[0], il + STEP * k3[1])
        vc += STEP / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        il += STEP / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    return times, output


def crossing(times, values, level, rising, start=0):
    """Time at which the curve crosses a level, searching from an index."""
    for index in range(max(start, 1), len(values)):
        before, after = values[index - 1], values[index]
        if (rising and before < level <= after) or (not rising and before > level >= after):
            ratio = (level - before) / (after - before)
            return times[index - 1] + ratio * (times[index] - times[index - 1])
    return None


def measure(times, values, low, front_factor, duration_factor):
    """Peak, front time and duration as the surge standard defines them."""
    peak = max(values)
    top = values.index(peak)
    t_low = crossing(times, values, low * peak, True)
    t_high = crossing(times, values, 0.9 * peak, True)
    t_half_up = crossing(times, values, 0.5 * peak, True)
    t_half_down = crossing(times, values, 0.5 * peak, False, top)
    if None in (t_low, t_high, t_half_up, t_half_down):
        return None
    return dict(peak=peak, front=front_factor * (t_high - t_low),
                duration=duration_factor * (t_half_down - t_half_up), undershoot=-min(values) / peak)


def evaluate(values):
    open_circuit = measure(*simulate(values, False), low=0.3, front_factor=1.67, duration_factor=1.0)
    short_circuit = measure(*simulate(values, True), low=0.1, front_factor=1.25, duration_factor=1.18)
    if open_circuit is None or short_circuit is None:
        return None
    return dict(front_oc=open_circuit['front'], duration_oc=open_circuit['duration'],
                front_sc=short_circuit['front'], duration_sc=short_circuit['duration'],
                impedance=open_circuit['peak'] / short_circuit['peak'],
                peak_oc=open_circuit['peak'], undershoot_sc=short_circuit['undershoot'])


def error(vector, keys):
    values = {key: math.exp(item) for key, item in zip(keys, vector)}
    result = evaluate(values)
    if result is None:
        return 1e9
    total = sum(((result[key] - goal) / goal) ** 2 for key, goal in TARGET.items())
    # the standard limits the undershoot of the current to 30 % of the peak
    over = max(0.0, result['undershoot_sc'] - UNDERSHOOT_GOAL)
    return total + 20 * (over / UNDERSHOOT_GOAL) ** 2


def nelder_mead(function, start, step=0.15, rounds=400):
    count = len(start)
    simplex = [list(start)] + [[value + (step if index == axis else 0.0) for index, value in enumerate(start)]
                               for axis in range(count)]
    scores = [function(point) for point in simplex]
    for _ in range(rounds):
        order = sorted(range(count + 1), key=lambda index: scores[index])
        simplex = [simplex[index] for index in order]
        scores = [scores[index] for index in order]
        if scores[0] < 1e-10:
            break
        centre = [sum(point[axis] for point in simplex[:-1]) / count for axis in range(count)]
        reflected = [centre[axis] + (centre[axis] - simplex[-1][axis]) for axis in range(count)]
        score = function(reflected)
        if score < scores[0]:
            expanded = [centre[axis] + 2 * (centre[axis] - simplex[-1][axis]) for axis in range(count)]
            expanded_score = function(expanded)
            simplex[-1], scores[-1] = (expanded, expanded_score) if expanded_score < score else (reflected, score)
        elif score < scores[-2]:
            simplex[-1], scores[-1] = reflected, score
        else:
            contracted = [centre[axis] + 0.5 * (simplex[-1][axis] - centre[axis]) for axis in range(count)]
            contracted_score = function(contracted)
            if contracted_score < scores[-1]:
                simplex[-1], scores[-1] = contracted, contracted_score
            else:
                simplex = [simplex[0]] + [[simplex[0][axis] + 0.5 * (point[axis] - simplex[0][axis])
                                           for axis in range(count)] for point in simplex[1:]]
                scores = [scores[0]] + [function(point) for point in simplex[1:]]
    best = min(range(count + 1), key=lambda index: scores[index])
    return simplex[best], scores[best]


def main():
    keys = list(START)
    vector, score = nelder_mead(lambda point: error(point, keys), [math.log(START[key]) for key in keys])
    values = {key: math.exp(item) for key, item in zip(keys, vector)}
    result = evaluate(values)
    print('error %.3g' % score)
    print('cc = %.4g F, rs1 = %.4g ohm, rm = %.4g ohm, lr = %.4g H, rs2 = %.4g ohm' % (
        values['cc'], values['rs1'], values['rm'], values['lr'], values['rs2']))
    print('open circuit: front %.3f us, duration %.2f us' % (result['front_oc'] * 1e6, result['duration_oc'] * 1e6))
    print('short circuit: front %.3f us, duration %.2f us, undershoot %.1f %%' % (
        result['front_sc'] * 1e6, result['duration_sc'] * 1e6, result['undershoot_sc'] * 100))
    print('peak voltage / peak current: %.4f ohm' % result['impedance'])
    print('open circuit peak per volt of charge: %.5f' % result['peak_oc'])


if __name__ == '__main__':
    main()
