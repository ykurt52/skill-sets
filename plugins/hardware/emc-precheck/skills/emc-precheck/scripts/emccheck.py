#!/usr/bin/env python
"""Port protection pre-check on the design.json and layout.json written by hw-read.

Usage:
  python emccheck.py PATH [--config FILE] [--out DIR]

PATH is a design.json, the folder that holds it, or a project folder with a
.hwread/ folder inside. Writes next to design.json (or into --out):
  emc-findings.json   ports, protection chains and findings as data
  emc-checks.md       the same as text, also printed to stdout
  emc-board.html      board drawing with the findings marked
"""
import argparse
import collections
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boardview  # noqa: E402

SUPPORTED_SCHEMA = 'hw-read/1'

DEFAULT_CONFIG = dict(
    # matched against the last path segment of a net name
    ground_net_regex=r'(?i)^(a|d|p|s|e)?gnd\w*$|^vss\w*$|^0v$',
    shield_net_regex=r'(?i)shield|chassis|earth|^pe$|^fg$|^shld',
    power_net_regex=(r'(?i)^\+?\d+(\.\d+)?v\d*(_\w*)?$|^v(cc|dd|in|bat|bus|sys|usb|com|ref|out)\w*$'
                     r'|^[adp]?v(cc|dd)\w*$|(^|_)(pwr|vcc|vdd|vbus|vbat)(_|$)'),
    # text in value, description or fields that marks a clamping part
    clamp_text_regex=r'(?i)\btvs\b|\besd\b|transient|suppress|varistor|\bmov\b|zener|surge',
    clamp_prefixes=['TVS', 'RV', 'VR', 'MOV', 'Z', 'ZD'],
    # part number families of protection parts, matched at the start of value or MPN
    clamp_part_regex=(r'(?i)^(TPD\d|P?ESD\d|ESDA|SMAJ|SMBJ|SMCJ|SMF\d|SM6T|P4KE|P6KE|1\.5KE|USBLC|PRTR'
                      r'|RCLAMP|SRV05|NUP\d|CDSOT|PTVS|D\d+V\d|SD\d+C?-|SP\d{4}|IP4\d{3}|TVS\d)'),
    # voltages of nets whose name does not carry one, e.g. {"VBUS_SENSE": 5.0}
    net_voltages={},
    # connectors that do not leave the product (board to board, module sockets)
    internal_connectors=[],
    # connectors with at least this many pins count as internal unless listed below
    internal_pin_count=60,
    external_connectors=[],
    # series parts followed from the connector toward the circuit
    max_series_parts=3,
    # a clamp farther than this from its connector pin is reported (mm)
    clamp_max_distance_mm=20.0,
    # a clamp ground pad farther than this from a ground via, with no ground zone on its layer (mm)
    clamp_ground_via_mm=2.0,
    reference_kinds=dict(C='capacitor', R='resistor', L='inductor', FB='inductor', D='diode',
                         LED='led', U='ic', IC='ic', J='connector', P='connector', CN='connector',
                         CON='connector', TP='testpoint', Q='transistor', T='transistor', F='fuse',
                         Y='crystal', SW='switch', FD='mechanical', MH='mechanical', H='mechanical'),
    skip_checks=[],
)

SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}
MULTIPLIERS = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3, 'r': 1.0, '': 1.0,
               'k': 1e3, 'meg': 1e6, 'g': 1e9}
SERIES_KINDS = ('resistor', 'inductor', 'fuse', 'capacitor', 'diode')


def load_config(design_dir, path=None):
    config = dict(DEFAULT_CONFIG)
    if path is None:
        path = os.path.join(design_dir, 'emc-precheck.json')
        if not os.path.exists(path):
            return config
    with open(path, encoding='utf-8') as handle:
        config.update(json.load(handle))
    return config


def natural(text):
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r'(\d+)', text)]


def compress(items, limit=16):
    items = sorted(set(items), key=natural)
    return ', '.join(items[:limit]) + (' and %d more' % (len(items) - limit) if len(items) > limit else '')


def parse_value(text, farads=False):
    """Number from '4k7', '49R9', '100n', '2.2uF', 'R_10k_0402'. None when not found."""
    if not text:
        return None
    for token in re.split(r'[\s_/,]+', text):
        match = re.fullmatch(r'(\d+)([pnuµmkKMRrG])(\d+)(?:F|H|R|Ω)?', token)
        if match:
            unit = match.group(2)
            scale = 1e6 if unit == 'M' and not farads else MULTIPLIERS[unit.lower()]
            return float('%s.%s' % (match.group(1), match.group(3))) * scale
        match = re.fullmatch(r'(\d+(?:\.\d+)?)(meg|[pnuµmkKMRrG]?)(?:F|H|R|Ω|ohm|ohms)?', token,
                             re.IGNORECASE)
        if match and (match.group(2) or re.search(r'(F|H|R|Ω|ohm|ohms)$', token, re.IGNORECASE)):
            unit = match.group(2)
            scale = 1e6 if unit == 'M' and not farads else MULTIPLIERS[unit.lower()]
            return float(match.group(1)) * scale
    return None


class Design:
    def __init__(self, data, layout, config):
        self.data, self.layout, self.config = data, layout, config
        self.parts = data['components']
        self.ground = re.compile(config['ground_net_regex'])
        self.shield = re.compile(config['shield_net_regex'])
        self.power = re.compile(config['power_net_regex'])
        self.clamp_text = re.compile(config['clamp_text_regex'])
        self.clamp_part = re.compile(config['clamp_part_regex'])
        self.members = collections.defaultdict(list)  # net -> [(ref, pin)]
        for ref, part in self.parts.items():
            for pin, data_pin in part['pins'].items():
                if data_pin['net']:
                    self.members[data_pin['net']].append((ref, pin))
        self.power_typed = {data_pin['net'] for part in self.parts.values()
                            for data_pin in part['pins'].values()
                            if data_pin['net'] and (data_pin['type'] or '').startswith('power')}
        self.pads = collections.defaultdict(list)  # (ref, pad) -> [pad dict]
        for pad in (layout or {}).get('pads', []):
            self.pads[(pad['ref'], pad['pad'])].append(pad)
        self.ground_vias = [via for via in (layout or {}).get('vias', [])
                            if via['net'] and self.is_ground(via['net'])]

    # -- classification
    def prefix(self, ref):
        return re.match(r'[A-Za-z]*', ref).group(0).upper()

    def kind(self, ref):
        found = self.config['reference_kinds'].get(self.prefix(ref))
        if found:
            return found
        return 'ic' if len(self.parts[ref]['pins']) >= 8 else 'other'

    def fitted(self, ref):
        return not self.parts[ref]['dnp']

    @staticmethod
    def base(net):
        return net.rsplit('/', 1)[-1] if net else ''

    def is_ground(self, net):
        return bool(net) and bool(self.ground.search(self.base(net)))

    def is_shield(self, net):
        return bool(net) and bool(self.shield.search(self.base(net)))

    def is_power(self, net):
        if not net or self.is_ground(net) or self.is_shield(net):
            return False
        return bool(self.power.search(self.base(net))) or net in self.power_typed

    def is_rail(self, net):
        """A supply net that behaves like one: typed, decoupled, or widely connected."""
        if not self.is_power(net):
            return False
        if net in self.power_typed or len(self.members.get(net, [])) >= 4:
            return True
        for ref, _pin in self.members.get(net, []):
            nets = self.two_pin_nets(ref)
            if nets and self.kind(ref) == 'capacitor' and any(self.is_ground(other) for other in nets):
                return True
        return False

    def voltage(self, net):
        name = self.base(net)
        declared = self.config['net_voltages']
        for key in (net, name):
            if key in declared:
                return float(declared[key])
        match = re.search(r'(?<![A-Za-z0-9])\+?(\d+)V(\d+)(?!\d)', name, re.IGNORECASE)
        if match:
            return float('%s.%s' % match.groups())
        match = re.search(r'(?<![A-Za-z0-9])\+?(\d+(?:\.\d+)?)V(?![A-Za-z0-9])', name, re.IGNORECASE)
        return float(match.group(1)) if match else None

    def text(self, ref):
        part = self.parts[ref]
        pieces = [part.get('value') or '', part.get('description') or '', part.get('mpn') or '']
        pieces += [str(item) for item in part.get('fields', {}).values()]
        return ' '.join(pieces)

    def is_clamp(self, ref):
        kind = self.kind(ref)
        if kind in ('led', 'connector', 'testpoint', 'mechanical', 'capacitor', 'resistor', 'inductor'):
            return False
        if self.prefix(ref) in self.config['clamp_prefixes']:
            return True
        part = self.parts[ref]
        if any(self.clamp_part.search(text or '') for text in (part.get('value'), part.get('mpn'))):
            return True
        return kind != 'ic' and bool(self.clamp_text.search(self.text(ref)))

    def standoff(self, ref):
        """Working (standoff) voltage of a clamp from its fields or description, or None."""
        part = self.parts[ref]
        for name, text in part.get('fields', {}).items():
            key = re.sub(r'[^a-z]', '', name.lower())
            if 'standoff' in key or key in ('vrwm', 'workingvoltage', 'voltagereversestandoff'):
                match = re.search(r'(\d+(?:\.\d+)?)\s*V', text or '')
                if match:
                    return float(match.group(1))
        match = re.search(r'(\d+(?:\.\d+)?)\s*V\s*WM', self.text(ref), re.IGNORECASE)
        return float(match.group(1)) if match else None

    def describe(self, ref):
        part = self.parts[ref]
        value = part['fields'].get('Val') or part.get('value') or part.get('mpn') or ''
        return ('%s %s' % (ref, value)).strip() + ('' if self.fitted(ref) else ' (not fitted)')

    def two_pin_nets(self, ref):
        nets = [data_pin['net'] for data_pin in self.parts[ref]['pins'].values()]
        return tuple(nets) if len(nets) == 2 else None

    # -- geometry
    def pad_points(self, ref, pin=None, net=None):
        """Pad centers of a part: one pin, or all pads on a net."""
        if pin is not None:
            return [(pad['x'], pad['y']) for pad in self.pads.get((ref, pin), [])]
        return [(pad['x'], pad['y']) for name, data_pin in self.parts[ref]['pins'].items()
                if data_pin['net'] == net for pad in self.pads.get((ref, name), [])]

    @staticmethod
    def distance(first, second):
        """Smallest distance between two point lists, with the closest pair, or None."""
        best = None
        for a in first:
            for b in second:
                length = math.hypot(a[0] - b[0], a[1] - b[1])
                if best is None or length < best[0]:
                    best = (length, a, b)
        return best


# ---------------------------------------------------------------- protection chain
def choke_partner(design, ref, pin):
    """Pins of a multi-pin choke that a line leaves through: the one a bypass part shows, else the rest."""
    part = design.parts[ref]
    net = part['pins'][pin]['net']
    others = {name: data_pin['net'] for name, data_pin in part['pins'].items()
              if name != pin and data_pin['net'] and data_pin['net'] != net}
    for link_ref, _pin in design.members.get(net, []):
        nets = design.two_pin_nets(link_ref)
        if link_ref != ref and nets and design.kind(link_ref) in ('resistor', 'capacitor'):
            far = nets[1] if nets[0] == net else nets[0]
            if far in others.values():
                return [far], True
    return sorted(set(others.values())), len(set(others.values())) <= 1


def protection_chain(design, start_net, is_power_port):
    """Walk from a connector net toward the circuit. Returns a list of stages."""
    limit = design.config['max_series_parts']
    stages, seen = [], {start_net}
    queue = collections.deque([(start_net, 0, [])])
    uncertain = False
    while queue:
        net, depth, path = queue.popleft()
        stage = dict(net=net, depth=depth, path=list(path), clamps=[], capacitors=[], victims=[],
                     series=[])
        stages.append(stage)
        for ref, pin in design.members.get(net, []):
            part = design.parts[ref]
            kind = design.kind(ref)
            if not design.fitted(ref) or kind in ('connector', 'testpoint', 'mechanical'):
                continue
            nets = design.two_pin_nets(ref)
            other = None
            if nets:
                other = nets[1] if nets[0] == net else nets[0]
            if design.is_clamp(ref):
                if is_power_port and not nets:
                    continue
                stage['clamps'].append(ref)
                # pins with the same name are one node inside a protection array
                name = part['pins'][pin]['name']
                for twin, data_pin in part['pins'].items():
                    twin_net = data_pin['net']
                    if twin != pin and name and data_pin['name'] == name and twin_net and twin_net not in seen:
                        seen.add(twin_net)
                        queue.append((twin_net, depth, path))
                continue
            if kind == 'capacitor' and other and (design.is_ground(other) or design.is_shield(other)):
                stage['capacitors'].append(ref)
                continue
            if kind in ('ic', 'transistor', 'other') and not nets:
                if kind == 'other' and len(part['pins']) < 3:
                    continue
                stage['victims'].append('%s.%s' % (ref, pin))
                continue
            follow = []
            if kind == 'inductor' and not nets and len(part['pins']) >= 3:
                follow, sure = choke_partner(design, ref, pin)
                uncertain = uncertain or not sure
            elif nets and other and kind in SERIES_KINDS and other != net:
                if design.is_ground(other) or design.is_shield(other):
                    continue
                if is_power_port and kind not in ('fuse', 'inductor', 'diode'):
                    continue
                if not is_power_port and design.is_rail(other):
                    continue  # pull resistor or rail, not a series part
                follow = [other]
            for far in follow:
                stage['series'].append(ref)
                if far not in seen and depth < limit:
                    seen.add(far)
                    queue.append((far, depth + 1, path + [ref]))
    return stages, uncertain


def connector_ports(design):
    """External connectors and the pins that leave the board."""
    config = design.config
    ports, internal = [], []
    for ref in sorted(design.parts, key=natural):
        if design.kind(ref) != 'connector' or not design.fitted(ref):
            continue
        part = design.parts[ref]
        listed_internal = ref in config['internal_connectors']
        big = len(part['pins']) >= config['internal_pin_count'] and ref not in config['external_connectors']
        if listed_internal or big:
            internal.append(dict(ref=ref, pins=len(part['pins']),
                                 reason='listed as internal' if listed_internal else 'pin count'))
            continue
        by_net = collections.OrderedDict()
        for pin in sorted(part['pins'], key=natural):
            net = part['pins'][pin]['net']
            if net:
                by_net.setdefault(net, []).append(pin)
        for net, pins in by_net.items():
            if design.is_ground(net):
                role = 'ground'
            elif design.is_shield(net):
                role = 'shield'
            elif design.is_power(net):
                role = 'power'
            else:
                role = 'signal'
            ports.append(dict(connector=ref, pins=pins, net=net, role=role, volts=design.voltage(net)))
    return ports, internal


# ---------------------------------------------------------------- checks
def run_checks(design):
    config = design.config
    ports, internal = connector_ports(design)
    findings = []

    def add(check, severity, message, refs=(), nets=(), marks=(), basis=None):
        if check in config['skip_checks']:
            return
        findings.append(dict(check=check, severity=severity, refs=sorted(set(refs), key=natural),
                             nets=sorted(set(nets)), message=message, basis=basis, marks=list(marks)))

    def mark_point(point, label):
        return dict(kind='point', x=point[0], y=point[1], label=label)

    def mark_line(first, second, label):
        return dict(kind='line', x1=first[0], y1=first[1], x2=second[0], y2=second[1], label=label)

    has_layout = bool(design.layout)
    bare = collections.defaultdict(list)  # (connector, is power) -> [(pins, net, victims, marks)]
    direct = collections.defaultdict(list)    # connector -> ['pin net: victim at clamp']
    unfused = collections.defaultdict(list)   # connector -> [(pins, net, capacitors, marks)]
    placed = set()
    for port in ports:
        if port['role'] in ('ground', 'shield'):
            continue
        connector, net = port['connector'], port['net']
        where = '%s pin %s (%s)' % (connector, ', '.join(port['pins'][:4]), net)
        stages, uncertain = protection_chain(design, net, port['role'] == 'power')
        port['stages'] = stages
        port['pairing_uncertain'] = uncertain
        victims = [(stage['depth'], victim, stage) for stage in stages for victim in stage['victims']]
        clamps = [(stage['depth'], clamp, stage) for stage in stages for clamp in stage['clamps']]
        connector_points = [point for pin in port['pins'] for point in design.pad_points(connector, pin=pin)]

        def spot(ref, stage_net):
            return design.pad_points(ref, net=stage_net) or []

        if victims and not clamps:
            series = sorted({ref for stage in stages for ref in stage['path']}, key=natural)
            marks = [mark_point(point, '%s.%s' % (connector, port['pins'][0])) for point in connector_points[:1]]
            marks += [mark_point(point, victim) for _depth, victim, stage in victims[:1]
                      for point in spot(victim.split('.')[0], stage['net'])[:1]]
            bare[(connector, port['role'] == 'power')].append(
                (port['pins'], net, [victim for _d, victim, _s in victims], series, marks))
        elif victims and clamps:
            first_clamp = min(depth for depth, _clamp, _stage in clamps)
            early = [(victim, stage) for depth, victim, stage in victims if depth < first_clamp]
            if early:
                add('VICTIM_BEFORE_CLAMP', 'warning',
                    '%s reaches %s before the first clamp (%s).' % (
                        where, compress([victim for victim, _s in early], 4),
                        compress([clamp for _d, clamp, _s in clamps], 3)),
                    refs=[connector] + [victim.split('.')[0] for victim, _s in early], nets=[net],
                    marks=[mark_point(point, victim) for victim, stage in early[:3]
                           for point in spot(victim.split('.')[0], stage['net'])[:1]])
            same = [(victim, stage) for depth, victim, stage in victims
                    if any(clamp_stage is stage for _d, _c, clamp_stage in clamps)]
            if same and not early and port['role'] != 'power':
                direct[connector].append('pin %s %s: %s at %s' % (
                    port['pins'][0], net, compress([victim for victim, _s in same], 2),
                    compress([clamp for _d, clamp, stage in clamps if stage in [s for _v, s in same]], 2)))

        # clamp ratings and placement
        for depth, clamp, stage in clamps:
            standoff = design.standoff(clamp)
            volts = design.voltage(stage['net'])
            if standoff is not None and volts is not None and standoff <= volts:
                tight = standoff == volts
                add('CLAMP_STANDOFF', 'info' if tight else 'warning',
                    '%s on %s: standoff %gV %s the %gV working voltage.' % (
                        design.describe(clamp), stage['net'], standoff,
                        'equals' if tight else 'is below', volts),
                    refs=[clamp], nets=[stage['net']],
                    marks=[mark_point(point, clamp) for point in spot(clamp, stage['net'])[:1]],
                    basis='standoff from the part fields; net voltage from its name or net_voltages')
            if not has_layout or not connector_points:
                continue
            clamp_points = spot(clamp, stage['net'])
            reach = design.distance(connector_points, clamp_points)
            if reach is None:
                continue
            nearer = []
            for victim_depth, victim, victim_stage in victims:
                if victim_depth > depth or port['role'] == 'power':
                    continue
                victim_reach = design.distance(connector_points, spot(victim.split('.')[0], victim_stage['net']))
                if victim_reach and victim_reach[0] < reach[0]:
                    nearer.append((victim, victim_reach))
            if (clamp, connector) in placed:
                continue
            if nearer:
                placed.add((clamp, connector))
                victim, victim_reach = min(nearer, key=lambda item: item[1][0])
                add('CLAMP_PLACEMENT', 'warning',
                    '%s is %.1f mm from %s, while %s is only %.1f mm away: the clamp is not the first '
                    'part the transient meets.' % (clamp, reach[0], connector, victim, victim_reach[0]),
                    refs=[clamp, connector, victim.split('.')[0]], nets=[stage['net']],
                    marks=[mark_line(reach[1], reach[2], '%s %.1f mm' % (clamp, reach[0])),
                           mark_line(victim_reach[1], victim_reach[2], '%s %.1f mm' % (victim, victim_reach[0]))],
                    basis='straight distance between pad centers, not the routed path')
            elif reach[0] > config['clamp_max_distance_mm']:
                placed.add((clamp, connector))
                add('CLAMP_PLACEMENT', 'info',
                    '%s is %.1f mm from %s (limit %.0f mm).' % (
                        clamp, reach[0], connector, config['clamp_max_distance_mm']),
                    refs=[clamp, connector], nets=[stage['net']],
                    marks=[mark_line(reach[1], reach[2], '%s %.1f mm' % (clamp, reach[0]))],
                    basis='straight distance between pad centers, not the routed path')

        if port['role'] == 'power' and port['stages']:
            parts_on_path = {ref for stage in stages for ref in stage['series']}
            if not any(design.kind(ref) == 'fuse' for ref in parts_on_path):
                bulk = [ref for stage in stages for ref in stage['capacitors']]
                unfused[connector].append((port['pins'], net, bulk,
                                           [mark_point(point, '%s.%s' % (connector, port['pins'][0]))
                                            for point in connector_points[:1]]))

    for (connector, is_power), items in sorted(bare.items(), key=lambda item: natural(item[0][0])):
        lines = []
        for pins, net, victims, series, _marks in items[:12]:
            target = ('%d IC pins' % len(victims) if len(victims) > 3 else ', '.join(victims))
            lines.append('pin %s %s to %s%s' % (pins[0], net, target,
                                                 ' through %s' % '+'.join(series[:3]) if series else ''))
        add('PORT_NO_CLAMP', 'info' if is_power else 'warning',
            '%s: %d %s with no clamping part before the first IC: %s%s.' % (
                connector, len(items), 'supply nets' if is_power else 'signal nets', '; '.join(lines),
                ' ...' if len(items) > 12 else ''),
            refs=[connector] + [victim.split('.')[0] for item in items for victim in item[2]],
            nets=[item[1] for item in items], marks=[mark for item in items[:12] for mark in item[4]],
            basis='clamps recognized by reference prefix, part number family or part text')

    for connector, lines in sorted(direct.items(), key=lambda item: natural(item[0])):
        add('CLAMP_NO_SERIES', 'info',
            '%s: %d lines where the IC pin sits on the same net as its clamp, with no series part between '
            'them, so the pin sees the full clamping voltage: %s%s.' % (
                connector, len(lines), '; '.join(lines[:8]), ' ...' if len(lines) > 8 else ''),
            refs=[connector])
    for connector, items in sorted(unfused.items(), key=lambda item: natural(item[0])):
        add('POWER_PORT_NO_FUSE', 'info',
            '%s: supply nets on the connector with no fuse in line: %s.' % (
                connector, '; '.join('pin %s %s (capacitance to ground: %s)' % (
                    pins[0], net, compress([design.describe(ref) for ref in bulk], 3) or 'none')
                    for pins, net, bulk, _marks in items[:8])),
            refs=[connector], nets=[item[1] for item in items],
            marks=[mark for item in items[:8] for mark in item[3]],
            basis='direction of the supply is not known; a rail that only leaves the board needs no fuse')

    # ground return of every clamp found on a port
    seen = set()
    for port in ports:
        for stage in port.get('stages', []):
            for clamp in stage['clamps']:
                if clamp in seen or not has_layout:
                    continue
                seen.add(clamp)
                part = design.parts[clamp]
                ground_pins = [pin for pin, data_pin in part['pins'].items() if design.is_ground(data_pin['net'])]
                if not ground_pins:
                    continue
                ground_net = part['pins'][ground_pins[0]]['net']
                points = [point for pin in ground_pins for point in design.pad_points(clamp, pin=pin)]
                pad_layers = {layer for pin in ground_pins for pad in design.pads.get((clamp, pin), [])
                              for layer in pad['layers']}
                zone_layers = set(design.data['nets'].get(ground_net, {}).get('zone_layers', []))
                via_points = [(via['x'], via['y']) for via in design.ground_vias if via['net'] == ground_net]
                reach = design.distance(points, via_points)
                if pad_layers & zone_layers:
                    continue
                if reach is None or reach[0] > config['clamp_ground_via_mm']:
                    add('CLAMP_GROUND_RETURN', 'warning',
                        '%s ground pad: %s, and no %s zone on its layer.' % (
                            clamp, 'no ground via found' if reach is None
                            else 'nearest %s via is %.1f mm away' % (ground_net, reach[0]), ground_net),
                        refs=[clamp], nets=[ground_net],
                        marks=[mark_line(reach[1], reach[2], '%s to via %.1f mm' % (clamp, reach[0]))]
                        if reach else [mark_point(point, clamp) for point in points[:1]],
                        basis='via positions and zone layers from the board file')

    # shields
    shield_nets = sorted({port['net'] for port in ports if port['role'] == 'shield'})
    for net in shield_nets:
        ties = []
        for ref, _pin in design.members.get(net, []):
            nets = design.two_pin_nets(ref)
            if nets and design.fitted(ref):
                other = nets[1] if nets[0] == net else nets[0]
                if design.is_ground(other):
                    ties.append(design.describe(ref))
        connectors = [port['connector'] for port in ports if port['net'] == net]
        add('SHIELD_TIE', 'info',
            'Shield net %s (%s) is tied to ground through: %s.' % (
                net, compress(connectors, 4), compress(ties, 6) or 'nothing found'),
            refs=connectors, nets=[net])

    findings.sort(key=lambda item: (SEVERITY_ORDER[item['severity']], item['check'],
                                    natural(item['refs'][0]) if item['refs'] else []))
    for index, item in enumerate(findings):
        item['id'] = index + 1
    return ports, internal, findings


def report(design, ports, internal, findings):
    lines = []
    out = lines.append
    source = design.data['source']
    counts = collections.Counter(item['severity'] for item in findings)
    out('# Port protection pre-check: %s' % source['project'])
    out('')
    out('- %d errors, %d warnings, %d notes' % (counts['error'], counts['warning'], counts['info']))
    out('- layout data: %s' % ('used' if design.layout else 'not available, placement checks skipped'))
    for item in internal:
        out('- %s (%d pins) treated as internal: %s' % (item['ref'], item['pins'], item['reason']))
    out('')
    out('## Findings')
    out('')
    if not findings:
        out('No findings.')
    for item in findings:
        basis = ' Basis: %s.' % item['basis'] if item['basis'] else ''
        out('- **%s** #%d %s: %s%s' % (item['severity'], item['id'], item['check'], item['message'], basis))
    out('')
    out('## Ports')
    out('')
    out('| Connector | Pins | Net | Role | Volt | Path from the connector |')
    out('| --- | --- | --- | --- | --- | --- |')
    for port in ports:
        steps = []
        for stage in port.get('stages', []):
            items = []
            if stage['clamps']:
                items.append('clamp ' + compress(stage['clamps'], 3))
            if stage['capacitors']:
                items.append('cap ' + compress(stage['capacitors'], 3))
            if stage['victims']:
                items.append('pins ' + compress(stage['victims'], 3))
            lead = 'through %s: ' % '+'.join(stage['path']) if stage['path'] else ''
            if items:
                steps.append(lead + ', '.join(items))
        note = ' (choke pairing assumed)' if port.get('pairing_uncertain') else ''
        out('| %s | %s | %s | %s | %s | %s%s |' % (
            port['connector'], compress(port['pins'], 4), port['net'], port['role'],
            '%g' % port['volts'] if port['volts'] is not None else '', ' -> '.join(steps), note))
    return '\n'.join(lines) + '\n'


def find_design(path):
    if os.path.isfile(path):
        return path
    for candidate in (os.path.join(path, 'design.json'), os.path.join(path, '.hwread', 'design.json')):
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description='Port protection pre-check on hw-read output.')
    parser.add_argument('path', nargs='?', default=os.getcwd())
    parser.add_argument('--config')
    parser.add_argument('--out')
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    design_path = find_design(args.path)
    if design_path is None:
        sys.exit('No design.json at %s. Run hw-read on the project first.' % args.path)
    with open(design_path, encoding='utf-8') as handle:
        data = json.load(handle)
    if data.get('schema') != SUPPORTED_SCHEMA:
        sys.exit('Unsupported design.json schema: %s' % data.get('schema'))
    design_dir = os.path.dirname(os.path.abspath(design_path))
    layout_path = os.path.join(design_dir, 'layout.json')
    layout = None
    if os.path.exists(layout_path):
        with open(layout_path, encoding='utf-8') as handle:
            layout = json.load(handle)
    design = Design(data, layout, load_config(design_dir, args.config))
    ports, internal, findings = run_checks(design)
    out_dir = args.out or design_dir
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'emc-findings.json'), 'w', encoding='utf-8') as handle:
        json.dump(dict(schema='emc-precheck/1', project=data['source']['project'], ports=ports,
                       internal_connectors=internal, findings=findings), handle, ensure_ascii=False, indent=1)
    text = report(design, ports, internal, findings)
    with open(os.path.join(out_dir, 'emc-checks.md'), 'w', encoding='utf-8') as handle:
        handle.write(text)
    if layout:
        title = 'Port protection pre-check: %s' % data['source']['project']
        html = boardview.render(title, data, layout, findings)
        with open(os.path.join(out_dir, 'emc-board.html'), 'w', encoding='utf-8') as handle:
            handle.write(html)
    print(text)
    print('Written to %s' % out_dir)


if __name__ == '__main__':
    main()
