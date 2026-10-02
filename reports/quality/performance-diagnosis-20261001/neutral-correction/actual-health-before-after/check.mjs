import { readFileSync } from 'node:fs';
import { assessObservationWindow as before } from './before.mjs';
import { assessObservationWindow as after } from './after.mjs';
const input = JSON.parse(readFileSync(new URL('./input.json', import.meta.url)));
const result = { before: before(input.evidence, input.target), after: after(input.evidence, input.target) };
if(result.before.code !== 'INSUFFICIENT_OBSERVABILITY' || result.after.code !== 'NORMAL_OBSERVED') throw Error('Regression did not reproduce');
console.log(JSON.stringify(result));
