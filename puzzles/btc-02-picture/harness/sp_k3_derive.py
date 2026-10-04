"""pyopencl wrapper for K3 (BIP32 walk + both-form hash160 + target compare)."""
import numpy as np
import pyopencl as cl

from sp_gpu import BASE_CRYPTO


class DeriveKernel:
    def __init__(self, gpu, space, targets, hit_cap=256):
        self.gpu, self.space, self.hit_cap = gpu, space, hit_cap
        self.npaths = len(space.paths)
        prog = gpu.build(BASE_CRYPTO + ["sp_derive"])
        self.k_hit = cl.Kernel(prog, "sp_derive")
        self.k_dump = cl.Kernel(prog, "sp_derive_dump")
        self.paths = gpu.ro(space.path_table())
        self.last_slice = None      # (surv_off, nsurv) exactly as the last run() was handed them
        self.set_targets(targets)
        self.hits = gpu.rw(4 * (1 + hit_cap * 8))

    def set_targets(self, targets):
        """Replace the whole target list (the run sets [prize, this block's plant] in a per-block order before every
        block). The uploaded buffer is read back and must equal the request byte for byte (RuntimeError otherwise).
        The hit buffer and hit_cap are kept; a refused or failed list leaves the previous one in place."""
        targets = list(targets)
        if not targets or any(len(t) != 20 for t in targets):
            raise ValueError("targets must be a non-empty list of 20-byte hash160s")
        data = b"".join(bytes(t) for t in targets)
        buf = self.gpu.ro(np.frombuffer(data, dtype=np.uint8))
        back = bytes(self.gpu.read(buf, np.uint8, len(data)))
        if back != data:
            bad = next(i for i in range(len(data)) if back[i:i + 1] != data[i:i + 1])
            raise RuntimeError(f"the device holds different targets than were uploaded (first difference at byte "
                               f"{bad} of {len(data)}, target #{bad // 20})")
        self.targets, self.ntgt = buf, len(targets)

    def run(self, seeds_buf, surv_buf, surv_off, nsurv, npass):
        """Hits over survivors [surv_off, surv_off + nsurv) x npass. The slice received is recorded in `last_slice`
        (plain ints, before anything else): the caller compares it with the tile it meant to hand over."""
        self.last_slice = (int(surv_off), int(nsurv))
        nthreads = nsurv * npass
        if nthreads == 0:
            return []
        g = self.gpu
        g.zero_u32(self.hits, 1)
        self.k_hit.set_args(seeds_buf, surv_buf, np.uint32(surv_off), np.uint32(npass), np.uint32(nthreads),
                            self.paths, np.uint32(self.npaths), self.targets, np.uint32(self.ntgt),
                            self.hits, np.uint32(self.hit_cap))
        g.launch(self.k_hit, nthreads)
        count = int(g.read(self.hits, np.uint32, 1)[0])
        if count > self.hit_cap:
            raise RuntimeError(f"hit buffer overflow: {count} hits > cap {self.hit_cap}")
        if count == 0:
            return []
        rec = g.read(self.hits, np.uint32, 1 + self.hit_cap * 8)[1:1 + count * 8].reshape(count, 8)
        return [(int(r[0]) | (int(r[1]) << 32), int(r[2]), int(r[3]), int(r[4]), int(r[5])) for r in rec]

    def dump(self, seeds):
        g = self.gpu
        n = len(seeds)
        buf = g.ro(np.ascontiguousarray(seeds, dtype=np.uint64))
        out = g.rw(n * self.npaths * 40)
        self.k_dump.set_args(buf, np.uint32(n), self.paths, np.uint32(self.npaths), out)
        g.launch(self.k_dump, n)
        return g.read(out, np.uint8, n * self.npaths * 40).reshape(n, self.npaths, 2, 20)
