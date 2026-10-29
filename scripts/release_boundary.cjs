"use strict";

// Data-only release checks; this module never loads SkillSinc or PR code.
const assert = require("node:assert/strict");
const RELEASE_BRANCH = "release-please--branches--main--components--skillsinc";

class ReleaseBoundary {
  static version(value) {
    assert.equal(typeof value, "string", "Version must be text");
    assert.match(value, /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/, "Invalid release version");
    return value;
  }

  static sha(value) {
    assert.equal(typeof value, "string");
    assert.match(value, /^[0-9a-f]{40}$/, "Expected an immutable commit SHA");
    return value;
  }

  static identity(pr, repository, baseSha) {
    assert.equal(pr.state, "open");
    assert.equal(pr.user.id, 41898282, "Only the verified github-actions bot may own the version PR");
    assert.equal(pr.user.login, "github-actions[bot]");
    assert.equal(pr.head.repo.full_name, repository, "Cross-repository PR rejected");
    assert.equal(pr.base.repo.full_name, repository);
    assert.equal(pr.head.ref, RELEASE_BRANCH, "Unexpected release branch");
    assert.equal(pr.base.ref, "main");
    assert.equal(pr.base.sha, baseSha, "main changed; prepare from the new main run");
    this.sha(pr.head.sha);
  }

  static files(files, before, after) {
    const allowed = [".release-please-manifest.json", "CHANGELOG.md", "pyproject.toml", "uv.lock"];
    assert.equal(files.length, allowed.length, "Expected only the four generated version/changelog files");
    assert.deepEqual(files.map(file => file.filename).sort(), [...allowed].sort());
    for (const file of files) {
      assert.equal(file.status, "modified", "No added/deleted/renamed files");
      assert.ok(!file.previous_filename);
      assert.equal(typeof before[file.filename], "string");
      assert.equal(typeof after[file.filename], "string");
      assert.ok(after[file.filename].length <= 2000000, "Generated file too large");
    }
    const oldManifest = JSON.parse(before[allowed[0]]);
    const newManifest = JSON.parse(after[allowed[0]]);
    assert.deepEqual(Object.keys(oldManifest), ["."]);
    assert.deepEqual(Object.keys(newManifest), ["."]);
    const oldVersion = this.version(oldManifest["."]);
    const newVersion = this.version(newManifest["."]);
    assert.notEqual(oldVersion, newVersion, "Version must change");
    const projectLine = `version = "${oldVersion}"`;
    assert.equal(before["pyproject.toml"].split("\n").filter(line => /^version = /.test(line)).length, 1);
    assert.ok(before["pyproject.toml"].split("\n").includes(projectLine));
    assert.equal(after["pyproject.toml"], before["pyproject.toml"].replace(projectLine, `version = "${newVersion}"`), "Only project.version may change");
    const blocks = before["uv.lock"].split(/(?=^\[\[package\]\]$)/m);
    const own = blocks.filter(block => /^name = "skillsinc"$/m.test(block));
    assert.equal(own.length, 1);
    assert.equal(own[0].split("\n").filter(line => /^version = /.test(line)).length, 1);
    assert.ok(own[0].split("\n").includes(projectLine));
    const changed = own[0].replace(projectLine, `version = "${newVersion}"`);
    assert.equal(after["uv.lock"], blocks.map(block => block === own[0] ? changed : block).join(""), "Dependency/lock changes are not version-only");
    assert.ok(after["CHANGELOG.md"].includes(newVersion), "Changelog must identify the version");
    return newVersion;
  }

  static target(version, tag, tagSha, workflowSha, ancestor) {
    this.version(version);
    assert.equal(tag, `v${version}`);
    this.sha(tagSha);
    this.sha(workflowSha);
    assert.equal(ancestor, tagSha, "Tag is not an ancestor of main");
    assert.equal(tagSha, workflowSha, "Retry an unpublished older version from its original release run; do not mix source and provenance");
  }

  static async context({ github, context }) {
    assert.equal(context.repo.owner, "mikejhill");
    assert.equal(context.actor, "mikejhill");
    assert.equal(context.ref, "refs/heads/main");
    this.sha(context.sha);
    const { data } = await github.rest.repos.getBranch({ ...context.repo, branch: "main" });
    assert.equal(data.commit.sha, context.sha, "main advanced before the writer started");
  }

  static async content(github, repository, path, ref) {
    const { data } = await github.rest.repos.getContent({ ...repository, path, ref });
    assert.equal(data.type, "file");
    assert.equal(data.encoding, "base64");
    assert.ok(data.size <= 2000000);
    return Buffer.from(data.content, "base64").toString("utf8");
  }

  static async versionPr({ github, context, core }) {
    await this.context({ github, context });
    const args = { ...context.repo, base: "main", head: `${context.repo.owner}:${RELEASE_BRANCH}`, state: "open", per_page: 100 };
    const { data: found } = await github.rest.pulls.list(args);
    if (found.length === 0) { core.info("No generated version PR"); return; }
    assert.equal(found.length, 1);
    const { data: pr } = await github.rest.pulls.get({ ...context.repo, pull_number: found[0].number });
    this.identity(pr, `${context.repo.owner}/${context.repo.repo}`, context.sha);
    const { data: commit } = await github.rest.git.getCommit({ ...context.repo, commit_sha: pr.head.sha });
    assert.deepEqual(commit.parents.map(parent => parent.sha), [context.sha], "Version branch must be one commit on current main");
    const files = await github.paginate(github.rest.pulls.listFiles, { ...context.repo, pull_number: pr.number, per_page: 100 });
    const before = {}, after = {};
    for (const file of files) {
      assert.ok([".release-please-manifest.json", "CHANGELOG.md", "pyproject.toml", "uv.lock"].includes(file.filename));
      before[file.filename] = await this.content(github, context.repo, file.filename, context.sha);
      after[file.filename] = await this.content(github, context.repo, file.filename, pr.head.sha);
    }
    this.files(files, before, after);
    const { data: tree } = await github.rest.git.getTree({ ...context.repo, tree_sha: commit.tree.sha, recursive: "1" });
    assert.equal(tree.truncated, false);
    for (const file of files) assert.ok(tree.tree.some(node => node.path === file.filename && node.mode === "100644" && node.type === "blob"), "No symlink/executable version files");
    for (const workflow_id of ["ci.yml", "security.yml"]) {
      const { data: current } = await github.rest.git.getRef({ ...context.repo, ref: `heads/${pr.head.ref}` });
      assert.equal(current.object.sha, pr.head.sha, "Version branch advanced; do not dispatch an unverified head");
      await github.rest.actions.createWorkflowDispatch({ ...context.repo, workflow_id, ref: pr.head.ref });
    }
    core.info(`Dispatched readonly checks for verified PR #${pr.number} at ${pr.head.sha}; no run approval or merge performed`);
  }

  static async releaseTarget({ github, context, core }, testpypi) {
    assert.equal(context.repo.owner, "mikejhill");
    assert.equal(context.actor, "mikejhill");
    assert.equal(context.ref, "refs/heads/main");
    this.sha(context.sha);
    const manifest = JSON.parse(await this.content(github, context.repo, ".release-please-manifest.json", context.sha));
    const version = this.version(manifest["."]);
    core.setOutput("sha", context.sha);
    core.setOutput("version", version);
    if (testpypi) { core.setOutput("mode", "testpypi"); return; }
    let release;
    try { release = (await github.rest.repos.getReleaseByTag({ ...context.repo, tag: `v${version}` })).data; }
    catch (error) { if (error.status !== 404) throw error; core.setOutput("mode", "none"); return; }
    if (release.assets.length !== 0) { core.setOutput("mode", "none"); return; }
    const { data: commit } = await github.rest.repos.getCommit({ ...context.repo, ref: release.tag_name });
    const { data: comparison } = await github.rest.repos.compareCommitsWithBasehead({ ...context.repo, basehead: `${commit.sha}...main` });
    this.target(version, release.tag_name, commit.sha, context.sha, comparison.merge_base_commit.sha);
    core.setOutput("tag", release.tag_name);
    core.setOutput("mode", "pypi");
  }
}

module.exports = { ReleaseBoundary };
