// Execute the real page functions with API responses, without a live browser.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  for (const file of ['index.html', 'index_test.html']) {
    const html = fs.readFileSync(path.join(__dirname, '../Portfolio_2026/static', file), 'utf8');
    const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/render\(\);\s*$/, '');
    const fields = {};
    const context = vm.createContext({console, URLSearchParams,
      document: {getElementById: id => fields[id] ??= {value: '', innerHTML: '', textContent: ''}},
      localStorage: {getItem: () => null}, setTimeout: () => {}});
    vm.runInContext(script, context);
    for (const [id, value] of Object.entries({tx_product_id: '1', tx_change_type: 'IN', tx_quantity: '5'}))
      fields[id] = {value};
    vm.runInContext(`api = async () => ({before_quantity:39, after_quantity:44});
      toast = (title, message) => { globalThis.result = message; };
      refreshAll = async () => {};`, context);
    await vm.runInContext('postTransaction()', context);
    assert.equal(context.result, '新しい在庫数: 44');
    vm.runInContext(`api = async () => [{user_id:1, name:'Admin', email:'admin@example.com', role:'admin'}];`, context);
    await vm.runInContext('loadUsers()', context);
    assert.match(fields.usersBox.innerHTML, /admin@example.com/);
    assert.doesNotMatch(fields.usersBox.innerHTML, /<th>active<\/th>|undefined|>\?<\/td>/);
    console.log(file + ': stock message and user table passed');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
