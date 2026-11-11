"""Minimal Altium (binary SchDoc / PcbDoc) readers used by collect.py.

Only standard library + olefile. Coordinates are kept in Altium units
(SchDoc: 10 mil grid units, PcbDoc: mil).
"""
import collections
import math
import struct

try:
    import olefile
except ImportError:  # pragma: no cover
    olefile = None


# ---------------------------------------------------------------- schematic
def _records(data):
    i = 0
    out = []
    while i + 4 <= len(data):
        ln, = struct.unpack('<I', data[i:i + 4]); i += 4
        flag = ln >> 24; ln &= 0xFFFFFF
        chunk = data[i:i + ln]; i += ln
        if flag:
            out.append({'_binary': True})
            continue
        d = {}
        for kv in chunk.rstrip(b'\0').decode('latin-1').split('|'):
            if '=' in kv:
                k, v = kv.split('=', 1)
                d[k.upper()] = v
        out.append(d)
    return out


def _txt(d, k='TEXT'):
    v = d.get('%UTF8%' + k)
    if v is not None:
        try:
            return v.encode('latin-1').decode('utf-8')
        except UnicodeError:
            return v
    return d.get(k, '')


def _coord(d, base):
    x = int(d.get(base + '.X', '0') or 0) + int(d.get(base + '.X_FRAC', '0') or 0) / 1e5
    y = int(d.get(base + '.Y', '0') or 0) + int(d.get(base + '.Y_FRAC', '0') or 0) / 1e5
    return (round(x, 3), round(y, 3))


def read_schdoc(path):
    ole = olefile.OleFileIO(path)
    R = _records(ole.openstream('FileHeader').read())[1:]
    ole.close()
    comps = {}
    for i, d in enumerate(R):
        if d.get('RECORD') == '1':
            comps[i] = dict(lib=_txt(d, 'LIBREFERENCE'), desc=_txt(d, 'COMPONENTDESCRIPTION'),
                            part=d.get('CURRENTPARTID', '1'), mode=d.get('DISPLAYMODE', '0'),
                            pins=[], params={}, des='?')
    for d in R:
        o = d.get('OWNERINDEX')
        if o is None or int(o) not in comps:
            continue
        c = comps[int(o)]
        r = d.get('RECORD')
        if r == '34':
            c['des'] = _txt(d)
        elif r == '41':
            c['params'][_txt(d, 'NAME')] = _txt(d)
        elif r == '2':
            if d.get('OWNERPARTID', '1') not in ('-1', c['part']):
                continue
            if d.get('OWNERPARTDISPLAYMODE', '0') != c['mode']:
                continue
            con = int(d.get('PINCONGLOMERATE', '0'))
            L = int(d.get('PINLENGTH', '0')) + int(d.get('PINLENGTH_FRAC', '0')) / 1e5
            x, y = _coord(d, 'LOCATION')
            hx, hy = [(x + L, y), (x, y + L), (x - L, y), (x, y - L)][con & 3]
            c['pins'].append(dict(num=_txt(d, 'DESIGNATOR'), name=_txt(d, 'NAME'),
                                  pt=(round(hx, 3), round(hy, 3)),
                                  hidden=bool(con & 4), hnet=_txt(d, 'HIDDENNETNAME')))
    wires, labels, pows, ports, juncs = [], [], [], [], []
    for d in R:
        r = d.get('RECORD')
        if r == '27':
            n = int(d['LOCATIONCOUNT'])
            wires.append([(round(int(d.get(f'X{k}', '0')) + int(d.get(f'X{k}_FRAC', '0')) / 1e5, 3),
                           round(int(d.get(f'Y{k}', '0')) + int(d.get(f'Y{k}_FRAC', '0')) / 1e5, 3))
                          for k in range(1, n + 1)])
        elif r == '25':
            labels.append((_txt(d), _coord(d, 'LOCATION')))
        elif r == '17':
            pows.append((_txt(d), _coord(d, 'LOCATION')))
        elif r == '18':
            p = _coord(d, 'LOCATION'); w = int(d.get('WIDTH', '0')); st = int(d.get('STYLE', '0'))
            ends = [p, (p[0] + w, p[1])] if st < 4 else [p, (p[0], p[1] - w)]
            ports.append((_txt(d, 'NAME'), ends))
        elif r == '29':
            juncs.append(_coord(d, 'LOCATION'))
    return comps, wires, labels, pows, ports, juncs


def _onseg(p, a, b, eps=0.01):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) > eps * max(1, abs(x2 - x1) + abs(y2 - y1)):
        return False
    return min(x1, x2) - eps <= x <= max(x1, x2) + eps and min(y1, y2) - eps <= y <= max(y1, y2) + eps


class _UF:
    def __init__(self):
        self.p = {}

    def f(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def schematic(paths):
    """Return {'comps': {des: {...}}, 'nets': {pin: netkey}, 'netnames': {netkey: [names]}}.

    Flat connectivity: power ports global, ports global, net labels per sheet,
    hidden pins via HiddenNetName.
    """
    uf = _UF()
    allc = {}
    for path in paths:
        sh = path
        comps, wires, labels, pows, ports, juncs = read_schdoc(path)
        segs = []
        for wi, w in enumerate(wires):
            for si in range(len(w) - 1):
                segs.append((('seg', sh, wi, si), w[si], w[si + 1]))
                if si:
                    uf.u(('seg', sh, wi, si), ('seg', sh, wi, si - 1))

        def attach(node, p):
            uf.f(node); uf.u(node, ('pt', sh, p))
            for sn, a, b in segs:
                if _onseg(p, a, b):
                    uf.u(node, sn)
        for w in wires:
            for p in (w[0], w[-1]):
                attach(('pt', sh, p), p)
        for j in juncs:
            attach(('pt', sh, j), j)
        for n, p in labels:
            attach(('lbl', sh, n, p), p); uf.u(('lbl', sh, n, p), ('LNAME', sh, n))
        for n, p in pows:
            attach(('pow', sh, n, p), p); uf.u(('pow', sh, n, p), ('GNAME', n))
        for n, ends in ports:
            for e in ends:
                attach(('port', sh, n, e), e); uf.u(('port', sh, n, e), ('PNAME', n))
        for c in comps.values():
            des = c['des']
            if des not in allc:
                allc[des] = dict(lib=c['lib'], desc=c['desc'], params=dict(c['params']),
                                 sheet=path.replace('\\', '/').split('/')[-1])
            for p in c['pins']:
                node = ('pin', des, p['num'])
                if p['hidden'] and p['hnet']:
                    uf.u(node, ('GNAME', p['hnet']))
                else:
                    attach(node, p['pt'])
    groups = collections.defaultdict(list)
    for k in list(uf.p):
        groups[uf.f(k)].append(k)
    nets, names = {}, {}
    for g, mem in groups.items():
        pins = [m for m in mem if m[0] == 'pin']
        if not pins:
            continue
        nm = sorted({m[1] for m in mem if m[0] == 'GNAME'}) + sorted({m[1] for m in mem if m[0] == 'PNAME'}) \
            + sorted({m[2] for m in mem if m[0] == 'LNAME'})
        key = nm[0] if nm else 'N$' + min(f'{p[1]}.{p[2]}' for p in pins)
        names[key] = nm
        for p in pins:
            nets[f'{p[1]}.{p[2]}'] = key
    return dict(comps=allc, nets=nets, netnames=names)


# ---------------------------------------------------------------- pcb
def _textrecs(data):
    i = 0; out = []
    while i + 4 <= len(data):
        ln, = struct.unpack('<I', data[i:i + 4]); i += 4
        s = data[i:i + ln].rstrip(b'\0').decode('latin-1'); i += ln
        d = {}
        for kv in s.split('|'):
            if '=' in kv:
                k, v = kv.split('=', 1); d[k.upper()] = v
        out.append(d)
    return out


def _mil(v):
    return float(str(v).replace('mil', '') or 0)


def _count_binary(data):
    i = 0; n = 0
    while i + 5 <= len(data):
        i += 1
        ln, = struct.unpack('<I', data[i:i + 4]); i += 4 + ln; n += 1
    return n


def pcb(path):
    ole = olefile.OleFileIO(path)
    rd = lambda n: ole.openstream(n + '/Data').read() if ole.exists(n + '/Data') else b''
    comps = {}
    for c in _textrecs(rd('Components6')):
        des = c.get('SOURCEDESIGNATOR')
        if not des:
            continue
        comps[des] = dict(x=round(_mil(c.get('X', '0')), 2), y=round(_mil(c.get('Y', '0')), 2),
                          rot=round(float(c.get('ROTATION', '0') or 0), 1) % 360,
                          layer=c.get('LAYER', ''), footprint=c.get('PATTERN', ''))
    board = _textrecs(rd('Board6'))
    origin = (0.0, 0.0)
    if board:
        origin = (_mil(board[0].get('ORIGINX', '0mil')), _mil(board[0].get('ORIGINY', '0mil')))
    polys = []
    for p in _textrecs(rd('Polygons6')):
        polys.append(dict(layer=p.get('LAYER'), net=int(p.get('NET', '-1'))))
    nets = [n.get('NAME') for n in _textrecs(rd('Nets6'))]
    for p in polys:
        p['net'] = nets[p['net']] if 0 <= p['net'] < len(nets) else None
    stats = dict(vias=_count_binary(rd('Vias6')), tracks=_count_binary(rd('Tracks6')),
                 arcs=_count_binary(rd('Arcs6')), polygons=len(polys), nets=len(nets))
    ole.close()
    return dict(comps=comps, origin=origin, polygons=polys, stats=stats)


# ---------------------------------------------------------------- project / variants
def prjpcb(path):
    txt = open(path, encoding='latin-1').read()
    docs = [l.split('=', 1)[1] for l in txt.splitlines() if l.startswith('DocumentPath=')]
    variants = collections.OrderedDict()
    in_var = False; cur = None
    for line in txt.splitlines():
        if line.startswith('['):
            in_var = line.startswith('[ProjectVariant'); cur = None
            continue
        if not in_var:
            continue
        if line.startswith('Description='):
            cur = line.split('=', 1)[1]; variants.setdefault(cur, set())
        elif cur and line.startswith('Variation') and not line.startswith('VariationCount'):
            rec = dict(kv.split('=', 1) for kv in line.split('=', 1)[1].split('|') if '=' in kv)
            if rec.get('Kind') == '1' and rec.get('Designator'):
                variants[cur].add(rec['Designator'])
    return dict(documents=docs, variants={k: sorted(v) for k, v in variants.items()})
