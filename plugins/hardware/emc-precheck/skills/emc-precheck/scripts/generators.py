"""Test generator models for surge, burst and ESD pulses.

Each model is built from the waveform definitions of its basic standard. The
parameters are stored here in our own form with the clause they come from;
no text, table or figure of a standard is reproduced. `emcsim.py verify`
simulates every generator into its calibration loads and checks the result
against the tolerances listed here.
"""
import math

SURGE = dict(
    source='IEC 61000-4-5:2014, 6.2 (generator circuit and waveform definitions), 6.3 (coupling)',
    # Component values are not given by the standard. They are fitted to the
    # waveform definitions with tools/fit_cwg.py.
    cc=6.457e-6, rs1=18.18, rm=0.9153, lr=10.48e-6, rs2=25.1, peak_per_charge=0.93829,
    # (nominal, relative tolerance)
    front_open=(1.2e-6, 0.30), duration_open=(50e-6, 0.20),
    front_short=(8e-6, 0.20), duration_short=(20e-6, 0.20),
    impedance=(2.0, 0.10), undershoot_max=0.30,
    # coupling between generator and line: series capacitor and resistor
    couplings=dict(
        direct=None,
        line_to_line=dict(c=18e-6, r=0.0),
        line_to_ground=dict(c=9e-6, r=10.0),
        io_line=dict(c=0.5e-6, r=40.0),
    ),
)

BURST = dict(
    source='IEC 61000-4-4:2012, 6.2.2 (characteristics, ideal waveform), 6.3.1 (coupling)',
    # ideal single pulse: v1 * (t/tau1)^n / (1 + (t/tau1)^n) * exp(-t/tau2) / k
    v1=0.92, tau1=3.5e-9, tau2=51e-9, n=1.8,
    impedance=50.0, blocking_c=10e-9,
    rise=(5e-9, 0.30), width_50=(50e-9, 0.30), width_1000=(50e-9, -0.30, 2.0),
    peak_ratio_50=(0.5, 0.10), peak_ratio_1000=(0.95, 0.20),
    couplings=dict(
        direct=None,
        power_line=dict(c=33e-9),
        # the standard gives no capacitance for the clamp: it depends on the cable
        clamp=dict(c=None),
    ),
)

ESD = dict(
    source='IEC 61000-4-2:2025, 6.2 (ideal contact discharge waveform, waveform parameters)',
    # ideal contact discharge current, amplitudes per kV of test voltage
    tau1=1.1e-9, tau2=2.0e-9, tau3=12.0e-9, tau4=37.0e-9, n=1.8,
    i1_per_kv=16.6 / 4, i2_per_kv=9.3 / 4,
    # typical discharge resistor; used as the source resistance of the model
    discharge_r=330.0,
    first_peak_per_kv=(3.75, 0.15), rise=(0.8e-9, 0.25),
    at_30ns_per_kv=(2.0, 0.30), at_60ns_per_kv=(1.0, 0.30),
    second_peak_per_kv=(2.25, -0.20, 0.40),
)

DEFAULT_TIMES = dict(surge=(20e-9, 200e-6), burst=(50e-12, 400e-9), esd=(10e-12, 150e-9))


def _shape_constant(tau_rise, tau_fall, n):
    return math.exp(-(tau_rise / tau_fall) * (n * tau_fall / tau_rise) ** (1.0 / n))


def _pulse(amplitude, tau_rise, tau_fall, n):
    """SPICE expression of one rise-and-decay term of the ideal waveforms."""
    k = _shape_constant(tau_rise, tau_fall, n)
    return ('%.6g*pow(time/%.6g,%g)/(1+pow(time/%.6g,%g))*exp(-time/%.6g)'
            % (amplitude / k, tau_rise, n, tau_rise, n, tau_fall))


def surge_lines(level_v, coupling='direct', out='port'):
    """Combination wave generator charged for a given open circuit peak voltage."""
    data = SURGE
    charge = level_v / data['peak_per_charge']
    lines = ['* surge generator, open circuit peak %g V, coupling %s' % (level_v, coupling),
             'Ccwg cwg_a 0 %.6g ic=%.6g' % (data['cc'], charge),
             'Rcwg1 cwg_a 0 %.6g' % data['rs1'],
             'Rcwgm cwg_a cwg_b %.6g' % data['rm'],
             'Lcwg cwg_b gen %.6g' % data['lr'],
             'Rcwg2 gen 0 %.6g' % data['rs2']]
    return lines + _coupling(data['couplings'], coupling, out)


def burst_lines(level_v, coupling='direct', out='port', clamp_pf=None):
    """One pulse of the burst generator for a given set voltage."""
    data = BURST
    lines = ['* burst generator, set voltage %g V, coupling %s' % (level_v, coupling),
             'Beft eft_a 0 V=' + _pulse(level_v * data['v1'], data['tau1'], data['tau2'], data['n']),
             'Reft eft_a eft_b %.6g' % data['impedance'],
             'Ceft eft_b gen %.6g' % data['blocking_c']]
    couplings = dict(data['couplings'])
    if coupling == 'clamp':
        if clamp_pf is None:
            raise ValueError('burst coupling "clamp" needs clamp_pf: the standard gives no value, '
                             'it depends on the cable')
        couplings['clamp'] = dict(c=clamp_pf * 1e-12)
    return lines + _coupling(couplings, coupling, out)


def esd_lines(level_v, out='port'):
    """Contact discharge as the ideal current waveform behind the discharge resistor."""
    data = ESD
    kilovolt = level_v / 1000.0
    current = (_pulse(data['i1_per_kv'] * kilovolt, data['tau1'], data['tau2'], data['n']) + '+'
               + _pulse(data['i2_per_kv'] * kilovolt, data['tau3'], data['tau4'], data['n']))
    return ['* ESD contact discharge, %g V' % level_v,
            'Besd 0 %s I=%s' % (out, current),
            'Resd %s 0 %.6g' % (out, data['discharge_r'])]


def _coupling(couplings, name, out):
    if name not in couplings:
        raise ValueError('unknown coupling "%s", choose from: %s' % (name, ', '.join(sorted(couplings))))
    spec = couplings[name]
    if spec is None:
        return ['Rcpl gen %s 1m' % out]
    if spec.get('r'):
        return ['Ccpl gen cpl_a %.6g' % spec['c'], 'Rcpl cpl_a %s %.6g' % (out, spec['r'])]
    return ['Ccpl gen %s %.6g' % (out, spec['c'])]
