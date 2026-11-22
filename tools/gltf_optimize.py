"""Shrink a glTF bike model for the web and normalize it to the configurator's convention.

- bakes node transforms into world space
- simplifies untextured meshes by vertex clustering (vertices closer than --cell meters merge)
- quantizes positions (int16) and normals (int8) with KHR_mesh_quantization
- embeds the images that are still used (large PNGs without transparency become JPEG via `sips`)
- converts to meters, rotates so the front of the bike faces +z, puts the tires on y = 0

usage: python3 gltf_optimize.py scene.gltf out.glb [--cell 0.002] [--rotate-y DEG] [--scale S] [--keep Seat,Body]
"""
import argparse, json, os, struct, subprocess, tempfile
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('src'); ap.add_argument('out')
ap.add_argument('--cell', type=float, default=0.002, help='clustering cell size in meters (0 = off)')
ap.add_argument('--rotate-y', type=float, default=0.0, help='rotation about Y applied after baking (degrees)')
ap.add_argument('--scale', type=float, default=1.0, help='uniform scale to meters')
ap.add_argument('--keep', default='', help='comma separated material names never simplified')
ap.add_argument('--info', action='store_true', help='only print per-material bounds after baking')
args = ap.parse_args()

base = os.path.dirname(os.path.abspath(args.src))
g = json.load(open(args.src))
buffers = [open(os.path.join(base, b['uri']), 'rb').read() for b in g['buffers']]
CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

def read(ai):
    a = g['accessors'][ai]; bv = g['bufferViews'][a['bufferView']]
    dt = np.dtype(CT[a['componentType']]); n = NC[a['type']]
    off = bv.get('byteOffset', 0) + a.get('byteOffset', 0); stride = bv.get('byteStride', dt.itemsize * n)
    raw = np.frombuffer(buffers[bv['buffer']], dtype=np.uint8, count=stride * (a['count'] - 1) + dt.itemsize * n, offset=off)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(a['count'], dt.itemsize * n), strides=(stride, 1))
    out = np.ascontiguousarray(rows).view(dt).reshape(a['count'], n).astype(np.float64)
    if a.get('normalized'): out /= np.iinfo(dt).max
    return out if n > 1 else out[:, 0]

def node_matrix(n):   # column-vector 4x4
    if 'matrix' in n: return np.array(n['matrix'], dtype=np.float64).reshape(4, 4).T
    T = np.eye(4); T[:3, 3] = n.get('translation', [0, 0, 0])
    x, y, z, w = n.get('rotation', [0, 0, 0, 1])
    R = np.eye(4); R[:3, :3] = [[1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                                [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                                [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]]
    S = np.diag(list(n.get('scale', [1, 1, 1])) + [1])
    return T @ R @ S

a = np.radians(args.rotate_y); c, s = np.cos(a), np.sin(a)
FIX = np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]) @ np.diag([args.scale] * 3 + [1])

prims = []
def walk(ni, parent):
    n = g['nodes'][ni]; M = parent @ node_matrix(n)
    if 'mesh' in n:
        for p in g['meshes'][n['mesh']]['primitives']:
            if p.get('mode', 4) != 4: continue
            P = read(p['attributes']['POSITION']); N = read(p['attributes']['NORMAL']) if 'NORMAL' in p['attributes'] else None
            UV = read(p['attributes']['TEXCOORD_0']) if 'TEXCOORD_0' in p['attributes'] else None
            I = read(p['indices']).astype(np.int64) if 'indices' in p else np.arange(len(P))
            W = FIX @ M
            P = (np.c_[P, np.ones(len(P))] @ W.T)[:, :3]
            if N is not None:
                N = N @ np.linalg.inv(W[:3, :3]); N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
            if np.linalg.det(W[:3, :3]) < 0: I = I.reshape(-1, 3)[:, ::-1].reshape(-1)
            prims.append({'mat': p.get('material'), 'P': P, 'N': N, 'UV': UV, 'I': I.reshape(-1, 3)})
    for ch in n.get('children', []): walk(ch, M)
for root in g['scenes'][g.get('scene', 0)]['nodes']: walk(root, np.eye(4))

ground = min(p['P'][:, 1].min() for p in prims)
for p in prims: p['P'][:, 1] -= ground
mname = lambda m: g['materials'][m]['name'] if m is not None else '-'

if args.info:
    from collections import defaultdict
    bb = defaultdict(lambda: [np.full(3, 1e9), np.full(3, -1e9), 0])
    for p in prims:
        b = bb[mname(p['mat'])]; b[0] = np.minimum(b[0], p['P'].min(0)); b[1] = np.maximum(b[1], p['P'].max(0)); b[2] += len(p['P'])
    for k, (lo, hi, n) in sorted(bb.items()): print(k.ljust(14), lo.round(3), hi.round(3), n)
    raise SystemExit

# ---- merge primitives by material, then simplify ----
keep = set(filter(None, args.keep.split(',')))
by_mat = {}
for p in prims: by_mat.setdefault(p['mat'], []).append(p)
merged = []
for m, ps in by_mat.items():
    off, P, N, UV, I = 0, [], [], [], []
    has_n = all(p['N'] is not None for p in ps); has_uv = all(p['UV'] is not None for p in ps)
    for p in ps:
        P.append(p['P']); I.append(p['I'] + off); off += len(p['P'])
        if has_n: N.append(p['N'])
        if has_uv: UV.append(p['UV'])
    mat = g['materials'][m] if m is not None else {}
    textured = 'baseColorTexture' in mat.get('pbrMetallicRoughness', {})
    rec = {'mat': m, 'P': np.vstack(P), 'N': np.vstack(N) if has_n else None,
           'UV': np.vstack(UV) if (has_uv and textured) else None, 'I': np.vstack(I)}
    if args.cell > 0 and not textured and mname(m) not in keep:
        key = np.floor(rec['P'] / args.cell).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True); inv = inv.reshape(-1)
        k = inv.max() + 1
        cnt = np.bincount(inv, minlength=k)[:, None]
        Pn = np.zeros((k, 3)); np.add.at(Pn, inv, rec['P']); Pn /= cnt
        Nn = None
        if rec['N'] is not None:
            Nn = np.zeros((k, 3)); np.add.at(Nn, inv, rec['N']); Nn /= np.linalg.norm(Nn, axis=1, keepdims=True) + 1e-12
        T = inv[rec['I']]
        T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
        T = np.unique(T, axis=0)
        print(f"{mname(m):14} {len(rec['P']):7} -> {k:7} verts, {len(rec['I']):7} -> {len(T):7} tris")
        rec.update(P=Pn, N=Nn, I=T)
    merged.append(rec)

# ---- write GLB ----
bin_ = bytearray(); views, accessors, meshes, nodes = [], [], [], []
def add_view(data, target=None, stride=None):
    while len(bin_) % 4: bin_.append(0)
    v = {'buffer': 0, 'byteOffset': len(bin_), 'byteLength': len(data)}; bin_.extend(data)
    if target: v['target'] = target
    if stride: v['byteStride'] = stride
    views.append(v); return len(views) - 1

used_tex = set()
for r in merged:
    P, N, UV, I = r['P'], r['N'], r['UV'], r['I'].reshape(-1)
    lo, hi = P.min(0), P.max(0); ext = np.maximum(hi - lo, 1e-9)
    q = np.zeros((len(P), 4), np.int16); q[:, :3] = np.round((P - lo) / ext * 65535 - 32768)
    accessors.append({'bufferView': add_view(q.tobytes(), 34962, 8), 'componentType': 5122, 'count': len(P), 'type': 'VEC3',
                      'min': q[:, :3].min(0).tolist(), 'max': q[:, :3].max(0).tolist()})
    attrs = {'POSITION': len(accessors) - 1}
    if N is not None:
        n8 = np.zeros((len(N), 4), np.int8); n8[:, :3] = np.clip(np.round(N * 127), -127, 127)
        accessors.append({'bufferView': add_view(n8.tobytes(), 34962, 4), 'componentType': 5120, 'normalized': True, 'count': len(N), 'type': 'VEC3'})
        attrs['NORMAL'] = len(accessors) - 1
    if UV is not None:
        accessors.append({'bufferView': add_view(UV.astype(np.float32).tobytes(), 34962), 'componentType': 5126, 'count': len(UV), 'type': 'VEC2'})
        attrs['TEXCOORD_0'] = len(accessors) - 1
    big = len(P) > 65535
    accessors.append({'bufferView': add_view(I.astype(np.uint32 if big else np.uint16).tobytes(), 34963), 'componentType': 5125 if big else 5123, 'count': len(I), 'type': 'SCALAR'})
    prim = {'attributes': attrs, 'indices': len(accessors) - 1}
    if r['mat'] is not None: prim['material'] = r['mat']
    meshes.append({'name': mname(r['mat']), 'primitives': [prim]})
    sc = ext / 65535.0
    nodes.append({'name': mname(r['mat']), 'mesh': len(meshes) - 1, 'translation': (lo + 32768 * sc).tolist(), 'scale': sc.tolist()})

# materials: drop references to textures we no longer carry (texCoord 1, unused); collect used images
mats = json.loads(json.dumps(g['materials']))
def visit_tex(obj):
    if isinstance(obj, dict):
        if 'index' in obj and set(obj) <= {'index', 'texCoord', 'scale', 'strength', 'extensions'}:
            obj.pop('texCoord', None); used_tex.add(obj['index'])
        for v in obj.values(): visit_tex(v)
    elif isinstance(obj, list):
        for v in obj: visit_tex(v)
used_mats = {r['mat'] for r in merged}
for i, m in enumerate(mats):
    if i in used_mats: visit_tex(m)
images, textures, tex_map, img_map = [], [], {}, {}
for ti in sorted(used_tex):
    t = g['textures'][ti]; src_i = t['source']
    if src_i not in img_map:
        path = os.path.join(base, g['images'][src_i]['uri']); data, mime = open(path, 'rb').read(), 'image/png'
        if len(data) > 200_000:
            info = subprocess.run(['sips', '-g', 'hasAlpha', path], capture_output=True, text=True).stdout
            if 'hasAlpha: no' in info:
                with tempfile.TemporaryDirectory() as td:
                    jp = os.path.join(td, 'x.jpg')
                    subprocess.run(['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '82', path, '--out', jp], capture_output=True)
                    data, mime = open(jp, 'rb').read(), 'image/jpeg'
        img_map[src_i] = len(images); images.append({'bufferView': add_view(data), 'mimeType': mime})
    tex_map[ti] = len(textures); textures.append({'source': img_map[src_i], **({'sampler': t['sampler']} if 'sampler' in t else {})})
def remap(obj):
    if isinstance(obj, dict):
        if 'index' in obj and obj['index'] in tex_map and set(obj) <= {'index', 'scale', 'strength', 'extensions'}: obj['index'] = tex_map[obj['index']]
        for v in obj.values(): remap(v)
    elif isinstance(obj, list):
        for v in obj: remap(v)
remap(mats)

out = {'asset': {'version': '2.0', 'generator': 'gltf_optimize'}, 'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}],
       'nodes': nodes, 'meshes': meshes, 'materials': mats, 'accessors': accessors, 'bufferViews': views,
       'buffers': [{'byteLength': 0}],
       'extensionsUsed': sorted(set(['KHR_mesh_quantization']) | set(g.get('extensionsUsed', []))),
       'extensionsRequired': ['KHR_mesh_quantization']}
if images: out['images'] = images; out['textures'] = textures; out['samplers'] = g.get('samplers', [{}])
while len(bin_) % 4: bin_.append(0)
out['buffers'][0]['byteLength'] = len(bin_)
js = json.dumps(out, separators=(',', ':')).encode()
while len(js) % 4: js += b' '
with open(args.out, 'wb') as f:
    f.write(struct.pack('<III', 0x46546C67, 2, 28 + len(js) + len(bin_)))
    f.write(struct.pack('<II', len(js), 0x4E4F534A)); f.write(js)
    f.write(struct.pack('<II', len(bin_), 0x004E4942)); f.write(bin_)
print('wrote', args.out, 28 + len(js) + len(bin_), 'bytes')
