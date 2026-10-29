'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {publish, summaryError} = require('./mutation_alerts.cjs');
const A = 'a'.repeat(40);
const B = 'b'.repeat(40);
const repository = {id: 7, full_name: 'mikejhill/skillsinc'};

function run(number = 1, overrides = {}) {
  return {id: 1000 + number, run_number: number, run_attempt: 1, workflow_id: 10,
    status: 'completed', conclusion: 'failure', event: 'push', head_sha: number === 1 ? A : B,
    head_branch: 'main', name: 'Mutation testing', path: '.github/workflows/mutation.yml',
    repository: {...repository}, head_repository: {...repository}, ...overrides};
}

function summary(producer, detected = 80, extra = {}) {
  return {schema_version: 1, commit: producer.head_sha, run_id: String(producer.id),
    run_attempt: String(producer.run_attempt), scope: 'full-package', complete: true,
    candidate_count: 100, classified_count: 100, unclassified_count: 0,
    detected, missed: 100 - detected, score: detected, threshold: 85,
    outcomes: {killed: detected, survived: 100 - detected},
    result: detected >= 85 ? 'healthy' : 'findings', ...extra};
}

function harness(initialIssues = [], completed = []) {
  const issues = structuredClone(initialIssues);
  const writes = [];
  const pages = [];
  const github = {rest: {issues: {}, actions: {}, repos: {}}};
  github.rest.issues.listForRepo = async params => {
    pages.push(['issues', params.page]);
    return {data: issues.slice((params.page - 1) * 2, params.page * 2)};
  };
  github.rest.actions.listWorkflowRuns = async params => {
    pages.push(['runs', params.page]);
    return {data: completed.slice((params.page - 1) * 2, params.page * 2)};
  };
  github.paginate = async (method, params) => {
    const all = [];
    for (let page = 1; ; page++) {
      const {data} = await method({...params, page});
      all.push(...data);
      if (data.length < 2) break;
    }
    return all;
  };
  github.rest.issues.create = async params => {
    writes.push(['create', structuredClone(params)]);
    const issue = {...params, number: issues.length + 1, state: 'open',
      user: {login: 'github-actions[bot]', type: 'Bot'},
      assignees: params.assignees.map(login => ({login}))};
    issues.push(issue);
    return {data: issue};
  };
  github.rest.issues.update = async params => {
    writes.push(['update', structuredClone(params)]);
    const issue = issues.find(value => value.number === params.issue_number);
    Object.assign(issue, params, {assignees: params.assignees.map(login => ({login}))});
    return {data: issue};
  };
  github.rest.repos.compareCommitsWithBasehead = async params => {
    return {data: {merge_base_commit: {sha: params.basehead.split('...')[0]}}};
  };
  return {github, issues, writes, pages, call: (producer, data, extra = {}) => publish({
    github, owner: 'mikejhill', repo: 'skillsinc', defaultBranch: 'main',
    run: producer, summary: data, ...extra,
  })};
}

test('creates one assigned findings issue with durable run, score and triage evidence', async () => {
  const h = harness();
  const producer = run();
  assert.deepEqual((await h.call(producer, summary(producer))).actions, ['created:findings']);
  assert.equal(h.issues.length, 1);
  assert.deepEqual(h.writes[0][1].assignees, ['mikejhill']);
  for (const text of ['80.00%', 'threshold: 85%', '100 candidates', 'full-package', A, 'Triage:', 'skillsinc-mutation-alert:']) {
    assert.ok(h.issues[0].body.includes(text), `missing ${text}`);
  }
});

test('healthy first result creates no issue and no spam', async () => {
  const h = harness();
  const producer = run(1, {conclusion: 'success'});
  assert.deepEqual((await h.call(producer, summary(producer, 95))).actions, []);
  assert.equal(h.writes.length, 0);
});

test('updates findings in place; duplicate deliveries do not write again', async () => {
  const h = harness();
  const first = run();
  await h.call(first, summary(first));
  await h.call(first, summary(first));
  assert.equal(h.writes.length, 1);
  const next = run(2);
  assert.deepEqual((await h.call(next, summary(next, 81))).actions, ['updated:findings']);
  assert.equal(h.issues.length, 1);
});

test('newer complete comparable descendant closes findings and later regression reopens it', async () => {
  const h = harness();
  const first = run();
  await h.call(first, summary(first));
  const healthy = run(2, {conclusion: 'success'});
  assert.deepEqual((await h.call(healthy, summary(healthy, 90))).actions, ['closed:findings']);
  assert.equal(h.issues[0].state, 'closed');
  const regression = run(3);
  assert.deepEqual((await h.call(regression, summary(regression))).actions, ['reopened:findings']);
  assert.equal(h.issues.length, 1);
});

test('threshold changes cannot close earlier findings', async () => {
  const h = harness();
  await h.call(run(), summary(run()));
  const healthy = run(2, {conclusion: 'success'});
  await h.call(healthy, summary(healthy, 90, {threshold: 80}));
  assert.equal(h.issues[0].state, 'open');
  assert.equal(h.writes.length, 2);
  assert.ok(h.issues[1].body.includes('repository floor of 85%'));
});

test('non-descendant healthy commits cannot close findings', async () => {
  const h = harness();
  await h.call(run(), summary(run()));
  h.github.rest.repos.compareCommitsWithBasehead = async () => ({data: {merge_base_commit: {sha: 'c'.repeat(40)}}});
  const healthy = run(2, {conclusion: 'success'});
  await h.call(healthy, summary(healthy, 90));
  assert.equal(h.issues[0].state, 'open');
  assert.equal(h.writes.length, 1);
});

test('a newer completed result suppresses stale failure even when healthy created no issue', async () => {
  const newest = run(3, {conclusion: 'success'});
  const h = harness([], [run(), run(2), newest]);
  const outcome = await h.call(run(), null, {error: 'missing artifact'});
  assert.equal(outcome.ignored, true);
  assert.equal(h.writes.length, 0);
  assert.ok(h.pages.some(([kind, page]) => kind === 'runs' && page === 2));
});

test('pending or foreign newer runs do not suppress completed findings', async () => {
  const h = harness([], [run(2, {status: 'in_progress'}), run(3, {head_branch: 'feature'})]);
  await h.call(run(), summary(run()));
  assert.equal(h.issues.length, 1);
});

test('durable newer issue evidence rejects stale events when API history is absent', async () => {
  const h = harness();
  const newest = run(3);
  await h.call(newest, summary(newest));
  const outcome = await h.call(run(), null, {error: 'missing'});
  assert.equal(outcome.ignored, true);
  assert.equal(h.writes.length, 1);
});

test('rerun attempt ordering rejects old attempts and recovers a newer attempt', async () => {
  const h = harness();
  const first = run();
  await h.call(first, summary(first));
  const retry = run(1, {run_attempt: 2, conclusion: 'success'});
  await h.call(retry, summary(retry, 95));
  const outcome = await h.call(first, summary(first));
  assert.equal(outcome.ignored, true);
  assert.equal(h.issues[0].state, 'closed');
  assert.equal(h.writes.length, 2);
});

test('incomplete results open separate execution issue without closing findings', async () => {
  const h = harness();
  await h.call(run(), summary(run()));
  const next = run(2);
  const data = summary(next, 80, {candidate_count: 101, unclassified_count: 1,
    outcomes: {killed: 80, survived: 20, 'not checked': 1}, complete: false, result: 'incomplete'});
  assert.deepEqual((await h.call(next, data)).actions, ['created:execution-failure']);
  assert.equal(h.issues.length, 2);
  assert.equal(h.issues[0].state, 'open');
  assert.equal(h.issues[1].state, 'open');
});

test('complete below-threshold result recovers execution while retaining findings', async () => {
  const h = harness();
  await h.call(run(), null, {error: 'missing'});
  const next = run(2);
  assert.deepEqual((await h.call(next, summary(next))).actions, ['created:findings', 'closed:execution-failure']);
  assert.equal(h.issues[0].state, 'closed');
  assert.equal(h.issues[1].state, 'open');
});

test('execution failures deduplicate, reopen after recovery and avoid leaking raw errors', async () => {
  const h = harness();
  await h.call(run(), null, {error: 'private token must never appear'});
  await h.call(run(), null, {error: 'private token must never appear'});
  const good = run(2, {conclusion: 'success'});
  await h.call(good, summary(good, 90));
  await h.call(run(3), null, {error: 'private token must never appear'});
  assert.equal(h.issues.length, 1);
  assert.equal(h.issues[0].state, 'open');
  assert.equal(h.writes.length, 3);
  assert.ok(!h.issues[0].body.includes('private token'));
});

for (const conclusion of ['cancelled', 'skipped', 'timed_out', 'neutral', 'action_required']) {
  test(`${conclusion} producer cannot resolve findings even with a healthy artifact`, async () => {
    const h = harness();
    await h.call(run(), summary(run()));
    const next = run(2, {conclusion});
    await h.call(next, summary(next, 95));
    assert.equal(h.issues[0].state, 'open');
    assert.equal(h.issues.length, 2);
  });
}

test('a failed producer with healthy telemetry is an execution failure', async () => {
  const h = harness();
  const outcome = await h.call(run(), summary(run(), 95));
  assert.equal(outcome.result, 'incomplete');
  assert.deepEqual(outcome.actions, ['created:execution-failure']);
});

for (const override of [
  {event: 'pull_request'}, {head_branch: 'feature', event: 'workflow_dispatch'},
  {head_repository: {id: 9, full_name: 'stranger/skillsinc'}}, {head_repository: {...repository, id: 9}},
  {path: '.github/workflows/other.yml'}, {name: 'Other'}, {status: 'in_progress'},
  {head_sha: 'bad'}, {repository: {full_name: repository.full_name}}, {run_attempt: 0},
]) {
  test(`untrusted producer metadata is ignored: ${JSON.stringify(override)}`, async () => {
    const h = harness();
    const producer = run(1, override);
    assert.equal((await h.call(producer, summary(producer))).ignored, true);
    assert.equal(h.writes.length, 0);
    assert.equal(h.pages.length, 0);
  });
}

test('paginated discovery ignores human issues, PRs, and malformed markers', async () => {
  const seed = harness();
  await seed.call(run(), summary(run()));
  const managed = seed.issues[0];
  managed.number = 8;
  const h = harness([
    {...managed, number: 1, user: {login: 'human', type: 'User'}},
    {...managed, number: 2, pull_request: {}},
    {...managed, number: 3, body: '<!-- skillsinc-mutation-alert:{not json} -->'},
    managed,
  ]);
  await h.call(run(2), summary(run(2)));
  assert.equal(h.issues.length, 4);
  assert.equal(h.writes[0][1].issue_number, 8);
  assert.ok(h.pages.some(([kind, page]) => kind === 'issues' && page === 2));
});

test('zero candidates are incomplete, never a zero-percent finding or passing score', async () => {
  const h = harness();
  const data = summary(run(), 0, {candidate_count: 0, classified_count: 0, missed: 0,
    outcomes: {}, score: null, complete: false, result: 'incomplete'});
  assert.equal(summaryError(data, run()), null);
  assert.deepEqual((await h.call(run(), data)).actions, ['created:execution-failure']);
  assert.ok(h.issues[0].body.includes('Score: N/A'));
});

for (const override of [
  {schema_version: 2}, {run_id: '999'}, {commit: B}, {run_attempt: '2'}, {scope: 'changed-modules'},
  {complete: false}, {candidate_count: -1}, {classified_count: 1.5}, {unclassified_count: 1},
  {detected: 81}, {missed: 19}, {score: NaN}, {score: 79}, {score: null}, {threshold: Infinity},
  {outcomes: []}, {outcomes: {killed: 80, survived: 19}}, {result: 'healthy'},
]) {
  test(`malformed summary becomes an execution issue: ${JSON.stringify(override)}`, async () => {
    const h = harness();
    assert.deepEqual((await h.call(run(), summary(run(), 80, override))).actions, ['created:execution-failure']);
  });
}

test('partial publisher retries complete unfinished recovery without rewriting processed issues', async () => {
  const h = harness();
  await h.call(run(), summary(run()));
  await h.call(run(2), null, {error: 'missing'});
  const original = h.github.rest.issues.update;
  let fail = true;
  h.github.rest.issues.update = async params => {
    if (params.issue_number === 2 && fail) { fail = false; throw new Error('transient API error'); }
    return original(params);
  };
  const next = run(3, {conclusion: 'success'});
  await assert.rejects(h.call(next, summary(next, 90)), /transient API error/);
  assert.equal(h.issues[0].state, 'closed');
  assert.equal(h.issues[1].state, 'open');
  assert.deepEqual((await h.call(next, summary(next, 90))).actions, ['closed:execution-failure']);
  assert.equal(h.issues[1].state, 'closed');
});
