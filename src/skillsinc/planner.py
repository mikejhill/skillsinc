"""Build a plan: read sources, render through profiles, compare with destinations."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Final

from skillsinc.config import Config, DestinationConfig, RouteConfig
from skillsinc.diff import DestinationDiff
from skillsinc.exceptions import ConfigurationError, SourceError
from skillsinc.manifest import ManifestStore
from skillsinc.models import Customization, EntryOutput, FileOutput
from skillsinc.paths import Environment
from skillsinc.plan import DestinationPlan, Plan, Skip
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.sources import SourceReader

logger = logging.getLogger(__name__)

WARNING_SAMPLE: Final = 5


@dataclass(slots=True)
class DesiredState:
    """Everything one destination should contain (accumulated while rendering)."""

    files: dict[str, FileOutput] = field(default_factory=dict)
    blocks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    entries: dict[str, dict[str, EntryOutput]] = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)
    skipped: list[Skip] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Planner:
    """Turn configuration plus current disk state into a ``Plan``."""

    def __init__(
        self,
        profiles: ProfileRegistry,
        reader: SourceReader,
        store: ManifestStore,
        environment: Environment,
    ) -> None:
        self._profiles = profiles
        self._reader = reader
        self._store = store
        self._environment = environment

    def plan(
        self, config: Config, *, only: Sequence[str] = (), adopt: bool = False
    ) -> Plan:
        """Return the plan for every enabled destination (or just ``only``).

        Raises:
            ConfigurationError: If ``only`` names an unknown destination.
        """
        targets = self._targets(config, only)
        routes = [
            route
            for route in config.routes
            if any(name in route.destinations for name in targets)
        ]
        items, failures = self._read_sources(config, routes)
        plans = tuple(
            self._plan_destination(
                config, config.destinations[name], routes, items, failures, adopt=adopt
            )
            for name in targets
        )
        return Plan(
            destinations=plans,
            source_errors=failures,
            warnings=self._duplicates(plans),
        )

    # Steps

    @staticmethod
    def _targets(config: Config, only: Sequence[str]) -> list[str]:
        """Return the destination names to plan, in configuration order."""
        unknown = [name for name in only if name not in config.destinations]
        if unknown:
            msg = f"unknown destination(s): {', '.join(unknown)}"
            raise ConfigurationError(msg)
        return [
            name
            for name, destination in config.destinations.items()
            if destination.enabled and (not only or name in only)
        ]

    def _read_sources(
        self, config: Config, routes: Iterable[RouteConfig]
    ) -> tuple[dict[str, tuple[Customization, ...]], dict[str, str]]:
        """Read every routed source once, collecting failures instead of raising."""
        names = sorted({name for route in routes for name in route.sources})
        items: dict[str, tuple[Customization, ...]] = {}
        failures: dict[str, str] = {}
        for name in names:
            try:
                items[name] = self._reader.read(config.sources[name])
            except SourceError as err:
                logger.warning("%s", err)
                failures[name] = str(err)
        return items, failures

    def _plan_destination(  # noqa: PLR0913 — the planning context for one destination
        self,
        config: Config,
        destination: DestinationConfig,
        routes: Sequence[RouteConfig],
        items: Mapping[str, tuple[Customization, ...]],
        failures: Mapping[str, str],
        *,
        adopt: bool,
    ) -> DestinationPlan:
        """Plan one destination, refusing to touch it if any of its sources failed."""
        own_routes = [
            route for route in routes if destination.name in route.destinations
        ]
        failed = sorted(
            {name for route in own_routes for name in route.sources if name in failures}
        )
        if failed:
            reason = (
                f"source(s) {', '.join(failed)} could not be read;"
                " destination left unchanged"
            )
            return DestinationPlan(destination, errors=(reason,))

        overlap = self._source_overlap(config, destination, own_routes)
        if overlap is not None:
            return DestinationPlan(destination, errors=(overlap,))

        desired = self._render(config, destination, own_routes, items)
        if desired.errors:
            return DestinationPlan(destination, errors=tuple(desired.errors))

        try:
            manifest = self._store.load(destination.name, destination.path)
        except ConfigurationError as err:
            return DestinationPlan(destination, errors=(str(err),))
        outside = self._profiles.get(destination.profile).outside
        diff = DestinationDiff(destination.path, manifest, adopt=adopt, outside=outside)
        entries = {
            path: tuple(found.values()) for path, found in desired.entries.items()
        }
        changes, errors = diff.compare(desired.files, desired.blocks, entries)
        return DestinationPlan(
            destination,
            changes=() if errors else changes,
            skipped=tuple(desired.skipped),
            notes=tuple(desired.notes),
            errors=errors,
            delivered=tuple(desired.delivered),
        )

    @staticmethod
    def _source_overlap(
        config: Config, destination: DestinationConfig, routes: Sequence[RouteConfig]
    ) -> str | None:
        """Return an error if the destination resolves into or around a routed source.

        Links are followed, so a destination linked into the library is caught.
        """
        target = destination.path.resolve()
        for name in sorted({name for route in routes for name in route.sources}):
            source = config.sources[name].path.resolve()
            if target.is_relative_to(source) or source.is_relative_to(target):
                return (
                    f"destination {destination.path} overlaps source '{name}'"
                    f" ({source}); a sync would write into the library"
                )
        return None

    def _duplicates(self, plans: Sequence[DestinationPlan]) -> tuple[str, ...]:
        """Warn when one harness would load the same item from two destinations.

        A harness reads its own destination plus the kinds its profile lists in
        ``also_reads``, but only from destinations at that profile's default
        directory, since harnesses search their documented folders only.
        """
        warnings: list[str] = []
        for reader in plans:
            profile = self._profiles.get(reader.destination.profile)
            if not profile.harness:
                continue
            seen = self._loads(reader, plans)
            groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
            for label, names in seen.items():
                if len(names) > 1:
                    groups[tuple(names)].append(label.partition(":")[2])
            warnings.extend(
                f"{profile.harness} loads {len(labels)} item(s) from both"
                f" {' and '.join(names)}: {self._sample(labels)};"
                " exclude them from one route"
                for names, labels in sorted(groups.items())
            )
        return tuple(warnings)

    def _loads(
        self, reader: DestinationPlan, plans: Sequence[DestinationPlan]
    ) -> dict[str, list[str]]:
        """Map each item ``reader``'s harness loads to the destinations it is in."""
        also_reads = self._profiles.get(reader.destination.profile).also_reads
        seen: dict[str, list[str]] = defaultdict(list)
        if self._at_default(reader.destination):
            for label in reader.delivered:
                seen[label].append(reader.destination.name)
        for other in plans:
            kinds = also_reads.get(other.destination.profile)
            if other is reader or kinds is None:
                continue
            if not self._at_default(other.destination):
                continue
            for label in other.delivered:
                if self._kind(label) in kinds:
                    seen[label].append(other.destination.name)
        return seen

    def _covering(
        self, config: Config, destination: DestinationConfig, item: Customization
    ) -> str | None:
        """Return the destination that already delivers ``item`` to this harness.

        Only destinations at their profile's default directory count, on both
        sides, and only if a route sends ``item`` there and it renders without
        a refusal or an unaccepted loss. Every enabled destination is checked,
        not just the planned ones, so ``--destination`` gives the same result.
        """
        covered_by = self._profiles.get(destination.profile).covered_by
        if not covered_by or not self._at_default(destination):
            return None
        for other in config.destinations.values():
            kinds = covered_by.get(other.profile)
            if (
                kinds is None
                or item.kind not in kinds
                or other is destination
                or not other.enabled
                or not self._at_default(other)
            ):
                continue
            routes = [
                route
                for route in config.routes
                if other.name in route.destinations and route.selects(item)
            ]
            if not routes:
                continue
            rendering = self._profiles.get(other.profile).render(item)
            losses = (*rendering.losses, *item.losses)
            allowed = any(route.allow_lossy for route in routes)
            if rendering.unsupported is None and (allowed or not losses):
                return other.name
        return None

    def _at_default(self, destination: DestinationConfig) -> bool:
        """Return True if the destination is its profile's default directory."""
        default = self._profiles.get(destination.profile).default_path(
            self._environment
        )
        return default is not None and default == destination.path

    @staticmethod
    def _sample(labels: list[str]) -> str:
        """Return up to five sorted item names, counting the rest."""
        shown = sorted(labels)[:WARNING_SAMPLE]
        rest = len(labels) - len(shown)
        return ", ".join(shown) + (f" and {rest} more" if rest else "")

    @staticmethod
    def _kind(label: str) -> str:
        """Return the kind directory named in a ``source:kind/name`` label."""
        return label.partition(":")[2].partition("/")[0]

    def _render(
        self,
        config: Config,
        destination: DestinationConfig,
        routes: Sequence[RouteConfig],
        items: Mapping[str, tuple[Customization, ...]],
    ) -> DesiredState:
        """Render every routed item through the destination's profile."""
        profile = self._profiles.get(destination.profile)
        selected = self._select(routes, items)
        desired = DesiredState()
        for label, (item, allow_lossy) in sorted(selected.items()):
            if item.refusal is not None:
                desired.skipped.append(Skip(label, item.refusal))
                continue
            cover = self._covering(config, destination, item)
            if cover is not None:
                reason = f"{profile.harness} already loads it from destination {cover}"
                desired.skipped.append(Skip(label, reason))
                continue
            rendering = profile.render(item)
            if rendering.unsupported is not None:
                desired.skipped.append(Skip(label, rendering.unsupported))
                continue
            losses = (*rendering.losses, *item.losses)
            if losses and not allow_lossy:
                desired.skipped.append(Skip(label, "; ".join(losses), lossy=True))
                continue
            desired.notes.extend(f"{label}: {loss}" for loss in losses)
            desired.delivered.append(label)
            self._add_files(desired, label, rendering.files)
            for block in rendering.blocks:
                desired.blocks.setdefault(block.path, []).append((label, block.text))
            entries = (
                replace(
                    entry,
                    path=profile.locate(
                        entry.path, destination.path, self._environment
                    ),
                )
                for entry in rendering.entries
            )
            self._add_entries(desired, label, entries)
        return desired

    @staticmethod
    def _select(
        routes: Sequence[RouteConfig], items: Mapping[str, tuple[Customization, ...]]
    ) -> dict[str, tuple[Customization, bool]]:
        """Return routed items keyed by ``source:kind/name`` with loss permission."""
        allow: dict[str, bool] = defaultdict(bool)
        found: dict[str, Customization] = {}
        for route in routes:
            for source in route.sources:
                for item in items.get(source, ()):
                    if route.selects(item):
                        key = f"{item.source}:{item.selector}"
                        found[key] = item
                        allow[key] = allow[key] or route.allow_lossy
        return {key: (item, allow[key]) for key, item in found.items()}

    @staticmethod
    def _add_files(
        desired: DesiredState, label: str, files: Iterable[FileOutput]
    ) -> None:
        """Add rendered files, deduplicating identical outputs and flagging clashes."""
        for output in files:
            previous = desired.files.get(output.path)
            if previous is not None and previous != output:
                other = desired.origins[output.path]
                desired.errors.append(
                    f"{output.path}: {other} and {label} produce different content"
                )
                continue
            desired.files[output.path] = output
            desired.origins.setdefault(output.path, label)

    @staticmethod
    def _add_entries(
        desired: DesiredState, label: str, entries: Iterable[EntryOutput]
    ) -> None:
        """Add rendered config entries, flagging items that set one key differently.

        Mergeable list entries are concatenated, skipping elements already present.
        """
        for output in entries:
            found = desired.entries.setdefault(output.path, {})
            origin = f"{output.path}#{output.entry}"
            previous = found.get(output.entry)
            if (
                previous is not None
                and previous.merge
                and output.merge
                and isinstance(previous.value, list)
                and isinstance(output.value, list)
            ):
                merged = list(previous.value)
                merged.extend(value for value in output.value if value not in merged)
                found[output.entry] = replace(previous, value=merged)
                continue
            if previous is not None and previous != output:
                other = desired.origins[origin]
                desired.errors.append(
                    f"{output.path}: entry {output.entry}: {other} and {label}"
                    " produce different content"
                )
                continue
            found[output.entry] = output
            desired.origins.setdefault(origin, label)
