// Execute the actual selector with synthetic completed-trial ledgers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const functions = ['capturePlan', 'chooseNext', 'trialTitle'].map(name => {
  const match = source.match(new RegExp(`function ${name}\\(\\) \\{[\\s\\S]*?\\n\\}`));
  assert(match, `Actual ${name} function is present`);
  return match[0];
}).join('\n');
const plan = {
  profile: 'two-actions', actions: [12, 13], groups: 3,
  repetitions_per_group: 6, repetitions: 18, total: 36,
};
const state = { templates: { capture_plan: plan }, session: null, completed: [], action: 0, repetition: 0 };
const context = vm.createContext({ state, t: zh => zh, name: action => `Action ${action}` });
vm.runInContext(functions, context);
const observed = [];
while (context.chooseNext()) {
  const trial = { action: state.action, repetition: state.repetition };
  const title = context.trialTitle();
  assert(context.chooseNext(), 'An unfinished repetition remains selectable after pause');
  assert.equal(state.action, trial.action);
  assert.equal(state.repetition, trial.repetition);
  observed.push(trial);
  state.completed.push(trial);
  const group = Math.floor((trial.repetition - 1) / 6) + 1;
  assert(title.includes(`第 ${group}/3 组`), 'Displayed group follows the stored repetition');
}
assert.equal(observed.length, 36);
assert.equal(new Set(observed.map(r => `${r.action}/${r.repetition}`)).size, 36);
assert.deepEqual(observed[0], { action: 12, repetition: 1 });
assert.deepEqual(observed[6], { action: 13, repetition: 1 });
assert.deepEqual(observed[12], { action: 12, repetition: 7 });
assert.deepEqual(observed[24], { action: 12, repetition: 13 });
assert.deepEqual(observed[35], { action: 13, repetition: 18 });
assert.equal(context.chooseNext(), false);
// A recovered full-plan session takes precedence over the server's new plan.
state.session = { capture_plan: { profile: 'full', actions: [1, 2], groups: 1, repetitions_per_group: 6 } };
state.completed = [];
assert(context.chooseNext());
assert.equal(state.action, 1);
assert.equal(state.repetition, 1);
console.log('PASS: all 36 group/action/repetition keys, pause selection, labels, and recovered-plan precedence');
