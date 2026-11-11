#!/usr/bin/env python
"""Mechanical schematic checks on the design.json written by hw-read.

Usage:
  python schcheck.py PATH [--config FILE] [--out DIR]

PATH is a design.json, the folder that holds it, or a project folder with a
.hwread/ folder inside. Writes next to design.json (or into --out):
  sch-findings.json   findings as data
  sch-checks.md       findings plus the context tables for the datasheet pass
The Markdown is also printed to stdout.
"""
import argparse
import collections
import json
import os
import re
import sys

SUPPORTED_SCHEMA = 'hw-read/1'

DEFAULT_CONFIG = dict(
    # matched against the last path segment of a net name
    ground_net_regex=r'(?i)^(a|d|p|s|e)?gnd\w*$|^vss\w*$|^0v$',
    power_net_regex=(r'(?i)^\+?\d+(\.\d+)?v\d*(_\w*)?$|^v(cc|dd|in|bat|bus|sys|usb|com|ref|out)\w*$'
                     r'|^[adp]?v(cc|dd)\w*$|(^|_)(pwr|vcc|vdd|vbus|vbat)(_|$)'),
    # pin names treated as supply pins when the symbol does not type them
    supply_pin_regex=r'(?i)^(a|d|p|io)?v(cc|dd|in|bat|bus|io|s|ref)\w*$|^v\+$|^vdd\w*$',
    # supply-looking pin names that are sense, feedback or status pins
    not_supply_pin_regex=r'(?i)det|sns|sense|mon|good|_ok|_en$|^fb|_fb|adj',
    # pin names listed in the configuration table of the context section
    config_pin_regex=(r'(?i)(^|[^a-z])(n?en|enable|n?rst|n?reset|boot\d*|mode\d*|sel\d*|addr?\d*|a[0-2]'
                      r'|cfg\d*|n?shdn|n?sleep|n?stby|pg|pgood|n?wp|n?hold|n?oe|n?cs|n?ss|n?fault|n?int)($|[^a-z])'),
    i2c_net_regex=r'(?i)(?<![a-z])(sda|scl)(?!k)',
    # voltages of nets whose name does not carry one, e.g. {"VBUS_SENSE": 5.0, "VDD33": 3.3}
    net_voltages={},
    # a signal pin may sit this far above the highest supply of its IC (volt)
    pin_overvoltage_margin=0.3,
    # capacitor rated voltage must be at least this times the rail voltage
    cap_voltage_margin=1.5,
    # resistors at or below this value join two nets for the checks (ohm)
    bridge_max_ohm=1.0,
    reference_kinds=dict(C='capacitor', R='resistor', L='inductor', FB='inductor', D='diode',
                         LED='diode', U='ic', IC='ic', J='connector', P='connector', CN='connector',
                         CON='connector', TP='testpoint', Q='transistor', T='transistor', F='fuse',
                         Y='crystal', SW='switch', FD='mechanical', MH='mechanical', H='mechanical'),
    # check ids to skip, e.g. ["BOM_NO_MPN"]
    skip_checks=[],
)

EXTERNAL_PIN_LIMIT = 12  # context lines per IC in the Markdown
SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}
MULTIPLIERS = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'µ': 1e-6, 'm': 1e-3, 'r': 1.0, '': 1.0,
               'k': 1e3, 'meg': 1e6, 'g': 1e9}
AUTO_NET = re.compile(r'^(Net-\(|Net[A-Z]+\d+_|unnamed-\(|unconnected-\()')


def load_config(design_dir, path=None):
    config = dict(DEFAULT_CONFIG)
    if path is None:
        path = os.path.join(design_dir, 'sch-review.json')
        if not os.path.exists(path):
            return config
    with open(path, encoding='utf-8') as handle:
        config.update(json.load(handle))
    return config


def parse_value(text, farads=False):
    """Number from '4k7', '49R9', '100n', '2.2uF', 'R_10k_0402'. None when not found."""
    if not text:
        return None
    for token in re.split(r'[\s_/,]+', text):
        token = token.strip()
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


def rated_voltage(part):
    for name, text in part.get('fields', {}).items():
        key = re.sub(r'[^a-z]', '', name.lower())
        if key in ('voltage', 'voltagerated', 'ratedvoltage', 'voltagerating', 'vrated'):
            match = re.search(r'(\d+(?:\.\d+)?)\s*(k?)V', text or '', re.IGNORECASE)
            if match:
                return float(match.group(1)) * (1000 if match.group(2) else 1)
    match = re.search(r'(?<![\w.])(\d+(?:\.\d+)?)\s*V(?![\w])', part.get('value') or '')
    return float(match.group(1)) if match else None


class Design:
    def __init__(self, data, config):
        self.data = data
        self.config = config
        self.parts = data['components']
        self.nets = data['nets']
        self.ground = re.compile(config['ground_net_regex'])
        self.power = re.compile(config['power_net_regex'])
        self.supply_pin = re.compile(config['supply_pin_regex'])
        self.not_supply_pin = re.compile(config['not_supply_pin_regex'])
        self.members = collections.defaultdict(list)  # net -> [(ref, pin)]
        for ref, part in self.parts.items():
            for pin, data_pin in part['pins'].items():
                if data_pin['net']:
                    self.members[data_pin['net']].append((ref, pin))
        self.power_typed = {data_pin['net'] for part in self.parts.values()
                            for data_pin in part['pins'].values()
                            if data_pin['net'] and (data_pin['type'] or '').startswith('power')}
        self._bridges = None

    # -- classification
    def kind(self, ref):
        prefix = re.match(r'[A-Za-z]*', ref).group(0).upper()
        found = self.config['reference_kinds'].get(prefix)
        if found:
            return found
        part = self.parts[ref]
        return 'ic' if len(part['pins']) >= 8 else 'other'

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

    def supply_basis(self, ref, pin):
        """'pin type' or 'pin name' when the pin is a supply input, else None."""
        data_pin = self.parts[ref]['pins'][pin]
        if self.is_ground(data_pin['net']):
            return None
        if self.pin_type(ref, pin) == 'power_in':
            return 'pin type'
        name = data_pin['name'] or ''
        if self.supply_pin.search(name) and not self.not_supply_pin.search(name):
            return 'pin name'
        return None

    def rail_like(self, net):
        """A rail by name or pin type, or an unnamed node that has a capacitor to ground."""
        if self.is_power(net):
            return True
        if not net or self.is_ground(net):
            return False
        return any(self.is_ground(other) and self.fitted(ref)
                   for ref, other in self.two_pin_links(net, 'capacitor'))

    def voltage(self, net):
        """Net voltage from the settings or read from the net name, or None."""
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

    def supply_voltage(self, ref):
        """Highest known voltage on the supply pins of a part, or None."""
        known = [self.voltage(self.parts[ref]['pins'][pin]['net'])
                 for pin in self.parts[ref]['pins'] if self.supply_basis(ref, pin)]
        if not known or any(volts is None for volts in known):
            return None
        return max(known)

    def rail_voltage(self, net):
        """Voltage of a net that is a rail or has a declared voltage; signal names do not count."""
        declared = self.config['net_voltages']
        if net in declared or self.base(net) in declared or self.is_power(net):
            return self.voltage(net)
        return None

    def pull_down_ohms(self, net):
        """Parallel value of the fitted resistors from a net to ground: (ohm or None, all known)."""
        values = []
        for ref, other in self.two_pin_links(net, 'resistor'):
            if self.is_ground(other) and self.fitted(ref):
                values.append(self.ohms(ref))
        if not values:
            return None, True
        if any(value is None or value <= 0 for value in values):
            return None, False
        return 1.0 / sum(1.0 / value for value in values), True

    def external_paths(self, net):
        """Connector pins a net reaches directly or through one series part: [(connector pin, via)]."""
        found = [('%s.%s' % (ref, pin), None) for ref, pin in self.members.get(net, [])
                 if self.kind(ref) == 'connector']
        for kind in ('resistor', 'inductor'):
            for ref, other in self.two_pin_links(net, kind):
                if not self.fitted(ref) or self.is_ground(other) or self.is_power(other):
                    continue
                for other_ref, other_pin in self.members.get(other, []):
                    if self.kind(other_ref) == 'connector':
                        found.append(('%s.%s' % (other_ref, other_pin), ref))
        return found

    def pin_type(self, ref, pin):
        return (self.parts[ref]['pins'][pin]['type'] or '').split('+')[0]

    def no_connect(self, ref, pin):
        return 'no_connect' in (self.parts[ref]['pins'][pin]['type'] or '')

    def two_pin_nets(self, ref):
        """(net a, net b) of a two-terminal part, or None."""
        nets = [data_pin['net'] for data_pin in self.parts[ref]['pins'].values()]
        return tuple(nets) if len(nets) == 2 else None

    def value_text(self, ref):
        part = self.parts[ref]
        return part['fields'].get('Val') or part['value'] or ''

    def ohms(self, ref):
        return parse_value(self.value_text(ref))

    # -- connectivity helpers
    def bridges(self):
        """net -> nets joined through fitted links of about zero ohm."""
        if self._bridges is None:
            self._bridges = collections.defaultdict(set)
            for ref in self.parts:
                if self.kind(ref) != 'resistor' or not self.fitted(ref):
                    continue
                nets = self.two_pin_nets(ref)
                value = self.ohms(ref)
                if nets and all(nets) and value is not None and value <= self.config['bridge_max_ohm']:
                    self._bridges[nets[0]].add(nets[1])
                    self._bridges[nets[1]].add(nets[0])
        return self._bridges

    def joined(self, net, through_inductors=False):
        """The net plus nets one link away."""
        result = {net} | self.bridges().get(net, set())
        if through_inductors:
            for ref, _pin in self.members.get(net, []):
                if self.kind(ref) == 'inductor' and self.fitted(ref):
                    nets = self.two_pin_nets(ref)
                    if nets and all(nets):
                        result.update(nets)
        return result

    def two_pin_links(self, net, kind):
        """Two-terminal parts of a kind on a net: [(ref, other net)]."""
        found = []
        for ref, _pin in self.members.get(net, []):
            if self.kind(ref) != kind:
                continue
            nets = self.two_pin_nets(ref)
            if nets and nets[0] != nets[1]:
                found.append((ref, nets[1] if nets[0] == net else nets[0]))
        return found

    def decoupling(self, net):
        """Capacitors from the net (or a net one link away) to ground: (fitted, not fitted)."""
        fitted, absent = [], []
        for candidate in self.joined(net, through_inductors=True):
            for ref, other in self.two_pin_links(candidate, 'capacitor'):
                if self.is_ground(other):
                    (fitted if self.fitted(ref) else absent).append(ref)
        return sorted(set(fitted), key=natural), sorted(set(absent), key=natural)

    def pulls(self, net):
        """Resistors from the net to a rail or ground: [(ref, 'up'|'down', rail, fitted)]."""
        found = []
        for candidate in self.joined(net):
            for ref, other in self.two_pin_links(candidate, 'resistor'):
                value = self.ohms(ref)
                if value is not None and value <= self.config['bridge_max_ohm']:
                    continue
                if self.is_ground(other):
                    found.append((ref, 'down', other, self.fitted(ref)))
                elif self.rail_like(other):
                    found.append((ref, 'up', other, self.fitted(ref)))
        return found

    def describe(self, ref):
        part = self.parts[ref]
        text = '%s %s' % (ref, self.value_text(ref) or part.get('mpn') or '')
        return text.strip() + ('' if self.fitted(ref) else ' (not fitted)')


def natural(text):
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r'(\d+)', text)]


def compress(refs, limit=24):
    refs = sorted(set(refs), key=natural)
    text = ', '.join(refs[:limit])
    return text + (' and %d more' % (len(refs) - limit) if len(refs) > limit else '')


# ---------------------------------------------------------------- checks
def run_checks(design):
    findings = []
    parts, config = design.parts, design.config

    def add(check, severity, message, refs=(), nets=(), basis=None):
        if check in config['skip_checks']:
            return
        refs = sorted(set(refs), key=natural)
        sheet = parts[refs[0]].get('sheet') if refs else None
        findings.append(dict(check=check, severity=severity, sheet=sheet, refs=refs,
                             nets=sorted(set(nets)), message=message, basis=basis))

    def supply_pins(ref):
        for pin, data_pin in parts[ref]['pins'].items():
            basis = design.supply_basis(ref, pin)
            if basis:
                yield pin, data_pin, basis

    # supply pins left open, and supply nets without a capacitor to ground
    for ref in parts:
        if design.kind(ref) != 'ic' or not design.fitted(ref):
            continue
        bare = collections.defaultdict(list)
        for pin, data_pin, basis in supply_pins(ref):
            net = data_pin['net']
            label = '%s (%s)' % (pin, data_pin['name'] or '?')
            if design.no_connect(ref, pin):
                continue
            if net is None or len(design.members[net]) < 2:
                add('SUPPLY_PIN_OPEN', 'error',
                    '%s supply pin %s is not connected to anything.' % (ref, label),
                    refs=[ref], nets=[net] if net else [], basis='supply pin by ' + basis)
                continue
            fitted, absent = design.decoupling(net)
            if not fitted:
                bare[net].append((label, absent, basis))
        for net, pins in bare.items():
            absent = pins[0][1]
            note = ' Only %s, which is not fitted.' % compress(absent) if absent else ''
            add('NO_DECOUPLING', 'warning',
                '%s: no capacitor to ground on supply net %s (pins %s).%s' % (
                    ref, net, ', '.join(label for label, _absent, _basis in pins), note),
                refs=[ref], nets=[net],
                basis='supply pin by %s; ground by net name' % pins[0][2])

    # nets
    i2c = re.compile(config['i2c_net_regex'])
    lone = collections.defaultdict(list)       # (ref, severity) -> ['pin (net)']
    unpulled = collections.defaultdict(list)   # (reason, off board) -> [(net, refs, note)]
    for net, members in design.members.items():
        types = [design.pin_type(ref, pin) for ref, pin in members]
        flagged = [design.no_connect(ref, pin) for ref, pin in members]
        refs = [ref for ref, _pin in members]
        active = [(ref, pin) for ref, pin in members if design.fitted(ref)]

        if len(members) == 1 and not flagged[0]:
            ref, pin = members[0]
            name = parts[ref]['pins'][pin]['name']
            if design.kind(ref) not in ('testpoint', 'mechanical'):
                quiet = AUTO_NET.match(net) or design.kind(ref) == 'connector'
                label = '%s%s on %s' % (pin, ' (%s)' % name if name and name != pin else '', net)
                lone[(ref, 'info' if quiet else 'warning')].append((label, net))

        if types and all(kind == 'input' for kind in types) and not any(flagged):
            add('INPUT_NOT_DRIVEN', 'warning',
                'Net %s has only input pins (%s) and nothing that drives it.' % (
                    net, ', '.join('%s.%s' % member for member in members[:6])),
                refs=refs, nets=[net], basis='pin types from the symbols')

        drivers = collections.defaultdict(list)
        for (ref, pin), kind in zip(members, types):
            if kind in ('output', 'power_out') and design.fitted(ref):
                drivers[ref].append(pin)
        if len(drivers) > 1:
            add('OUTPUTS_TIED', 'warning',
                'Net %s is driven by outputs of several parts: %s.' % (
                    net, ', '.join('%s.%s' % (ref, pins[0]) for ref, pins in sorted(drivers.items()))),
                refs=list(drivers), nets=[net], basis='pin types from the symbols')

        needs_pull = None
        if i2c.search(design.base(net)) and len(active) > 1:
            needs_pull = 'looks like an I2C line'
        elif 'open_collector' in types or 'open_emitter' in types:
            if not any(kind in ('output', 'power_out') for kind in types):
                needs_pull = 'has an open-collector pin'
        if needs_pull and len(members) > 1 and not any(flagged) \
                and not design.is_power(net) and not design.is_ground(net):
            pulls = design.pulls(net)
            if not any(fitted for _ref, _direction, _rail, fitted in pulls):
                absent = [ref for ref, _direction, _rail, fitted in pulls if not fitted]
                offboard = any(design.kind(ref) == 'connector' for ref in refs)
                unpulled[(needs_pull, offboard)].append(
                    (net, refs, ' (%s not fitted)' % compress(absent) if absent else ''))

    for (ref, severity), items in sorted(lone.items(), key=lambda item: natural(item[0][0])):
        add('SINGLE_PIN_NET', severity,
            '%s: nets with no other pin: %s.' % (ref, ', '.join(label for label, _net in items[:20])
                                                + (' ...' if len(items) > 20 else '')),
            refs=[ref], nets=[net for _label, net in items])
    for (reason, offboard), items in sorted(unpulled.items()):
        add('NO_PULL_RESISTOR', 'info' if offboard else 'warning',
            'No fitted pull resistor on this board for nets that %s: %s.%s' % (
                reason.replace('looks like an I2C line', 'look like I2C lines')
                      .replace('has an open-collector pin', 'have an open-collector pin'),
                ', '.join(net + note for net, _refs, note in sorted(items)),
                ' They leave through a connector, so the pull may be off board.' if offboard else ''),
            refs=[ref for _net, refs, _note in items for ref in refs if design.kind(ref) == 'ic'],
            nets=[net for net, _refs, _note in items],
            basis='net name or pin type; a rail is a power net or a node with a capacitor to ground')

    # two-terminal parts
    margin = config['cap_voltage_margin']
    for ref, part in parts.items():
        kind = design.kind(ref)
        nets = design.two_pin_nets(ref)
        if not nets or not design.fitted(ref):
            continue
        if kind in ('resistor', 'capacitor', 'inductor', 'diode') and nets[0] and nets[0] == nets[1]:
            add('PART_SHORTED', 'warning',
                '%s has both pins on net %s.' % (design.describe(ref), nets[0]), refs=[ref], nets=[nets[0]])
        if kind == 'capacitor' and all(nets):
            rail = next((net for net in nets if not design.is_ground(net)), None)
            other = nets[1] if nets[0] == rail else nets[0]
            rating = rated_voltage(part)
            volts = design.voltage(rail) if rail and design.is_ground(other) else None
            if rating and volts and rating < volts * margin:
                add('CAP_VOLTAGE', 'error' if rating < volts else 'warning',
                    '%s is rated %gV on %s (%gV): below %gx the rail.' % (
                        design.describe(ref), rating, rail, volts, margin),
                    refs=[ref], nets=[rail], basis='rail voltage read from the net name')

    # signal pins that see a net above the supply of their IC
    allowed = config['pin_overvoltage_margin']
    for ref, part in parts.items():
        if design.kind(ref) != 'ic' or not design.fitted(ref):
            continue
        supply = design.supply_voltage(ref)
        if supply is None:
            continue
        for pin, data_pin in part['pins'].items():
            net = data_pin['net']
            if not net or design.is_ground(net) or design.supply_basis(ref, pin):
                continue
            label = '%s.%s%s' % (ref, pin, ' (%s)' % data_pin['name'] if data_pin['name'] else '')
            direct = design.rail_voltage(net)
            if direct is not None and direct > supply + allowed:
                add('PIN_ABOVE_SUPPLY', 'warning',
                    '%s is on %s (%gV) while the part is supplied with %gV.' % (label, net, direct, supply),
                    refs=[ref], nets=[net], basis='voltages from net names or the net_voltages setting')
                continue
            down, known = design.pull_down_ohms(net)
            for resistor, other in design.two_pin_links(net, 'resistor'):
                volts = design.rail_voltage(other)
                if not design.fitted(resistor) or volts is None or volts <= supply + allowed:
                    continue
                series = design.ohms(resistor)
                if down is None and known:
                    level, how = volts, 'no resistor to ground on the pin side'
                elif down is None or series is None:
                    continue
                else:
                    level = volts * down / (series + down)
                    how = 'divider gives %.2fV' % level
                if level > supply + allowed:
                    add('PIN_ABOVE_SUPPLY', 'warning',
                        '%s reaches %s (%gV) through %s, %s; the part is supplied with %gV.' % (
                            label, other, volts, design.describe(resistor), how, supply),
                        refs=[ref, resistor], nets=[net, other],
                        basis='voltages from net names or the net_voltages setting')

    # pins without any net (the reader could not tell a no-connect mark apart)
    for ref, part in parts.items():
        if not design.fitted(ref) or design.kind(ref) in ('testpoint', 'mechanical'):
            continue
        open_pins = [(pin, data_pin['name']) for pin, data_pin in part['pins'].items()
                     if data_pin['net'] is None and data_pin['type'] is not None
                     and not design.no_connect(ref, pin)]
        if open_pins and design.kind(ref) in ('ic', 'transistor', 'other'):
            shown = ', '.join('%s%s' % (pin, ' (%s)' % name if name and name != pin else '')
                              for pin, name in sorted(open_pins, key=lambda item: natural(item[0]))[:16])
            add('PINS_OPEN', 'info',
                '%s: pins with no connection (%d): %s%s.' % (
                    ref, len(open_pins), shown, ' ...' if len(open_pins) > 16 else ''),
                refs=[ref], basis='no-connect marks are not visible in this data')

    # bill of materials
    countable = [ref for ref, part in parts.items()
                 if design.fitted(ref) and part.get('in_bom', True) and not part.get('board_only')
                 and design.kind(ref) not in ('testpoint', 'mechanical')]
    without = [ref for ref in countable if not parts[ref].get('mpn')]
    if without and len(without) == len(countable):
        add('BOM_NO_MPN', 'info',
            'No part has a recognized manufacturer part number field; see the field names in design.json.')
    elif without:
        add('BOM_NO_MPN', 'info', 'Fitted parts without a manufacturer part number: %s.' % compress(without),
            refs=without)

    # differences already found by the reader
    drift = (design.data.get('checks') or {}).get('schematic_vs_board') or {}
    if drift.get('only_in_schematic'):
        add('SCH_BOARD_DIFF', 'warning', 'In the schematic but not on the board: %s.'
            % compress(drift['only_in_schematic']))
    if drift.get('only_in_board'):
        add('SCH_BOARD_DIFF', 'warning', 'On the board but not in the schematic: %s.'
            % compress(drift['only_in_board']))
    for item in drift.get('differences', [])[:40]:
        add('SCH_BOARD_DIFF', 'warning', '%(ref)s %(field)s differs: schematic %(schematic)s, board %(board)s.'
            % item, refs=[item['ref']] if item['ref'] in parts else [])
    for item in drift.get('net_differences', [])[:40]:
        if 'board_net' in item:
            add('SCH_BOARD_DIFF', 'warning', 'Board net %s spans several schematic nets: %s.' % (
                item['board_net'], ', '.join(item['schematic_nets'])), nets=[item['board_net']])
        else:
            add('SCH_BOARD_DIFF', 'warning', 'Schematic net %s spans several board nets: %s.' % (
                item['schematic_net'], ', '.join(item['board_nets'])))

    findings.sort(key=lambda item: (SEVERITY_ORDER[item['severity']], item['check'],
                                    natural(item['refs'][0]) if item['refs'] else []))
    return findings


# ---------------------------------------------------------------- context for the datasheet pass
def context(design):
    """One entry per fitted IC: supply pins with decoupling, configuration pins with pulls."""
    config_pin = re.compile(design.config['config_pin_regex'])
    entries = []
    for ref in sorted(design.parts, key=natural):
        if design.kind(ref) != 'ic' or not design.fitted(ref):
            continue
        part = design.parts[ref]
        supplies, configs = collections.OrderedDict(), []
        for pin in sorted(part['pins'], key=natural):
            data_pin = part['pins'][pin]
            name, net = data_pin['name'] or '', data_pin['net']
            if design.supply_basis(ref, pin) and net:
                entry = supplies.setdefault(net, dict(pins=[], net=net, volts=design.voltage(net)))
                entry['pins'].append('%s %s' % (pin, name))
            elif name and config_pin.search(name):
                pulls = ['%s %s to %s' % (design.describe(pull_ref), direction, rail)
                         for pull_ref, direction, rail, _fitted in design.pulls(net)] if net else []
                others = ['%s.%s' % member for member in design.members.get(net, [])
                          if member != (ref, pin)][:6] if net else []
                configs.append(dict(pin='%s %s' % (pin, name), net=net, pulls=pulls, others=others))
        for entry in supplies.values():
            fitted, absent = design.decoupling(entry['net'])
            entry['capacitors'] = [design.describe(cap) for cap in fitted + absent]
        external = []
        for pin in sorted(part['pins'], key=natural):
            data_pin = part['pins'][pin]
            net = data_pin['net']
            if not net or design.is_ground(net) or design.supply_basis(ref, pin):
                continue
            for connector_pin, via in design.external_paths(net):
                far = net if via is None else next(other for link, other in
                                                   design.two_pin_links(net, design.kind(via)) if link == via)
                volts = design.rail_voltage(far)
                external.append(dict(
                    pin='%s %s' % (pin, data_pin['name'] or ''), net=net, connector=connector_pin,
                    via=design.describe(via) if via else None, far_net=far,
                    volts=volts,
                    pulls=['%s %s to %s' % (design.describe(pull_ref), direction, rail)
                           for pull_ref, direction, rail, _fitted in design.pulls(net)]))
        entries.append(dict(ref=ref, value=part['value'], mpn=part.get('mpn'),
                            datasheet=part.get('datasheet'), sheet=part.get('sheet'),
                            pins=len(part['pins']), supplies=list(supplies.values()), configs=configs,
                            supply_volts=design.supply_voltage(ref), external=external))
    return entries


def report(design, findings, entries):
    lines = []
    out = lines.append
    source = design.data['source']
    out('# Schematic checks: %s' % source['project'])
    out('')
    counts = collections.Counter(item['severity'] for item in findings)
    out('- %d errors, %d warnings, %d notes' % (counts['error'], counts['warning'], counts['info']))
    out('- netlist taken from: %s' % source.get('netlist_from'))
    variants = design.data.get('variants') or {}
    if len(variants) > 1:
        out('- several variants (%s): a part counts as not fitted only when it is absent in all of them'
            % ', '.join(variants))
    for limit in design.data.get('limits', []):
        out('- reader limit: %s' % limit)
    out('')
    out('## Findings')
    out('')
    if not findings:
        out('No findings.')
    for item in findings:
        where = ' [%s]' % item['sheet'] if item['sheet'] else ''
        basis = ' Basis: %s.' % item['basis'] if item['basis'] else ''
        out('- **%s** %s%s: %s%s' % (item['severity'], item['check'], where, item['message'], basis))
    out('')
    out('## Context for the datasheet pass')
    for entry in entries:
        out('')
        out('### %s %s' % (entry['ref'], entry['value'] or ''))
        out('')
        out('- MPN: %s, pins: %d, sheet: %s' % (entry['mpn'] or 'unknown', entry['pins'], entry['sheet'] or '?'))
        out('- datasheet: %s' % (entry['datasheet'] or 'not in the design data'))
        for supply in entry['supplies']:
            volts = ' (%gV by name)' % supply['volts'] if supply['volts'] else ''
            out('- supply %s%s: pins %s; capacitors to ground: %s' % (
                supply['net'], volts, ', '.join(supply['pins']),
                ', '.join(supply['capacitors']) or 'none'))
        if entry['external']:
            out('- part supply voltage: %s' % ('%gV' % entry['supply_volts'] if entry['supply_volts']
                                                else 'unknown, declare it in net_voltages'))
        shown = entry['external'][:EXTERNAL_PIN_LIMIT]
        for pin in shown:
            level = '%gV' % pin['volts'] if pin['volts'] is not None else 'level unknown'
            out('- external pin %s: %s%s, net %s (%s); %s' % (
                pin['pin'].strip(), pin['connector'],
                ' through %s' % pin['via'] if pin['via'] else ' directly', pin['far_net'], level,
                '; '.join(pin['pulls']) or 'no pull resistor on the pin side'))
        if len(entry['external']) > len(shown):
            out('- %d more external pins are in sch-findings.json' % (len(entry['external']) - len(shown)))
        for pin in entry['configs']:
            state = '; '.join(pin['pulls']) or 'no pull resistor'
            out('- pin %s: net %s; %s; also on the net: %s' % (
                pin['pin'], pin['net'] or 'not connected', state, ', '.join(pin['others']) or 'nothing'))
    return '\n'.join(lines) + '\n'


def find_design(path):
    if os.path.isfile(path):
        return path
    for candidate in (os.path.join(path, 'design.json'), os.path.join(path, '.hwread', 'design.json')):
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(description='Mechanical schematic checks on a hw-read design.json.')
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
    design = Design(data, load_config(design_dir, args.config))
    findings = run_checks(design)
    entries = context(design)
    out_dir = args.out or design_dir
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'sch-findings.json'), 'w', encoding='utf-8') as handle:
        json.dump(dict(schema='sch-review/1', project=data['source']['project'], findings=findings,
                       ics=entries), handle, ensure_ascii=False, indent=1)
    text = report(design, findings, entries)
    with open(os.path.join(out_dir, 'sch-checks.md'), 'w', encoding='utf-8') as handle:
        handle.write(text)
    print(text)
    print('Written to %s' % out_dir)


if __name__ == '__main__':
    main()
