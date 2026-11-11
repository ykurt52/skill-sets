"""Self-contained HTML page: the board drawn from layout.json with findings marked on it."""
import collections
import html

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
:root { --bg:#f4f5f7; --panel:#ffffff; --text:#1c2330; --muted:#5d6878; --line:#d5d9e0;
  --board:#e9efe6; --edge:#2c3a2a; --top:#c0392b; --bottom:#2b6cb0; --inner:#8a93a3;
  --pad:#6b5b2e; --via:#55606f; --error:#c62828; --warning:#c77700; --info:#1f6fb2; }
@media (prefers-color-scheme: dark) { :root { --bg:#12161c; --panel:#1b2129; --text:#e4e8ee;
  --muted:#9aa5b5; --line:#313a47; --board:#1d2a22; --edge:#b9c7b4; --top:#e06c5d; --bottom:#5fa0e0;
  --inner:#6c7686; --pad:#cdb56a; --via:#9aa5b5; --error:#ef6b6b; --warning:#f0a43a; --info:#62b0ee; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, sans-serif; }
header { padding:12px 16px; border-bottom:1px solid var(--line); }
h1 { font-size:17px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; }
main { display:flex; flex-wrap:wrap; gap:12px; padding:12px 16px; }
.board { flex:3 1 520px; min-width:0; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:8px; }
.list { flex:1 1 300px; min-width:0; max-height:86vh; overflow:auto; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; }
svg { width:100%%; height:auto; max-height:80vh; display:block; }
.toggles { display:flex; flex-wrap:wrap; gap:12px; padding:4px 4px 8px; color:var(--muted); font-size:13px; }
.outline { fill:var(--board); stroke:var(--edge); stroke-width:0.15; }
.trk { fill:none; stroke-linecap:round; opacity:0.55; }
.l-top { stroke:var(--top); } .l-bottom { stroke:var(--bottom); } .l-inner { stroke:var(--inner); opacity:0.3; }
.pad { fill:var(--pad); opacity:0.75; } .via { fill:var(--via); opacity:0.6; }
.mark circle { fill:none; stroke-width:0.25; }
.mark line { stroke-width:0.25; stroke-dasharray:0.8 0.5; }
.mark text { font-size:1.3px; stroke:none; paint-order:stroke; }
.mark.error { stroke:var(--error); fill:var(--error); }
.mark.warning { stroke:var(--warning); fill:var(--warning); }
.mark.info { stroke:var(--info); fill:var(--info); }
svg.focus .mark { opacity:0.12; } svg.focus .mark.on { opacity:1; }
svg.focus .mark.on circle, svg.focus .mark.on line { stroke-width:0.45; }
.item { padding:9px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
.item:hover, .item.on { background:var(--bg); }
.tag { display:inline-block; font-size:11px; font-weight:600; padding:1px 6px; border-radius:4px;
  color:#fff; margin-right:6px; }
.tag.error { background:var(--error); } .tag.warning { background:var(--warning); } .tag.info { background:var(--info); }
.check { font-weight:600; font-size:13px; }
.msg { margin-top:3px; overflow-wrap:anywhere; }
.basis { margin-top:3px; color:var(--muted); font-size:12px; }
.none { padding:14px; color:var(--muted); }
</style>
</head>
<body>
<header><h1>%(title)s</h1><div class="sub">%(subtitle)s</div></header>
<main>
<div class="board">
<div class="toggles">
<label><input type="checkbox" data-layer="l-top" checked> top copper</label>
<label><input type="checkbox" data-layer="l-bottom" checked> bottom copper</label>
<label><input type="checkbox" data-layer="l-inner"> inner copper</label>
<label><input type="checkbox" data-layer="pads" checked> pads and vias</label>
</div>
<svg id="board" viewBox="%(viewbox)s" xmlns="http://www.w3.org/2000/svg">
%(svg)s
</svg>
</div>
<div class="list">%(items)s</div>
</main>
<script>
var board = document.getElementById('board');
function select(id) {
  var same = board.dataset.on === id;
  board.dataset.on = same ? '' : id;
  board.classList.toggle('focus', !same);
  document.querySelectorAll('.mark, .item').forEach(function (el) {
    el.classList.toggle('on', !same && el.dataset.id === id);
  });
}
document.querySelectorAll('.item').forEach(function (el) {
  el.addEventListener('click', function () { select(el.dataset.id); });
});
document.querySelectorAll('.toggles input').forEach(function (box) {
  function apply() {
    document.querySelectorAll('.' + box.dataset.layer).forEach(function (el) {
      el.style.display = box.checked ? '' : 'none';
    });
  }
  box.addEventListener('change', apply); apply();
});
</script>
</body>
</html>
"""


def _number(value):
    return ('%.3f' % value).rstrip('0').rstrip('.')


def render(title, design, layout, findings):
    board = design.get('board') or {}
    flip = -1.0 if board.get('y_axis') == 'up' else 1.0
    copper = [layer['name'] for layer in board.get('copper_layers', [])]
    top, bottom = (copper[0], copper[-1]) if copper else (None, None)

    def y(value):
        return value * flip

    def layer_class(name):
        return 'l-top' if name == top else 'l-bottom' if name == bottom else 'l-inner'

    points = [point for shape in layout.get('outline', []) for point in shape['points']]
    if not points:
        points = [[pad['x'], pad['y']] for pad in layout.get('pads', [])]
    xs = [point[0] for point in points] or [0.0, 1.0]
    ys = [y(point[1]) for point in points] or [0.0, 1.0]
    margin = 3.0
    viewbox = ' '.join(_number(value) for value in (min(xs) - margin, min(ys) - margin,
                                                    max(xs) - min(xs) + 2 * margin,
                                                    max(ys) - min(ys) + 2 * margin))
    parts = []
    for shape in layout.get('outline', []):
        if shape['kind'] in ('poly', 'rect') and len(shape['points']) > 2:
            parts.append('<polygon class="outline" points="%s"/>' % ' '.join(
                '%s,%s' % (_number(px), _number(y(py))) for px, py in shape['points']))
        elif len(shape['points']) >= 2:
            parts.append('<polyline class="outline" style="fill:none" points="%s"/>' % ' '.join(
                '%s,%s' % (_number(px), _number(y(py))) for px, py in shape['points']))

    paths = collections.defaultdict(list)
    for track in layout.get('tracks', []):
        chain = [track['start']] + ([track['mid']] if 'mid' in track else []) + [track['end']]
        paths[(layer_class(track['layer']), track['width'])].append(
            'M' + ' L'.join('%s %s' % (_number(px), _number(y(py))) for px, py in chain))
    order = {'l-inner': 0, 'l-bottom': 1, 'l-top': 2}
    for (css, width), segments in sorted(paths.items(), key=lambda item: order[item[0][0]]):
        parts.append('<path class="trk %s" stroke-width="%s" d="%s"/>' % (
            css, _number(max(width, 0.05)), ' '.join(segments)))

    parts.append('<g class="pads">')
    for via in layout.get('vias', []):
        parts.append('<circle class="via" cx="%s" cy="%s" r="%s"/>' % (
            _number(via['x']), _number(y(via['y'])), _number((via['size'] or 0.4) / 2)))
    for pad in layout.get('pads', []):
        size = pad.get('size') or [0.5, 0.5]
        cx, cy = _number(pad['x']), _number(y(pad['y']))
        if pad.get('shape') in ('round', 'circle') and abs(size[0] - size[1]) < 1e-6:
            parts.append('<circle class="pad" cx="%s" cy="%s" r="%s"/>' % (cx, cy, _number(size[0] / 2)))
        else:
            parts.append('<rect class="pad" x="%s" y="%s" width="%s" height="%s" '
                         'transform="rotate(%s %s %s)"/>' % (
                             _number(pad['x'] - size[0] / 2), _number(y(pad['y']) - size[1] / 2),
                             _number(size[0]), _number(size[1]), _number(-(pad.get('rotation') or 0)),
                             cx, cy))
    parts.append('</g>')

    for item in findings:
        if not item['marks']:
            continue
        parts.append('<g class="mark %s" data-id="%d">' % (item['severity'], item['id']))
        for mark in item['marks']:
            label = html.escape('#%d %s' % (item['id'], mark['label']))
            if mark['kind'] == 'point':
                parts.append('<circle cx="%s" cy="%s" r="1.4"/><text x="%s" y="%s">%s</text>' % (
                    _number(mark['x']), _number(y(mark['y'])), _number(mark['x'] + 1.7),
                    _number(y(mark['y']) - 0.6), label))
            else:
                parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s"/><circle cx="%s" cy="%s" r="0.9"/>'
                             '<circle cx="%s" cy="%s" r="0.9"/><text x="%s" y="%s">%s</text>' % (
                                 _number(mark['x1']), _number(y(mark['y1'])), _number(mark['x2']),
                                 _number(y(mark['y2'])), _number(mark['x1']), _number(y(mark['y1'])),
                                 _number(mark['x2']), _number(y(mark['y2'])),
                                 _number((mark['x1'] + mark['x2']) / 2 + 1.0),
                                 _number(y((mark['y1'] + mark['y2']) / 2) - 0.6), label))
        parts.append('</g>')

    items = []
    for item in findings:
        basis = '<div class="basis">Basis: %s</div>' % html.escape(item['basis']) if item['basis'] else ''
        located = '' if item['marks'] else '<div class="basis">not located on the board</div>'
        items.append('<div class="item" data-id="%d"><span class="tag %s">%s</span>'
                     '<span class="check">#%d %s</span><div class="msg">%s</div>%s%s</div>' % (
                         item['id'], item['severity'], item['severity'], item['id'],
                         html.escape(item['check']), html.escape(item['message']), basis, located))
    size = board.get('size')
    subtitle = '%s, %d copper layers, %d findings. Click a finding to locate it.' % (
        '%.1f x %.1f mm' % tuple(size) if size else 'board size unknown', len(copper), len(findings))
    return PAGE % dict(title=html.escape(title), subtitle=html.escape(subtitle), viewbox=viewbox,
                       svg='\n'.join(parts),
                       items='\n'.join(items) or '<div class="none">No findings.</div>')
