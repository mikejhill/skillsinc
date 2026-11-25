"""Sync profiles: loading, selection, narrowing, and the CLI that exposes them."""

from __future__ import annotations

import io
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from tests.conftest import Sandbox
from tests.test_schedule import FakeRunner
from tests.test_schedule import settings as schedule_settings

from skillsinc.commands import ScheduleCommand
from skillsinc.conditions import Condition, host_name, system_name
from skillsinc.config import ConfigLoader
from skillsinc.exceptions import ConfigurationError
from skillsinc.paths import Environment
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.schedule import Completed
from skillsinc.selection import PROFILES_VARIABLE, Origin, ProfileSelector

pytestmark = pytest.mark.integration


def with_variables(environment: Environment, **variables: str) -> Environment:
    """Return ``environment`` with extra variables."""
    return replace(environment, variables={**environment.variables, **variables})


def write(sandbox: Sandbox, **extra: object) -> Path:
    """Write a config with work and home destinations, routes tagged by profile."""
    root = sandbox.environment.home
    data: dict[str, object] = {
        "version": 1,
        "sources": {"library": {"path": sandbox.library.as_posix()}},
        "destinations": {
            "shared": {"profile": "generic", "path": (root / "shared").as_posix()},
            "work": {"profile": "generic", "path": (root / "work").as_posix()},
            "home": {"profile": "generic", "path": (root / "home").as_posix()},
        },
        "profiles": {
            "work": {
                "description": "Work machine",
                "when": {"host": ["X1*"]},
            },
            "home": {"default": False},
        },
        "routes": [
            {
                "sources": ["library"],
                "destinations": ["shared"],
                "include": ["skills/*"],
                "allow_lossy": True,
            },
            {
                "sources": ["library"],
                "destinations": ["work", "shared"],
                "include": ["agents/*"],
                "allow_lossy": True,
                "profiles": ["work"],
            },
            {
                "sources": ["library"],
                "destinations": ["home"],
                "include": ["prompts/*"],
                "allow_lossy": True,
                "profiles": ["home"],
            },
        ],
        **extra,
    }
    sandbox.config_path.parent.mkdir(parents=True, exist_ok=True)
    sandbox.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return sandbox.config_path


def run(
    sandbox: Sandbox, *argv: str, host: str = "laptop", **variables: str
) -> tuple[int, str]:
    """Run the CLI on a machine called ``host``."""
    environment = with_variables(
        sandbox.environment, SKILLSINC_HOSTNAME=host, **variables
    )
    return Sandbox(environment, sandbox.library).run(*argv)


def load(sandbox: Sandbox, text: str) -> None:
    """Load a config whose top-level members are ``text`` (raises on errors)."""
    library = sandbox.library.as_posix()
    sandbox.config_path.parent.mkdir(parents=True, exist_ok=True)
    sandbox.config_path.write_text(
        '{"version": 1, "sources": {"library": {"path": "'
        + library
        + '"}}, "destinations": {"d": {"profile": "generic", "path": "d"}}, '
        + text
        + "}",
        encoding="utf-8",
    )
    ConfigLoader(ProfileRegistry(), sandbox.environment).load(sandbox.config_path)


class TestLoading:
    """The loader validates profiles and route tags."""

    def test_profiles_and_tags_load(self, sandbox: Sandbox) -> None:
        """Profiles, conditions, and route tags become typed values."""
        write(sandbox)
        config = sandbox.load()

        work = config.profiles["work"]
        assert (work.description, work.default, work.when) == (
            "Work machine",
            True,
            Condition(hosts=("X1*",)),
        ), f"Got {work!r}"
        assert config.profiles["home"].default is False, "Expected default false"
        assert [route.profiles for route in config.routes] == [
            (),
            ("work",),
            ("home",),
        ], "Expected route tags in order"

    def test_config_without_profiles_is_unchanged(self, sandbox: Sandbox) -> None:
        """A configuration without profiles selects every route."""
        config = sandbox.configure()
        narrowed, selection = ProfileSelector(sandbox.environment).select(config)

        assert (narrowed, selection.origin) == (config, Origin.NONE), (
            "Expected the configuration untouched"
        )

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            (
                '"routes": [{"sources": ["library"], "destinations": ["d"],'
                ' "profiles": ["nope"]}]',
                "undefined profile(s): nope",
            ),
            ('"profiles": {"bad name": {}}', "invalid profile name"),
            ('"profiles": {"p": {"when": {}}}', "list at least one of host"),
            ('"profiles": {"p": {"when": {"os": ["bsd"]}}}', "unknown system(s) bsd"),
            ('"profiles": {"p": {"when": {"env": {"A": 1}}}}', "an object of variable"),
            ('"profiles": {"p": {"when": {"env": []}}}', "an object of variable"),
            ('"profiles": {"p": {"when": {"env": {"A": ""}}}}', "an object of var"),
            ('"profiles": {"p": {"colour": "red"}}', "unknown key(s): colour"),
            ('"profiles": {"p": {"when": {"hosts": ["a"]}}}', "unknown key(s): hosts"),
            ('"profiles": {"p": {"default": "no"}}', "expected true or false"),
            ('"profiles": []', "expected an object"),
        ],
    )
    def test_invalid_profiles_are_refused(
        self, sandbox: Sandbox, text: str, message: str
    ) -> None:
        """Mistakes in profiles are configuration errors naming the key."""
        with pytest.raises(ConfigurationError, match=re.escape(message)):
            load(sandbox, text)


class TestConditions:
    """``when`` matches host, operating system, and variables."""

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("win32", "windows"),
            ("darwin", "macos"),
            ("linux", "linux"),
            ("freebsd14", "freebsd14"),
        ],
    )
    def test_system_names(self, platform: str, expected: str) -> None:
        """``sys.platform`` values map to the names ``os`` accepts."""
        assert system_name(platform) == expected, f"{platform} -> {expected}"

    def test_host_name_defaults_to_the_machine(self, environment: Environment) -> None:
        """Without the override the socket host name is used."""
        assert host_name(environment), "Expected a host name"

    @pytest.mark.parametrize(
        ("condition", "variables", "failures"),
        [
            (Condition(hosts=("x1",)), {"SKILLSINC_HOSTNAME": "X1.corp.example"}, 0),
            (Condition(hosts=("x1*",)), {"SKILLSINC_HOSTNAME": "laptop"}, 1),
            (Condition(variables={"A": True}), {"A": "1"}, 0),
            (Condition(variables={"A": True}), {"A": ""}, 1),
            (Condition(variables={"A": False}), {}, 0),
            (Condition(variables={"A": False}), {"A": "1"}, 1),
            (Condition(variables={"A": "w*"}), {"A": "work"}, 0),
            (Condition(variables={"A": "w*"}), {"A": "home"}, 1),
            (Condition(variables={"A": "w*"}), {}, 1),
        ],
    )
    def test_conditions(
        self,
        environment: Environment,
        condition: Condition,
        variables: dict[str, str],
        failures: int,
    ) -> None:
        """Each key is checked; the failures explain what did not match."""
        found = condition.failures(with_variables(environment, **variables))

        assert len(found) == failures, f"Expected {failures} failures, got {found}"

    def test_systems(self, environment: Environment) -> None:
        """``os`` compares the normalized system name."""
        windows = replace(environment, platform="win32")

        assert Condition(systems=("windows",)).failures(windows) == [], "Expected ok"
        assert Condition(systems=("linux", "macos")).failures(windows) == [
            "system windows is not linux or macos"
        ], "Expected one failure"


class TestSelection:
    """The command line, the variable, and defaults choose active profiles."""

    def select(
        self,
        sandbox: Sandbox,
        requested: tuple[str, ...] = (),
        variables: dict[str, str] | None = None,
    ) -> tuple[tuple[str, ...], Origin, tuple[str, ...], tuple[str, ...]]:
        """Return the active profiles, origin, routed destinations, and warnings."""
        write(sandbox)
        environment = with_variables(sandbox.environment, **(variables or {}))
        config = ConfigLoader(ProfileRegistry(), environment).load(sandbox.config_path)
        narrowed, selection = ProfileSelector(environment).select(config, requested)
        enabled = tuple(
            name
            for name, destination in narrowed.destinations.items()
            if destination.enabled
        )
        return selection.active, selection.origin, enabled, selection.warnings

    def test_defaults_follow_conditions(self, sandbox: Sandbox) -> None:
        """On a non-matching host only untagged routes apply."""
        assert self.select(sandbox, variables={"SKILLSINC_HOSTNAME": "laptop"}) == (
            (),
            Origin.DEFAULTS,
            ("shared",),
            (),
        ), "Expected work and home destinations left alone"

    def test_defaults_on_matching_host(self, sandbox: Sandbox) -> None:
        """A matching ``when`` activates the profile; default false stays off."""
        active, _, enabled, _ = self.select(
            sandbox, variables={"SKILLSINC_HOSTNAME": "X1-desk"}
        )

        assert (active, enabled) == (("work",), ("shared", "work")), "Expected work"

    def test_command_line_overrides_everything(self, sandbox: Sandbox) -> None:
        """``--profile`` accepts globs and comma lists and ignores conditions."""
        active, origin, enabled, _ = self.select(
            sandbox,
            ("h*", "work,"),
            {"SKILLSINC_HOSTNAME": "laptop", PROFILES_VARIABLE: "work"},
        )

        assert (active, origin, enabled) == (
            ("work", "home"),
            Origin.COMMAND_LINE,
            ("shared", "work", "home"),
        ), "Expected both profiles from the command line"

    def test_unknown_requested_profile_fails(self, sandbox: Sandbox) -> None:
        """A ``--profile`` that matches nothing is an error listing the names."""
        with pytest.raises(ConfigurationError, match=r"unknown profile\(s\): w0rk"):
            self.select(sandbox, ("w0rk",))

    def test_variable_selects_and_warns_on_unknown(self, sandbox: Sandbox) -> None:
        """``SKILLSINC_PROFILES`` replaces defaults; unknown names only warn."""
        active, origin, _, warnings = self.select(
            sandbox,
            variables={"SKILLSINC_HOSTNAME": "X1", PROFILES_VARIABLE: "home, other"},
        )

        assert (active, origin) == (("home",), Origin.VARIABLE), "Expected home only"
        assert len(warnings) == 1, f"Expected one warning, got {warnings}"
        assert "other" in warnings[0], f"Expected the unknown name, got {warnings}"

    def test_profiles_requested_without_definitions(self, sandbox: Sandbox) -> None:
        """``--profile`` on a configuration without profiles is an error."""
        config = sandbox.configure()

        with pytest.raises(ConfigurationError, match="no profiles are defined"):
            ProfileSelector(sandbox.environment).select(config, ("work",))

    def test_disabled_destination_is_not_reported(self, sandbox: Sandbox) -> None:
        """An already disabled destination is not listed as left out by profiles."""
        write(sandbox)
        config = sandbox.load()
        disabled = replace(config.destinations["home"], enabled=False)
        config = replace(config, destinations={**config.destinations, "home": disabled})
        environment = with_variables(sandbox.environment, SKILLSINC_HOSTNAME="laptop")

        _, selection = ProfileSelector(environment).select(config)

        assert selection.inactive_destinations == ("work",), "Expected only work"


class TestCommands:
    """plan, sync, and profiles report and apply the selection."""

    def test_sync_applies_only_active_profiles(self, sandbox: Sandbox) -> None:
        """Untagged routes sync; destinations of inactive profiles stay untouched."""
        write(sandbox)
        status, output = run(sandbox, "sync")
        root = sandbox.environment.home

        assert status == 0, output
        assert "Profiles: none (from defaults)" in output, output
        assert "Left alone by profiles: work, home" in output, output
        assert (root / "shared/skills/sample/SKILL.md").is_file(), "Expected skill"
        assert not (root / "shared/agents").exists(), "Expected no work agents"
        assert not (root / "work").exists(), "Expected work untouched"

    def test_deselected_profile_items_are_removed(self, sandbox: Sandbox) -> None:
        """A shared destination follows the active set; dropping a profile cleans up.

        Its own destination is left alone, never destroyed.
        """
        write(sandbox)
        root = sandbox.environment.home
        status, output = run(sandbox, "sync", host="X1")
        assert status == 0, output
        agents = list((root / "shared").rglob("reviewer*"))
        assert agents, "Expected the work agent in the shared destination"
        assert list((root / "work").rglob("reviewer*")), "Expected work synced"

        status, output = run(sandbox, "sync", host="laptop")

        assert status == 0, output
        assert not list((root / "shared").rglob("reviewer*")), "Expected removal"
        assert list((root / "work").rglob("reviewer*")), "Expected work left alone"

    def test_plan_json_reports_profiles(self, sandbox: Sandbox) -> None:
        """The JSON report carries the origin, active set, and left-out names."""
        write(sandbox)
        status, output = run(sandbox, "plan", "--json", "-p", "home")
        data = json.loads(output)

        assert status == 0, output
        assert data["profiles"] == {
            "origin": "command line",
            "active": ["home"],
            "inactive_destinations": ["work"],
        }, f"Got {data['profiles']!r}"

    def test_plan_json_without_profiles(self, sandbox: Sandbox) -> None:
        """A configuration without profiles reports null."""
        sandbox.configure()
        status, output = run(sandbox, "plan", "--json")

        assert (status, json.loads(output)["profiles"]) == (0, None), output

    def test_variable_warning_reaches_reports(self, sandbox: Sandbox) -> None:
        """Unknown names in the variable are plan warnings, text and JSON."""
        write(sandbox)
        variables = {PROFILES_VARIABLE: "ghost"}
        _, text = run(sandbox, "plan", **variables)
        _, raw = run(sandbox, "plan", "--json", **variables)

        assert "warning: SKILLSINC_PROFILES names profile(s)" in text, text
        assert any("ghost" in warning for warning in json.loads(raw)["warnings"]), raw

    def test_destination_left_out_by_profiles_fails(self, sandbox: Sandbox) -> None:
        """``-d`` naming a destination no active profile reaches is an error."""
        write(sandbox)
        status, _ = run(sandbox, "sync", "-d", "work")

        assert status == 2, "Expected a configuration error"

    def test_profiles_command_text(self, sandbox: Sandbox) -> None:
        """``profiles`` lists each profile, whether it is active, and why."""
        write(sandbox)
        status, output = run(sandbox, "profiles", host="X1")
        rows = {line.split()[0]: line for line in output.splitlines() if line}

        assert status == 0, output
        assert "yes" in rows["work"], f"Expected work active: {output}"
        assert "conditions match" in rows["work"], output
        assert "default is false" in rows["home"], output
        assert "Selected by: defaults" in output, output
        assert "Left alone: home" in output, output

    def test_profiles_command_reasons_and_warnings(self, sandbox: Sandbox) -> None:
        """Failed conditions and unknown variable names are explained."""
        write(sandbox)
        status, output = run(sandbox, "profiles", **{PROFILES_VARIABLE: "ghost"})

        assert status == 0, output
        assert "not selected by SKILLSINC_PROFILES" in output, output
        assert "warning: SKILLSINC_PROFILES" in output, output

    def test_profiles_command_reports_host_mismatch(self, sandbox: Sandbox) -> None:
        """A failed host condition names the host and the patterns."""
        write(sandbox)
        _, output = run(sandbox, "profiles", host="laptop")

        assert "host laptop is not X1*" in output, output

    def test_profiles_everywhere(self, sandbox: Sandbox) -> None:
        """A profile without ``when`` applies everywhere."""
        write(sandbox, profiles={"work": {}, "home": {"default": False}})
        _, output = run(sandbox, "profiles")

        assert "applies everywhere" in output, output

    def test_profiles_command_json(self, sandbox: Sandbox) -> None:
        """``profiles --json`` is a versioned report."""
        write(sandbox)
        status, output = run(sandbox, "profiles", "--json", "-p", "*")
        data = json.loads(output)

        assert status == 0, output
        assert (data["origin"], data["active"]) == (
            "command line",
            ["work", "home"],
        ), output
        assert data["profiles"][0] == {
            "name": "work",
            "description": "Work machine",
            "active": True,
            "reason": "selected by command line",
            "routes": 1,
        }, output

    def test_profiles_command_without_profiles(self, sandbox: Sandbox) -> None:
        """Without profiles the command explains how to add them."""
        sandbox.configure()
        status, output = run(sandbox, "profiles")

        assert status == 0, output
        assert "No sync profiles are defined" in output, output


class TestReviewFixes:
    """Safeguards that must hold however profiles narrow the run."""

    def test_inactive_route_still_guards_its_source(self, sandbox: Sandbox) -> None:
        """A destination inside a library an inactive route reads stays blocked."""
        root = sandbox.environment.home
        inner = sandbox.library / "out"
        data = {
            "version": 1,
            "sources": {
                "library": {"path": sandbox.library.as_posix()},
                "other": {"path": (root / "other").as_posix()},
            },
            "destinations": {"d": {"profile": "generic", "path": inner.as_posix()}},
            "profiles": {"lab": {"default": False}},
            "routes": [
                {"sources": ["other"], "destinations": ["d"]},
                {"sources": ["library"], "destinations": ["d"], "profiles": ["lab"]},
            ],
        }
        (root / "other").mkdir()
        sandbox.config_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox.config_path.write_text(json.dumps(data), encoding="utf-8")

        status, output = run(sandbox, "plan")

        assert status == 1, output
        assert "overlaps source 'library'" in output, output

    @pytest.mark.parametrize("value", ["", " , "])
    def test_empty_profile_option_is_an_error(
        self, sandbox: Sandbox, value: str
    ) -> None:
        """``--profile ""`` never falls back to the variable or defaults."""
        write(sandbox)
        config = sandbox.load()

        with pytest.raises(ConfigurationError, match="names no profile"):
            ProfileSelector(sandbox.environment).select(config, (value,))

    def test_windows_variable_names_ignore_case(self, environment: Environment) -> None:
        """On Windows ``UserName`` finds ``USERNAME``; elsewhere case matters."""
        condition = Condition(variables={"UserName": "mike"})
        windows = replace(environment, platform="win32", variables={"USERNAME": "mike"})
        linux = replace(environment, platform="linux", variables={"USERNAME": "mike"})

        assert condition.failures(windows) == [], "Expected a match on Windows"
        assert condition.failures(linux) == ["UserName is not 'mike'"], "POSIX"

    def test_schedule_warns_about_uncarried_variables(self, sandbox: Sandbox) -> None:
        """``schedule install`` flags ``when.env`` variables a job does not carry."""
        write(
            sandbox,
            profiles={
                "work": {"when": {"env": {"WORK_MODE": True}}},
                "home": {"default": False, "when": {"env": {"HOME_MODE": True}}},
            },
        )
        argv = ("schedule", "install", "--scheduler", "cron", "--dry-run")

        _, output = run(sandbox, *argv)
        _, chosen = run(sandbox, *argv, **{PROFILES_VARIABLE: "work"})

        assert "Warning: profile conditions read WORK_MODE," in output, output
        assert "HOME_MODE" not in output, "default false profiles never auto-apply"
        assert "profile conditions" not in chosen, chosen

    def test_schedule_without_config_does_not_warn(self, sandbox: Sandbox) -> None:
        """A missing or invalid configuration adds no warning to ``install``."""
        _, output = run(
            sandbox, "schedule", "install", "--scheduler", "cron", "--dry-run"
        )

        assert "profile conditions" not in output, output

    def test_install_warns_about_uncarried_variables(self, sandbox: Sandbox) -> None:
        """A real install prints the same warning after registering."""
        write(sandbox, profiles={"work": {"when": {"env": {"WORK": True}}}, "home": {}})
        runner = FakeRunner({("crontab", "-l"): Completed(0, "")})
        out = io.StringIO()
        command = ScheduleCommand(sandbox.environment, runner, Path("/opt/py/bin/py"))

        status = command.run(schedule_settings(), out)

        assert status == 0, out.getvalue()
        assert "Warning: profile conditions read WORK," in out.getvalue(), out

    def test_conditions_without_env_do_not_warn(self, sandbox: Sandbox) -> None:
        """Host and system conditions need nothing from the job's environment."""
        write(sandbox)
        out = io.StringIO()
        command = ScheduleCommand(sandbox.environment, FakeRunner(), Path("/py"))

        command.run(schedule_settings(dry_run=True), out)

        assert "profile conditions" not in out.getvalue(), out.getvalue()
