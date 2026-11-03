// The picture views shared by the picture lab and the clock page: the list, their sliders, and the pixels of a view,
// worked out from a picture's original pixels with lab-ops.js. The original pixels are never changed.
(function (root) {
  "use strict";
  const L = root.LabOps;

  const SLIDERS = {
    radius: { name: "Blur radius", min: 1, max: 40, step: 1, unit: "px" },
    low: { name: "Black point", min: 0, max: 254, step: 1, unit: "" },
    high: { name: "White point", min: 1, max: 255, step: 1, unit: "" },
    dark: { name: "Treat as art below", min: 100, max: 240, step: 1, unit: "" },
    paperSigma: { name: "Blur radius", min: 5, max: 60, step: 1, unit: "px" },
    gain: { name: "Gain", min: 2, max: 30, step: 1, unit: "x" },
    bit: { name: "Bit", min: 0, max: 7, step: 1, unit: "" },
    faintLo: { name: "Black at", min: -40, max: 0, step: 1, unit: "" },
    faintHi: { name: "White at", min: -4, max: 10, step: 0.5, unit: "" },
  };
  const VIEWS = [
    { id: "original", name: "Original", note: "The file as published, 1600 by 1200." },
    { id: "negative", name: "Negative", note: "Every colour inverted." },
    { id: "grey", name: "Greyscale", scalar: 1, note: "Brightness only, with the colour taken out." },
    { id: "red", name: "Red channel", scalar: 1, note: "The red values alone." },
    { id: "green", name: "Green channel", scalar: 1, note: "The green values alone." },
    { id: "blue", name: "Blue channel", scalar: 1, note: "The blue values alone." },
    { id: "saturation", name: "Saturation", scalar: 1, note: "How far each pixel is from grey. Coloured marks stand out from black ink and paper." },
    { id: "redblue", name: "Red minus blue", scalar: 1, note: "Separates warm paint from cool paint." },
    { id: "greenopp", name: "Green against red and blue", scalar: 1, note: "Green less the mean of red and blue. Shows green tints the eye skips." },
    { id: "highpass", name: "High-pass", scalar: 1, controls: ["radius"], note: "Brightness less a blurred copy. Keeps fine detail and drops large shapes." },
    { id: "levels", name: "Levels", controls: ["low", "high"], note: "Maps one brightness range to full black and white. The default is the pale range, 205 to 248." },
    { id: "paper", name: "Paper texture", controls: ["dark", "paperSigma", "gain"], note: "High-pass of the paper alone: dark art is filled in first, so faint shapes between the lines show." },
    { id: "faint", name: "Faint wash recovery", controls: ["faintLo", "faintHi"], note: "Estimates the paper level from the pixels between the ink, then removes the smooth background. Marks a few grey levels below the wash go dark. Slate means no paper was visible there. Takes a few seconds." },
    { id: "bit", name: "Bit plane", controls: ["channel", "bit"], note: "One bit of one channel. Bit 0 is the lowest." },
  ];
  // The settings a view reads, at their starting values.
  const DEFAULTS = { view: "original", stretch: "whole", radius: 8, low: 205, high: 248, dark: 190, paperSigma: 25, gain: 12,
                     channel: 0, bit: 0, faintLo: -16, faintHi: 2 };

  // The views of one picture's RGBA pixels, width W by height H. The last field worked out is kept, so a slider that
  // only changes the shading does not work the field out again.
  function renderer(rgba, W, H) {
    let field = null, fieldKey = "", invalid = null;

    function fieldFor(S) {
      const view = S.view;
      const key = view === "highpass" ? "highpass:" + S.radius
        : view === "paper" ? ["paper", S.dark, S.paperSigma, S.gain].join(":")
        : view === "bit" ? ["bit", S.channel, S.bit].join(":") : view;
      if (key === fieldKey) return field;
      const grey = () => L.scalar(rgba, L.VIEWS.grey);
      invalid = null;
      if (view === "highpass") field = L.highPass(grey(), W, H, S.radius);
      else if (view === "levels") field = grey();
      else if (view === "paper") field = L.paperHighPass(grey(), W, H, S.dark, S.paperSigma, S.gain);
      else if (view === "bit") field = L.bitPlane(rgba, S.channel, S.bit);
      else if (view === "faint") {
        const r = L.faintLayer(grey(), W, H);
        field = r.residual;
        invalid = r.invalid;
      } else field = L.scalar(rgba, L.VIEWS[view]);
      fieldKey = key;
      return field;
    }

    // `box` is the [x0, y0, x1, y1] on screen, for the stretch to what is on screen; null stretches to the whole picture.
    function shade(S, values, box) {
      const view = VIEWS.find((v) => v.id === S.view);
      if (S.view === "levels") return L.grey(values, S.low, Math.max(S.high, S.low + 1));
      if (S.view === "paper" || S.view === "bit") return L.grey(values, 0, 255);
      if (S.view === "faint") return L.grey(values, S.faintLo, Math.max(S.faintHi, S.faintLo + 0.5), invalid);
      if (!view.scalar) return null;
      if (S.stretch === "none") return L.grey(values, S.view === "highpass" ? -127.5 : 0, S.view === "highpass" ? 127.5 : 255);
      const part = S.stretch === "screen" && box ? L.region(values, W, ...box) : values;
      const [lo, hi] = L.percentiles(part);
      return L.grey(values, lo, hi);
    }

    return {
      // True when the next pixels() call works out the faint field, which takes a few seconds.
      slow: (S) => S.view === "faint" && !fieldKey.startsWith("faint"),
      pixels(S, box) {
        if (S.view === "original") return rgba;
        if (S.view === "negative") return L.negative(rgba);
        return shade(S, fieldFor(S), box);
      },
      // The field value at pixel index i, when the field on hand is this view's; otherwise null.
      valueAt: (S, i) => (field && fieldKey.split(":")[0] === S.view ? field[i] : null),
    };
  }

  root.LabViews = { SLIDERS, VIEWS, DEFAULTS, renderer, find: (id) => VIEWS.find((v) => v.id === id) };
})(globalThis);
