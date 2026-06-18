"""The Thesis — the immutable, recorded analyst view (Phase 3a).

A ``Thesis`` is the recorded *commitment*: the fair value an analyst stood behind
at a moment in time, the model run that anchored it, the load-bearing assumptions
(each with a tolerance band), and the prose case. Drift (3b) and the report
cadence (3c) are measured against this record — it is the standard of truth, so
it is **immutable**: you never edit a thesis, you record a new one that
supersedes it (HARD RULE 1 / ADR-015).

Purity (ADR-004): stdlib + pydantic only. No SQLAlchemy, no database, no clock at
construction. Persistence is expressed through the repository; the
``anchor_value_per_share`` here is a FROZEN historical fact (the value committed
to at record time), recorded as a column — NOT projected from the run on load,
because the DCF engine is versioned (``code_version``) and a thesis's fair value
is a fact about the decision made, not a view of whatever the model is now
(ADR-015, amendment 1). The record-time integrity assertion that keeps that
freeze sound lives in the repository, where it can read the run.

3b will resolve each assumption against a future ``Snapshot.inputs`` by its
``metric_key`` (a flat key into that dict — the same convention ``Claim`` uses);
3a only *records* the key. No resolver is built here.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToleranceBand(BaseModel):
    """The acceptable range around a recorded assumption.

    Either bound may be ``None`` for a one-directional band (e.g. ``lower=None,
    upper=0.30`` — "no floor, breach above 30%"). At least one bound must be set
    (a band with neither is meaningless), and ``lower <= upper`` when both are.
    """

    model_config = ConfigDict(frozen=True)

    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def _check_band(self) -> ToleranceBand:
        if self.lower is None and self.upper is None:
            raise ValueError("a tolerance band needs at least one of lower/upper")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(
                f"tolerance band lower ({self.lower}) exceeds upper ({self.upper})"
            )
        return self


class ThesisAssumption(BaseModel):
    """One load-bearing premise of a thesis.

    ``metric_key`` is a flat key into a future ``Snapshot.inputs`` (the same
    "key/path into snapshot.inputs" convention ``Claim`` uses); ``recorded_value``
    is what the analyst assumed; ``band`` is the tolerance 3b will judge a future
    actual against. 3a records these facts only — it does not resolve them.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    metric_key: str
    recorded_value: float
    band: ToleranceBand


class Thesis(BaseModel):
    """An immutable recorded analyst view, anchored to a model run.

    Identity is the ``id`` UUID. ``anchor_valuation_run_id`` is mandatory
    provenance (HARD RULE 4). ``analyst_target`` is the optional adjusted fair
    value; when ``None`` the thesis fair value falls back to the anchored run's
    frozen output. ``anchor_value_per_share`` and ``anchor_snapshot_id`` are the
    frozen-at-record-time facts of that anchor (ADR-015): persisted as columns,
    asserted consistent with the run at record time by the repository.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    recorded_at: dt.datetime

    # Provenance (always present) + the frozen anchor facts.
    anchor_valuation_run_id: int
    anchor_snapshot_id: int
    anchor_value_per_share: float

    # The analyst's adjusted view (optional) and why it differs from the model.
    analyst_target: float | None = None
    override_rationale: str | None = None

    assumptions: list[ThesisAssumption] = Field(default_factory=list)

    # Structural prose (pre-LLM, entered by hand for now).
    summary: str | None = None
    bull: str | None = None
    bear: str | None = None

    created_at: dt.datetime | None = None

    def fair_value(self) -> float:
        """The thesis fair value (the ONLY computed value in 3a).

        ``analyst_target`` when the analyst set an adjusted view; otherwise the
        anchored run's frozen output. A pure read — the model->judgment gap and
        all drift are 3b, not here.
        """
        if self.analyst_target is not None:
            return self.analyst_target
        return self.anchor_value_per_share
