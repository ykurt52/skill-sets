#!/usr/bin/env python
"""Collect facts about an Altium hardware repo for commit / Redmine notes.

Usage:
  python collect.py [--repo PATH] [--since REF] [--until REF] [--full] [--json OUT] [--config FILE]

Default comparison:
  - working tree has Altium/output changes -> HEAD vs working tree
  - working tree clean                     -> HEAD~1 vs HEAD
  - no previous commit with project files  -> full summary
Prints a Markdown fact sheet to stdout.

Repo conventions (output folders, version tag, local-only documents) come from
<repo>/.hwnotes/config.json when it exists; see DEFAULT_CONFIG for the keys.
"""
import argparse, collections, datetime as dt, fnmatch, glob, json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import altium  # noqa: E402

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

DEFAULT_CONFIG = dict(
    # folder names (fnmatch patterns, any path level) that hold generated outputs
    output_dirs=['Doc', 'Docs', 'Outputs', 'Project Outputs*'],
    # substrings of file names that stay local and must not appear in a commit text
    local_only_hints=[],
    # searched in output file names; named groups: ver (required), rev (optional)
    version_regex=r'(?:^|[_-])V(?P<ver>\d+)(?:[_.-]|$)',
    # matched against branch name, then repo folder name; named groups: stage, num
    stage_regex=r'(?P<stage>alpha|beta|rc|rel)(?P<num>\d+)',
    # placeholders: {ver} {rev} {stage} {Stage} {stage_num}
    tag_format='V{ver}',
)


def load_config(repo, path=None):
    cfg = dict(DEFAULT_CONFIG)
    if path is None:
        path = os.path.join(repo, '.hwnotes', 'config.json')
        if not os.path.exists(path):
            return cfg
    cfg.update(json.load(open(path, encoding='utf-8')))
    return cfg


def is_output(relpath, cfg):
    """True when any folder in relpath matches one of the output_dirs patterns."""
    return any(fnmatch.fnmatch(seg.lower(), pat.lower())
               for seg in relpath.split('/')[:-1] for pat in cfg['output_dirs'])


def git(repo, *args, check=True, binary=False):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.decode('utf-8', 'replace'))
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace')


def ref_exists(repo, ref):
    return subprocess.run(['git', '-C', repo, 'rev-parse', '--verify', '--quiet', ref + '^{commit}'],
                          capture_output=True).returncode == 0


def materialize(repo, ref, relpath, tmpdir):
    """Return a local path to relpath at ref (None = working tree)."""
    if ref is None:
        p = os.path.join(repo, relpath)
        return p if os.path.exists(p) else None
    r = subprocess.run(['git', '-C', repo, 'show', f'{ref}:{relpath}'], capture_output=True)
    if r.returncode != 0:
        return None
    out = os.path.join(tmpdir, re.sub(r'[^\w.-]', '_', f'{ref}_{relpath}'))
    open(out, 'wb').write(r.stdout)
    return out


def list_files(repo, ref):
    if ref is None:
        out = git(repo, 'ls-files', '--cached', '--others', '--exclude-standard')
    else:
        out = git(repo, 'ls-tree', '-r', '--name-only', ref)
    return [l for l in out.splitlines() if l]


def project_files(files):
    prj = [f for f in files if f.lower().endswith('.prjpcb') and '/History/' not in f]
    sch = sorted(f for f in files if f.lower().endswith('.schdoc') and '/History/' not in f)
    pcbs = [f for f in files if f.lower().endswith('.pcbdoc') and '/History/' not in f]
    main = [f for f in pcbs if 'panel' not in os.path.basename(f).lower()]
    return (prj[0] if prj else None), sch, (main[0] if main else None), pcbs


def load_state(repo, ref, tmpdir):
    files = list_files(repo, ref)
    prj, sch, pcbf, pcbs = project_files(files)
    st = dict(ref=ref or 'WORKTREE', files=files, prj=prj, sch=sch, pcb=pcbf)
    paths = [materialize(repo, ref, s, tmpdir) for s in sch]
    paths = [p for p in paths if p]
    st['schematic'] = altium.schematic(paths) if paths else None
    if st['schematic']:
        # keep sheet names readable
        for c in st['schematic']['comps'].values():
            c['sheet'] = re.sub(r'^.*?_\w*?(\[\d+\].*)$', r'\1', c['sheet'])
    pp = materialize(repo, ref, pcbf, tmpdir) if pcbf else None
    st['pcb'] = altium.pcb(pp) if pp else None
    pr = materialize(repo, ref, prj, tmpdir) if prj else None
    st['project'] = altium.prjpcb(pr) if pr else None
    return st


def val(c):
    p = c['params']
    return p.get('Value') or p.get('Comment') or ''


def mpn(c):
    return c['params'].get('Manufacturer Part Number', '')


def natkey(d):
    m = re.match(r'([A-Za-z]+)(\d*)', d)
    return (m.group(1), int(m.group(2) or 0)) if m else (d, 0)


def diff_states(a, b):
    out = {}
    if a['schematic'] and b['schematic']:
        ca, cb = a['schematic']['comps'], b['schematic']['comps']
        out['added'] = sorted(set(cb) - set(ca), key=natkey)
        out['removed'] = sorted(set(ca) - set(cb), key=natkey)
        ch = []
        for d in sorted(set(ca) & set(cb), key=natkey):
            x, y = ca[d], cb[d]
            fields = []
            if val(x) != val(y): fields.append(('value', val(x), val(y)))
            if mpn(x) != mpn(y): fields.append(('mpn', mpn(x), mpn(y)))
            if x['lib'] != y['lib']: fields.append(('lib', x['lib'], y['lib']))
            if fields: ch.append((d, fields))
        out['changed'] = ch
        # nets: compare peer sets per pin
        na, nb = a['schematic']['nets'], b['schematic']['nets']
        def groups(n):
            g = collections.defaultdict(set)
            for p, k in n.items(): g[k].add(p)
            return g
        ga, gb = groups(na), groups(nb)
        sig_a = {frozenset(v): k for k, v in ga.items()}
        sig_b = {frozenset(v): k for k, v in gb.items()}
        renamed = [(sig_a[s], sig_b[s]) for s in set(sig_a) & set(sig_b) if sig_a[s] != sig_b[s]
                   and not (sig_a[s].startswith('N$') and sig_b[s].startswith('N$'))]
        moved = []
        common = set(na) & set(nb)
        for p in sorted(common, key=lambda s: natkey(s.split('.')[0]) + (s,)):
            pa = (ga[na[p]] & common) - {p}; pb = (gb[nb[p]] & common) - {p}
            if pa != pb and (na[p] != nb[p] or na[p].startswith('N$')):
                moved.append((p, na[p], nb[p]))
        out['net_renamed'] = renamed
        out['pins_reconnected'] = moved
        out['nets_count'] = (len(ga), len(gb))
    if a['pcb'] and b['pcb']:
        pa, pb = a['pcb']['comps'], b['pcb']['comps']
        mv = []
        for d in sorted(set(pa) & set(pb), key=natkey):
            x, y = pa[d], pb[d]
            dist = ((x['x'] - y['x']) ** 2 + (x['y'] - y['y']) ** 2) ** .5 * 0.0254
            if dist > 0.05 or x['rot'] != y['rot'] or x['layer'] != y['layer'] or x['footprint'] != y['footprint']:
                mv.append(dict(des=d, dist_mm=round(dist, 2), dx_mm=round((y['x'] - x['x']) * 0.0254, 2),
                               dy_mm=round((y['y'] - x['y']) * 0.0254, 2),
                               rot=(x['rot'], y['rot']) if x['rot'] != y['rot'] else None,
                               layer=(x['layer'], y['layer']) if x['layer'] != y['layer'] else None,
                               footprint=(x['footprint'], y['footprint']) if x['footprint'] != y['footprint'] else None))
        out['pcb_moved'] = mv
        out['pcb_added'] = sorted(set(pb) - set(pa), key=natkey)
        out['pcb_removed'] = sorted(set(pa) - set(pb), key=natkey)
        out['pcb_stats'] = {k: (a['pcb']['stats'][k], b['pcb']['stats'][k]) for k in b['pcb']['stats']}
        pga = collections.Counter((p['layer'], p['net']) for p in a['pcb']['polygons'])
        pgb = collections.Counter((p['layer'], p['net']) for p in b['pcb']['polygons'])
        out['pours_added'] = sorted(f'{l} {n}' for (l, n) in (pgb - pga))
        out['pours_removed'] = sorted(f'{l} {n}' for (l, n) in (pga - pgb))
    if a['project'] and b['project']:
        va, vb = a['project']['variants'], b['project']['variants']
        vd = {}
        for k in sorted(set(va) | set(vb)):
            sa, sb = set(va.get(k, [])), set(vb.get(k, []))
            if sa != sb or k not in va or k not in vb:
                vd[k] = dict(new=k not in va, deleted=k not in vb,
                             now_not_fitted=sorted(sb - sa, key=natkey), now_fitted=sorted(sa - sb, key=natkey))
        out['variants'] = vd
    return out


def group_by_sheet(des_list, comps):
    g = collections.defaultdict(list)
    for d in des_list:
        g[comps.get(d, {}).get('sheet', '?')].append(d)
    return g


def compress(des):
    """R11, R12, R13 -> R11-R13"""
    des = sorted(des, key=natkey); out = []; i = 0
    while i < len(des):
        p, n = natkey(des[i]); j = i
        while j + 1 < len(des) and natkey(des[j + 1]) == (p, natkey(des[j])[1] + 1): j += 1
        if j - i >= 2: out.append(f'{des[i]}-{des[j]}')
        else: out.extend(des[i:j + 1])
        i = j + 1
    return ', '.join(out)


def timing(repo, prj_rel, since_dt):
    """Work sessions from Altium History saves + changed file mtimes."""
    stamps = []
    if prj_rel:
        hist = os.path.join(repo, os.path.dirname(prj_rel), 'History')
        for f in glob.glob(os.path.join(hist, '*')):
            stamps.append(dt.datetime.fromtimestamp(os.path.getmtime(f)))
    for f in git(repo, 'ls-files', '--modified', '--others', '--exclude-standard').splitlines():
        p = os.path.join(repo, f)
        if os.path.exists(p): stamps.append(dt.datetime.fromtimestamp(os.path.getmtime(p)))
    for line in git(repo, 'log', '--format=%ct', '-50').splitlines():
        stamps.append(dt.datetime.fromtimestamp(int(line)))
    if since_dt: stamps = [s for s in stamps if s > since_dt]
    stamps.sort()
    if not stamps: return None
    days = collections.OrderedDict(); sessions = []
    start = prev = stamps[0]
    for s in stamps[1:]:
        if (s - prev).total_seconds() > 60 * 60 or s.date() != prev.date():
            sessions.append((start, prev)); start = s
        prev = s
    sessions.append((start, prev))
    for a, b in sessions:
        d = a.date().isoformat(); days.setdefault(d, [a, b, 0.0])
        days[d][0] = min(days[d][0], a); days[d][1] = max(days[d][1], b)
        days[d][2] += max((b - a).total_seconds() / 3600, 0.25)
    return dict(first=stamps[0], last=stamps[-1], days=days,
                total_h=round(sum(v[2] for v in days.values()) * 2) / 2)


def version_tag(repo, files, cfg):
    m = None
    for f in files:
        m = re.search(cfg['version_regex'], os.path.basename(f))
        if m: break
    folder = os.path.basename(repo.rstrip('/\\'))
    br = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    stage = re.match(cfg['stage_regex'], br) or re.match(cfg['stage_regex'], folder)
    found = m.groupdict() if m else {}
    stage_found = stage.groupdict() if stage else {}
    fields = dict(ver=found.get('ver'), rev=found.get('rev'), stage=stage_found.get('stage'),
                  Stage=(stage_found.get('stage') or '').capitalize() or None, stage_num=stage_found.get('num'))
    used = re.findall(r'\{(\w+)\}', cfg['tag_format'])
    tag = cfg['tag_format'].format(**fields) if used and all(fields.get(u) for u in used) else None
    return dict(tag=tag, pcb_rev=fields['rev'], hw_ver=fields['ver'], branch=br)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=os.getcwd()); ap.add_argument('--since'); ap.add_argument('--until')
    ap.add_argument('--full', action='store_true'); ap.add_argument('--json'); ap.add_argument('--config')
    a = ap.parse_args()
    repo = git(a.repo, 'rev-parse', '--show-toplevel').strip()
    cfg = load_config(repo, a.config)
    status = git(repo, 'status', '--porcelain=v1', '-uall')
    dirty = [l[3:] for l in status.splitlines() if l[3:] and
             (re.search(r'\.(SchDoc|PcbDoc|PrjPCB|OutJob)$', l[3:], re.I) or is_output(l[3:], cfg))]
    target = a.until  # None = worktree
    if target is None and not dirty and ref_exists(repo, 'HEAD'): target = 'HEAD'
    base = a.since
    if base is None:
        base = 'HEAD' if target is None else (target + '~1')
    if not ref_exists(repo, base): base = None
    tmp = tempfile.mkdtemp(prefix='hwnotes_')
    B = load_state(repo, target, tmp)
    A = load_state(repo, base, tmp) if base else None
    if A and not A['schematic']: A = None  # base had no project files -> initial release
    full = a.full or A is None
    D = diff_states(A, B) if A else {}

    # file changes
    if A:
        rng = [base] + ([target] if target else [])
        names = git(repo, 'diff', '--name-status', *rng).splitlines()
    else:
        names = ['A\t' + f for f in B['files']]
    untracked = [l[3:] for l in status.splitlines() if l.startswith('??')] if target is None else []
    ignored = git(repo, 'status', '--porcelain', '--ignored', '-uall').splitlines()
    hints = cfg['local_only_hints']
    local_only = sorted({l[3:] for l in ignored if l.startswith('!!') and any(h in l for h in hints)} |
                        {u for u in untracked if any(h in u for h in hints)})
    since_dt = None
    if base:
        since_dt = dt.datetime.fromtimestamp(int(git(repo, 'log', '-1', '--format=%ct', base).strip()))
    T = timing(repo, B['prj'], since_dt)
    V = version_tag(repo, B['files'], cfg)
    log = git(repo, 'log', '--format=--- %h %an %ad%n%B', '--date=format:%d.%m.%Y', '-6') if ref_exists(repo, 'HEAD') else ''

    sc = B['schematic']; comps = sc['comps'] if sc else {}
    P = print
    P(f'# Hardware fact sheet: {os.path.basename(B["prj"] or repo)}')
    P(f'- repo: `{repo}`  branch: `{V["branch"]}`  compare: `{base or "(none, full summary)"}` -> `{target or "WORKTREE"}`')
    P(f'- version tag guess: `{V["tag"]}`  PCB rev: {V["pcb_rev"]}  HW: {V["hw_ver"]}')
    if B['project']:
        P('- variants (not fitted): ' + '; '.join(f'{k}: {len(v)} [{compress(v)}]' for k, v in B['project']['variants'].items()))
    if full and sc:
        P('\n## Full summary')
        bysheet = collections.defaultdict(list)
        for d, c in comps.items(): bysheet[c['sheet']].append(d)
        for s in sorted(bysheet):
            ds = bysheet[s]
            ics = [f'{d}={val(comps[d]) or comps[d]["lib"]}' for d in sorted(ds, key=natkey) if re.match(r'(U|J|Y|L|F)\d', d)]
            P(f'- {s}: {len(ds)} parts; key parts: ' + ', '.join(ics[:18]))
        P(f'- nets: {len(set(sc["nets"].values()))}')
        named = sorted(k for k in sc['netnames'] if not k.startswith('N$'))
        P('- named nets: ' + ', '.join(named[:120]))
    if B['pcb']:
        s = B['pcb']['stats']
        P(f'- PCB: {len(B["pcb"]["comps"])} components, vias {s["vias"]}, tracks {s["tracks"]}, arcs {s["arcs"]}, polygons {s["polygons"]}')
        P('- pours: ' + ', '.join(sorted({f"{p['layer']} {p['net']}" for p in B['pcb']['polygons']})))
    if D:
        P('\n## Schematic changes')
        for k, lbl in (('added', 'added'), ('removed', 'removed')):
            if D.get(k):
                g = group_by_sheet(D[k], comps if k == 'added' else A['schematic']['comps'])
                for s, ds in g.items():
                    src = comps if k == 'added' else A['schematic']['comps']
                    P(f'- {lbl} ({s}): ' + ', '.join(f'{d} {val(src[d])} {mpn(src[d])}'.strip() for d in ds))
        for d, f in D.get('changed', []):
            P(f'- changed {d}: ' + '; '.join(f'{k} {x} -> {y}' for k, x, y in f))
        for x, y in D.get('net_renamed', []): P(f'- net renamed: {x} -> {y}')
        rc = D.get('pins_reconnected', [])
        if rc:
            P(f'- pins reconnected: {len(rc)}')
            for p, x, y in rc[:60]: P(f'  - {p}: {x} -> {y}')
        if D.get('nets_count'): P(f'- net count: {D["nets_count"][0]} -> {D["nets_count"][1]}')
        P('\n## PCB changes')
        if D.get('pcb_added'): P('- PCB added: ' + compress(D['pcb_added']))
        if D.get('pcb_removed'): P('- PCB removed: ' + compress(D['pcb_removed']))
        mv = D.get('pcb_moved', [])
        if mv:
            g = group_by_sheet([m['des'] for m in mv], comps)
            P(f'- components moved/rotated/flipped: {len(mv)}')
            for s, ds in g.items():
                ms = [m for m in mv if m['des'] in ds]
                avg = (round(sum(m['dx_mm'] for m in ms) / len(ms), 1), round(sum(m['dy_mm'] for m in ms) / len(ms), 1))
                P(f'  - {s}: {compress(ds)} (avg dx/dy {avg[0]}/{avg[1]} mm)')
            for m in mv:
                extra = [f'rot {m["rot"][0]}->{m["rot"][1]}' if m['rot'] else '', f'layer {m["layer"][0]}->{m["layer"][1]}' if m['layer'] else '',
                         f'footprint {m["footprint"][0]}->{m["footprint"][1]}' if m['footprint'] else '']
                if any(extra): P(f'  - {m["des"]}: ' + ', '.join(e for e in extra if e))
        for k, (x, y) in D.get('pcb_stats', {}).items():
            if x != y: P(f'- {k}: {x} -> {y}')
        if D.get('pours_added'): P('- pours added: ' + ', '.join(D['pours_added']))
        if D.get('pours_removed'): P('- pours removed: ' + ', '.join(D['pours_removed']))
        if D.get('variants'):
            P('\n## Variant changes')
            for k, v in D['variants'].items():
                P(f'- {k}: ' + ('NEW ' if v['new'] else '') + ('DELETED ' if v['deleted'] else '') +
                  (f'now not fitted: {compress(v["now_not_fitted"])} ' if v['now_not_fitted'] else '') +
                  (f'now fitted: {compress(v["now_fitted"])}' if v['now_fitted'] else ''))
    P('\n## Files')
    cls = collections.defaultdict(list)
    for l in names:
        parts = l.split('\t'); st, f = parts[0], parts[-1]
        key = 'Schematic' if f.lower().endswith('.schdoc') else 'PCB' if f.lower().endswith('.pcbdoc') else \
            'Outputs' if is_output(f, cfg) else 'Project/Job' if re.search(r'\.(PrjPCB|OutJob)$', f, re.I) else 'Other'
        cls[key].append(f'{st[0]} {f}')
    for k, v in cls.items():
        P(f'- {k} ({len(v)}): ' + ', '.join(x.split('/')[-1] if k == 'Outputs' else x for x in v[:40]) + (' ...' if len(v) > 40 else ''))
    if untracked: P('- untracked (will NOT be in commit unless added): ' + ', '.join(untracked[:30]))
    if local_only: P('- LOCAL-ONLY docs (never mention in commit): ' + ', '.join(local_only[:20]))
    if T:
        P('\n## Work time (from Altium History saves, file mtimes, commits; gaps >60 min = break)')
        P(f'- start: {T["first"]:%d.%m.%Y %H:%M}  end: {T["last"]:%d.%m.%Y %H:%M}  total ≈ {T["total_h"]} h')
        for d, (s, e, h) in T['days'].items(): P(f'  - {dt.date.fromisoformat(d):%d.%m.%Y}: {s:%H:%M}-{e:%H:%M} ≈ {h:.1f} h')
    if log:
        P('\n## Recent commits (style reference)')
        P('```'); P(log.strip()[:2500]); P('```')
    if a.json:
        json.dump(dict(diff=D, version=V, files=names, untracked=untracked, local_only=local_only), open(a.json, 'w', encoding='utf-8'),
                  default=str, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
