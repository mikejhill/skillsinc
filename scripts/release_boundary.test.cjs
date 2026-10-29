"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { ReleaseBoundary: B } = require("./release_boundary.cjs");
const sha = "a".repeat(40), other = "b".repeat(40);
const fixture = () => {
  const before = { ".release-please-manifest.json": '{".":"0.2.1"}', "pyproject.toml": '[project]\nversion = "0.2.1"\nname = "skillsinc"\n', "uv.lock": 'version = 1\n[[package]]\nname = "skillsinc"\nversion = "0.2.1"\nsource = { editable = "." }\n[[package]]\nname = "pyyaml"\nversion = "6.0.2"\n', "CHANGELOG.md": "old" };
  const after = Object.fromEntries(Object.entries(before).map(([key, value]) => [key, value.replaceAll("0.2.1", "0.2.2")]));
  after["CHANGELOG.md"] = "## 0.2.2\nFixes\n";
  return { before, after, files: Object.keys(before).map(filename => ({ filename, status: "modified" })) };
};
test("accept generated version-only files", () => { const f = fixture(); assert.equal(B.files(f.files, f.before, f.after), "0.2.2"); });
for (const [name, mutate] of [
  ["workflow insertion", f => f.files.push({ filename: ".github/workflows/ci.yml", status: "added" })],
  ["renamed file", f => f.files[0].previous_filename = "other"],
  ["source deletion", f => f.files[0].status = "removed"],
  ["dependency change", f => f.after["uv.lock"] = f.after["uv.lock"].replace("6.0.2", "9.0.0")],
  ["project script injection", f => f.after["pyproject.toml"] += '[tool.evil]\nrun="bad"\n'],
  ["manifest injection", f => f.after[".release-please-manifest.json"] = '{".":"0.2.2","evil":true}'],
  ["missing version", f => f.after[".release-please-manifest.json"] = '{".":"$(bad)"}'],
  ["unchanged version", f => f.after = { ...f.before }],
]) test(`reject ${name}`, () => { const f = fixture(); mutate(f); assert.throws(() => B.files(f.files, f.before, f.after)); });
const pr = () => ({ state: "open", user: { id: 41898282, login: "github-actions[bot]" }, head: { repo: { full_name: "mikejhill/skillsinc" }, ref: "release-please--branches--main--components--skillsinc", sha: other }, base: { repo: { full_name: "mikejhill/skillsinc" }, ref: "main", sha } });
test("accept verified version PR identity", () => B.identity(pr(), "mikejhill/skillsinc", sha));
for (const [name, mutate] of [
  ["fork", p => p.head.repo.full_name = "attacker/skillsinc"],
  ["human labelled PR", p => p.user.id = 12],
  ["wrong branch", p => p.head.ref = "untrusted"],
  ["stale base", p => p.base.sha = other],
  ["wrong base", p => p.base.ref = "feature"],
]) test(`reject ${name}`, () => { const p = pr(); mutate(p); assert.throws(() => B.identity(p, "mikejhill/skillsinc", sha)); });
test("accept matching tested tag SHA", () => B.target("0.2.2", "v0.2.2", sha, sha, sha));
test("reject old-tag backfill on newer CI source", () => assert.throws(() => B.target("0.2.2", "v0.2.2", other, sha, other)));
test("reject tag outside main", () => assert.throws(() => B.target("0.2.2", "v0.2.2", sha, sha, other)));
test("reject mismatched version tag", () => assert.throws(() => B.target("0.2.2", "v9.0.0", sha, sha, sha)));
const ctx = () => ({ repo: {owner:'mikejhill', repo:'skillsinc'}, actor:'mikejhill', ref:'refs/heads/main', sha });
test('writer refuses advanced main before any writer API', async () => {
  await assert.rejects(B.context({context:ctx(),github:{rest:{repos:{getBranch:async()=>({data:{commit:{sha:other}}})}}}}));
});
test('writer refuses nonowner actor', async () => {
  await assert.rejects(B.context({context:{...ctx(),actor:'contributor'},github:{}}));
});
test('missing release skips publication', async () => {
  const outputs={};
  await B.releaseTarget({context:ctx(),core:{setOutput:(k,v)=>outputs[k]=v},github:{rest:{repos:{
    getContent:async()=>({data:{type:'file',encoding:'base64',size:20,content:Buffer.from('{".":"0.2.2"}').toString('base64')}}),
    getReleaseByTag:async()=>{throw Object.assign(new Error('absent'),{status:404});}
  }}}}, false);
  assert.equal(outputs.mode,'none');
});
test('release API failures do not become an empty release', async () => {
  await assert.rejects(B.releaseTarget({context:ctx(),core:{setOutput:()=>{}},github:{rest:{repos:{
    getContent:async()=>({data:{type:'file',encoding:'base64',size:20,content:Buffer.from('{".":"0.2.2"}').toString('base64')}}),
    getReleaseByTag:async()=>{throw Object.assign(new Error('denied'),{status:403});}
  }}}}, false));
});

const generated = () => {
  const f = fixture();
  const versionPr = { ...pr(), number: 41 };
  versionPr.head.ref = 'release-please--branches--main--components--skillsinc';
  const calls = [];
  const rest = {
    repos: {
      getBranch: async () => ({ data: { commit: { sha } } }),
      getContent: async ({ path, ref }) => {
        const content = (ref === sha ? f.before : f.after)[path];
        return { data: { type: 'file', encoding: 'base64', size: content.length,
          content: Buffer.from(content).toString('base64') } };
      },
    },
    pulls: {
      list: async args => {
        calls.push(['lookup', args]);
        return { data: args.head === `mikejhill:${versionPr.head.ref}` ? [{ number: 41 }] : [] };
      },
      get: async () => ({ data: versionPr }),
      listFiles: () => {},
    },
    git: {
      getCommit: async () => ({ data: { parents: [{ sha }], tree: { sha } } }),
      getTree: async () => ({ data: { truncated: false, tree: f.files.map(file =>
        ({ path: file.filename, mode: '100644', type: 'blob' })) } }),
      getRef: async () => ({ data: { object: { sha: versionPr.head.sha } } }),
    },
    actions: { createWorkflowDispatch: async args => calls.push(['dispatch', args]) },
  };
  return { f, versionPr, calls, rest,
    github: { rest, paginate: async () => f.files },
    core: { info: message => calls.push(['info', message]) } };
};

test('dispatches CI and Security for the actual component version branch', async () => {
  const g = generated();
  await B.versionPr({ context: ctx(), github: g.github, core: g.core });
  assert.deepEqual(g.calls.filter(([kind]) => kind === 'dispatch').map(([, args]) =>
    [args.workflow_id, args.ref]), [
    ['ci.yml', g.versionPr.head.ref], ['security.yml', g.versionPr.head.ref],
  ]);
});

test('version workflow rejects executable generated files before dispatch', async () => {
  const g = generated();
  g.rest.git.getTree = async () => ({ data: { truncated: false, tree:
    g.f.files.map(file => ({ path: file.filename, mode: '100755', type: 'blob' })) } });
  await assert.rejects(B.versionPr({ context: ctx(), github: g.github, core: g.core }));
  assert.equal(g.calls.filter(([kind]) => kind === 'dispatch').length, 0);
});

test('version workflow rejects a branch moving after verification', async () => {
  const g = generated();
  g.rest.git.getRef = async () => ({ data: { object: { sha } } });
  await assert.rejects(B.versionPr({ context: ctx(), github: g.github, core: g.core }));
  assert.equal(g.calls.filter(([kind]) => kind === 'dispatch').length, 0);
});

test('publication exports the same verified SHA for CI and the package build', async () => {
  const outputs = {};
  const getContent = async () => ({ data: { type: 'file', encoding: 'base64', size: 20,
    content: Buffer.from('{".":"0.2.2"}').toString('base64') } });
  await B.releaseTarget({ context: ctx(), core: { setOutput: (k, v) => outputs[k] = v },
    github: { rest: { repos: {
      getContent,
      getReleaseByTag: async () => ({ data: { tag_name: 'v0.2.2', assets: [] } }),
      getCommit: async () => ({ data: { sha } }),
      compareCommitsWithBasehead: async () => ({ data: { merge_base_commit: { sha } } }),
    } } } }, false);
  assert.deepEqual(outputs, { sha, version: '0.2.2', tag: 'v0.2.2', mode: 'pypi' });
});
