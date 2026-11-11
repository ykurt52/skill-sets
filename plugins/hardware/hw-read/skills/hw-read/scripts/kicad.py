"""KiCad project reader.

Reads .kicad_pro, .kicad_sch and .kicad_pcb directly. Written against the
KiCad 10 file format and also accepts KiCad 9 boards (numbered net table).
Lengths are in mm, angles in degrees, coordinates as stored in the file.
"""
import fnmatch
import glob
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

import sexpr
from common import (MANUFACTURER_FIELD_KEYS, MPN_FIELD_KEYS, SCHEMA, arc_length, attach_nets, bbox,
                    clean, pick_field, polygon_area, rounded, routing_summary)
from sexpr import child, children, number, value

STANDARD_FIELDS = ('Reference', 'Value', 'Footprint', 'Datasheet', 'Description')
OUTLINE_LAYER = 'Edge.Cuts'


def _point(node, tag):
    found = child(node, tag)
    if found is None or len(found) < 3:
        return None
    return (float(found[1]), float(found[2]))


def _flag(node, tag):
    """Read (tag yes|no); a bare tag atom also counts as yes."""
    found = child(node, tag)
    if found is not None:
        return len(found) < 2 or found[1] == 'yes'
    return tag in node


def _points(node):
    pts = child(node, 'pts')
    if pts is None:
        return []
    return [(float(xy[1]), float(xy[2])) for xy in children(pts, 'xy')]


# ---------------------------------------------------------------- board
class _NetNames:
    """Resolve the net of a board item for both numbered and named formats."""

    def __init__(self, board):
        self.table = {net[1]: net[2] for net in children(board, 'net') if len(net) >= 3}

    def of(self, node):
        net = child(node, 'net')
        if net is None or len(net) < 2:
            return None
        if len(net) >= 3:
            name = net[2]
        elif self.table:
            name = self.table.get(net[1], '')
        else:
            name = net[1]
        return name or None


def _stackup(board):
    setup = child(board, 'setup')
    stack = child(setup, 'stackup') if setup else None
    if stack is None:
        return None
    layers = []
    for layer in children(stack, 'layer'):
        entry = dict(name=layer[1], type=value(layer, 'type'))
        for key in ('thickness', 'epsilon_r', 'loss_tangent'):
            found = number(layer, key)
            if found is not None:
                entry[key] = found
        for key in ('material', 'color'):
            found = value(layer, key)
            if found is not None:
                entry[key] = found
        layers.append(entry)
    return dict(layers=layers, copper_finish=value(stack, 'copper_finish'),
                dielectric_constraints=value(stack, 'dielectric_constraints'),
                edge_connector=value(stack, 'edge_connector'),
                thickness_sum=rounded(sum(l.get('thickness', 0) for l in layers)))


def _copper_layers(board):
    result = []
    for layer in child(board, 'layers')[1:]:
        if isinstance(layer, list) and len(layer) >= 3 and layer[1].endswith('.Cu'):
            entry = dict(name=layer[1], type=layer[2])
            if len(layer) > 3:
                entry['user_name'] = layer[3]
            result.append(entry)
    return result


def _drill(pad):
    drill = child(pad, 'drill')
    if drill is None:
        return None
    sizes = [float(item) for item in drill[1:] if isinstance(item, str) and item != 'oval']
    if not sizes:
        return None
    return dict(shape='oval' if 'oval' in drill else 'round', size=sizes[:2])


def _footprint(footprint, nets):
    props = {p[1]: p[2] for p in children(footprint, 'property') if len(p) > 2 and isinstance(p[2], str)}
    reference = props.get('Reference')
    part_value = props.get('Value')
    for text in children(footprint, 'fp_text'):
        if len(text) > 2 and text[1] == 'reference' and reference is None:
            reference = text[2]
        elif len(text) > 2 and text[1] == 'value' and part_value is None:
            part_value = text[2]
    at = child(footprint, 'at')
    x, y = float(at[1]), float(at[2])
    rotation = float(at[3]) if len(at) > 3 else 0.0
    layer = value(footprint, 'layer')
    flags = set((child(footprint, 'attr') or ['attr'])[1:])
    fields = {name: text for name, text in props.items()
              if name not in STANDARD_FIELDS and clean(text)}
    component = dict(
        value=part_value,
        footprint=footprint[1] if len(footprint) > 1 and isinstance(footprint[1], str) else None,
        description=clean(props.get('Description')) or clean(value(footprint, 'descr')),
        mpn=pick_field(fields, MPN_FIELD_KEYS),
        manufacturer=pick_field(fields, MANUFACTURER_FIELD_KEYS),
        datasheet=clean(props.get('Datasheet')),
        fields=fields,
        sheet=value(footprint, 'sheetname'),
        sheet_file=value(footprint, 'sheetfile'),
        side='bottom' if layer == 'B.Cu' else 'top',
        x=rounded(x), y=rounded(y), rotation=rotation,
        mount='smd' if 'smd' in flags else 'tht' if 'through_hole' in flags else 'other',
        dnp='dnp' in flags,
        in_bom='exclude_from_bom' not in flags,
        in_position_file='exclude_from_pos_files' not in flags,
        board_only='board_only' in flags,
        pins={},
    )
    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    pads = []
    for pad in children(footprint, 'pad'):
        pad_at = child(pad, 'at')
        px, py = float(pad_at[1]), float(pad_at[2])
        size = child(pad, 'size')
        net = nets.of(pad)
        pad_number = pad[1] if len(pad) > 1 and isinstance(pad[1], str) else ''
        pads.append(dict(
            ref=reference, pad=pad_number, net=net,
            x=rounded(x + px * cos_a + py * sin_a), y=rounded(y - px * sin_a + py * cos_a),
            rotation=float(pad_at[3]) if len(pad_at) > 3 else 0.0,
            type=pad[2] if len(pad) > 2 else None, shape=pad[3] if len(pad) > 3 else None,
            size=[float(size[1]), float(size[2])] if size else None,
            drill=_drill(pad),
            layers=[item for item in (child(pad, 'layers') or [])[1:] if isinstance(item, str)],
        ))
        if pad_number:
            pin = component['pins'].setdefault(pad_number, dict(net=None, name=None, type=None))
            pin['net'] = pin['net'] or net
            pin['name'] = pin['name'] or value(pad, 'pinfunction')
            pin['type'] = pin['type'] or value(pad, 'pintype')
    return reference, component, pads


def _zone(zone, nets):
    layers = [item for item in (child(zone, 'layers') or [])[1:] if isinstance(item, str)]
    single = value(zone, 'layer')
    if single:
        layers.append(single)
    connect = child(zone, 'connect_pads') or []
    mode = next((item for item in connect[1:] if isinstance(item, str)), 'thermal')
    fill = child(zone, 'fill') or []
    outline = _points(child(zone, 'polygon') or [])
    filled = {}
    for polygon in children(zone, 'filled_polygon'):
        layer = value(polygon, 'layer')
        filled[layer] = filled.get(layer, 0.0) + polygon_area(_points(polygon))
    entry = dict(
        net=value(zone, 'net_name') or nets.of(zone),
        name=clean(value(zone, 'name')),
        layers=layers,
        priority=int(value(zone, 'priority', '0')),
        pad_connection={'yes': 'solid', 'no': 'none'}.get(mode, mode),
        clearance=number(connect, 'clearance') if connect else None,
        min_thickness=number(zone, 'min_thickness'),
        thermal_gap=number(fill, 'thermal_gap') if fill else None,
        thermal_bridge_width=number(fill, 'thermal_bridge_width') if fill else None,
        filled_area_mm2={layer: rounded(area, 2) for layer, area in filled.items()},
    )
    keepout = child(zone, 'keepout')
    if keepout is not None:
        entry['keepout'] = {item[0]: item[1] for item in keepout[1:]
                            if isinstance(item, list) and len(item) > 1}
    if outline:
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        entry['bbox'] = [rounded(min(xs)), rounded(min(ys)), rounded(max(xs)), rounded(max(ys))]
    return entry, outline


def _outline_shapes(node, transform):
    """Graphic items on the board outline layer as point lists."""
    shapes = []
    for item in node:
        if not isinstance(item, list) or not item or value(item, 'layer') != OUTLINE_LAYER:
            continue
        kind = item[0].split('_', 1)[-1]
        if kind == 'line':
            pts = [_point(item, 'start'), _point(item, 'end')]
        elif kind == 'rect':
            start, end = _point(item, 'start'), _point(item, 'end')
            pts = [start, (end[0], start[1]), end, (start[0], end[1])]
        elif kind == 'arc':
            pts = [_point(item, 'start'), _point(item, 'mid'), _point(item, 'end')]
        elif kind == 'circle':
            center, edge = _point(item, 'center'), _point(item, 'end')
            radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            pts = [(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)]
        elif kind in ('poly', 'curve'):
            pts = _points(item)
        else:
            continue
        pts = [transform(p) for p in pts if p is not None]
        if pts:
            shapes.append(dict(kind=kind, points=[[rounded(p[0]), rounded(p[1])] for p in pts]))
    return shapes


def read_board(path):
    """Return (board dict for design.json, layout dict with raw geometry)."""
    board = sexpr.load(path)
    nets = _NetNames(board)
    general = child(board, 'general') or []
    copper = _copper_layers(board)

    components, pads = {}, []
    outline = _outline_shapes(board, lambda p: p)
    duplicates = []
    for footprint in children(board, 'footprint'):
        reference, component, footprint_pads = _footprint(footprint, nets)
        pads.extend(footprint_pads)
        at = child(footprint, 'at')
        fx, fy = float(at[1]), float(at[2])
        angle = math.radians(float(at[3]) if len(at) > 3 else 0.0)
        outline.extend(_outline_shapes(footprint, lambda p: (
            fx + p[0] * math.cos(angle) + p[1] * math.sin(angle),
            fy - p[0] * math.sin(angle) + p[1] * math.cos(angle))))
        if reference is None:
            continue
        if reference in components:
            if not component['board_only']:
                duplicates.append(reference)
            continue
        components[reference] = component

    tracks = []
    for segment in children(board, 'segment'):
        start, end = _point(segment, 'start'), _point(segment, 'end')
        tracks.append(dict(net=nets.of(segment), layer=value(segment, 'layer'),
                           width=number(segment, 'width'), start=list(start), end=list(end),
                           length=rounded(math.hypot(end[0] - start[0], end[1] - start[1]))))
    for arc in children(board, 'arc'):
        start, mid, end = _point(arc, 'start'), _point(arc, 'mid'), _point(arc, 'end')
        tracks.append(dict(net=nets.of(arc), layer=value(arc, 'layer'), width=number(arc, 'width'),
                           start=list(start), mid=list(mid), end=list(end),
                           length=rounded(arc_length(start, mid, end))))

    vias = []
    for via in children(board, 'via'):
        at = _point(via, 'at')
        kind = next((item for item in via[1:] if item in ('blind', 'micro', 'buried')), 'through')
        entry = dict(net=nets.of(via), x=at[0], y=at[1], type=kind, size=number(via, 'size'),
                     drill=number(via, 'drill'),
                     layers=[item for item in child(via, 'layers')[1:] if isinstance(item, str)])
        if _flag(via, 'free'):
            entry['free'] = True
        for key in ('capping', 'filling'):
            found = value(via, key)
            if found is not None and found != 'no':
                entry[key] = found
        for key in ('covering', 'plugging', 'tenting'):
            found = child(via, key)
            if found is not None:
                sides = [item[0] for item in found[1:] if isinstance(item, list)
                         and len(item) > 1 and item[1] == 'yes']
                sides += [item for item in found[1:] if isinstance(item, str) and item != 'none']
                if sides:
                    entry[key] = sides
        vias.append(entry)

    zones, zone_outlines = [], []
    for zone in children(board, 'zone'):
        entry, points = _zone(zone, nets)
        zones.append(entry)
        zone_outlines.append([[rounded(p[0]), rounded(p[1])] for p in points])

    routing = routing_summary(tracks, vias, zones, [layer['name'] for layer in copper])

    box = bbox([p for shape in outline for p in shape['points']])
    setup = child(board, 'setup') or []
    summary = dict(
        file=os.path.basename(path),
        format_version=value(board, 'version'),
        generator_version=value(board, 'generator_version'),
        thickness=number(general, 'thickness'),
        copper_layers=copper,
        outline_bbox=box,
        size=[rounded(box[2] - box[0]), rounded(box[3] - box[1])] if box else None,
        y_axis='down',
        aux_origin=list(_point(setup, 'aux_axis_origin')) if _point(setup, 'aux_axis_origin') else None,
        counts=dict(footprints=len(components), pads=len(pads), tracks=len(tracks), vias=len(vias),
                    zones=len(zones)),
        duplicate_references=sorted(set(duplicates)),
    )
    layout = dict(pads=pads, tracks=tracks, vias=vias, zone_outlines=zone_outlines, outline=outline)
    return dict(summary=summary, stackup=_stackup(board), components=components, zones=zones,
                routing=routing), layout


# ---------------------------------------------------------------- schematic
def _sheet_property(sheet, names):
    for prop in children(sheet, 'property'):
        if len(prop) > 2 and prop[1] in names:
            return prop[2]
    return None


def read_schematic(root_path, project_name):
    """Walk the sheet hierarchy and return components keyed by reference."""
    cache, sheets, components, notes, missing = {}, [], {}, [], []
    no_instance = []

    def load(path):
        if path not in cache:
            cache[path] = sexpr.load(path) if os.path.exists(path) else None
        return cache[path]

    def read_symbols(tree, file_name, uuid_path, name_path):
        count = 0
        for symbol in children(tree, 'symbol'):
            props = {p[1]: p[2] for p in children(symbol, 'property')
                     if len(p) > 2 and isinstance(p[2], str)}
            reference, unit = None, value(symbol, 'unit', '1')
            instances = child(symbol, 'instances')
            for project in children(instances, 'project') if instances else []:
                for instance in children(project, 'path'):
                    if instance[1] == uuid_path and (reference is None or project[1] == project_name):
                        reference = value(instance, 'reference')
                        unit = value(instance, 'unit', unit)
            if reference is None:
                reference = props.get('Reference')
                no_instance.append(reference)
            if not reference or reference.startswith('#'):
                continue
            count += 1
            fields = {name: text for name, text in props.items()
                      if name not in STANDARD_FIELDS and clean(text)}
            entry = components.setdefault(reference, dict(
                value=props.get('Value'), footprint=clean(props.get('Footprint')),
                symbol=value(symbol, 'lib_id'),
                description=clean(props.get('Description')), datasheet=clean(props.get('Datasheet')),
                mpn=pick_field(fields, MPN_FIELD_KEYS),
                manufacturer=pick_field(fields, MANUFACTURER_FIELD_KEYS), fields=fields,
                sheet=name_path, sheet_file=file_name,
                dnp=_flag(symbol, 'dnp'), in_bom=value(symbol, 'in_bom', 'yes') == 'yes',
                on_board=value(symbol, 'on_board', 'yes') == 'yes', units=[]))
            entry['units'].append(unit)
        return count

    def walk(path, uuid_path, name_path, page, trail):
        tree = load(path)
        file_name = os.path.basename(path)
        if tree is None:
            missing.append(file_name)
            return
        sheet = dict(path=name_path, file=file_name, page=page,
                     components=read_symbols(tree, file_name, uuid_path, name_path),
                     no_connects=len(children(tree, 'no_connect')))
        sheets.append(sheet)
        for text in children(tree, 'text'):
            if len(text) > 1 and isinstance(text[1], str) and text[1].strip():
                notes.append(dict(sheet=name_path, text=text[1].strip()))
        for sub in children(tree, 'sheet'):
            sub_file = _sheet_property(sub, ('Sheetfile', 'Sheet file'))
            sub_name = _sheet_property(sub, ('Sheetname', 'Sheet name')) or sub_file
            sub_uuid = uuid_path + '/' + value(sub, 'uuid', '')
            sub_page = None
            instances = child(sub, 'instances')
            for project in children(instances, 'project') if instances else []:
                for instance in children(project, 'path'):
                    if instance[1] == uuid_path:
                        sub_page = value(instance, 'page')
            sub_path = os.path.normpath(os.path.join(os.path.dirname(path), sub_file or ''))
            if sub_file is None or sub_path in trail:
                continue
            walk(sub_path, sub_uuid, name_path + sub_name + '/', sub_page, trail + [sub_path])

    root = load(root_path)
    root_page = None
    for instance in children(child(root, 'sheet_instances') or [], 'path'):
        if instance[1] == '/':
            root_page = value(instance, 'page')
    walk(root_path, '/' + value(root, 'uuid', ''), '/', root_page, [os.path.normpath(root_path)])

    title = child(root, 'title_block') or []
    title_block = dict(title=value(title, 'title'), revision=value(title, 'rev'),
                       date=value(title, 'date'), company=value(title, 'company'),
                       comments=[c[2] for c in children(title, 'comment') if len(c) > 2])
    return dict(file=os.path.basename(root_path), format_version=value(root, 'version'),
                generator_version=value(root, 'generator_version'), title_block=title_block,
                sheets=sheets, components=components, notes=notes, missing_sheets=missing,
                symbols_without_instance=sorted({ref for ref in no_instance
                                                 if ref and not ref.startswith('#')}))


# ---------------------------------------------------------------- netlist from kicad-cli
def find_kicad_cli():
    found = shutil.which('kicad-cli')
    if found:
        return found
    candidates = glob.glob(os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                                        'KiCad', '*', 'bin', 'kicad-cli.exe'))
    candidates += glob.glob('/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')

    def version_key(path):
        match = re.search(r'KiCad[\\/](\d+)\.(\d+)', path)
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    return max(candidates, key=version_key) if candidates else None


def export_netlist(root_schematic, kicad_cli=None):
    """Return {net: [(ref, pin, pin name, pin type)]} from kicad-cli, or None when not available."""
    kicad_cli = kicad_cli or find_kicad_cli()
    if kicad_cli is None:
        return None
    folder = tempfile.mkdtemp(prefix='hwread_')
    target = os.path.join(folder, 'netlist.net')
    try:
        result = subprocess.run([kicad_cli, 'sch', 'export', 'netlist', '--format', 'kicadsexpr',
                                 '--output', target, root_schematic], capture_output=True)
        if result.returncode != 0 or not os.path.exists(target):
            return None
        tree = sexpr.load(target)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    nets = {}
    for net in children(child(tree, 'nets') or [], 'net'):
        nets[value(net, 'name')] = [(value(node, 'ref'), value(node, 'pin'),
                                     value(node, 'pinfunction'), value(node, 'pintype'))
                                    for node in children(net, 'node')]
    return nets


# ---------------------------------------------------------------- project settings
def read_project(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    settings = data.get('net_settings', {})
    classes = {}
    for entry in settings.get('classes', []):
        classes[entry.get('name')] = {key: entry.get(key) for key in (
            'clearance', 'track_width', 'via_diameter', 'via_drill', 'microvia_diameter',
            'microvia_drill', 'diff_pair_width', 'diff_pair_gap', 'diff_pair_via_gap', 'priority')
            if entry.get(key) is not None}
    patterns = [(p.get('pattern', ''), p.get('netclass')) for p in settings.get('netclass_patterns') or []]
    assignments = settings.get('netclass_assignments') or {}
    design = data.get('board', {}).get('design_settings', {})
    return dict(classes=classes, patterns=patterns, assignments=assignments,
                rules=design.get('rules', {}),
                track_widths=design.get('track_widths', []),
                via_dimensions=design.get('via_dimensions', []),
                diff_pair_dimensions=design.get('diff_pair_dimensions', []),
                text_variables=data.get('text_variables', {}))


def _net_classes(net, project):
    """Net classes of a net: explicit assignment first, then patterns in file order."""
    found = []
    assigned = project['assignments'].get(net)
    if assigned:
        found.extend(assigned if isinstance(assigned, list) else [assigned])
    for pattern, net_class in project['patterns']:
        if net_class in found:
            continue
        matched = pattern == net or fnmatch.fnmatchcase(net, pattern)
        if not matched:
            try:
                matched = re.fullmatch(pattern, net) is not None
            except re.error:
                matched = False
        if matched:
            found.append(net_class)
    return found or ['Default']


# ---------------------------------------------------------------- merge
def _compare(schematic, board):
    sch, pcb = schematic['components'], board['components']
    only_sch = sorted(ref for ref, part in sch.items() if ref not in pcb and part['on_board'])
    only_pcb = sorted(ref for ref, part in pcb.items() if ref not in sch and not part['board_only'])
    differences = []
    for ref in sorted(set(sch) & set(pcb)):
        for key in ('value', 'footprint', 'dnp'):
            if sch[ref][key] != pcb[ref][key] and not (key == 'footprint' and sch[ref][key] is None):
                differences.append(dict(ref=ref, field=key, schematic=sch[ref][key], board=pcb[ref][key]))
    return dict(only_in_schematic=only_sch, only_in_board=only_pcb, differences=differences)


def read(project_path, use_kicad_cli=True):
    """Read a KiCad project. project_path is the .kicad_pro, .kicad_sch or .kicad_pcb file."""
    base = os.path.splitext(project_path)[0]
    pro_path, sch_path, pcb_path = base + '.kicad_pro', base + '.kicad_sch', base + '.kicad_pcb'
    name = os.path.basename(base)
    limits = []

    project = read_project(pro_path) if os.path.exists(pro_path) else None
    if project is None:
        limits.append('No .kicad_pro file: net classes and design rules were not read.')
    schematic = read_schematic(sch_path, name) if os.path.exists(sch_path) else None
    if schematic is None:
        limits.append('No root schematic: component data comes from the board only.')
    elif schematic['missing_sheets']:
        limits.append('Sheet files not found: ' + ', '.join(schematic['missing_sheets']))
    board, layout = read_board(pcb_path) if os.path.exists(pcb_path) else (None, None)

    components, netlist_source = {}, None
    if board is not None:
        netlist_source = 'board'
        for ref, part in board['components'].items():
            components[ref] = dict(part, in_schematic=False, on_board=True)
        if board['summary']['duplicate_references']:
            limits.append('Duplicate references on the board (first one kept): '
                          + ', '.join(board['summary']['duplicate_references']))
    if schematic is not None:
        for ref, part in schematic['components'].items():
            if ref in components:
                entry = components[ref]
                entry['in_schematic'] = True
                entry['symbol'] = part['symbol']
                entry['units'] = part['units']
                for key in ('mpn', 'manufacturer', 'datasheet', 'description'):
                    entry[key] = entry[key] or part[key]
                for field, text in part['fields'].items():
                    entry['fields'].setdefault(field, text)
            else:
                components[ref] = dict(part, in_schematic=True, on_board=False, pins={},
                                       in_position_file=False, board_only=False)
    if board is None and schematic is not None:
        limits.append('No board file: placement, stackup, routing and zones are missing.')
        exported = export_netlist(sch_path) if use_kicad_cli else None
        if exported is None:
            limits.append('Netlist not available: without a board file it needs kicad-cli.')
        else:
            netlist_source = 'kicad-cli'
            for net, nodes in exported.items():
                for ref, pin, pin_name, pin_type in nodes:
                    if ref in components:
                        components[ref]['pins'][pin] = dict(net=net, name=clean(pin_name), type=pin_type)

    nets = attach_nets(components, board['routing'] if board else {},
                       lambda net: _net_classes(net, project) if project else None)

    design = dict(
        schema=SCHEMA,
        source=dict(tool='kicad', project=name,
                    files=[os.path.basename(p) for p in (pro_path, sch_path, pcb_path) if os.path.exists(p)],
                    schematic_format=schematic['format_version'] if schematic else None,
                    board_format=board['summary']['format_version'] if board else None,
                    generator_version=(board['summary']['generator_version'] if board
                                       else schematic['generator_version'] if schematic else None),
                    netlist_from=netlist_source),
        title_block=schematic['title_block'] if schematic else None,
        sheets=schematic['sheets'] if schematic else [],
        board=board['summary'] if board else None,
        stackup=board['stackup'] if board else None,
        net_classes=project['classes'] if project else {},
        rules=dict(project['rules'], track_widths=project['track_widths'],
                   via_dimensions=project['via_dimensions'],
                   diff_pair_dimensions=project['diff_pair_dimensions']) if project else {},
        components=components,
        nets=nets,
        zones=board['zones'] if board else [],
        notes=schematic['notes'] if schematic else [],
        checks=dict(schematic_vs_board=_compare(schematic, board) if schematic and board else None),
        limits=limits,
    )
    custom_rules = base + '.kicad_dru'
    if os.path.exists(custom_rules):
        with open(custom_rules, encoding='utf-8') as handle:
            design['rules']['custom_rules_text'] = handle.read()
    if schematic and schematic['symbols_without_instance']:
        limits.append('Symbols without instance data (reference taken from the symbol itself): '
                      + ', '.join(schematic['symbols_without_instance'][:20]))
    return design, layout
