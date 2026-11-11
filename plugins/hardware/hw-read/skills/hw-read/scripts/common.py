"""Helpers shared by the KiCad and Altium readers."""
import math
import re

SCHEMA = 'hw-read/1'

# Field names that hold the manufacturer part number, in order of preference.
MPN_FIELD_KEYS = ('mpn', 'manufacturerpartnumber', 'mfrpartnumber', 'mfrpartno', 'mfgpartno',
                  'mfrpn', 'mfpn')
MANUFACTURER_FIELD_KEYS = ('manufacturer', 'mfr', 'mfg')


def rounded(number_value, digits=4):
    return round(float(number_value), digits)


def clean(text):
    """None for empty or placeholder text."""
    if text is None or text.strip() in ('', '~', '*'):
        return None
    return text


def field_key(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def pick_field(fields, keys):
    """Value of the first field whose normalized name is in keys, by key order."""
    by_key = {}
    for name, text in fields.items():
        if text:
            by_key.setdefault(field_key(name), text)
    for key in keys:
        if key in by_key:
            return by_key[key]
    return None


def polygon_area(points):
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def arc_length(start, mid, end):
    """Length of the arc through three points."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    det = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(det) < 1e-12:
        return math.hypot(cx - ax, cy - ay)
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / det
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / det
    radius = math.hypot(ax - ux, ay - uy)
    angle_start = math.atan2(ay - uy, ax - ux)
    sweep = (math.atan2(cy - uy, cx - ux) - angle_start) % (2 * math.pi)
    sweep_mid = (math.atan2(by - uy, bx - ux) - angle_start) % (2 * math.pi)
    if sweep_mid > sweep:
        sweep = 2 * math.pi - sweep
    return radius * sweep


def bbox(points):
    """[min x, min y, max x, max y] of a point list, or None when empty."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [rounded(min(xs)), rounded(min(ys)), rounded(max(xs)), rounded(max(ys))]


def routing_summary(tracks, vias, zones, copper_layer_names):
    """Per-net track length, widths, layers, via count and zone layers."""
    order = {name: index for index, name in enumerate(copper_layer_names)}
    nets = {}

    def entry_for(name):
        return nets.setdefault(name, dict(track_length=0.0, widths=set(), layers=set(),
                                          vias=0, zone_layers=set()))
    for track in tracks:
        if track['net']:
            entry = entry_for(track['net'])
            entry['track_length'] += track['length']
            entry['widths'].add(track['width'])
            entry['layers'].add(track['layer'])
    for via in vias:
        if via['net']:
            entry_for(via['net'])['vias'] += 1
    for zone in zones:
        if zone['net'] and 'keepout' not in zone:
            entry_for(zone['net'])['zone_layers'].update(zone['layers'])

    def ordered(layers):
        return sorted(layers, key=lambda name: order.get(name, 999))
    return {name: dict(track_length=rounded(entry['track_length'], 3),
                       min_width=min(entry['widths']) if entry['widths'] else None,
                       max_width=max(entry['widths']) if entry['widths'] else None,
                       layers=ordered(entry['layers']), vias=entry['vias'],
                       zone_layers=ordered(entry['zone_layers']))
            for name, entry in nets.items()}


def attach_nets(components, routing, classes_of):
    """Build the nets table from component pins plus the routing summary."""
    nets = {}
    for ref, part in components.items():
        for pin, data in part['pins'].items():
            if data['net']:
                nets.setdefault(data['net'], dict(pins=[]))['pins'].append('%s.%s' % (ref, pin))
    for net in routing:
        nets.setdefault(net, dict(pins=[]))
    for net, entry in nets.items():
        entry['pins'].sort()
        classes = classes_of(net)
        if classes is not None:
            entry['classes'] = classes
        entry.update(routing.get(net, {}))
    return nets
