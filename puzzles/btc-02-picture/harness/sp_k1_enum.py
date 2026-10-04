"""pyopencl wrapper for K1 (enumerate + filter + compact).

pyopencl is imported inside EnumKernel, so expected_idx_sum() is usable (and testable) without OpenCL."""
import numpy as np

NCTR = 4          # uint32 device counters: scanned, tested, valid, slot (survivor-store cursor); the index sum is
                  # a separate 64-bit device word (EnumKernel.isum)


def expected_idx_sum(start, count):
    """What K1's index accumulator must hold after scanning exactly [start, start + count): the sum of the indices
    mod 2**64 (the accumulator is a 64-bit atomic). A thread-count check alone cannot tell that range from a shifted
    one, and a 32-bit sum could not see a shift k with count*k = 0 mod 2**32 or a start cut to 32 bits."""
    return (count * start + count * (count - 1) // 2) % (1 << 64)


class EnumKernel:
    def __init__(self, gpu, space, cap=1 << 22, mode=None):
        import pyopencl as cl
        self.gpu, self.space, self.cap = gpu, space, cap
        prog = gpu.build(["common", "sha2", "sp_sha512", "sp_decode", "sp_enum"])
        self.knl = cl.Kernel(prog, "sp_enum")
        tokb, tokl = space.token_tables()
        self.d, self.tokb, self.tokl = gpu.ro(space.descriptor(mode)), gpu.ro(tokb), gpu.ro(tokl)
        self.surv = gpu.rw(cap * 8)
        self.ctr = gpu.rw(4 * NCTR)
        self.isum = gpu.rw(8)
        self.last_seconds = 0.0
        self.last_idx_sum = None        # the device's index sum of the last launch (an int; None until one ran)

    def run(self, start, count):
        """(scanned, tested, valid) for [start, start + count). Raises RuntimeError unless the threads that ran
        covered exactly that range (exact 64-bit index sum) and the survivor cursor agrees with `valid`. The sum the
        device reported is left in `last_idx_sum` (also when it disagreed)."""
        self.last_idx_sum = None
        if not (0 <= start and 0 <= count < (1 << 32) and start + count <= self.space.total):
            raise ValueError(f"range [{start}, {start + count}) is outside the space of {self.space.total} "
                             f"candidates (or one launch exceeds 2**32; the device counters are uint32)")
        g = self.gpu
        g.zero_u32(self.ctr, NCTR)
        g.zero_u32(self.isum, 2)
        self.knl.set_args(np.uint64(start), np.uint64(count), self.d, self.tokb, self.tokl,
                          self.surv, np.uint32(self.cap), self.ctr, self.isum)
        self.last_seconds = g.launch(self.knl, count)
        scanned, tested, valid, slots = (int(x) for x in g.read(self.ctr, np.uint32, NCTR))
        isum = self.last_idx_sum = int(g.read(self.isum, np.uint64, 1)[0])
        want = expected_idx_sum(start, count)
        if isum != want:
            raise RuntimeError(f"K1 index checksum (exact 64-bit sum) {isum:#018x} != {want:#018x} for "
                               f"[{start:,}, {start + count:,}): the kernel did not scan exactly that range (it "
                               f"reported scanned={scanned:,})")
        if slots != valid:
            raise RuntimeError(f"K1 survivor cursor {slots:,} != valid {valid:,} for [{start:,}, {start + count:,})")
        return scanned, tested, valid

    def survivors(self, n):
        k = min(int(n), self.cap)
        if k <= 0:
            return np.zeros(0, np.uint64)
        return self.gpu.read(self.surv, np.uint64, k)          # only the stored prefix, not all of `cap`
