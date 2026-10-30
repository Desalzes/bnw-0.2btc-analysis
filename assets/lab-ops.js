// The picture page's image operations, pure functions over typed arrays.
(function (root) {
  "use strict";

  const luma = (r, g, b) => 0.299 * r + 0.587 * g + 0.114 * b;

  // One float per pixel from an RGBA buffer. `fn(r, g, b, a)`.
  function scalar(rgba, fn) {
    const n = rgba.length / 4, out = new Float32Array(n);
    for (let i = 0, j = 0; i < n; i++, j += 4) out[i] = fn(rgba[j], rgba[j + 1], rgba[j + 2], rgba[j + 3]);
    return out;
  }

  const VIEWS = {
    red: (r) => r,
    green: (r, g) => g,
    blue: (r, g, b) => b,
    grey: luma,
    saturation: (r, g, b) => Math.max(r, g, b) - Math.min(r, g, b),
    redblue: (r, g, b) => r - b,
    greenopp: (r, g, b) => g - (r + b) / 2,
  };

  // The 1st and 99th percentiles, as numpy.percentile's linear interpolation. A large input is thinned first.
  function percentiles(values, limit = 400000) {
    const step = Math.max(1, Math.floor(values.length / limit));
    const sample = new Float32Array(Math.ceil(values.length / step));
    for (let i = 0, j = 0; i < values.length; i += step, j++) sample[j] = values[i];
    sample.sort();
    const at = (p) => {
      const k = (sample.length - 1) * p, lo = Math.floor(k), hi = Math.min(lo + 1, sample.length - 1);
      return sample[lo] + (sample[hi] - sample[lo]) * (k - lo);
    };
    return [at(0.01), at(0.99)];
  }

  // A rectangle of a width x height field, as a flat copy.
  function region(values, width, x0, y0, x1, y1) {
    const out = new Float32Array((x1 - x0) * (y1 - y0));
    for (let y = y0, k = 0; y < y1; y++) for (let x = x0; x < x1; x++) out[k++] = values[y * width + x];
    return out;
  }

  // Grey RGBA from floats: lo maps to 0 and hi to 255. `invalid`, when given, marks pixels painted a flat colour.
  function grey(values, lo, hi, invalid) {
    const out = new Uint8ClampedArray(values.length * 4), span = Math.max(hi - lo, 1e-6);
    for (let i = 0, j = 0; i < values.length; i++, j += 4) {
      if (invalid && invalid[i]) { out[j] = 60; out[j + 1] = 66; out[j + 2] = 74; out[j + 3] = 255; continue; }
      const v = (values[i] - lo) * 255 / span;
      out[j] = out[j + 1] = out[j + 2] = v;
      out[j + 3] = 255;
    }
    return out;
  }

  function negative(rgba) {
    const out = new Uint8ClampedArray(rgba.length);
    for (let j = 0; j < rgba.length; j += 4) {
      out[j] = 255 - rgba[j]; out[j + 1] = 255 - rgba[j + 1]; out[j + 2] = 255 - rgba[j + 2]; out[j + 3] = 255;
    }
    return out;
  }

  function bitPlane(rgba, channel, bit) {
    const n = rgba.length / 4, out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = (rgba[i * 4 + channel] >> bit) & 1 ? 255 : 0;
    return out;
  }

  // Widths of three box filters whose total variance is closest to sigma squared (Kovesi 2010).
  function boxWidths(sigma) {
    let wl = Math.floor(Math.sqrt(4 * sigma * sigma + 1));
    if (wl % 2 === 0) wl -= 1;
    wl = Math.max(wl, 1);
    const m = Math.round((3 * (wl * wl + 4 * wl + 3) - 12 * sigma * sigma) / (4 * wl + 4));
    return [0, 1, 2].map((i) => (i < m ? wl : wl + 2));
  }

  const mirror = (i, n) => {
    if (n === 1) return 0;
    const period = 2 * n - 2;
    const k = ((i % period) + period) % period;
    return k < n ? k : period - k;
  };

  function boxLine(src, dst, start, stride, n, width) {
    const r = (width - 1) / 2, norm = 1 / width;
    let sum = 0;
    for (let i = -r; i <= r; i++) sum += src[start + mirror(i, n) * stride];
    for (let i = 0; i < n; i++) {
      dst[start + i * stride] = sum * norm;
      const come = i + r + 1, gone = i - r;
      sum += src[start + (come < n ? come : mirror(come, n)) * stride] - src[start + (gone >= 0 ? gone : mirror(gone, n)) * stride];
    }
  }

  // A Gaussian of this sigma, by three box passes along each axis, with mirrored edges.
  function blur(values, width, height, sigma) {
    let a = Float32Array.from(values), b = new Float32Array(values.length);
    for (const w of boxWidths(sigma)) {
      if (w < 2) continue;
      for (let y = 0; y < height; y++) boxLine(a, b, y * width, 1, width, w);
      [a, b] = [b, a];
      for (let x = 0; x < width; x++) boxLine(a, b, x, width, height, w);
      [a, b] = [b, a];
    }
    return a;
  }

  function highPass(values, width, height, sigma) {
    const low = blur(values, width, height, sigma), out = new Float32Array(values.length);
    for (let i = 0; i < out.length; i++) out[i] = values[i] - low[i];
    return out;
  }

  // Keep paper and leave the art out: dark pixels are filled from their surroundings before the blur, then shown as flat.
  function paperHighPass(values, width, height, dark, sigma, gain) {
    const w = new Float32Array(values.length), vw = new Float32Array(values.length);
    for (let i = 0; i < values.length; i++) {
      w[i] = values[i] < dark ? 0 : 1;
      vw[i] = values[i] * w[i];
    }
    const num = blur(vw, width, height, 15), den = blur(w, width, height, 15);
    const filled = Float32Array.from(values);
    for (let i = 0; i < filled.length; i++) if (!w[i]) filled[i] = num[i] / (den[i] + 1e-6);
    const bg = blur(filled, width, height, sigma), out = new Float32Array(values.length);
    for (let i = 0; i < out.length; i++) out[i] = w[i] ? (filled[i] - bg[i]) * gain + 128 : 128;
    return out;
  }

  // Sliding-window percentile of a field quantised into `bins`, with mirrored edges (scipy.ndimage.percentile_filter).
  function percentileFilter(values, width, height, win, pct, bins, top) {
    const rank = Math.floor(win * win * pct / 100), r = (win - 1) / 2, scale = (bins - 1) / top;
    const q = new Uint16Array(values.length), out = new Float32Array(values.length);
    for (let i = 0; i < q.length; i++) q[i] = Math.min(bins - 1, Math.max(0, Math.round(values[i] * scale)));
    const mirrorOut = (i, n) => (i < 0 ? -i - 1 : i >= n ? 2 * n - i - 1 : i);
    const hist = new Int32Array(bins);
    for (let y = 0; y < height; y++) {
      hist.fill(0);
      let below = 0, level = 0;
      for (let dy = -r; dy <= r; dy++) {
        const row = mirrorOut(y + dy, height) * width;
        for (let dx = -r; dx <= r; dx++) hist[q[row + mirrorOut(dx, width)]]++;
      }
      for (let x = 0; x < width; x++) {
        while (below + hist[level] <= rank) { below += hist[level]; level++; }
        while (below > rank) { level--; below -= hist[level]; }
        out[y * width + x] = level / scale;
        if (x + 1 === width) break;
        for (let dy = -r; dy <= r; dy++) {
          const row = mirrorOut(y + dy, height) * width;
          const gone = q[row + mirrorOut(x - r, width)], come = q[row + mirrorOut(x + r + 1, width)];
          hist[gone]--; hist[come]++;
          if (gone < level) below--;
          if (come < level) below++;
        }
      }
    }
    return out;
  }

  function shrink4(values, width, height) {
    const w = Math.round(width / 4), h = Math.round(height / 4), out = new Float32Array(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let sum = 0, n = 0;
        for (let v = y * 4; v < Math.min(height, y * 4 + 4); v++) {
          for (let u = x * 4; u < Math.min(width, x * 4 + 4); u++) { sum += values[v * width + u]; n++; }
        }
        out[y * w + x] = sum / n;
      }
    }
    return { values: out, width: w, height: h };
  }

  function grow(values, w, h, width, height) {
    const out = new Float32Array(width * height), fx = w / width, fy = h / height;
    for (let y = 0; y < height; y++) {
      const sy = Math.min(h - 1, Math.max(0, (y + 0.5) * fy - 0.5)), y0 = Math.floor(sy), y1 = Math.min(h - 1, y0 + 1), dy = sy - y0;
      for (let x = 0; x < width; x++) {
        const sx = Math.min(w - 1, Math.max(0, (x + 0.5) * fx - 0.5)), x0 = Math.floor(sx), x1 = Math.min(w - 1, x0 + 1), dx = sx - x0;
        const top = values[y0 * w + x0] * (1 - dx) + values[y0 * w + x1] * dx;
        const bottom = values[y1 * w + x0] * (1 - dx) + values[y1 * w + x1] * dx;
        out[y * width + x] = top * (1 - dy) + bottom * dy;
      }
    }
    return out;
  }

  // A per-pixel paper level taken from the bright mode, less a smooth upper envelope, so anything painted a few grey levels
  // below the wash shows as a negative residual.
  function faintLayer(gray, width, height, opts = {}) {
    const { bandwidth = 4, modeSigma = 5, smooth = 3, minPaper = 0.05, envPct = 90, envWindow = 121, iters = 8 } = opts;
    const n = gray.length, weight = new Float32Array(n), gw = new Float32Array(n);
    let mu = blur(percentileFilter(gray, width, height, 17, 88, 256, 255), width, height, modeSigma / 2);
    const weigh = () => {
      for (let i = 0; i < n; i++) {
        const d = (gray[i] - mu[i]) / bandwidth;
        weight[i] = Math.exp(-0.5 * d * d);
        gw[i] = gray[i] * weight[i];
      }
    };
    for (let k = 0; k < iters; k++) {
      weigh();
      const num = blur(gw, width, height, modeSigma), den = blur(weight, width, height, modeSigma);
      for (let i = 0; i < n; i++) mu[i] = num[i] / Math.max(den[i], 1e-6);
    }
    weigh();
    const num = blur(gw, width, height, smooth), den = blur(weight, width, height, smooth);
    const level = new Float32Array(n);
    for (let i = 0; i < n; i++) level[i] = num[i] / Math.max(den[i], 1e-6);
    const small = shrink4(level, width, height);
    const k = Math.max(3, Math.round(envWindow / 4) | 1);
    const peak = percentileFilter(small.values, small.width, small.height, k, envPct, 1021, 255);
    const env = grow(blur(peak, small.width, small.height, (k + 1) / 4), small.width, small.height, width, height);
    const residual = new Float32Array(n), invalid = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      residual[i] = level[i] - env[i];
      invalid[i] = den[i] > minPaper && level[i] > 0 ? 0 : 1;
    }
    return { residual, invalid };
  }

  // Polar unwrap of an RGBA buffer: bearing along x, clockwise from straight up, and radius along y, with bilinear sampling.
  function polar(rgba, width, height, { cx, cy, phi0, phi1, r0, r1, sx = 4, sy = 4 }) {
    const w = Math.max(1, Math.round((phi1 - phi0) * sx)), h = Math.max(1, Math.round((r1 - r0) * sy));
    const out = new Uint8ClampedArray(w * h * 4);
    for (let v = 0; v < h; v++) {
      const r = r0 + (v + 0.5) / sy;
      for (let u = 0; u < w; u++) {
        const phi = (phi0 + (u + 0.5) / sx) * Math.PI / 180;
        const x = cx + r * Math.sin(phi), y = cy - r * Math.cos(phi);
        const j = (v * w + u) * 4;
        if (x < 0 || y < 0 || x > width - 1 || y > height - 1) { out[j + 3] = 255; continue; }
        const x0 = Math.min(Math.floor(x), width - 2), y0 = Math.min(Math.floor(y), height - 2), fx = x - x0, fy = y - y0;
        const a = (y0 * width + x0) * 4, b = a + 4, c = a + width * 4, d = c + 4;
        for (let ch = 0; ch < 3; ch++) {
          out[j + ch] = rgba[a + ch] * (1 - fx) * (1 - fy) + rgba[b + ch] * fx * (1 - fy) + rgba[c + ch] * (1 - fx) * fy + rgba[d + ch] * fx * fy;
        }
        out[j + 3] = 255;
      }
    }
    return { rgba: out, width: w, height: h };
  }

  const api = { VIEWS, luma, scalar, percentiles, region, grey, negative, bitPlane, blur, boxWidths, highPass, paperHighPass,
                percentileFilter, faintLayer, polar };
  root.LabOps = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
