// 用一个最小 DOM 桩验证 common.js 的工具函数真的能工作
const fs = require('fs');
const path = process.argv[2];
const vm = require('vm');

const store = {};
const mkEl = (tag) => ({
  tagName: tag, id: '', className: '', textContent: '', innerHTML: '',
  style: {}, children: [], dataset: {},
  classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
  appendChild(c){ this.children.push(c); return c; },
  insertBefore(c){ this.children.push(c); return c; },
  remove(){},
  querySelector(){ return null; },
  querySelectorAll(){ return []; },
  addEventListener(){},
  getBoundingClientRect(){ return { width: 100, height: 20 }; },
});
const body = mkEl('BODY');
const sandbox = {
  window: {},
  document: {
    readyState: 'complete',
    body,
    getElementById: () => null,
    createElement: mkEl,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(){},
  },
  fetch: async () => ({ ok: true, status: 200, text: async () => '{}' }),
  location: { pathname: '/x', href: '' },
  localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0,
  MutationObserver: function(){ this.observe = () => {}; },
  navigator: { userAgent: 'node' },
  addEventListener(){}, removeEventListener(){},
  console,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);

const BP = sandbox.window.BP;
const t = [];
t.push(['fmt(0)', BP.fmt(0), '0 秒']);
t.push(['fmt(45)', BP.fmt(45), '45 秒']);
t.push(['fmt(125)', BP.fmt(125), '2 分 5 秒']);
t.push(['fmt(3725)', BP.fmt(3725), '1 时 2 分']);
t.push(['fmtN(999)', BP.fmtN(999), '999']);
t.push(['fmtN(12000)', BP.fmtN(12000), '1.2万']);
t.push(['fmtN(340000000)', BP.fmtN(340000000), '3.4亿']);
t.push(['maskMid("123456789")', BP.maskMid('123456789'), '123…89']);
t.push(['maskMid("123")', BP.maskMid('123'), '123']);
t.push(['esc("<a>")', BP.esc('<a>'), '&lt;a&gt;']);
t.push(['accName', BP.accName({alias_name:'别名', uname:'真名'}), '别名']);
t.push(['accName 回退', BP.accName({mid:'99'}), 'UID 99']);
t.push(['accMid 别名', BP.accMid({alias_mid:'A1', mid:'99'}), 'A1']);
t.push(['faceUrl 直通', BP.faceUrl('/api/img/proxy?u=x'), '/api/img/proxy?u=x']);
t.push(['faceUrl 代理', BP.faceUrl('http://i0.hdslb.com/a.jpg'),
        '/api/img/proxy?u=' + encodeURIComponent('http://i0.hdslb.com/a.jpg')]);
t.push(['ico', BP.ico('vip'), '<svg class="ic "><use href="#ic-vip"></use></svg>']);
t.push(['mb(512)', BP.mb(512), '512 MB']);
t.push(['mb(-1)', BP.mb(-1), '不限']);

let bad = 0;
for (const [name, got, want] of t) {
  const ok = got === want;
  console.log((ok ? '  PASS  ' : '  FAIL  ') + name + (ok ? '' : ' -> ' + JSON.stringify(got) + ' 期望 ' + JSON.stringify(want)));
  if (!ok) bad++;
}
console.log(bad ? '失败 ' + bad : 'common.js 行为测试全部通过');
process.exit(bad ? 1 : 0);
