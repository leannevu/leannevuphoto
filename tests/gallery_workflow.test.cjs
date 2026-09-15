const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const helper = source.slice(source.indexOf('function workflowTarget('), source.indexOf('function updateWorkflowNavigation('));

test('flow links finals to proofs and back, enabling editing after submission', () => {
  const state = {galleryId: 'final', sent: new Map(), stageFolders: [
    {id: 'proof', stage: 'choose_edits'}, {id: 'final', stage: 'final_edits'}
  ]};
  const context = vm.createContext({state});
  vm.runInContext(helper, context);
  assert.equal(context.workflowTarget('choose_edits').id, 'proof');
  assert.equal(context.workflowTarget('final_edits').id, 'final');
  assert.equal(context.workflowTarget('wait_for_edits'), undefined);
  state.galleryId = 'proof';
  state.sent.set('photo', {});
  assert.equal(context.workflowTarget('wait_for_edits').id, 'proof');
  assert.equal(context.workflowTarget('final_edits').id, 'final');
});

test('missing stages are unavailable and waiting folders can still select more', () => {
  const state = {galleryId: 'waiting', sent: new Map(), stageFolders: [{id: 'waiting', stage: 'wait_for_edits'}]};
  const context = vm.createContext({state});
  vm.runInContext(helper, context);
  assert.equal(context.workflowTarget('choose_edits').id, 'waiting');
  assert.equal(context.workflowTarget('final_edits'), undefined);
  state.stageFolders = [{id: 'final', stage: 'final_edits'}];
  assert.equal(context.workflowTarget('choose_edits'), undefined);
  assert.equal(context.workflowTarget('wait_for_edits'), undefined);
});
