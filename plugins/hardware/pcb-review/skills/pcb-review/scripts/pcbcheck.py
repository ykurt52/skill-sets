#!/usr/bin/env python
"""Layout checks on the design.json and layout.json written by hw-read.

Usage:
  python pcbcheck.py PATH [--config FILE] [--out DIR]

PATH is a design.json, the folder that holds it, or a project folder with a
.hwread/ folder inside. Writes next to design.json (or into --out):
  pcb-findings.json   findings as data, with board coordinates
  pcb-checks.md       findings and a supply net table, also printed to stdout
  pcb-board.html      board drawing with the findings marked
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
    ground_net_regex=r'(?i)^(a|d|p|s|e)?gnd\w*$|^vss\w*$|^0v$',
    power_net_regex=(r'(?i)^\+?\d+(\.\d+)?v\d*(_\w*)?$|^v(cc|dd|in|bat|bus|sys|usb|com|ref|out)\w*$'
                     r'|^[adp]?v(cc|dd)\w*$|(^|_)(pwr|vcc|vdd|vbus|vbat)(_|$)'),
    supply_pin_regex=r'(?i)^(a|d|p|io)?v(cc|dd|in|bat|bus|io|s|ref)\w*$|^v\+$|^vdd\w*$|^3v3$|^5v$',
    not_supply_pin_regex=r'(?i)det|sns|sense|mon|good|_ok|_en$|^fb|_fb|adj',
    # an IC supply pin must have a capacitor to ground within this distance (mm)
    decoupling_max_mm=5.0,
    # length difference allowed between the two nets of a differential pair (mm)
    diff_pair_max_skew_mm=1.0,
    # suffix pairs that mark the two nets of a differential pair
    diff_pair_suffixes=[['_P', '_N'], ['_DP', '_DM'], ['+', '-'], ['_DP', '_DN'], ['P', 'N']],
    # nets checked for a reference plane, besides differential pairs and non-default net classes
    critical_net_regex=r'(?i)clk|usb|eth|rgmii|mipi|csi|dsi|hdmi|tmds|lvds|sdio|ddr',
    # routed length of a critical net allowed without copper on an adjacent layer (mm)
    reference_gap_max_mm=1.0,
    # tracks and vias must keep this distance from the board edge (mm)
    edge_clearance_mm=0.3,
    # isolation rules: [{"name": "field to logic", "a": "<net regex>", "b": "<net regex>", "min_mm": 3.0}]
    isolation=[],
    reference_kinds=dict(C='capacitor', R='resistor', L='inductor', FB='inductor', D='diode',
                         LED='led', U='ic', IC='ic', J='connector', P='connector', CN='connector',
                         CON='connector', TP='testpoint', Q='transistor', T='transistor', F='fuse',
                         Y='crystal', SW='switch', FD='mechanical', MH='mechanical', H='mechanical'),
    skip_checks=[],
)

SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}
TOUCH = 0.02  # mm, two track ends closer than this are joined


def load_config(design_dir, path=None):
    config = dict(DEFAULT_CONFIG)
    if path is None:
        path = os.path.join(design_dir, 'pcb-review.json')
        if not os.path.exists(path):
            return config
    with open(path, encoding='utf-8') as handle:
        config.update(json.load(handle))
    return config


def natural(text):
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r'(\d+)', text)]


def compress(items, limit=12):
    items = sorted(set(items), key=natural)
    return ', '.join(items[:limit]) + (' and %d more' % (len(items) - limit) if len(items) > limit else '')


# ---------------------------------------------------------------- geometry
def point_segment(point, start, end):
    """Distance from a point to a segment, and the closest point on it."""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    ratio = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    cx, cy = ax + ratio * dx, ay + ratio * dy
    return math.hypot(px - cx, py - cy), (cx, cy)


def segments_cross(a1, a2, b1, b2):
    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = side(b1, b2, a1), side(b1, b2, a2), side(a1, a2, b1), side(a1, a2, b2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def segment_segment(a1, a2, b1, b2):
    """Smallest distance between two segments, with the closest points."""
    if segments_cross(a1, a2, b1, b2):
        return 0.0, a1, a1
    options = []
    for point, start, end, flip in ((a1, b1, b2, False), (a2, b1, b2, False),
                                    (b1, a1, a2, True), (b2, a1, a2, True)):
        length, closest = point_segment(point, start, end)
        options.append((length, closest, point) if flip else (length, point, closest))
    return min(options, key=lambda item: item[0])


def inside(point, polygon):
    x, y = point
    hit = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


class Design:
    def __init__(self, data, layout, config):
        self.data, self.layout, self.config = data, layout, config
        self.parts = data['components']
        self.nets = data['nets']
        self.ground = re.compile(config['ground_net_regex'])
        self.power = re.compile(config['power_net_regex'])
        self.supply_pin = re.compile(config['supply_pin_regex'])
        self.not_supply_pin = re.compile(config['not_supply_pin_regex'])
        self.copper = [layer['name'] for layer in (data.get('board') or {}).get('copper_layers', [])]
        self.members = collections.defaultdict(list)
        for ref, part in self.parts.items():
            for pin, data_pin in part['pins'].items():
                if data_pin['net']:
                    self.members[data_pin['net']].append((ref, pin))
        self.power_typed = {data_pin['net'] for part in self.parts.values()
                            for data_pin in part['pins'].values()
                            if data_pin['net'] and (data_pin['type'] or '').startswith('power')}
        self.pads = collections.defaultdict(list)
        self.pads_by_net = collections.defaultdict(list)
        for pad in layout.get('pads', []):
            self.pads[(pad['ref'], pad['pad'])].append(pad)
            if pad['net']:
                self.pads_by_net[pad['net']].append(pad)
        self.tracks_by_net = collections.defaultdict(list)
        for track in layout.get('tracks', []):
            self.tracks_by_net[track['net']].append(track)
        self.vias_by_net = collections.defaultdict(list)
        for via in layout.get('vias', []):
            self.vias_by_net[via['net']].append(via)
        self.zones = []  # (net, layers, outline, whole layer)
        self.zone_clearance = {}  # index in self.zones -> clearance in mm, when the file gives one
        outlines = layout.get('zone_outlines', [])
        for index, zone in enumerate(data.get('zones', [])):
            outline = outlines[index] if index < len(outlines) else []
            if 'keepout' in zone:
                continue
            if zone.get('clearance') is not None:
                self.zone_clearance[len(self.zones)] = zone['clearance']
            self.zones.append((zone['net'], zone['layers'], outline, zone.get('kind') == 'Internal Plane'))

    def kind(self, ref):
        prefix = re.match(r'[A-Za-z]*', ref).group(0).upper()
        found = self.config['reference_kinds'].get(prefix)
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

    def is_power(self, net):
        if not net or self.is_ground(net):
            return False
        return bool(self.power.search(self.base(net))) or net in self.power_typed

    def supply_pin_of(self, ref, pin):
        data_pin = self.parts[ref]['pins'][pin]
        if not data_pin['net'] or self.is_ground(data_pin['net']):
            return False
        if (data_pin['type'] or '').split('+')[0] == 'power_in':
            return True
        name = data_pin['name'] or ''
        return bool(self.supply_pin.search(name)) and not self.not_supply_pin.search(name)

    def two_pin_nets(self, ref):
        nets = [data_pin['net'] for data_pin in self.parts[ref]['pins'].values()]
        return tuple(nets) if len(nets) == 2 else None

    def pad_layers(self, pad):
        """Copper layers a pad is on."""
        names = set(pad.get('layers') or [])
        if names & {'*.Cu', 'F&B.Cu'} or pad.get('type') in ('thru_hole', 'np_thru_hole'):
            return set(self.copper)
        return names & set(self.copper)

    def covered(self, point, layer, net=None):
        """True when a zone on the layer covers the point (any net, or the given one)."""
        for zone_net, layers, outline, whole in self.zones:
            if layer not in layers or (net is not None and zone_net != net):
                continue
            if whole or (len(outline) > 2 and inside(point, outline)):
                return True
        return False


# ---------------------------------------------------------------- checks
def differential_pairs(design):
    pairs = [(pair['positive'], pair['negative']) for pair in design.data.get('diff_pairs') or []
             if pair.get('positive') in design.nets and pair.get('negative') in design.nets]
    if pairs:
        return pairs
    found = set()
    for net in design.nets:
        for positive, negative in design.config['diff_pair_suffixes']:
            if net.endswith(positive) and len(net) > len(positive):
                partner = net[:-len(positive)] + negative
                if partner in design.nets and (net, partner) not in found and (partner, net) not in found:
                    found.add((net, partner))
                    break
    return sorted(found)


def run_checks(design):
    config, parts = design.config, design.parts
    findings = []

    def add(check, severity, message, refs=(), nets=(), marks=(), basis=None):
        if check in config['skip_checks']:
            return
        findings.append(dict(check=check, severity=severity, refs=sorted(set(refs), key=natural),
                             nets=sorted(set(nets)), message=message, basis=basis, marks=list(marks)))

    def point(x, y, label):
        return dict(kind='point', x=x, y=y, label=label)

    def line(first, second, label):
        return dict(kind='line', x1=first[0], y1=first[1], x2=second[0], y2=second[1], label=label)

    # decoupling distance
    limit = config['decoupling_max_mm']
    for ref in sorted(parts, key=natural):
        if design.kind(ref) != 'ic' or not design.fitted(ref):
            continue
        worst = {}
        for pin in parts[ref]['pins']:
            if not design.supply_pin_of(ref, pin):
                continue
            net = parts[ref]['pins'][pin]['net']
            pin_points = [(pad['x'], pad['y']) for pad in design.pads.get((ref, pin), [])]
            cap_pads = []
            for cap_ref, cap_pin in design.members.get(net, []):
                nets = design.two_pin_nets(cap_ref)
                if design.kind(cap_ref) == 'capacitor' and design.fitted(cap_ref) and nets \
                        and any(design.is_ground(other) for other in nets):
                    cap_pads += [(pad['x'], pad['y'], cap_ref) for pad in design.pads.get((cap_ref, cap_pin), [])]
            if not pin_points or not cap_pads:
                continue
            reach = min(((math.hypot(px - cx, py - cy), (px, py), (cx, cy), cap_ref)
                         for px, py in pin_points for cx, cy, cap_ref in cap_pads), key=lambda item: item[0])
            if reach[0] > limit and (net not in worst or reach[0] > worst[net][0][0]):
                worst[net] = (reach, pin)
        for net, (reach, pin) in sorted(worst.items()):
            name = parts[ref]['pins'][pin]['name']
            add('DECOUPLING_DISTANCE', 'warning',
                '%s pin %s%s on %s: nearest capacitor to ground is %s, %.1f mm away (limit %.0f mm).' % (
                    ref, pin, ' (%s)' % name if name else '', net, reach[3], reach[0], limit),
                refs=[ref, reach[3]], nets=[net],
                marks=[line(reach[1], reach[2], '%s.%s to %s %.1f mm' % (ref, pin, reach[3], reach[0]))],
                basis='straight distance between pad centers')

    # differential pairs
    pairs = differential_pairs(design)
    skew_limit = config['diff_pair_max_skew_mm']
    for positive, negative in pairs:
        first, second = design.nets[positive], design.nets[negative]
        length_p, length_n = first.get('track_length') or 0.0, second.get('track_length') or 0.0
        if not length_p or not length_n:
            continue
        marks = []
        longer = positive if length_p >= length_n else negative
        if design.tracks_by_net.get(longer):
            start = design.tracks_by_net[longer][0]['start']
            marks = [point(start[0], start[1], longer)]
        if abs(length_p - length_n) > skew_limit:
            add('DIFF_PAIR_SKEW', 'warning',
                '%s / %s: routed lengths %.2f and %.2f mm differ by %.2f mm (limit %.1f mm).' % (
                    positive, negative, length_p, length_n, abs(length_p - length_n), skew_limit),
                nets=[positive, negative], marks=marks,
                basis='sum of track lengths per net; via and pad lengths are not counted')
        if (first.get('vias') or 0) != (second.get('vias') or 0):
            add('DIFF_PAIR_VIAS', 'info',
                '%s / %s: %d and %d vias.' % (positive, negative, first.get('vias') or 0, second.get('vias') or 0),
                nets=[positive, negative], marks=marks)

    # reference plane under critical nets
    critical = {net for pair in pairs for net in pair}
    pattern = re.compile(config['critical_net_regex']) if config['critical_net_regex'] else None
    for net, entry in design.nets.items():
        classes = entry.get('classes') or []
        if any(name != 'Default' for name in classes) or (pattern and pattern.search(design.base(net))):
            critical.add(net)
    gap_limit = config['reference_gap_max_mm']
    order = {name: index for index, name in enumerate(design.copper)}
    for net in sorted(critical, key=natural):
        if design.is_ground(net) or design.is_power(net):
            continue
        bare, spots = 0.0, []
        for track in design.tracks_by_net.get(net, []):
            index = order.get(track['layer'])
            if index is None:
                continue
            neighbours = [design.copper[i] for i in (index - 1, index + 1) if 0 <= i < len(design.copper)]
            middle = track.get('mid') or [(track['start'][0] + track['end'][0]) / 2,
                                          (track['start'][1] + track['end'][1]) / 2]
            if not any(design.covered(middle, layer) for layer in neighbours):
                bare += track['length']
                spots.append((track['length'], middle, track['layer']))
        if bare > gap_limit:
            spots.sort(key=lambda item: -item[0])
            add('NO_REFERENCE_PLANE', 'warning',
                '%s: %.1f mm of its routing has no copper zone on an adjacent layer (longest piece %.1f mm on %s).' % (
                    net, bare, spots[0][0], spots[0][2]),
                nets=[net], marks=[point(spot[1][0], spot[1][1], net) for spot in spots[:4]],
                basis='zone outlines, not the poured copper; cutouts inside a zone are not seen')

    # dangling track ends and nets without copper
    for net in sorted((name for name in design.tracks_by_net if name), key=natural):
        tracks = design.tracks_by_net[net]
        loose = []
        for index, track in enumerate(tracks):
            for end in (track['start'], track['end']):
                joined = False
                for other_index, other in enumerate(tracks):
                    if other_index == index or other['layer'] != track['layer']:
                        continue
                    reach, _closest = point_segment(end, other['start'], other['end'])
                    if reach <= max(TOUCH, (other['width'] or 0) / 2):
                        joined = True
                        break
                if not joined:
                    for pad in design.pads_by_net.get(net, []):
                        if track['layer'] in design.pad_layers(pad) and math.hypot(
                                end[0] - pad['x'], end[1] - pad['y']) <= max(pad['size'] or [0.2]) / 2 + 0.05:
                            joined = True
                            break
                if not joined:
                    joined = any(math.hypot(end[0] - via['x'], end[1] - via['y']) <= (via['size'] or 0.4) / 2 + 0.05
                                 for via in design.vias_by_net.get(net, []))
                if not joined and not design.covered(end, track['layer'], net=net):
                    loose.append((end, track['layer']))
        if loose:
            add('DANGLING_TRACK', 'warning',
                '%s: %d track end%s not connected to a pad, via, track or zone of the net (first on %s).' % (
                    net, len(loose), 's' if len(loose) > 1 else '', loose[0][1]),
                nets=[net], marks=[point(end[0], end[1], net) for end, _layer in loose[:6]],
                basis='track ends compared with pads, vias, tracks and zone outlines of the same net')
    for net, entry in sorted(design.nets.items(), key=lambda item: natural(item[0])):
        pads = design.pads_by_net.get(net, [])
        if len(pads) < 2 or design.tracks_by_net.get(net) or design.vias_by_net.get(net) \
                or entry.get('zone_layers') or any(fill['net'] == net for fill in design.layout.get('fills', [])):
            continue
        spread = max(math.hypot(a['x'] - b['x'], a['y'] - b['y']) for a in pads for b in pads)
        if spread > 0.3:
            add('NET_NO_COPPER', 'warning',
                '%s: %d pads but no track, via or zone on the net.' % (net, len(pads)),
                refs=[pad['ref'] for pad in pads if pad['ref']], nets=[net],
                marks=[point(pad['x'], pad['y'], '%s.%s' % (pad['ref'], pad['pad'])) for pad in pads[:4]],
                basis='copper objects read from the board file; overlapping pads are not detected')

    # board edge
    edge_limit = (design.data.get('rules') or {}).get('min_copper_edge_clearance') or config['edge_clearance_mm']
    edges = []
    for shape in design.layout.get('outline', []):
        pts = shape['points']
        if shape['kind'] == 'circle' or len(pts) < 2:
            continue
        closed = shape['kind'] in ('poly', 'rect')
        for index in range(len(pts) - (0 if closed else 1)):
            edges.append((pts[index], pts[(index + 1) % len(pts)]))
    near = []
    if edges:
        for track in design.layout.get('tracks', []):
            if not track['net']:
                continue
            reach = min(segment_segment(track['start'], track['end'], a, b)[0] for a, b in edges)
            gap = reach - (track['width'] or 0) / 2
            if gap < edge_limit:
                near.append((gap, track['start'], track['net'], track['layer']))
        for via in design.layout.get('vias', []):
            reach = min(point_segment((via['x'], via['y']), a, b)[0] for a, b in edges)
            gap = reach - (via['size'] or 0) / 2
            if gap < edge_limit:
                near.append((gap, (via['x'], via['y']), via['net'], 'via'))
    if near:
        near.sort(key=lambda item: item[0])
        add('EDGE_CLEARANCE', 'warning',
            '%d tracks or vias closer than %.2f mm to the board edge; closest %.2f mm (%s, %s).' % (
                len(near), edge_limit, near[0][0], near[0][2], near[0][3]),
            nets=[item[2] for item in near if item[2]],
            marks=[point(item[1][0], item[1][1], '%s %.2f mm' % (item[2], item[0])) for item in near[:6]],
            basis='board outline taken from the outline layer; cutouts drawn elsewhere are not seen')

    # isolation rules
    for rule in config['isolation']:
        violations = isolation(design, rule)
        for gap, first, second, net_a, net_b, layer in violations[:6]:
            if gap is None:
                add('ISOLATION', 'warning',
                    '%s: %s lies inside the %s on %s; the gap is set by the pour clearance, which the '
                    'board data does not give. Check it against %.2f mm.' % (
                        rule.get('name', 'isolation'), net_a, net_b, layer, rule['min_mm']),
                    nets=[net_a], marks=[point(first[0], first[1], net_a)],
                    basis='zone outline contains the copper; the poured shape is not read')
                continue
            add('ISOLATION', 'error',
                '%s: %s to %s is %.2f mm on %s (minimum %.2f mm).' % (
                    rule.get('name', 'isolation'), net_a, net_b, gap, layer, rule['min_mm']),
                nets=[net_a, net_b],
                marks=[line(first, second, '%.2f mm' % gap)] if first != second
                else [point(first[0], first[1], '%.2f mm' % gap)],
                basis='pads taken as circles of their larger dimension; pours by their clearance setting')
        if not violations:
            add('ISOLATION', 'info', '%s: no copper of the two groups closer than %.2f mm on any layer.' % (
                rule.get('name', 'isolation'), rule['min_mm']))

    findings.sort(key=lambda item: (SEVERITY_ORDER[item['severity']], item['check'],
                                    natural(item['nets'][0]) if item['nets'] else []))
    for index, item in enumerate(findings):
        item['id'] = index + 1
    return findings


def isolation(design, rule):
    """Copper of group a closer than min_mm to copper of group b on a shared layer."""
    minimum = float(rule['min_mm'])
    group = {'a': re.compile(rule['a']), 'b': re.compile(rule['b'])}

    def shapes(key):
        found = []  # (layers, start, end, radius, net)
        for net in design.nets:
            if not group[key].search(net):
                continue
            for track in design.tracks_by_net.get(net, []):
                found.append(({track['layer']}, tuple(track['start']), tuple(track['end']),
                              (track['width'] or 0) / 2, net))
            for via in design.vias_by_net.get(net, []):
                found.append((set(design.copper), (via['x'], via['y']), (via['x'], via['y']),
                              (via['size'] or 0) / 2, net))
            for pad in design.pads_by_net.get(net, []):
                found.append((design.pad_layers(pad), (pad['x'], pad['y']), (pad['x'], pad['y']),
                              max(pad['size'] or [0]) / 2, net))
        for zone_net, layers, outline, _whole in design.zones:
            if zone_net and group[key].search(zone_net) and len(outline) > 1:
                for index in range(len(outline)):
                    found.append((set(layers), tuple(outline[index]), tuple(outline[(index + 1) % len(outline)]),
                                  0.0, zone_net))
        return found

    first, second = shapes('a'), shapes('b')
    cell = max(minimum * 2, 2.0)
    grid = collections.defaultdict(list)
    for index, (_layers, start, end, radius, _net) in enumerate(second):
        reach = radius + minimum
        for gx in range(int(math.floor((min(start[0], end[0]) - reach) / cell)),
                        int(math.floor((max(start[0], end[0]) + reach) / cell)) + 1):
            for gy in range(int(math.floor((min(start[1], end[1]) - reach) / cell)),
                            int(math.floor((max(start[1], end[1]) + reach) / cell)) + 1):
                grid[(gx, gy)].append(index)
    violations = {}
    for layers, start, end, radius, net in first:
        seen = set()
        for gx in range(int(math.floor((min(start[0], end[0]) - radius) / cell)),
                        int(math.floor((max(start[0], end[0]) + radius) / cell)) + 1):
            for gy in range(int(math.floor((min(start[1], end[1]) - radius) / cell)),
                            int(math.floor((max(start[1], end[1]) + radius) / cell)) + 1):
                for index in grid.get((gx, gy), []):
                    if index in seen:
                        continue
                    seen.add(index)
                    other_layers, other_start, other_end, other_radius, other_net = second[index]
                    shared = layers & other_layers
                    if not shared or other_net == net:
                        continue
                    reach, near_a, near_b = segment_segment(start, end, other_start, other_end)
                    gap = reach - radius - other_radius
                    if gap < minimum:
                        key = (net, other_net)
                        if key not in violations or gap < violations[key][0]:
                            violations[key] = (max(gap, 0.0), near_a, near_b, net, other_net, sorted(shared)[0])
    # copper of one group lying inside a pour of the other: the gap is the pour clearance
    for own, other_key in ((first, 'b'), (second, 'a')):
        for layers, start, _end, _radius, net in own:
            for index, (zone_net, zone_layers, outline, whole) in enumerate(design.zones):
                if not zone_net or zone_net == net or not group[other_key].search(zone_net):
                    continue
                shared = layers & set(zone_layers)
                if not shared or not (whole or (len(outline) > 2 and inside(start, outline))):
                    continue
                gap = design.zone_clearance.get(index)
                key = (net, zone_net, 'pour')
                if gap is None or gap < minimum:
                    violations.setdefault(key, (gap, start, start, net, zone_net + ' pour', sorted(shared)[0]))
    return sorted(violations.values(), key=lambda item: -1.0 if item[0] is None else item[0])


def report(design, findings):
    lines = []
    out = lines.append
    counts = collections.Counter(item['severity'] for item in findings)
    board = design.data.get('board') or {}
    out('# Layout checks: %s' % design.data['source']['project'])
    out('')
    out('- %d errors, %d warnings, %d notes' % (counts['error'], counts['warning'], counts['info']))
    if board.get('size'):
        out('- board %.1f x %.1f mm, %d copper layers' % (board['size'][0], board['size'][1], len(design.copper)))
    for limit in design.data.get('limits', []):
        out('- reader limit: %s' % limit)
    out('')
    out('## Findings')
    out('')
    if not findings:
        out('No findings.')
    for item in findings:
        basis = ' Basis: %s.' % item['basis'] if item['basis'] else ''
        out('- **%s** #%d %s: %s%s' % (item['severity'], item['id'], item['check'], item['message'], basis))
    out('')
    out('## Supply nets')
    out('')
    out('| Net | Pins | Track mm | Narrowest mm | Vias | Zone layers |')
    out('| --- | --- | --- | --- | --- | --- |')
    for net, entry in sorted(design.nets.items(), key=lambda item: -len(item[1]['pins'])):
        if design.is_power(net) or design.is_ground(net):
            out('| %s | %d | %s | %s | %s | %s |' % (
                net, len(entry['pins']), entry.get('track_length', ''),
                entry.get('min_width') if entry.get('min_width') is not None else '',
                entry.get('vias', ''), ' '.join(entry.get('zone_layers', []))))
    return '\n'.join(lines) + '\n'


def find_design(path):
    if os.path.isfile(path):
        return path
    for candidate in (os.path.join(path, 'design.json'), os.path.join(path, '.hwread', 'design.json')):
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description='Layout checks on hw-read output.')
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
    if not os.path.exists(layout_path):
        sys.exit('No layout.json next to design.json. The project has no board file, or hw-read ran '
                 'with --no-layout.')
    with open(layout_path, encoding='utf-8') as handle:
        layout = json.load(handle)
    design = Design(data, layout, load_config(design_dir, args.config))
    findings = run_checks(design)
    out_dir = args.out or design_dir
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'pcb-findings.json'), 'w', encoding='utf-8') as handle:
        json.dump(dict(schema='pcb-review/1', project=data['source']['project'], findings=findings),
                  handle, ensure_ascii=False, indent=1)
    text = report(design, findings)
    with open(os.path.join(out_dir, 'pcb-checks.md'), 'w', encoding='utf-8') as handle:
        handle.write(text)
    with open(os.path.join(out_dir, 'pcb-board.html'), 'w', encoding='utf-8') as handle:
        handle.write(boardview.render('Layout checks: %s' % data['source']['project'], data, layout, findings))
    print(text)
    print('Written to %s' % out_dir)


if __name__ == '__main__':
    main()
