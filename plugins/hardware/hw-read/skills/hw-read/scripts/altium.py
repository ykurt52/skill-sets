"""Altium Designer project reader.

Reads .PrjPcb, binary .SchDoc and binary .PcbDoc directly. Needs olefile.
Lengths are converted to mm, angles are degrees. Board Y grows upward.
"""
import collections
import math
import os
import re
import struct

try:
    import olefile
except ImportError:  # reported by read()
    olefile = None

from common import (MANUFACTURER_FIELD_KEYS, MPN_FIELD_KEYS, SCHEMA, attach_nets, bbox, clean,
                    pick_field, polygon_area, rounded, routing_summary)

BOARD_UNIT = 2.54e-6  # mm per board file unit (1/10000 mil)
MIL = 0.0254
NO_INDEX = 0xFFFF
MULTI_LAYER = 74
FIRST_PLANE = 39
LAST_PLANE = 54
PIN_TYPES = ('input', 'bidirectional', 'output', 'open_collector', 'passive', 'tri_state',
             'open_emitter', 'power_in')
PAD_SHAPES = {1: 'round', 2: 'rect', 3: 'octagon'}
DIELECTRIC_TYPES = {'1': 'core', '2': 'prepreg', '3': 'surface material'}
RULE_NOISE = ('SELECTION', 'LAYER', 'LOCKED', 'POLYGONOUTLINE', 'USERROUTED', 'UNIONINDEX',
              'PRIMITIVELOCK', 'UNIQUEID', 'DEFINEDBYLOGICALDOCUMENT')
PER_LAYER_KEY = re.compile(r'^(TOP|BOTTOM|MID)\w*_')
MIL_VALUE = re.compile(r'^-?\d+(\.\d+)?mil$')


def _pairs(text):
    result = {}
    for item in text.split('|'):
        if '=' in item:
            key, found = item.split('=', 1)
            result[key.upper()] = found.rstrip('\r\n')
    return result


def _mil(text, default=0.0):
    if text is None:
        return default
    return float(text.replace('mil', '') or 0) * MIL


def _utf8(record, key):
    """Text of a schematic record, preferring its UTF-8 copy."""
    found = record.get('%UTF8%' + key)
    if found is not None:
        try:
            return found.encode('latin-1').decode('utf-8')
        except UnicodeError:
            return found
    return record.get(key, '')


# ---------------------------------------------------------------- board streams
def _text_records(data):
    records, offset = [], 0
    while offset + 4 <= len(data):
        length, = struct.unpack_from('<I', data, offset)
        offset += 4
        records.append(_pairs(data[offset:offset + length].rstrip(b'\0').decode('latin-1')))
        offset += length
    return records


def _binary_records(data):
    """Records stored as: type byte, uint32 length, payload."""
    records, offset = [], 0
    while offset + 5 <= len(data):
        length, = struct.unpack_from('<I', data, offset + 1)
        records.append(data[offset + 5:offset + 5 + length])
        offset += 5 + length
    return records


def _header(payload):
    """Common primitive header: layer id, net index, polygon index, component index."""
    layer, _flags, net, polygon, component = struct.unpack_from('<BHHHH', payload, 0)
    return layer, net, polygon, component


class _Layers:
    def __init__(self, board):
        self.board = board
        self.copper_ids = []
        layer_id = 1
        while layer_id and layer_id not in self.copper_ids:
            self.copper_ids.append(layer_id)
            layer_id = int(board.get('LAYER%dNEXT' % layer_id, '0') or 0)

    def name(self, layer_id):
        if layer_id == MULTI_LAYER:
            return 'Multi-Layer'
        return self.board.get('LAYER%dNAME' % layer_id) or 'Layer %d' % layer_id

    def from_text(self, text):
        text = (text or '').upper()
        if text == 'TOP':
            return self.name(1)
        if text == 'BOTTOM':
            return self.name(32)
        if text == 'MULTILAYER':
            return self.name(MULTI_LAYER)
        match = re.match(r'(MID|PLANE)(\d+)$', text)
        if match:
            base = 1 if match.group(1) == 'MID' else FIRST_PLANE - 1
            return self.name(base + int(match.group(2)))
        return text.title()

    def copper_names(self):
        return [self.name(layer_id) for layer_id in self.copper_ids]

    def is_plane(self, layer_id):
        return FIRST_PLANE <= layer_id <= LAST_PLANE


def _stackup(board, layers):
    entries = []

    def resist(prefix, label):
        height = _mil(board.get(prefix + 'HEIGHT'))
        if height > 0:
            entries.append(dict(name=label, type='solder mask', thickness=rounded(height),
                                material=board.get(prefix + 'MATERIAL', ''),
                                epsilon_r=float(board.get(prefix + 'CONST', '0') or 0)))
    resist('TOP', 'Top Solder Resist')
    for position, layer_id in enumerate(layers.copper_ids):
        key = 'LAYER%d' % layer_id
        entries.append(dict(name=layers.name(layer_id), type='copper',
                            thickness=rounded(_mil(board.get(key + 'COPTHICK')))))
        height = _mil(board.get(key + 'DIELHEIGHT'))
        if position < len(layers.copper_ids) - 1 and height > 0:
            entries.append(dict(name='dielectric %d' % (position + 1),
                                type=DIELECTRIC_TYPES.get(board.get(key + 'DIELTYPE'), 'dielectric'),
                                thickness=rounded(height),
                                material=board.get(key + 'DIELMATERIAL', ''),
                                epsilon_r=float(board.get(key + 'DIELCONST', '0') or 0)))
    resist('BOTTOM', 'Bottom Solder Resist')
    return dict(layers=entries, thickness_sum=rounded(sum(e['thickness'] for e in entries)))


def _outline(board):
    points, has_arc = [], False
    index = 0
    while 'VX%d' % index in board:
        points.append((_mil(board['VX%d' % index]), _mil(board['VY%d' % index])))
        has_arc = has_arc or board.get('KIND%d' % index, '0') != '0'
        index += 1
    return points, has_arc


def _vertices(record):
    points, index = [], 0
    while 'VX%d' % index in record:
        points.append((_mil(record['VX%d' % index]), _mil(record['VY%d' % index])))
        index += 1
    return points


def _pads(data):
    """Yield (designator, main block) for each pad record."""
    offset = 0
    while offset < len(data):
        offset += 1  # record type
        blocks = []
        for _ in range(6):
            length, = struct.unpack_from('<I', data, offset)
            offset += 4
            blocks.append(data[offset:offset + length])
            offset += length
        name = blocks[0][1:1 + blocks[0][0]].decode('latin-1') if blocks[0] else ''
        yield name, blocks[4]


def _region_areas(data):
    """Poured area in mm2 per (polygon index, layer id), and the solid regions drawn by hand.

    Returns (areas, free regions as (layer id, net index, outline in mm, area), unread count).
    """
    areas, free, unread = collections.defaultdict(float), [], 0
    for payload in _binary_records(data):
        try:
            layer, net, polygon, component = _header(payload)
            hole_count, = struct.unpack_from('<H', payload, 14)
            text_length, = struct.unpack_from('<I', payload, 18)
            props = _pairs(payload[22:22 + text_length].rstrip(b'\0').decode('latin-1'))
            offset = 22 + text_length
            rings = []
            for _ in range(1 + hole_count):
                count, = struct.unpack_from('<I', payload, offset)
                offset += 4
                values = struct.unpack_from('<%dd' % (count * 2), payload, offset)
                offset += count * 16
                rings.append(list(zip(values[0::2], values[1::2])))
        except struct.error:
            unread += 1
            continue
        if props.get('KIND', '0') != '0':
            continue
        area = (polygon_area(rings[0]) - sum(polygon_area(ring) for ring in rings[1:])) * BOARD_UNIT ** 2
        if polygon != NO_INDEX:
            areas[(polygon, layer)] += area
        elif component == NO_INDEX:
            free.append((layer, net, [(x * BOARD_UNIT, y * BOARD_UNIT) for x, y in rings[0]], area))
    return areas, free, unread


def _rule(record):
    rule = {}
    for key, text in record.items():
        if key in RULE_NOISE or PER_LAYER_KEY.match(key):
            continue
        rule[key.lower()] = rounded(_mil(text)) if MIL_VALUE.match(text) else text
    return rule


def read_board(path):
    """Return (board dict for design.json, layout dict with raw geometry)."""
    ole = olefile.OleFileIO(path)
    try:
        def stream(name):
            return ole.openstream(name + '/Data').read() if ole.exists(name + '/Data') else b''
        board = (_text_records(stream('Board6')) or [{}])[0]
        component_records = _text_records(stream('Components6'))
        net_names = [record.get('NAME') for record in _text_records(stream('Nets6'))]
        class_records = _text_records(stream('Classes6'))
        polygon_records = _text_records(stream('Polygons6'))
        pair_records = _text_records(stream('DifferentialPairs6'))
        pad_data, track_data = stream('Pads6'), stream('Tracks6')
        via_data, arc_data = stream('Vias6'), stream('Arcs6')
        region_data, rule_data = stream('Regions6'), stream('Rules6')
        fill_data = stream('Fills6')
    finally:
        ole.close()

    layers = _Layers(board)
    copper_ids = set(layers.copper_ids)
    limits = []
    if any(key.startswith('V9_STACK') for key in board) and len(layers.copper_ids) < 2:
        limits.append('Layer stack is stored in a newer format that is not read.')

    def net_of(index):
        return net_names[index] if index < len(net_names) else None

    references = [record.get('SOURCEDESIGNATOR') or None for record in component_records]
    components, duplicates = {}, []
    for record, reference in zip(component_records, references):
        if reference is None:
            continue
        if reference in components:
            duplicates.append(reference)
            continue
        components[reference] = dict(
            value=None, footprint=record.get('PATTERN'),
            description=clean(record.get('SOURCEDESCRIPTION')), mpn=None, manufacturer=None,
            datasheet=None, fields={}, sheet=clean(record.get('SOURCEHIERARCHICALPATH')),
            sheet_file=None, side='bottom' if record.get('LAYER') == 'BOTTOM' else 'top',
            x=rounded(_mil(record.get('X'))), y=rounded(_mil(record.get('Y'))),
            rotation=round(float(record.get('ROTATION', '0') or 0), 3) % 360,
            mount='other', dnp=False, in_bom=True, in_position_file=True, board_only=False, pins={})

    pads = []
    for name, block in _pads(pad_data):
        layer, net, _polygon, component = _header(block)
        x, y, width, height = struct.unpack_from('<4i', block, 13)
        hole, = struct.unpack_from('<i', block, 45)
        rotation, = struct.unpack_from('<d', block, 52)
        plated = block[60] != 0 if len(block) > 60 else True
        reference = references[component] if component < len(references) else None
        net_name = net_of(net)
        if hole > 0:
            kind = 'thru_hole' if plated else 'np_thru_hole'
        else:
            kind = 'smd'
        pads.append(dict(
            ref=reference, pad=name, net=net_name,
            x=rounded(x * BOARD_UNIT), y=rounded(y * BOARD_UNIT), rotation=round(rotation, 3),
            type=kind, shape=PAD_SHAPES.get(block[49], 'other'),
            size=[rounded(width * BOARD_UNIT), rounded(height * BOARD_UNIT)],
            drill=dict(shape='round', size=[rounded(hole * BOARD_UNIT)]) if hole > 0 else None,
            layers=layers.copper_names() if layer == MULTI_LAYER else [layers.name(layer)]))
        part = components.get(reference)
        if part is None:
            continue
        if hole > 0 and plated:
            part['mount'] = 'tht'
        elif part['mount'] == 'other':
            part['mount'] = 'smd'
        if name:
            pin = part['pins'].setdefault(name, dict(net=None, name=None, type=None))
            pin['net'] = pin['net'] or net_name

    tracks = []
    for payload in _binary_records(track_data):
        layer, net, _polygon, _component = _header(payload)
        if layer not in copper_ids:
            continue
        x1, y1, x2, y2, width = (item * BOARD_UNIT for item in struct.unpack_from('<5i', payload, 13))
        tracks.append(dict(net=net_of(net), layer=layers.name(layer), width=rounded(width),
                           start=[rounded(x1), rounded(y1)], end=[rounded(x2), rounded(y2)],
                           length=rounded(math.hypot(x2 - x1, y2 - y1))))
    for payload in _binary_records(arc_data):
        layer, net, _polygon, _component = _header(payload)
        if layer not in copper_ids:
            continue
        cx, cy, radius = (item * BOARD_UNIT for item in struct.unpack_from('<3i', payload, 13))
        angle_start, angle_end = struct.unpack_from('<2d', payload, 25)
        width = struct.unpack_from('<i', payload, 41)[0] * BOARD_UNIT
        sweep = (angle_end - angle_start) % 360 or 360.0

        def at(angle):
            return [rounded(cx + radius * math.cos(math.radians(angle))),
                    rounded(cy + radius * math.sin(math.radians(angle)))]
        tracks.append(dict(net=net_of(net), layer=layers.name(layer), width=rounded(width),
                           start=at(angle_start), mid=at(angle_start + sweep / 2),
                           end=at(angle_start + sweep),
                           length=rounded(radius * math.radians(sweep))))

    vias = []
    outer = {layers.copper_ids[0], layers.copper_ids[-1]}
    for payload in _binary_records(via_data):
        _layer, net, _polygon, _component = _header(payload)
        x, y, size, hole = (item * BOARD_UNIT for item in struct.unpack_from('<4i', payload, 13))
        span = {payload[29], payload[30]}
        kind = 'through' if span == outer else 'blind' if span & outer else 'buried'
        vias.append(dict(net=net_of(net), x=rounded(x), y=rounded(y), type=kind, size=rounded(size),
                         drill=rounded(hole), layers=[layers.name(payload[29]), layers.name(payload[30])]))

    fills = []
    for payload in _binary_records(fill_data):
        layer, net, _polygon, _component = _header(payload)
        if layer not in copper_ids:
            continue
        x1, y1, x2, y2 = (item * BOARD_UNIT for item in struct.unpack_from('<4i', payload, 13))
        rotation, = struct.unpack_from('<d', payload, 29)
        fills.append(dict(net=net_of(net), layer=layers.name(layer),
                          corner1=[rounded(x1), rounded(y1)], corner2=[rounded(x2), rounded(y2)],
                          rotation=round(rotation, 3)))

    region_areas, free_regions, unread_regions = _region_areas(region_data)
    if unread_regions:
        limits.append('%d copper regions could not be decoded; filled areas are incomplete.' % unread_regions)
    zones, zone_outlines = [], []
    for index, record in enumerate(polygon_records):
        if record.get('SHELVED') == 'TRUE':
            continue
        net_index = int(record.get('NET', '-1') or -1)
        points = _vertices(record)
        filled = {layers.name(layer): rounded(area, 2)
                  for (polygon, layer), area in region_areas.items() if polygon == index}
        zones.append(dict(
            net=net_of(net_index) if 0 <= net_index < NO_INDEX else None,
            name=clean(record.get('NAME')), layers=[layers.from_text(record.get('LAYER'))],
            priority=int(record.get('POURINDEX', '0') or 0), kind=record.get('POLYGONTYPE'),
            hatch=record.get('HATCHSTYLE'), pad_connection=None, clearance=None,
            min_thickness=None, thermal_gap=None, thermal_bridge_width=None,
            filled_area_mm2=filled, bbox=bbox(points)))
        zone_outlines.append([[rounded(p[0]), rounded(p[1])] for p in points])
    for layer_id, net_index, points, area in free_regions:
        if layer_id not in copper_ids:
            continue
        name = layers.name(layer_id)
        zones.append(dict(net=net_of(net_index), name=None, layers=[name], priority=0,
                          kind='Solid Region', hatch=None, pad_connection=None, clearance=None,
                          min_thickness=None, thermal_gap=None, thermal_bridge_width=None,
                          filled_area_mm2={name: rounded(area, 2)}, bbox=bbox(points)))
        zone_outlines.append([[rounded(p[0]), rounded(p[1])] for p in points])
    for layer_id in layers.copper_ids:
        if layers.is_plane(layer_id):
            net = clean(board.get('PLANE%dNETNAME' % (layer_id - FIRST_PLANE + 1)))
            zones.append(dict(net=None if net == '(No Net)' else net, name=None,
                              layers=[layers.name(layer_id)], priority=0, kind='Internal Plane',
                              hatch=None, pad_connection=None, clearance=None, min_thickness=None,
                              thermal_gap=None, thermal_bridge_width=None, filled_area_mm2={},
                              bbox=None))
            zone_outlines.append([])

    net_classes, class_of = {}, collections.defaultdict(list)
    for record in class_records:
        if record.get('KIND') != '0' or record.get('SUPERCLASS') == 'TRUE':
            continue
        class_name = record.get('NAME') or ''
        if class_name.endswith('_Nets') and class_name[:-len('_Nets')] in components:
            continue  # generated per component, carries no design intent
        members, index = [], 0
        while 'M%d' % index in record:
            members.append(record['M%d' % index])
            index += 1
        net_classes[record.get('NAME')] = dict(nets=len(members))
        for member in members:
            class_of[member].append(record.get('NAME'))

    rules, offset = [], 0
    while offset + 6 <= len(rule_data):
        length, = struct.unpack_from('<I', rule_data, offset + 2)
        text = rule_data[offset + 6:offset + 6 + length].rstrip(b'\0').decode('latin-1')
        rules.append(_rule(_pairs(text)))
        offset += 6 + length

    outline_points, outline_has_arc = _outline(board)
    if outline_has_arc:
        limits.append('Board outline has arcs; its size uses their end points only.')
    box = bbox(outline_points)
    stackup = _stackup(board, layers)
    summary = dict(
        file=os.path.basename(path), format_version=board.get('VERSION'), generator_version=None,
        thickness=stackup['thickness_sum'],
        copper_layers=[dict(name=layers.name(layer_id),
                            type='power' if layers.is_plane(layer_id) else 'signal')
                       for layer_id in layers.copper_ids],
        outline_bbox=box, size=[rounded(box[2] - box[0]), rounded(box[3] - box[1])] if box else None,
        y_axis='up',
        aux_origin=[rounded(_mil(board.get('ORIGINX'))), rounded(_mil(board.get('ORIGINY')))],
        counts=dict(footprints=len(components), pads=len(pads), tracks=len(tracks), vias=len(vias),
                    zones=len(zones)),
        duplicate_references=sorted(set(duplicates)))
    layout = dict(pads=pads, tracks=tracks, vias=vias, fills=fills, zone_outlines=zone_outlines,
                  outline=[dict(kind='poly', points=[[rounded(p[0]), rounded(p[1])]
                                                     for p in outline_points])] if outline_points else [])
    diff_pairs = [dict(name=record.get('NAME'), positive=record.get('POSITIVENETNAME'),
                       negative=record.get('NEGATIVENETNAME')) for record in pair_records]
    return dict(summary=summary, stackup=stackup, components=components, zones=zones,
                routing=routing_summary(tracks, vias, zones, layers.copper_names()),
                net_classes=net_classes, class_of=dict(class_of), rules=rules,
                diff_pairs=diff_pairs, limits=limits), layout


# ---------------------------------------------------------------- schematic
def _sheet_records(data):
    records, offset = [], 0
    while offset + 4 <= len(data):
        length, = struct.unpack_from('<I', data, offset)
        offset += 4
        binary = length >> 24
        length &= 0xFFFFFF
        chunk = data[offset:offset + length]
        offset += length
        records.append({} if binary else _pairs(chunk.rstrip(b'\0').decode('latin-1')))
    return records


def _coordinate(record, base):
    x = int(record.get(base + '.X', '0') or 0) + int(record.get(base + '.X_FRAC', '0') or 0) / 1e5
    y = int(record.get(base + '.Y', '0') or 0) + int(record.get(base + '.Y_FRAC', '0') or 0) / 1e5
    return (round(x, 3), round(y, 3))


def read_sheet(path):
    """Components, connectivity items and notes of one schematic sheet."""
    ole = olefile.OleFileIO(path)
    try:
        records = _sheet_records(ole.openstream('FileHeader').read())[1:]
    finally:
        ole.close()
    components, implementations = {}, {}
    for index, record in enumerate(records):
        kind = record.get('RECORD')
        if kind == '1':
            components[index] = dict(lib=_utf8(record, 'LIBREFERENCE'),
                                     description=_utf8(record, 'COMPONENTDESCRIPTION'),
                                     part=record.get('CURRENTPARTID', '1'),
                                     mode=record.get('DISPLAYMODE', '0'),
                                     pins=[], params={}, reference=None, footprint=None)
        elif kind == '44' and record.get('OWNERINDEX') is not None:
            implementations[index] = int(record['OWNERINDEX'])

    def owner_of(record):
        owner = record.get('OWNERINDEX')
        return int(owner) if owner is not None else None

    notes, document = [], {}
    wires, labels, power_ports, ports, junctions = [], [], [], [], []
    no_connects = 0
    for record in records:
        kind = record.get('RECORD')
        owner = owner_of(record)
        part = components.get(owner)
        if kind == '34' and part is not None:
            part['reference'] = _utf8(record, 'TEXT')
        elif kind == '41':
            if part is not None:
                part['params'][_utf8(record, 'NAME')] = _utf8(record, 'TEXT')
            elif owner is None:
                document[_utf8(record, 'NAME')] = _utf8(record, 'TEXT')
        elif kind == '45':
            target = components.get(implementations.get(owner))
            if target is not None and record.get('MODELTYPE') == 'PCBLIB' and (
                    record.get('ISCURRENT') == 'T' or target['footprint'] is None):
                target['footprint'] = _utf8(record, 'MODELNAME')
        elif kind == '2' and part is not None:
            if record.get('OWNERPARTID', '1') not in ('-1', part['part']):
                continue
            if record.get('OWNERPARTDISPLAYMODE', '0') != part['mode']:
                continue
            conglomerate = int(record.get('PINCONGLOMERATE', '0'))
            length = int(record.get('PINLENGTH', '0')) + int(record.get('PINLENGTH_FRAC', '0')) / 1e5
            x, y = _coordinate(record, 'LOCATION')
            end_x, end_y = [(x + length, y), (x, y + length), (x - length, y),
                            (x, y - length)][conglomerate & 3]
            electrical = int(record.get('ELECTRICAL', '0') or 0)
            part['pins'].append(dict(
                number=_utf8(record, 'DESIGNATOR'), name=_utf8(record, 'NAME'),
                type=PIN_TYPES[electrical] if electrical < len(PIN_TYPES) else None,
                point=(round(end_x, 3), round(end_y, 3)),
                hidden=bool(conglomerate & 4), hidden_net=_utf8(record, 'HIDDENNETNAME')))
        elif kind == '27':
            count = int(record['LOCATIONCOUNT'])
            wires.append([(round(int(record.get('X%d' % k, '0')) + int(record.get('X%d_FRAC' % k, '0')) / 1e5, 3),
                           round(int(record.get('Y%d' % k, '0')) + int(record.get('Y%d_FRAC' % k, '0')) / 1e5, 3))
                          for k in range(1, count + 1)])
        elif kind == '25':
            labels.append((_utf8(record, 'TEXT'), _coordinate(record, 'LOCATION')))
        elif kind == '17':
            power_ports.append((_utf8(record, 'TEXT'), _coordinate(record, 'LOCATION')))
        elif kind == '18':
            point = _coordinate(record, 'LOCATION')
            width = int(record.get('WIDTH', '0'))
            horizontal = int(record.get('STYLE', '0')) < 4
            ends = [point, (point[0] + width, point[1])] if horizontal else [point, (point[0], point[1] - width)]
            ports.append((_utf8(record, 'NAME'), ends))
        elif kind == '29':
            junctions.append(_coordinate(record, 'LOCATION'))
        elif kind == '22':
            no_connects += 1
        elif kind in ('4', '28', '209'):
            owner_kind = records[owner].get('RECORD') if owner is not None and owner < len(records) else None
            text = _utf8(record, 'TEXT').strip()
            if owner_kind not in ('1', '39') and text and not text.startswith('='):
                notes.append(text)
    return dict(components=components, wires=wires, labels=labels, power_ports=power_ports,
                ports=ports, junctions=junctions, notes=notes, document=document,
                no_connects=no_connects)


def _on_segment(point, start, end, tolerance=0.01):
    (x, y), (x1, y1), (x2, y2) = point, start, end
    cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
    if abs(cross) > tolerance * max(1, abs(x2 - x1) + abs(y2 - y1)):
        return False
    return (min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
            and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance)


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first, second):
        self.parent[self.find(first)] = self.find(second)


def _value(params):
    """Part value: the Value parameter, or the Comment with one level of =Name resolved."""
    comment = params.get('Comment', '')
    if comment.startswith('='):
        comment = params.get(comment[1:], '')
    return clean(params.get('Value')) or clean(comment)


def read_schematic(paths):
    """Flat netlist over all sheets.

    Power ports and ports connect across sheets by name, net labels within a
    sheet, hidden pins through their hidden net name.
    """
    groups = _UnionFind()
    components, sheets, notes = {}, [], []
    for path in paths:
        sheet = read_sheet(path)
        file_name = os.path.basename(path)
        sheet_path = '/%s/' % os.path.splitext(file_name)[0]
        segments = []
        for wire_index, wire in enumerate(sheet['wires']):
            for segment_index in range(len(wire) - 1):
                node = ('segment', path, wire_index, segment_index)
                segments.append((node, wire[segment_index], wire[segment_index + 1]))
                if segment_index:
                    groups.union(node, ('segment', path, wire_index, segment_index - 1))

        def attach(node, point):
            groups.find(node)
            groups.union(node, ('point', path, point))
            for segment, start, end in segments:
                if _on_segment(point, start, end):
                    groups.union(node, segment)
        for wire in sheet['wires']:
            for point in (wire[0], wire[-1]):
                attach(('point', path, point), point)
        for point in sheet['junctions']:
            attach(('point', path, point), point)
        for name, point in sheet['labels']:
            attach(('label', path, name, point), point)
            groups.union(('label', path, name, point), ('LOCAL', path, name))
        for name, point in sheet['power_ports']:
            attach(('power', path, name, point), point)
            groups.union(('power', path, name, point), ('GLOBAL', name))
        for name, ends in sheet['ports']:
            for end in ends:
                attach(('port', path, name, end), end)
                groups.union(('port', path, name, end), ('PORT', name))
        count = 0
        for part in sheet['components'].values():
            reference = part['reference']
            if not reference:
                continue
            if reference not in components:
                count += 1
                fields = {name: text for name, text in part['params'].items()
                          if name not in ('Comment', 'Value') and clean(text)}
                components[reference] = dict(
                    value=_value(part['params']), footprint=part['footprint'], symbol=part['lib'],
                    description=clean(part['description']),
                    datasheet=pick_field(fields, ('datasheet', 'componentlink1url', 'helpurl')),
                    mpn=pick_field(fields, MPN_FIELD_KEYS),
                    manufacturer=pick_field(fields, MANUFACTURER_FIELD_KEYS), fields=fields,
                    sheet=sheet_path, sheet_file=file_name, pin_info={})
            for pin in part['pins']:
                components[reference]['pin_info'][pin['number']] = dict(name=clean(pin['name']),
                                                                        type=pin['type'])
                node = ('pin', reference, pin['number'])
                if pin['hidden'] and pin['hidden_net']:
                    groups.union(node, ('GLOBAL', pin['hidden_net']))
                else:
                    attach(node, pin['point'])
        sheets.append(dict(path=sheet_path, file=file_name,
                           page=clean(sheet['document'].get('SheetNumber')), components=count,
                           no_connects=sheet['no_connects']))
        notes.extend(dict(sheet=sheet_path, text=text) for text in sheet['notes'])

    members = collections.defaultdict(list)
    for node in list(groups.parent):
        members[groups.find(node)].append(node)
    nets = {}
    for nodes in members.values():
        pins = ['%s.%s' % (node[1], node[2]) for node in nodes if node[0] == 'pin']
        if not pins:
            continue
        names = (sorted({node[1] for node in nodes if node[0] == 'GLOBAL'})
                 + sorted({node[1] for node in nodes if node[0] == 'PORT'})
                 + sorted({node[2] for node in nodes if node[0] == 'LOCAL'}))
        key = names[0] if names else 'unnamed-(%s)' % min(pins)
        for pin in pins:
            nets[pin] = key
    return dict(components=components, nets=nets, sheets=sheets, notes=notes)


# ---------------------------------------------------------------- project
def read_project(path):
    with open(path, encoding='latin-1') as handle:
        lines = handle.read().splitlines()
    documents = [line.split('=', 1)[1] for line in lines if line.startswith('DocumentPath=')]
    variants = collections.OrderedDict()
    in_variant, current = False, None
    for line in lines:
        if line.startswith('['):
            in_variant, current = line.startswith('[ProjectVariant'), None
            continue
        if not in_variant:
            continue
        if line.startswith('Description='):
            current = line.split('=', 1)[1]
            variants.setdefault(current, set())
        elif current and line.startswith('Variation') and not line.startswith('VariationCount'):
            record = dict(item.split('=', 1) for item in line.split('=', 1)[1].split('|') if '=' in item)
            if record.get('Kind') == '1' and record.get('Designator'):
                variants[current].add(record['Designator'])
    return dict(documents=documents, variants={name: sorted(refs) for name, refs in variants.items()})


def _compare(schematic, components):
    sch = schematic['components']
    only_sch = sorted(ref for ref in sch if ref not in components)
    only_board = sorted(ref for ref, part in components.items() if not part['in_schematic'])
    differences = []
    for ref in sorted(set(sch) & set(components)):
        footprint = sch[ref]['footprint']
        if footprint and components[ref]['footprint'] and footprint != components[ref]['footprint']:
            differences.append(dict(ref=ref, field='footprint', schematic=footprint,
                                    board=components[ref]['footprint']))
    board_net = {}
    for ref, part in components.items():
        for pin, data in part['pins'].items():
            board_net['%s.%s' % (ref, pin)] = data['net'] or 'unconnected-(%s.%s)' % (ref, pin)
    to_schematic, to_board, missing = collections.defaultdict(set), collections.defaultdict(set), []
    for pin, key in schematic['nets'].items():
        if pin not in board_net:
            if pin.split('.', 1)[0] in components:
                missing.append(pin)
            continue
        to_schematic[board_net[pin]].add(key)
        to_board[key].add(board_net[pin])
    net_differences = []
    for net, keys in sorted(to_schematic.items()):
        if len(keys) > 1:
            net_differences.append(dict(board_net=net, issue='several schematic nets',
                                        schematic_nets=sorted(keys)[:10]))
    for key, found in sorted(to_board.items()):
        if len(found) > 1:
            net_differences.append(dict(schematic_net=key, issue='several board nets',
                                        board_nets=sorted(found)[:10]))
    return dict(only_in_schematic=only_sch, only_in_board=only_board, differences=differences,
                net_differences=net_differences, pins_without_pad=sorted(missing))


def read(project_path):
    """Read an Altium project. project_path is a .PrjPcb, .PcbDoc or .SchDoc file."""
    if olefile is None:
        raise RuntimeError('Altium files need the olefile package: pip install olefile')
    folder = os.path.dirname(os.path.abspath(project_path))
    name = os.path.splitext(os.path.basename(project_path))[0]
    limits, project = [], None
    lower = project_path.lower()
    if lower.endswith('.prjpcb'):
        project = read_project(project_path)
        documents = [os.path.join(folder, doc.replace('\\', os.sep)) for doc in project['documents']]
        absent = [os.path.basename(doc) for doc in documents
                  if doc.lower().endswith(('.schdoc', '.pcbdoc')) and not os.path.exists(doc)]
        if absent:
            limits.append('Project documents not found: ' + ', '.join(absent))
        documents = [doc for doc in documents if os.path.exists(doc)]
    else:
        documents = [project_path]
    sheet_paths = [doc for doc in documents if doc.lower().endswith('.schdoc')]
    board_paths = [doc for doc in documents if doc.lower().endswith('.pcbdoc')]
    board_paths.sort(key=lambda doc: 'panel' in os.path.basename(doc).lower())
    if len(board_paths) > 1:
        limits.append('Several board files; read %s, skipped %s.' % (
            os.path.basename(board_paths[0]), ', '.join(os.path.basename(p) for p in board_paths[1:])))

    schematic = read_schematic(sheet_paths) if sheet_paths else None
    board, layout = read_board(board_paths[0]) if board_paths else (None, None)
    if schematic is None:
        limits.append('No schematic sheets: part values and parameters are missing.')
    if board is None:
        limits.append('No board file: placement, stackup, routing and zones are missing.')
    else:
        limits.extend(board['limits'])
        if board['summary']['duplicate_references']:
            limits.append('Duplicate references on the board (first one kept): '
                          + ', '.join(board['summary']['duplicate_references']))

    components, netlist_source = {}, None
    if board is not None:
        netlist_source = 'board'
        for ref, part in board['components'].items():
            components[ref] = dict(part, in_schematic=False, on_board=True)
    if schematic is not None:
        for ref, part in schematic['components'].items():
            pin_info = part['pin_info']
            data = {key: found for key, found in part.items() if key != 'pin_info'}
            if ref in components:
                entry = components[ref]
                entry['in_schematic'] = True
                for key in ('value', 'symbol', 'mpn', 'manufacturer', 'datasheet', 'fields',
                            'sheet', 'sheet_file'):
                    entry[key] = data[key]
                entry['description'] = entry['description'] or data['description']
            else:
                entry = components[ref] = dict(
                    data, in_schematic=True, on_board=False, pins={}, side=None, x=None, y=None,
                    rotation=None, mount=None, dnp=False, in_bom=True, in_position_file=False,
                    board_only=False)
            for pin, info in pin_info.items():
                if board is None:
                    entry['pins'][pin] = dict(net=schematic['nets'].get('%s.%s' % (ref, pin)), **info)
                elif pin in entry['pins']:
                    entry['pins'][pin].update(info)
        if board is None:
            netlist_source = 'schematic'
            limits.append('Netlist traced from the schematic wires; hierarchical and multi-channel '
                          'designs are treated as flat.')

    variants = project['variants'] if project else {}
    for ref, part in components.items():
        part['not_fitted_in'] = [variant for variant, refs in variants.items() if ref in refs]
        part['dnp'] = bool(variants) and len(part['not_fitted_in']) == len(variants)

    class_of = board['class_of'] if board else {}
    nets = attach_nets(components, board['routing'] if board else {},
                       lambda net: class_of.get(net, []) if board else None)
    design = dict(
        schema=SCHEMA,
        source=dict(tool='altium', project=name,
                    files=[os.path.basename(doc) for doc in ([project_path] if project else [])
                           + sheet_paths + board_paths[:1]],
                    schematic_format=None,
                    board_format=board['summary']['format_version'] if board else None,
                    generator_version=None, netlist_from=netlist_source),
        title_block=dict(title=name, revision=None, date=None, company=None, comments=[]),
        sheets=schematic['sheets'] if schematic else [],
        board=board['summary'] if board else None,
        stackup=board['stackup'] if board else None,
        net_classes=board['net_classes'] if board else {},
        rules=dict(altium_rules=board['rules']) if board else {},
        components=components,
        nets=nets,
        zones=board['zones'] if board else [],
        diff_pairs=board['diff_pairs'] if board else [],
        variants=variants,
        notes=schematic['notes'] if schematic else [],
        checks=dict(schematic_vs_board=_compare(schematic, components) if schematic and board else None),
        limits=limits,
    )
    return design, layout
