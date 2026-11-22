"""Minimal USDA -> GLB converter for the Sketchfab-exported bike model.

Handles: nested Xforms (matrix4d transform, translate / rotateXYZ / scale ops), triangle meshes with
vertex-interpolated normals, UsdPreviewSurface colors (diffuse, roughness, metallic).
Skips skinned meshes. Geometry is baked to world space; positions are quantized to int16
(KHR_mesh_quantization) and normals to int8 to keep the file small.

usage: python3 usda2glb.py scene.usda out.glb
"""
import json, re, struct, sys
import numpy as np

src, out = sys.argv[1], sys.argv[2]
text = open(src).read().split('\n')

num = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
def floats(s): return [float(x) for x in re.findall(num, s)]

def mat_rows(s):
    v = floats(s); return np.array(v[:16], dtype=np.float64).reshape(4, 4)

def rot(axis, deg):
    a = np.radians(deg); c, s = np.cos(a), np.sin(a); m = np.eye(4)
    i, j = {'x': (1, 2), 'y': (2, 0), 'z': (0, 1)}[axis]
    # row-vector convention (p' = p @ M)
    m[i, i] = c; m[i, j] = s; m[j, i] = -s; m[j, j] = c
    return m

materials, meshes, skel = {}, [], {}
stack = []            # entries: dict(kind, name, ops, attrs)
pending = None
cur_mat = None
i = 0
while i < len(text):
    line = text[i].strip()
    m = re.match(r'def (\w+) "([^"]+)"', line)
    if m:
        pending = {'kind': m.group(1), 'name': m.group(2), 'ops': {}, 'order': [], 'attrs': {}}
        if m.group(1) == 'Material': cur_mat = m.group(2); materials.setdefault(cur_mat, {})
    elif line == '{':
        stack.append(pending if pending else {'kind': 'block', 'name': '', 'ops': {}, 'order': [], 'attrs': {}})
        pending = None
    elif line.endswith('{'):   # value blocks such as `timeSamples = {` or dictionaries
        tm = re.match(r'\w+ xformOp:(\w+)\.timeSamples = \{', line)
        ta = re.match(r'\S+ (\w+)\.timeSamples = \{', line)
        stack.append({'kind': 'value', 'name': '', 'ops': {}, 'order': [], 'attrs': {}, 'owner': stack[-1] if stack else None,
                      'sampleOp': tm.group(1) if tm else None, 'sampleAttr': None if tm or not ta else ta.group(1)})
    elif stack and stack[-1].get('sampleAttr') and line != '}' and ':' in line:   # animated attribute: keep the last sample
        blk = stack[-1]; blk['owner']['attrs']['sample_' + blk['sampleAttr']] = line.split(':', 1)[1]   # last sample wins
    elif stack and stack[-1].get('sampleOp') and line != '}' and ':' in line:   # animated value: keep the last sample (the bike at rest)
        blk = stack[-1]; val = line.split(':', 1)[1]
        blk['owner']['ops'][blk['sampleOp']] = mat_rows(val) if blk['sampleOp'] == 'transform' else floats(val)   # last sample = settled pose
    elif line == '}':
        node = stack.pop()
        if node['kind'] == 'Mesh':
            meshes.append((node, list(stack)))   # keep the parent chain to build the world matrix later
    elif stack:
        node = stack[-1]
        xm = re.match(r'\w+ xformOp:(\w+) = (.*)', line)
        if xm:
            op, val = xm.group(1), xm.group(2)
            node['ops'][op] = mat_rows(val) if op == 'transform' else floats(val)
        elif line.startswith('uniform token[] xformOpOrder'): node['order'] = re.findall(r'xformOp:(\w+)', line)
        elif cur_mat and line.startswith('color3f inputs:diffuseColor ='): materials[cur_mat]['color'] = floats(line.split('=', 1)[1])
        elif cur_mat and line.startswith('float inputs:roughness ='): materials[cur_mat]['rough'] = floats(line.split('=', 1)[1])[0]
        elif cur_mat and line.startswith('float inputs:metallic ='): materials[cur_mat]['metal'] = floats(line.split('=', 1)[1])[0]
        elif node['kind'] in ('Skeleton', 'SkelAnimation') and re.match(r'uniform (token|matrix4d)\[\] (\w+) = ', line):
            km = re.match(r'uniform (token|matrix4d)\[\] (\w+) = (.*)', line); node['attrs'][km.group(2)] = km.group(3)
            if node['kind'] == 'Skeleton': skel['node'] = node
            else: skel['anim'] = node
        elif node['kind'] == 'Mesh':
            for key, pat in [('points', 'point3f[] points ='), ('normals', 'normal3f[] normals ='),
                             ('idx', 'int[] faceVertexIndices ='), ('counts', 'int[] faceVertexCounts ='),
                             ('skin', 'int[] primvars:skel:jointIndices')]:
                if line.startswith(pat): node['attrs'][key] = line.split('=', 1)[1]
            mm = re.match(r'rel material:binding = <.*/Materials/([^>]+)>', line)
            if mm: node['attrs']['mat'] = mm.group(1)
    i += 1

def local_matrix(n):
    ops, M = n['ops'], np.eye(4)
    for op in reversed(n['order']):          # row-vector: last op applies first
        if op == 'transform': L = ops['transform']
        elif op == 'translate': L = np.eye(4); L[3, :3] = ops['translate']
        elif op == 'scale': L = np.diag(list(ops['scale']) + [1.0])
        elif op == 'rotateXYZ':
            x, y, z = ops['rotateXYZ']; L = rot('x', x) @ rot('y', y) @ rot('z', z)
        else: continue
        M = M @ L
    return M

def quat_rowmat(w, x, y, z):
    R = np.array([[1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
                  [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                  [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])
    return R.T   # row-vector convention

def skeleton_matrices():
    sk, an = skel['node']['attrs'], skel.get('anim', {}).get('attrs', {})
    joints = re.findall(r'"([^"]+)"', sk['joints'])
    mats = lambda s: [np.array(v, dtype=np.float64).reshape(4, 4) for v in np.array(floats(s)).reshape(-1, 16)]
    bind, rest = mats(sk['bindTransforms']), mats(sk['restTransforms'])
    local = list(rest)
    if an and 'sample_rotations' in an:
        aj = re.findall(r'"([^"]+)"', an['joints'])
        T = np.array(floats(an['sample_translations'])).reshape(-1, 3)
        Q = np.array(floats(an['sample_rotations'])).reshape(-1, 4)
        S = np.array(floats(an['sample_scales'])).reshape(-1, 3) if 'sample_scales' in an else np.ones((len(aj), 3))
        for k, name in enumerate(aj):
            L = np.eye(4); L[:3, :3] = np.diag(S[k]) @ quat_rowmat(*Q[k]); L[3, :3] = T[k]
            local[joints.index(name)] = L
    J = [None] * len(joints)
    for j, name in enumerate(joints):          # parents always precede children in USD joint order
        parent = name.rsplit('/', 1)[0] if '/' in name else None
        J[j] = local[j] @ J[joints.index(parent)] if parent else local[j]
    return [np.linalg.inv(bind[j]) @ J[j] for j in range(len(joints))]

SKIN = skeleton_matrices() if 'node' in skel else None
prims = []
for node, chain in meshes:
    a = node['attrs']
    # skinned parts (fenders / number plates) are kept in their bind pose
    counts = np.array(floats(a['counts']), dtype=np.int64)
    if not np.all(counts == 3): print('non-triangle mesh skipped', node['name']); continue
    P = np.array(floats(a['points']), dtype=np.float64).reshape(-1, 3)
    N = np.array(floats(a['normals']), dtype=np.float64).reshape(-1, 3)
    I = np.array(floats(a['idx']), dtype=np.int64)
    if len(N) != len(P): print('normals not per-vertex, recomputing', node['name']); N = None
    W = local_matrix(node)
    for n in reversed(chain): W = W @ local_matrix(n)
    if 'skin' in a and SKIN:   # move each vertex by its joint (single joint, weight 1)
        J = np.array(floats(a['skin']), dtype=np.int64)
        for j in np.unique(J):
            sel = J == j; Mj = SKIN[j]
            P[sel] = (np.c_[P[sel], np.ones(sel.sum())] @ Mj)[:, :3]
            N[sel] = N[sel] @ np.linalg.inv(Mj[:3, :3]).T
    P = np.c_[P, np.ones(len(P))] @ W
    P = P[:, :3]
    if N is not None:
        N = N @ np.linalg.inv(W[:3, :3]).T   # row-vector normals use the inverse-transpose
        N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
    if np.linalg.det(W[:3, :3]) < 0: I = I.reshape(-1, 3)[:, ::-1].reshape(-1)
    prims.append({'name': node['name'], 'mat': a.get('mat'), 'P': P, 'N': N, 'I': I})

# normalize to the convention of the other bikes: meters, front = +z, up = +y, ground at y = 0
for p in prims:
    P = p['P'] / 100.0
    p['P'] = np.c_[-P[:, 2], P[:, 1], P[:, 0]]          # rotate +90 deg about Y: front (+x) -> +z
    if p['N'] is not None: p['N'] = np.c_[-p['N'][:, 2], p['N'][:, 1], p['N'][:, 0]]
ground = min(p['P'][:, 1].min() for p in prims)
for p in prims: p['P'][:, 1] -= ground
allP = np.vstack([p['P'] for p in prims])
lo, hi = allP.min(0), allP.max(0)
print('raw bbox', lo.round(3), hi.round(3))

# ---- write GLB ----
mat_names = sorted({p['mat'] for p in prims if p['mat']})
gl_mats = []
for name in mat_names:
    m = materials.get(name, {})
    c = m.get('color', [0.8, 0.8, 0.8])
    gl_mats.append({'name': name, 'doubleSided': True, 'pbrMetallicRoughness': {
        'baseColorFactor': [c[0], c[1], c[2], 1], 'metallicFactor': m.get('metal', 0.0), 'roughnessFactor': max(0.05, m.get('rough', 0.5))}})

bin_ = bytearray(); views, accessors, gl_meshes, nodes = [], [], [], []
def add_view(data, target=None):
    while len(bin_) % 4: bin_.append(0)
    off = len(bin_); bin_.extend(data)
    v = {'buffer': 0, 'byteOffset': off, 'byteLength': len(data)}
    if target: v['target'] = target
    views.append(v); return len(views) - 1

for p in prims:
    P, N, I = p['P'], p['N'], p['I']
    pmin, pmax = P.min(0), P.max(0); ext = np.maximum(pmax - pmin, 1e-9)
    q = np.round((P - pmin) / ext * 65535 - 32768).astype(np.int16)          # int16 per component
    q4 = np.zeros((len(q), 4), np.int16); q4[:, :3] = q                         # pad to 8 bytes (alignment)
    pv = add_view(q4.tobytes(), 34962); views[pv]['byteStride'] = 8
    accessors.append({'bufferView': pv, 'componentType': 5122, 'count': len(q), 'type': 'VEC3',
                      'min': [int(x) for x in q.min(0)], 'max': [int(x) for x in q.max(0)]})
    attrs = {'POSITION': len(accessors) - 1}
    if N is not None:
        n8 = np.zeros((len(N), 4), np.int8); n8[:, :3] = np.clip(np.round(N * 127), -127, 127)
        nv = add_view(n8.tobytes(), 34962); views[nv]['byteStride'] = 4
        accessors.append({'bufferView': nv, 'componentType': 5120, 'normalized': True, 'count': len(N), 'type': 'VEC3'})
        attrs['NORMAL'] = len(accessors) - 1
    big = len(P) > 65535
    iv = add_view(I.astype(np.uint32 if big else np.uint16).tobytes(), 34963)
    accessors.append({'bufferView': iv, 'componentType': 5125 if big else 5123, 'count': len(I), 'type': 'SCALAR'})
    prim = {'attributes': attrs, 'indices': len(accessors) - 1}
    if p['mat']: prim['material'] = mat_names.index(p['mat'])
    gl_meshes.append({'name': p['name'], 'primitives': [prim]})
    # node transform undoes the quantization: x = q * s + t
    s = ext / 65535.0; t = pmin + 32768 * s
    nodes.append({'name': p['name'], 'mesh': len(gl_meshes) - 1, 'translation': t.tolist(), 'scale': s.tolist()})

gltf = {'asset': {'version': '2.0', 'generator': 'usda2glb'}, 'extensionsUsed': ['KHR_mesh_quantization'],
        'extensionsRequired': ['KHR_mesh_quantization'], 'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}],
        'nodes': nodes, 'meshes': gl_meshes, 'materials': gl_mats, 'accessors': accessors, 'bufferViews': views,
        'buffers': [{'byteLength': len(bin_)}]}
while len(bin_) % 4: bin_.append(0)
js = json.dumps(gltf, separators=(',', ':')).encode()
while len(js) % 4: js += b' '
with open(out, 'wb') as f:
    f.write(struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
    f.write(struct.pack('<II', len(js), 0x4E4F534A)); f.write(js)
    f.write(struct.pack('<II', len(bin_), 0x004E4942)); f.write(bin_)
print('meshes', len(prims), 'materials', mat_names, 'bytes', 12 + 8 + len(js) + 8 + len(bin_))
