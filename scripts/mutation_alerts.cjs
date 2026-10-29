'use strict';

// Called only by the serialized, trusted publisher. Artifacts are data, never code.
const PREFIX = '<!-- skillsinc-mutation-alert:';
const KINDS = new Set(['findings', 'execution-failure']);
const EVENTS = new Set(['push', 'schedule', 'workflow_dispatch']);
const DETECTED = new Set(['killed', 'timeout', 'caught by type check', 'segfault']);
const MISSED = new Set(['survived', 'no tests', 'suspicious']);
const SHA = /^[a-f0-9]{40}$/;
const ID = /^[1-9][0-9]*$/;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const percentage = value => typeof value === 'number' && Number.isFinite(value)
  && value >= 0 && value <= 100;
const lower = value => typeof value === 'string' ? value.toLowerCase() : '';

function trustedRun(run, repository, defaultBranch, workflowPath, workflowName) {
  return run && run.status === 'completed' && EVENTS.has(run.event)
    && run.head_branch === defaultBranch && run.path === workflowPath
    && run.name === workflowName && lower(run.repository?.full_name) === lower(repository)
    && lower(run.head_repository?.full_name) === lower(repository)
    && integer(run.repository?.id) && run.repository.id > 0
    && run.repository.id === run.head_repository?.id
    && integer(run.workflow_id) && run.workflow_id > 0
    && ID.test(String(run.id)) && integer(run.run_number) && run.run_number > 0
    && integer(run.run_attempt) && run.run_attempt > 0 && SHA.test(run.head_sha);
}

function summaryError(data, run) {
  if (!data || typeof data !== 'object' || Array.isArray(data)
      || data.schema_version !== 1 || data.scope !== 'full-package'
      || data.commit !== run.head_sha || data.run_id !== String(run.id)
      || data.run_attempt !== String(run.run_attempt) || typeof data.complete !== 'boolean') {
    return 'The structured report identity, attempt, scope, or schema is invalid.';
  }
  const counts = ['candidate_count', 'classified_count', 'unclassified_count', 'detected', 'missed'];
  if (!counts.every(key => integer(data[key])) || !percentage(data.threshold)
      || data.candidate_count !== data.classified_count + data.unclassified_count
      || data.classified_count !== data.detected + data.missed
      || !data.outcomes || typeof data.outcomes !== 'object' || Array.isArray(data.outcomes)) {
    return 'The structured report counts or threshold are invalid.';
  }
  if (data.threshold !== 85) return 'The structured report threshold must match the repository floor of 85%.';
  const outcomes = Object.entries(data.outcomes);
  if (outcomes.length > 100 || outcomes.some(([key, count]) => key.length > 200 || !integer(count))) {
    return 'The structured report outcomes are invalid.';
  }
  const total = predicate => outcomes.reduce((sum, [key, count]) => sum + (predicate(key) ? count : 0), 0);
  if (total(() => true) !== data.candidate_count || total(key => DETECTED.has(key)) !== data.detected
      || total(key => MISSED.has(key)) !== data.missed
      || total(key => !DETECTED.has(key) && !MISSED.has(key)) !== data.unclassified_count) {
    return 'The outcome totals disagree with the structured report counts.';
  }
  const score = data.classified_count ? 100 * data.detected / data.classified_count : null;
  if (score === null ? data.score !== null : !percentage(data.score) || Math.abs(data.score - score) > 0.000001) {
    return 'The structured report score disagrees with its counts.';
  }
  const complete = data.classified_count > 0 && data.unclassified_count === 0;
  const result = complete ? (score >= data.threshold ? 'healthy' : 'findings') : 'incomplete';
  if (data.complete !== complete || data.result !== result) {
    return 'The structured report completeness or result is inconsistent.';
  }
  return null;
}

function evidence(run, repository, workflowPath, kind, summary) {
  return {
    schema_version: 1, kind, repository: lower(repository), workflow_path: workflowPath,
    workflow_id: run.workflow_id, run_id: String(run.id), run_number: run.run_number,
    run_attempt: run.run_attempt, commit: run.head_sha, scope: 'full-package',
    threshold: summary?.threshold ?? null, result: summary?.result ?? 'incomplete',
  };
}

function marker(issue, repository, workflowPath) {
  if (issue.pull_request || issue.user?.login !== 'github-actions[bot]' || issue.user?.type !== 'Bot') return null;
  const body = issue.body || '';
  const start = body.indexOf(PREFIX);
  if (start < 0 || body.indexOf(PREFIX, start + PREFIX.length) >= 0) return null;
  const end = body.indexOf(' -->', start);
  if (end < 0) return null;
  try {
    const data = JSON.parse(body.slice(start + PREFIX.length, end));
    if (data.schema_version !== 1 || !KINDS.has(data.kind) || data.repository !== lower(repository)
        || data.workflow_path !== workflowPath || data.scope !== 'full-package'
        || !ID.test(data.run_id) || !SHA.test(data.commit) || !integer(data.workflow_id)
        || !integer(data.run_number) || data.run_number < 1
        || !integer(data.run_attempt) || data.run_attempt < 1
        || !(data.threshold === null || percentage(data.threshold))) return null;
    return data;
  } catch { return null; }
}

function order(first, second) {
  if (first.workflow_id === second.workflow_id) {
    return first.run_number - second.run_number || first.run_attempt - second.run_attempt;
  }
  return BigInt(first.run_id) > BigInt(second.run_id) ? 1
    : BigInt(first.run_id) < BigInt(second.run_id) ? -1 : 0;
}

function runEvidence(run) {
  return {workflow_id: run.workflow_id, run_id: String(run.id), run_number: run.run_number, run_attempt: run.run_attempt};
}

function reportBody(kind, run, repository, workflowPath, summary, problem, recovered = false) {
  const record = evidence(run, repository, workflowPath, kind, summary);
  if (problem) record.result = 'incomplete';
  const runUrl = `https://github.com/${repository}/actions/runs/${run.id}/attempts/${run.run_attempt}`;
  const commitUrl = `https://github.com/${repository}/commit/${run.head_sha}`;
  const score = summary?.score === null || summary?.score === undefined ? 'N/A' : `${summary.score.toFixed(2)}%`;
  const lines = [
    kind === 'findings' ? (recovered ? 'Mutation findings recovered.' : 'Complete mutation results are below the required threshold.')
      : (recovered ? 'Mutation execution recovered with a complete report.' : 'Mutation execution did not establish a complete comparable result.'),
    '', `Run: [${run.id}, attempt ${run.run_attempt}](${runUrl})`,
    `Commit: [${run.head_sha}](${commitUrl})`, 'Scope: full-package, default branch.',
    `Producer conclusion: ${run.conclusion || 'unknown'}.`,
    `Score: ${score}; threshold: ${summary?.threshold ?? 'N/A'}%.`,
    `Counts: ${summary?.candidate_count ?? 'N/A'} candidates; ${summary?.classified_count ?? 'N/A'} classified; `
      + `${summary?.unclassified_count ?? 'N/A'} unclassified; ${summary?.detected ?? 'N/A'} detected; ${summary?.missed ?? 'N/A'} missed.`,
  ];
  if (problem) lines.push('', problem);
  lines.push('', kind === 'findings'
    ? 'Triage: inspect the producing run and missed mutants; strengthen tests or document a reviewed equivalent mutant. Do not lower the threshold to hide findings.'
    : 'Triage: inspect the producing run, baseline tests, timeout/cancellation and artifact upload. Restore a complete full-package result; missing evidence is not a passing score.',
  '', `${PREFIX}${JSON.stringify(record)} -->`);
  return lines.join('\n');
}

async function publish({github, owner, repo, run, defaultBranch, summary = null, error = null,
  workflowPath = '.github/workflows/mutation.yml', workflowName = 'Mutation testing', assignee = 'mikejhill'}) {
  const repository = `${owner}/${repo}`;
  if (!trustedRun(run, repository, defaultBranch, workflowPath, workflowName)) {
    return {ignored: true, reason: 'untrusted producer metadata', actions: []};
  }
  const newer = await github.paginate(github.rest.actions.listWorkflowRuns, {
    owner, repo, workflow_id: run.workflow_id, branch: defaultBranch, status: 'completed', per_page: 100,
  });
  if (newer.some(candidate => trustedRun(candidate, repository, defaultBranch, workflowPath, workflowName)
      && order(runEvidence(candidate), runEvidence(run)) > 0)) {
    return {ignored: true, reason: 'a newer default-branch producer completed', actions: []};
  }
  const all = await github.paginate(github.rest.issues.listForRepo, {owner, repo, state: 'all', per_page: 100});
  const managed = all.map(issue => ({issue, record: marker(issue, repository, workflowPath)}))
    .filter(item => item.record);
  if (managed.some(item => order(item.record, runEvidence(run)) > 0)) {
    return {ignored: true, reason: 'newer durable evidence already exists', actions: []};
  }
  const current = kind => managed.filter(item => item.record.kind === kind)
    .sort((a, b) => order(b.record, a.record) || a.issue.number - b.issue.number)[0];
  const validation = error ? 'The current-attempt structured report could not be retrieved safely.' : summaryError(summary, run);
  const accepted = validation ? null : summary;
  let problem = validation;
  if (!['success', 'failure'].includes(run.conclusion)) problem = `Producer finished with ${run.conclusion || 'an unknown conclusion'}; it cannot establish recovery.`;
  if (accepted?.result === 'healthy' && run.conclusion !== 'success') problem = 'A healthy report from a failed producer cannot establish recovery.';
  if (!problem && !accepted.complete) problem = 'The structured report is empty or contains unclassified outcomes.';
  const actions = [];

  async function update(kind, item, recovered) {
    const record = evidence(run, repository, workflowPath, kind, accepted);
    if (item && order(record, item.record) <= 0) return;
    const args = {owner, repo,
      title: kind === 'findings' ? 'Mutation testing: test-strength findings' : 'Mutation testing: execution failure',
      body: reportBody(kind, run, repository, workflowPath, accepted, problem, recovered),
      assignees: [...new Set([...(item?.issue.assignees || []).map(user => user.login), assignee])],
    };
    if (item) {
      const verb = recovered ? 'closed' : item.issue.state === 'closed' ? 'reopened' : 'updated';
      await github.rest.issues.update({...args, issue_number: item.issue.number, state: recovered ? 'closed' : 'open'});
      actions.push(`${verb}:${kind}`);
    } else if (!recovered) {
      await github.rest.issues.create(args);
      actions.push(`created:${kind}`);
    }
  }

  if (problem) {
    await update('execution-failure', current('execution-failure'), false);
    return {ignored: false, result: 'incomplete', actions};
  }
  if (accepted.result === 'findings') {
    await update('findings', current('findings'), false);
  } else {
    const finding = current('findings');
    if (finding && finding.issue.state === 'open' && order(runEvidence(run), finding.record) > 0
        && finding.record.workflow_id === run.workflow_id && finding.record.threshold === accepted.threshold) {
      const comparison = await github.rest.repos.compareCommitsWithBasehead({
        owner, repo, basehead: `${finding.record.commit}...${run.head_sha}`,
      });
      if (comparison.data?.merge_base_commit?.sha === finding.record.commit) await update('findings', finding, true);
    }
  }
  const failure = current('execution-failure');
  if (failure?.issue.state === 'open' && failure.record.workflow_id === run.workflow_id) {
    await update('execution-failure', failure, true);
  }
  return {ignored: false, result: accepted.result, actions};
}

module.exports = {publish, summaryError, trustedRun};
