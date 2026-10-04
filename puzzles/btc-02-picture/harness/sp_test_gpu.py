import unittest

from sp_testutil import require_gpu


@require_gpu
class GpuContext(unittest.TestCase):
    def test_shared_kernel_library_compiles(self):
        from sp_gpu import Gpu
        gpu = Gpu.get()
        prog = gpu.build(["sp_sha512", "sp_decode"])       # library only; no kernels yet
        self.assertIsNotNone(prog)
        self.assertIs(gpu.build(["sp_sha512", "sp_decode"]), prog)   # cached

    def test_launch_pads_and_reports_seconds(self):
        import numpy as np
        import pyopencl as cl
        from sp_gpu import Gpu
        gpu = Gpu.get()
        src = "__kernel void inc(__global uint *c, ulong n){ if (get_global_id(0) < n) atomic_inc(c); }"
        knl = cl.Kernel(cl.Program(gpu.ctx, src).build(), "inc")
        buf = gpu.rw(4)
        gpu.zero_u32(buf, 1)
        knl.set_args(buf, np.uint64(1000))
        dt = gpu.launch(knl, 1000)                       # 1000 is not a multiple of 256
        self.assertGreater(dt, 0)
        self.assertEqual(int(gpu.read(buf, np.uint32, 1)[0]), 1000)


if __name__ == "__main__":
    unittest.main()
