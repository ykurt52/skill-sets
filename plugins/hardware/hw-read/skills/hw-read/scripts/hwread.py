#!/usr/bin/env python
"""Read a hardware design project and write it as JSON.

Usage:
  python hwread.py PATH [--out DIR] [--no-layout] [--no-kicad-cli]

PATH is a project file or a folder that holds one. Output goes to
<project folder>/.hwread/ unless --out is given:
  design.json   components, pins, nets, net classes, rules, stackup, zones
  layout.json   pads, tracks, vias, zone outlines, board outline
  summary.md    short overview, also printed to stdout
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import altium  # noqa: E402
import kicad  # noqa: E402

KICAD_SUFFIXES = ('.kicad_pro', '.kicad_pcb', '.kicad_sch')
ALTIUM_SUFFIXES = ('.prjpcb', '.pcbdoc', '.schdoc')
ALTIUM_PROJECT_SUFFIX = '.prjpcb'
SKIP_FOLDERS = ('history', '__previews', 'project logs', 'project outputs')
SEARCH_DEPTH = 3


def find_projects(path):
    """Return project files below path as (kind, file) pairs."""
    if os.path.isfile(path):
        lower = path.lower()
        if lower.endswith(KICAD_SUFFIXES):
            return [('kicad', path)]
        if lower.endswith(ALTIUM_SUFFIXES):
            return [('altium', path)]
        return []
    found = []
    root_depth = os.path.abspath(path).count(os.sep)
    for folder, subfolders, files in os.walk(path):
        subfolders[:] = [name for name in subfolders if not name.startswith('.')
                         and not name.lower().startswith(SKIP_FOLDERS)
                         and not name.endswith('-backups')]
        if os.path.abspath(folder).count(os.sep) - root_depth >= SEARCH_DEPTH:
            subfolders[:] = []
        names = sorted(files)
        projects = [name for name in names if name.lower().endswith('.kicad_pro')]
        if not projects:
            # a board or schematic without a project file still counts
            stems = {os.path.splitext(name)[0] for name in names if name.lower().endswith('.kicad_pcb')}
            projects = [stem + '.kicad_pcb' for stem in sorted(stems)]
        found += [('kicad', os.path.join(folder, name)) for name in projects]
        found += [('altium', os.path.join(folder, name)) for name in names
                  if name.lower().endswith(ALTIUM_PROJECT_SUFFIX)]
    return found


def _prefix(reference):
    match = re.match(r'[A-Za-z_]+', reference)
    return match.group(0) if match else '?'


def _natural(reference):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', reference)]


def summary(design):
    lines = []
    out = lines.append
    source, title, board = design['source'], design['title_block'] or {}, design['board']
    components, nets = design['components'], design['nets']
    out('# %s' % (title.get('title') or source['project']))
    out('')
    tool = ' '.join(item for item in (source['tool'], source['generator_version']) if item)
    out('- project: `%s` (%s), revision %s, date %s' % (
        source['project'], tool, title.get('revision') or '?', title.get('date') or '?'))
    out('- files: ' + ', '.join(source['files']))
    out('- netlist taken from: %s' % (source['netlist_from'] or 'not available'))
    if board:
        size = board['size']
        out('- board: %s, %d copper layers, %.2f mm thick' % (
            '%.2f x %.2f mm' % tuple(size) if size else 'outline not found',
            len(board['copper_layers']), board['thickness'] or 0))
        out('- board items: %(footprints)d footprints, %(pads)d pads, %(tracks)d track segments, '
            '%(vias)d vias, %(zones)d zones' % board['counts'])
    fitted = [ref for ref, part in components.items() if not part['dnp']]
    variants = design.get('variants') or {}
    out('- components: %d (%d not fitted), nets: %d' % (
        len(components), len(components) - len(fitted), len(nets)))
    for variant, refs in variants.items():
        out('- variant `%s`: %d not fitted' % (variant, len(refs)))

    if design['stackup']:
        out('')
        out('## Stackup')
        out('')
        out('| Layer | Type | Thickness mm | Material | Er |')
        out('| --- | --- | --- | --- | --- |')
        names = {layer['name']: layer.get('user_name') for layer in board['copper_layers']}
        for layer in design['stackup']['layers']:
            if 'thickness' not in layer:
                continue
            label = layer['name'] + (' (%s)' % names[layer['name']] if names.get(layer['name']) else '')
            out('| %s | %s | %s | %s | %s |' % (label, layer['type'], layer['thickness'],
                                                layer.get('material', ''), layer.get('epsilon_r', '')))

    if design['sheets']:
        out('')
        out('## Sheets')
        out('')
        out('| Page | Sheet | File | Components |')
        out('| --- | --- | --- | --- |')
        for sheet in sorted(design['sheets'], key=lambda s: _natural(s['page'] or '')):
            out('| %s | %s | %s | %d |' % (sheet['page'] or '', sheet['path'], sheet['file'],
                                           sheet['components']))

    out('')
    out('## Components')
    out('')
    by_prefix = collections.Counter(_prefix(ref) for ref in components)
    out('- by reference prefix: ' + ', '.join('%s %d' % item for item in sorted(by_prefix.items())))
    out('')
    out('Parts with 5 or more pins:')
    out('')
    out('| Ref | Value | MPN | Pins | Sheet |')
    out('| --- | --- | --- | --- | --- |')
    for ref in sorted(components, key=_natural):
        part = components[ref]
        if len(part['pins']) >= 5:
            out('| %s | %s | %s | %d | %s |' % (ref, part['value'] or '', part['mpn'] or '',
                                                len(part['pins']), part['sheet'] or ''))

    out('')
    out('## Nets')
    out('')
    class_count = collections.Counter(net_class for net in nets.values()
                                      for net_class in net.get('classes', []))
    if design['net_classes']:
        out('| Net class | Nets | Clearance | Track | Diff pair width / gap |')
        out('| --- | --- | --- | --- | --- |')
        for name, rule in design['net_classes'].items():
            out('| %s | %d | %s | %s | %s / %s |' % (
                name, class_count.get(name, 0), rule.get('clearance', ''), rule.get('track_width', ''),
                rule.get('diff_pair_width', ''), rule.get('diff_pair_gap', '')))
        out('')
    out('Nets with the most pins:')
    out('')
    out('| Net | Pins | Track mm | Vias | Zone layers |')
    out('| --- | --- | --- | --- | --- |')
    for name, net in sorted(nets.items(), key=lambda item: -len(item[1]['pins']))[:15]:
        out('| %s | %d | %s | %s | %s |' % (name, len(net['pins']), net.get('track_length', ''),
                                            net.get('vias', ''), ' '.join(net.get('zone_layers', []))))

    checks = (design['checks'] or {}).get('schematic_vs_board')
    if checks:
        out('')
        out('## Schematic against board')
        out('')
        if not any(checks.get(key) for key in ('only_in_schematic', 'only_in_board', 'differences',
                                                'net_differences', 'pins_without_pad')):
            out('- no differences')
        if checks['only_in_schematic']:
            out('- only in schematic: ' + ', '.join(checks['only_in_schematic']))
        if checks['only_in_board']:
            out('- only on board: ' + ', '.join(checks['only_in_board']))
        for item in checks['differences']:
            out('- %(ref)s %(field)s: schematic `%(schematic)s`, board `%(board)s`' % item)
        for item in checks.get('net_differences', []):
            if 'board_net' in item:
                out('- board net `%s` covers %s: %s' % (
                    item['board_net'], item['issue'], ', '.join(item['schematic_nets'])))
            else:
                out('- schematic net `%s` covers %s: %s' % (
                    item['schematic_net'], item['issue'], ', '.join(item['board_nets'])))
        if checks.get('pins_without_pad'):
            out('- schematic pins without a pad: ' + ', '.join(checks['pins_without_pad'][:40]))

    if design['limits']:
        out('')
        out('## Not read')
        out('')
        for limit in design['limits']:
            out('- ' + limit)
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='Read a hardware design project into JSON.')
    parser.add_argument('path', nargs='?', default=os.getcwd())
    parser.add_argument('--out')
    parser.add_argument('--no-layout', action='store_true', help='skip layout.json')
    parser.add_argument('--no-kicad-cli', action='store_true',
                        help='never call kicad-cli, even when there is no board file')
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

    projects = find_projects(args.path)
    if not projects:
        sys.exit('No KiCad or Altium project found at %s' % args.path)
    if len(projects) > 1:
        print('Several projects found, pass one of them as PATH:', file=sys.stderr)
        for kind, name in projects:
            print('  [%s] %s' % (kind, name), file=sys.stderr)
        sys.exit(2)
    kind, project = projects[0]
    try:
        if kind == 'kicad':
            design, layout = kicad.read(project, use_kicad_cli=not args.no_kicad_cli)
        else:
            design, layout = altium.read(project)
    except RuntimeError as error:
        sys.exit(str(error))
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(project)), '.hwread')
    os.makedirs(out_dir, exist_ok=True)
    ignore = os.path.join(out_dir, '.gitignore')
    if not os.path.exists(ignore):
        with open(ignore, 'w', encoding='utf-8') as handle:
            handle.write('*\n')
    with open(os.path.join(out_dir, 'design.json'), 'w', encoding='utf-8') as handle:
        json.dump(design, handle, ensure_ascii=False, indent=1)
    if layout is not None and not args.no_layout:
        with open(os.path.join(out_dir, 'layout.json'), 'w', encoding='utf-8') as handle:
            json.dump(layout, handle, ensure_ascii=False, separators=(',', ':'))
    text = summary(design)
    with open(os.path.join(out_dir, 'summary.md'), 'w', encoding='utf-8') as handle:
        handle.write(text)
    print(text)
    print('Written to %s' % out_dir)


if __name__ == '__main__':
    main()
