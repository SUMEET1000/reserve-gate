const INTEGRATED_GPU = /(swiftshader|llvmpipe|intel.*(?:uhd|hd graphics|iris)|radeon\(tm\) graphics|radeon vega)/i;

export function isLowPowerHero({ gpu = '', deviceMemory, hardwareConcurrency } = {}) {
  // ponytail: browser hardware hints are coarse; use frame-time adaptation if
  // an unlisted integrated GPU proves slow in production.
  return INTEGRATED_GPU.test(gpu)
    || (deviceMemory > 0 && deviceMemory <= 4)
    || (hardwareConcurrency > 0 && hardwareConcurrency <= 4);
}

export function heroSamples({ lowPower, mobile, maxSamples }) {
  return Math.min(lowPower || mobile ? 2 : 4, maxSamples);
}
