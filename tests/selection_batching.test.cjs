const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
function setup() {
  const calls = [];
  const state = {pending: new Map(), selected: new Map(), sent: new Map(), stage: 'choose_edits', page: 'full'};
  const context = vm.createContext({state, rememberPending() {}, updateSelectionUI() {}, updateTray() {}, refreshGalleryPage() {}, mutateSelections: async (...args) => calls.push(args)});
  vm.runInContext(source.slice(source.indexOf('async function addToCart('), source.indexOf('function applySelections(')) + source.slice(source.indexOf('function toggleSelection('), source.indexOf('function openLightbox(')), context);
  return {context, state, calls};
}
test('pending picks toggle locally and save in one batch at 20', async () => {
  const {context, state, calls} = setup();
  for (let i = 0; i < 19; i++) await context.toggleSelection({id: String(i)});
  assert.equal(calls.length, 0);
  assert.equal(state.selected.size, 19);
  await context.toggleSelection({id: '0'});
  assert.equal(state.pending.size, 18);
  assert.equal(calls.length, 0);
  await context.toggleSelection({id: '0'});
  await context.toggleSelection({id: '19'});
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'save');
  assert.equal(calls[0][1].files.length, 20);
});
test('manual cart action batches fewer picks; failed writes keep pending picks', async () => {
  const {context, state, calls} = setup();
  await context.toggleSelection({id: 'a'});
  await context.toggleSelection({id: 'b'});
  await context.addToCart();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].files.length, 2);
  assert.equal(state.pending.size, 2);
});
test('download selections remain local and never enter the pending pile', async () => {
  const {context, state, calls} = setup();
  state.stage = 'final_edits';
  await context.toggleSelection({id: 'a'});
  assert.equal(state.selected.size, 1);
  assert.equal(state.pending.size, 0);
  assert.equal(calls.length, 0);
});
