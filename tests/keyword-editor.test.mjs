import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createKeywordEditor } from '../pages/groups/keyword-editor.js';
const require = createRequire(import.meta.url);
const {parseHTML} = require('linkedom');
const {window, document} = parseHTML('<html><body></body></html>');
globalThis.document = document;
globalThis.window = window;
// linkedom models select.value as read-only; emulate the native setter.
const descriptor = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
Object.defineProperty(window.HTMLSelectElement.prototype, 'value', {
  get: descriptor.get,
  set(value) {
    for (const option of this.options) option.removeAttribute("selected");
    Array.from(this.options).find(option => option.value === value)?.setAttribute("selected", "");
  }
});
globalThis.FileReader = class {
  readAsDataURL(file) { this.result = file.data; queueMicrotask(() => this.onload()); }
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const id = 'a'.repeat(64) + '.png';
function setup(rule, post = async () => ({id, data_url:'data:image/png;base64,AA=='})) {
  const details = document.createElement('details');
  details.open = true;
  const root = document.createElement('div');
  details.append(root);
  document.body.append(details);
  return {root, editor:createKeywordEditor(root, rule, post)};
}
function choose(root, files) {
  const input = root.querySelector('.reply-upload');
  Object.defineProperty(input, 'files', {configurable:true, value:files});
  input.dispatchEvent(new window.Event('change'));
}
const file = {name:'test.png', size:10, data:'data:image/png;base64,AA=='};
{
  const {editor, root} = setup({reply:'旧文字'});
  assert.deepEqual(editor.read(), {reply:'旧文字', trigger:'any', case_sensitive:false, send_mode:'combined'});
  root.querySelector('.reply-add').click();
  assert.throws(() => editor.read(), /至少/);
  assert.match(root.querySelector('[role=alert]').textContent, /至少/);
  root.querySelectorAll('.reply-text')[1].value = '随机文字';
  assert.equal(editor.read().responses.length, 2);
  root.querySelectorAll('.reply-remove')[1].click();
  assert.equal(editor.read().responses, undefined);
}
{
  let resolveUpload;
  const calls = [];
  const {editor, root} = setup({reply:''}, (path, body) => {
    calls.push({path,body});
    if (path.endsWith('/preview')) return Promise.resolve({id,data_url:'data:image/png;base64,AA=='});
    return new Promise(resolve => {resolveUpload = resolve;});
  });
  choose(root, [file]);
  assert.throws(() => editor.read(), /正在上传/);
  await tick();
  assert.equal(calls[0].path, 'keyword-images/upload');
  assert.equal(calls[0].body.base64, 'AA==');
  resolveUpload({id});
  await tick();
  assert.deepEqual(editor.read().responses, [{text:'', images:[id]}]);
  assert.match(root.querySelector('img').src, /^data:image\/png/);
  root.querySelector('figure button').click();
  assert.throws(() => editor.read(), /至少/);
}
{
  const {editor, root} = setup({responses:[{text:'已保存', images:[id]}]}, async path => {
    assert.equal(path, 'keyword-images/preview');
    return {id, data_url:'data:image/png;base64,AA=='};
  });
  await tick();
  assert.equal(editor.read().responses[0].images[0], id);
  assert.match(root.querySelector('img').src, /^data:image\/png/);
}
{
  let calls = 0;
  const {editor, root} = setup({reply:'文字保留'}, async () => {calls++; throw new Error('服务器拒绝图片');});
  choose(root, [file,file,file,file]);
  await tick();
  assert.equal(calls, 0);
  assert.match(root.querySelector('[role=alert]').textContent, /最多/);
  choose(root, [file]);
  await tick();
  assert.equal(calls, 1);
  assert.equal(root.querySelector('.reply-upload').disabled, false);
  assert.equal(editor.read().reply, '文字保留');
  assert.equal(root.querySelectorAll('img').length, 0);
}
console.log('keyword editor: 4 scenarios passed (legacy/random, upload/pending/remove, persisted preview, limits/failure)');
// Exercise the real page's two form builders and save handlers, without a browser.
{
  const {readFileSync} = require('node:fs');
  const page = parseHTML(readFileSync(new URL('../pages/groups/index.html', import.meta.url), 'utf8'));
  globalThis.document = page.document;
  globalThis.window = page.window;
  for (const dialog of page.document.querySelectorAll('dialog')) {
    dialog.showModal = () => { dialog.open = true; };
    dialog.close = () => { dialog.open = false; };
  }
  const group = {group_openid:'group-1', group_name:'测试群', bound:true, mode:'off', keyword_replies:[]};
  const posts = [];
  page.window.AstrBotPluginPage = {
    ready: async () => {},
    apiGet: async path => ({data: path === 'list' ? [group] : path === 'global-keyword-replies' ? {groups:[group],rules:[]} : {}}),
    apiPost: async (path, body) => {
      posts.push({path,body});
      return {data:path.endsWith('/save') ? body : {}};
    }
  };
  await import('../pages/groups/app.js');
  await tick();
  const doc = page.document;
  const edit = Array.from(doc.querySelectorAll('#group-rows button')).find(button => button.textContent === '编辑');
  assert.ok(edit, doc.querySelector('#group-rows').textContent);
  edit.click();
  doc.querySelector('#add-keyword-reply').click();
  const local = doc.querySelector('.keyword-reply-row');
  local.querySelector('.keyword-reply-name').value = '本群';
  local.querySelector('.keyword-reply-keywords').value = '帮助';
  doc.querySelector('#edit-form').dispatchEvent(new page.window.Event('submit'));
  assert.equal(posts.length, 0, 'blank response must not save');
  assert.match(doc.querySelector('#toast').textContent, /至少/);
  local.querySelector('.reply-text').value = '本群回复';
  doc.querySelector('#edit-form').dispatchEvent(new page.window.Event('submit'));
  assert.equal(posts[0].path, 'save');
  assert.equal(posts[0].body.keyword_replies[0].reply, '本群回复');
  assert.equal(posts[0].body.keyword_replies[0].send_mode, 'text_first');
  await tick();
  doc.querySelector('#add-global-keyword-reply').click();
  const global = doc.querySelector('.global-keyword-row');
  global.querySelector('.global-rule-name').value = '全局';
  global.querySelector('.global-rule-keywords').value = '(.*)作法';
  global.querySelector('.global-rule-mode').value = 'regex';
  global.querySelector('.global-rule-mode').dispatchEvent(new page.window.Event('change'));
  assert.equal(global.querySelector('.global-rule-logic').disabled, true);
  assert.equal(global.querySelector('.reply-send-mode').value, 'text_first');
  global.querySelector('.reply-send-mode').value = 'combined';
  global.querySelector('.reply-text').value = '{1}好';
  doc.querySelector('#global-keyword-form').dispatchEvent(new page.window.Event('submit'));
  assert.equal(posts[1].path, 'global-keyword-replies/save');
  assert.equal(posts[1].body.rules[0].match_type, 'regex');
  assert.equal(posts[1].body.rules[0].reply, '{1}好');
  assert.equal(posts[1].body.rules[0].send_mode, 'combined');
  await tick();
  assert.equal(doc.querySelector('.global-keyword-row .reply-send-mode').value, 'combined');
  console.log('page integration: group/global form save and validation passed');
}
