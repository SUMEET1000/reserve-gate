import assert from 'node:assert/strict';
import { heroSamples, isLowPowerHero } from './src/lib/heroQuality.js';

assert.equal(isLowPowerHero({ gpu: 'ANGLE (Intel(R) UHD Graphics 620)', deviceMemory: 8, hardwareConcurrency: 8 }), true);
assert.equal(isLowPowerHero({ gpu: 'AMD Radeon(TM) Graphics', deviceMemory: 16, hardwareConcurrency: 8 }), true);
assert.equal(isLowPowerHero({ gpu: 'Google SwiftShader', deviceMemory: 16, hardwareConcurrency: 8 }), true);
assert.equal(isLowPowerHero({ gpu: 'NVIDIA GeForce RTX 4050', deviceMemory: 16, hardwareConcurrency: 16 }), false);
assert.equal(isLowPowerHero({ gpu: 'Apple M3', deviceMemory: 16, hardwareConcurrency: 8 }), false);
assert.equal(isLowPowerHero({ gpu: '', deviceMemory: 4, hardwareConcurrency: 8 }), true);
assert.equal(isLowPowerHero({ gpu: '', deviceMemory: 8, hardwareConcurrency: 4 }), true);
assert.equal(isLowPowerHero({ gpu: 'Adreno 750', deviceMemory: 8, hardwareConcurrency: 8 }), false);
assert.equal(heroSamples({ lowPower: true, mobile: true, maxSamples: 8 }), 2);
assert.equal(heroSamples({ lowPower: false, mobile: true, maxSamples: 8 }), 2);
assert.equal(heroSamples({ lowPower: false, mobile: false, maxSamples: 8 }), 4);
assert.equal(heroSamples({ lowPower: false, mobile: false, maxSamples: 4 }), 4);

console.log('hero quality self-check passed');
