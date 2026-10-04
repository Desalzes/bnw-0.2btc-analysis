// index -> candidate tokens. Mirrors sp_space.Space.decode_ids exactly.
// D layout: see sp_space.LAYOUT (the SP_* defines are prepended by the host).

static void sp_decode(ulong idx, __global const ulong *D, ushort *tok) {
  int r = (int)D[SP_D_R], m = (int)D[SP_D_M], nalt = (int)D[SP_D_NALT];
  ulong per_unit = D[SP_D_PERUNIT], span = D[SP_D_SPAN], npool = D[SP_D_NPOOL];
  ulong unit = idx / per_unit;
  ulong within = idx - unit * per_unit;
  ulong a = within / span;
  ulong f = within - a * span;

  // FIX alternatives, FIX-file order, last one fastest
  for (int i = nalt - 1; i >= 0; i--) {
    ulong rad = D[SP_O_ALTRAD + i];
    ulong dig = a % rad;
    a /= rad;
    tok[D[SP_O_ALTSLOT + i]] = (ushort)D[SP_O_ALTTOK + D[SP_O_ALTOFF + i] + dig];
  }
  // REQUIRE placement: partial-Lehmer decode of `unit` over the free slots
  uchar avail[SP_NMAX];
  int navail = m;
  for (int i = 0; i < m; i++) avail[i] = (uchar)D[SP_O_FREE + i];
  ulong rem = unit;
  for (int j = 0; j < r; j++) {
    ulong w = D[SP_O_PW + j];
    int sel = (int)(rem / w);
    rem -= (ulong)sel * w;
    tok[avail[sel]] = (ushort)D[SP_O_REQ + j];
    for (int q = sel; q < navail - 1; q++) avail[q] = avail[q + 1];
    navail--;
  }
  // POOL over the unplaced free slots (avail[] is still ascending), last one fastest
  for (int t = navail - 1; t >= 0; t--) {
    ulong dig = f % npool;
    f /= npool;
    tok[avail[t]] = (ushort)D[SP_O_POOL + dig];
  }
}

static bool sp_distinct(const ushort *tok, int n) {
  for (int i = 0; i < n; i++)
    for (int j = i + 1; j < n; j++)
      if (tok[i] == tok[j]) return false;
  return true;
}
