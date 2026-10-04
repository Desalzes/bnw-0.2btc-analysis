"""OpenCL device, program cache and launch helper for the GPU set+perm engine.

Launches always finish() and return wall seconds so the caller can size them to
~0.3 s: this GPU also drives the desktop and has a kernel watchdog on."""
import os
import time

import numpy as np
import pyopencl as cl

import sp_space

HERE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.path.join(HERE, "kernels")
BASE_CRYPTO = ["common", "ripemd", "sha2", "secp256k1_common", "secp256k1_scalar", "secp256k1_field",
               "secp256k1_group", "secp256k1_prec", "secp256k1", "address"]


class Gpu:
    _inst = None

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def __init__(self):
        plat = next(p for p in cl.get_platforms() if "NVIDIA" in p.name)
        self.dev = plat.get_devices()[0]
        self.ctx = cl.Context([self.dev])
        self.q = cl.CommandQueue(self.ctx)
        self._progs = {}
        self._wg = {}

    def build(self, files):
        key = tuple(files)
        if key not in self._progs:
            src = sp_space.cl_defines() + "\n" + "\n".join(
                open(os.path.join(KDIR, f + ".cl"), encoding="utf-8").read() for f in files)
            self._progs[key] = cl.Program(self.ctx, src).build(options="-cl-std=CL1.2")
        return self._progs[key]

    def ro(self, arr):
        return cl.Buffer(self.ctx, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR,
                         hostbuf=np.ascontiguousarray(arr))

    def rw(self, nbytes):
        return cl.Buffer(self.ctx, cl.mem_flags.READ_WRITE, max(4, int(nbytes)))

    def zero_u32(self, buf, n_words):
        cl.enqueue_copy(self.q, buf, np.zeros(n_words, np.uint32))

    def read(self, buf, dtype, count):
        out = np.zeros(count, dtype)
        cl.enqueue_copy(self.q, out, buf)
        self.q.finish()
        return out

    def local_size(self, knl):
        key = knl.function_name
        if key not in self._wg:
            self._wg[key] = min(256, knl.get_work_group_info(cl.kernel_work_group_info.WORK_GROUP_SIZE, self.dev))
        return self._wg[key]

    def launch(self, knl, n):
        n = max(1, int(n))
        local = self.local_size(knl)
        g = -(-n // local) * local
        t0 = time.perf_counter()
        cl.enqueue_nd_range_kernel(self.q, knl, (g,), (local,))
        self.q.finish()
        return time.perf_counter() - t0
