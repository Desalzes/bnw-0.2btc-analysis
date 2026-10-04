"""pyopencl wrapper for K2 (PBKDF2-HMAC-SHA512)."""
import numpy as np
import pyopencl as cl


class Pbkdf2Kernel:
    def __init__(self, gpu, space, max_threads=1 << 20):
        self.gpu, self.space, self.max_threads = gpu, space, max_threads
        self.npass = len(space.passes)
        prog = gpu.build(["sp_sha512", "sp_decode", "sp_pbkdf2"])
        self.knl = cl.Kernel(prog, "sp_pbkdf2")
        tokb, tokl = space.token_tables()
        self.d, self.tokb, self.tokl = gpu.ro(space.descriptor()), gpu.ro(tokb), gpu.ro(tokl)
        self.salts = gpu.ro(space.salt_words())
        self.seeds = gpu.rw(max_threads * 64)
        self.last_slice = None      # (surv_off, nsurv) exactly as the last launch() was handed them

    def launch(self, surv_buf, surv_off, nsurv):
        """Run K2 over survivors [surv_off, surv_off + nsurv). The slice received is recorded in `last_slice` (plain
        ints, before anything is validated): the caller compares it with the tile it meant to hand over."""
        self.last_slice = (int(surv_off), int(nsurv))
        nthreads = nsurv * self.npass
        if nthreads == 0:
            return 0.0
        if not 0 < nthreads <= self.max_threads:
            raise ValueError(f"{nthreads} threads does not fit the seed buffer ({self.max_threads})")
        if surv_off < 0 or (surv_off + nsurv) * 8 > surv_buf.size:
            raise ValueError("survivor slice lies outside the survivor buffer")
        self.knl.set_args(surv_buf, np.uint32(surv_off), np.uint32(self.npass), np.uint32(nthreads),
                          self.d, self.tokb, self.tokl, self.salts, self.seeds)
        return self.gpu.launch(self.knl, nthreads)

    def run_np(self, surv):
        surv = np.ascontiguousarray(surv, dtype=np.uint64)
        sbuf = self.gpu.ro(surv)
        self.launch(sbuf, 0, len(surv))
        n = len(surv) * self.npass
        words = self.gpu.read(self.seeds, np.uint64, n * 8).reshape(n, 8)
        return np.frombuffer(words.astype(">u8").tobytes(), dtype=np.uint8).reshape(n, 64)
