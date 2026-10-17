"""Canonical data model. Events are *instances* (a recurring market on two
dates yields two events linked by series_id). Suitability is four-state:
unknown never silently passes a strict filter."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def uid(prefix: str = "ev") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_utc() -> datetime:
    return datetime.now(UTC)


class Suitability(StrEnum):
    VERIFIED_YES = "verified_yes"
    VERIFIED_NO = "verified_no"
    LIKELY = "likely"
    UNKNOWN = "unknown"

    @property
    def passes_strict(self) -> bool:
        return self in (Suitability.VERIFIED_YES, Suitability.LIKELY)


class Audience(StrEnum):
    VERIFIED_ADULT = "verified_adult"  # 18+/21+ with evidence
    ADULTS_LEANING = "adults_leaning"  # venue/genre prior (brewery, comedy, ...)
    ALL_AGES = "all_ages"
    UNKNOWN = "unknown"


class EffortState(StrEnum):
    COMPUTED = "computed"
    LIKELY = "likely"
    UNKNOWN = "unknown"


class Energy(StrEnum):
    TIRED = "tired"
    NORMAL = "normal"
    ADVENTUROUS = "adventurous"
    ESCAPE = "escape"


class Party(StrEnum):
    FAMILY = "family"
    ADULTS = "adults"


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"
    RESCHEDULED = "rescheduled"


class Evidence(BaseModel):
    state: Suitability | str
    quote: str = ""
    source_url: str = ""
    method: str = "rule"  # rule | source_default | llm | user
    ts: str = Field(default_factory=lambda: now_utc().isoformat())


class Venue(BaseModel):
    id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    address: str = ""
    venue_type: str = ""  # library | park | museum | zoo | arena | brewery | ...
    indoor_outdoor: str = "unknown"  # indoor | outdoor | mixed | unknown
    source: str = ""
    visit_count: int = 0  # from location import, if any


class EffortStatic(BaseModel):
    """Effort components derivable from the event itself, at ingest time."""

    duration_hours: float | None = None
    physical_intensity: float | None = None  # 0..1
    intensity_state: EffortState = EffortState.UNKNOWN
    overnight: bool = False
    indoor_outdoor: str = "unknown"
    stroller_friendly: bool | None = None
    cost_min: float | None = None  # per-person
    cost_max: float | None = None


class EffortComputed(BaseModel):
    """Effort relative to a party + context, at query/digest time."""

    drive_minutes: float | None = None
    drive_state: EffortState = EffortState.UNKNOWN
    kid_nap_overlap_min: float = 0.0
    planning_lead_days: float | None = None
    booking_required: bool = False
    effort_score: float | None = None  # 0..100
    effort_band: str | None = None  # low | medium | high
    band_state: EffortState = EffortState.UNKNOWN


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uid())
    series_id: str | None = None
    title: str
    description: str = ""
    url: str = ""
    source_id: str = ""
    source_event_id: str = ""
    start: datetime | None = None
    end: datetime | None = None
    timezone: str = "America/Los_Angeles"
    venue_id: str | None = None
    category: str = "other"
    subcategory: str = ""
    organizer: str = ""
    price_min: float | None = None
    price_max: float | None = None
    is_free: bool = False
    age_min: int | None = None  # venue-imposed minimum age
    status: EventStatus = EventStatus.SCHEDULED
    kid: Suitability = Suitability.UNKNOWN
    dog: Suitability = Suitability.UNKNOWN
    dog_leash_required: bool | None = None
    audience: Audience = Audience.UNKNOWN
    effort: EffortStatic = Field(default_factory=EffortStatic)
    provenance: list[dict] = Field(default_factory=list)
    first_seen: str = Field(default_factory=lambda: now_utc().isoformat())
    last_verified: str = Field(default_factory=lambda: now_utc().isoformat())
    embedding: bytes | None = None


class Feedback(BaseModel):
    ts: str = Field(default_factory=lambda: now_utc().isoformat())
    event_id: str
    signal: str  # interested | not_interested | went | dismissed | too_far | too_pricey | too_much_effort
    effort_match: str | None = None  # right_effort | too_much | too_little
    mood: str | None = None  # Energy at decision time
    note: str = ""

    @property
    def is_positive(self) -> bool:
        return self.signal in ("interested", "went")


class Household(BaseModel):
    """Declared household config + learned state. Stored as JSON in kv table."""

    home_lat: float | None = None
    home_lon: float | None = None
    home_label: str = ""
    kid_ages: list[int] = Field(default_factory=list)
    nap_windows: list[list[str]] = Field(default_factory=list)  # [["12:30","14:30"]]
    has_dog: bool = False
    dog_size: str = "medium"
    transport_mode: str = "car"
    budget_per_outing: float = 60.0
    interests: list[str] = Field(default_factory=list)
    # Learned tolerance curves, per mood: logistic center (minutes / hours / dollars)
    drive_tolerance: dict[str, float] = Field(
        default_factory=lambda: {"tired": 25.0, "normal": 45.0, "adventurous": 80.0, "escape": 160.0}
    )
    duration_tolerance: dict[str, float] = Field(
        default_factory=lambda: {"tired": 2.0, "normal": 3.5, "adventurous": 6.0, "escape": 48.0}
    )
    facet_alpha: dict[str, float] = Field(default_factory=dict)
    facet_beta: dict[str, float] = Field(default_factory=dict)
    category_momentum: dict[str, float] = Field(default_factory=dict)
