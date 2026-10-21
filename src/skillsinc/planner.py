"""Build a plan: read sources, render through profiles, compare with destinations."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from skillsinc.config import Config, DestinationConfig, RouteConfig
from skillsinc.diff import DestinationDiff
from skillsinc.exceptions import ConfigurationError, SourceError
from skillsinc.manifest import ManifestStore
from skillsinc.models import Customization, EntryOutput, FileOutput
from skillsinc.plan import DestinationPlan, Plan, Skip
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.sources import SourceReader

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DesiredState:
    """Everything one destination should contain (accumulated while rendering)."""

    files: dict[str, FileOutput] = field(default_factory=dict)
    blocks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    entries: dict[str, dict[str, EntryOutput]] = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)
    skipped: list[Skip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Planner:
    """Turn configuration plus current disk state into a ``Plan``."""

    def __init__(
        self, profiles: ProfileRegistry, reader: SourceReader, store: ManifestStore
    ) -> None:
        self._profiles = profiles
        self._reader = reader
        self._store = store

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
        return Plan(destinations=plans, source_errors=failures)

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

        desired = self._render(destination, own_routes, items)
        if desired.errors:
            return DestinationPlan(destination, errors=tuple(desired.errors))

        try:
            manifest = self._store.load(destination.name, destination.path)
        except ConfigurationError as err:
            return DestinationPlan(destination, errors=(str(err),))
        diff = DestinationDiff(destination.path, manifest, adopt=adopt)
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

    def _render(
        self,
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
            rendering = profile.render(item)
            if rendering.unsupported is not None:
                desired.skipped.append(Skip(label, rendering.unsupported))
                continue
            losses = (*rendering.losses, *item.losses)
            if losses and not allow_lossy:
                desired.skipped.append(Skip(label, "; ".join(losses), lossy=True))
                continue
            desired.notes.extend(f"{label}: {loss}" for loss in losses)
            self._add_files(desired, label, rendering.files)
            for block in rendering.blocks:
                desired.blocks.setdefault(block.path, []).append((label, block.text))
            self._add_entries(desired, label, rendering.entries)
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
        """Add rendered config entries, flagging items that set one key differently."""
        for output in entries:
            found = desired.entries.setdefault(output.path, {})
            origin = f"{output.path}#{output.entry}"
            previous = found.get(output.entry)
            if previous is not None and previous != output:
                other = desired.origins[origin]
                desired.errors.append(
                    f"{output.path}: entry {output.entry}: {other} and {label}"
                    " produce different content"
                )
                continue
            found[output.entry] = output
            desired.origins.setdefault(origin, label)
