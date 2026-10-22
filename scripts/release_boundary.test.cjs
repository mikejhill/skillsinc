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
const pr = () => ({ state: "open", user: { id: 41898282, login: "github-actions[bot]" }, head: { repo: { full_name: "mikejhill/skillsinc" }, ref: "release-please--branches--main", sha: other }, base: { repo: { full_name: "mikejhill/skillsinc" }, ref: "main", sha } });
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
