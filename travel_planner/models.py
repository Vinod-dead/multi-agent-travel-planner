"""Data models shared by the API, the agents and the storage layer.

Models that hold *language-model output* are deliberately forgiving: small local
models often return numbers as strings ("₹5,000"), ``null`` instead of an empty
list, and so on. The ``BeforeValidator`` helpers below normalise those cases so a
slightly messy answer is still usable instead of raising an error.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from travel_planner.config import get_settings

_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*|-?\.\d+")


# --------------------------------------------------------------------------- coercion
def _to_number(value: Any) -> float:
    """Turn ``5000``, ``"₹5,000"``, ``"about 1.5k"`` or ``None`` into a float."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        match = _NUMBER_RE.search(text.replace(" ", ""))
        if not match:
            return 0.0
        try:
            number = float(match.group(0).replace(",", ""))
        except ValueError:
            return 0.0
        tail = text.replace(" ", "")[match.end() : match.end() + 1]
        if tail == "k":
            number *= 1_000
        elif tail == "m" and "min" not in text:
            number *= 1_000_000
        return number
    return 0.0


def _to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip(" -•*\t") for p in re.split(r"[\n;]+", value)]
        return [p for p in parts if p]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("title") or next(iter(item.values()), "")
            text = _to_str(item)
            if text:
                out.append(text)
        return out
    return [_to_str(value)] if _to_str(value) else []


def _to_int(value: Any) -> int:
    return int(round(_to_number(value)))


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    return bool(value)


Money = Annotated[float, BeforeValidator(_to_number)]
Text = Annotated[str, BeforeValidator(_to_str)]
TextList = Annotated[list[str], BeforeValidator(_to_str_list)]
Count = Annotated[int, BeforeValidator(_to_int)]
Flag = Annotated[bool, BeforeValidator(_to_bool)]


def _list_or_empty(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return value


ListOf = BeforeValidator(_list_or_empty)


class _Lenient(BaseModel):
    """Base for model-produced content: ignore unknown keys, tolerate messy values."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------- request
Style = Literal["budget", "balanced", "luxury"]


class TripRequest(BaseModel):
    """What the user wants. Validated strictly because it comes from a form/API."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    origin: str = Field("", max_length=80, description="Departure city (optional).")
    destination: str = Field(
        "", max_length=80, description="Where to go. Leave empty to let the planner choose."
    )
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(None, ge=1, description="Trip length when no dates are given.")
    travelers: int = Field(2, ge=1, le=12)
    budget: float = Field(..., gt=0, le=1_000_000_000, description="Total trip budget.")
    currency: str = Field("INR", min_length=3, max_length=3)
    style: Style = "balanced"
    interests: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field("", max_length=600)
    include_flights: bool = True
    model: str | None = Field(None, max_length=80, description="Override the LLM model.")

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha():
            raise ValueError("currency must be a 3-letter code such as INR or USD")
        return v

    @field_validator("interests")
    @classmethod
    def _clean_interests(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in v:
            item = str(item).strip()[:40]
            if item and item.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _normalise_dates(self) -> TripRequest:
        max_days = get_settings().max_days
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date must not be before start_date")
            self.days = (self.end_date - self.start_date).days + 1
        elif self.start_date and self.days:
            self.end_date = self.start_date + timedelta(days=self.days - 1)
        elif self.days is None:
            self.days = 5
        if self.days is None or self.days < 1:
            raise ValueError("trip must be at least one day long")
        if self.days > max_days:
            raise ValueError(f"trip is too long (maximum {max_days} days)")
        return self

    @property
    def nights(self) -> int:
        return max(self.days - 1, 1) if self.days else 1

    @property
    def rooms(self) -> int:
        return max(1, math.ceil(self.travelers / 2))

    def day_date(self, day_number: int) -> str:
        if not self.start_date:
            return ""
        return (self.start_date + timedelta(days=day_number - 1)).isoformat()


# --------------------------------------------------------------------------- plan parts
Source = Literal["ai", "estimate"]


class WebSource(_Lenient):
    title: Text = ""
    url: Text = ""


class Destination(_Lenient):
    name: Text = ""
    country: Text = ""
    description: Text = ""
    best_time: Text = ""
    language: Text = ""
    currency_note: Text = ""
    highlights: TextList = Field(default_factory=list)
    local_tips: TextList = Field(default_factory=list)
    food_cost_per_person_per_day: Money = 0.0
    sources: list[WebSource] = Field(default_factory=list)
    source: Source = "ai"


class FlightOption(_Lenient):
    airline: Text = ""
    origin: Text = ""
    destination: Text = ""
    depart: Text = ""
    arrive: Text = ""
    duration: Text = ""
    stops: Count = 0
    price_per_person: Money = 0.0
    notes: Text = ""
    recommended: Flag = False

    travelers: int = Field(1, exclude=False)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_price(self) -> float:
        return round(self.price_per_person * self.travelers, 2)


class FlightPlan(_Lenient):
    options: Annotated[list[FlightOption], ListOf] = Field(default_factory=list)
    note: Text = ""
    skipped: bool = False
    source: Source = "ai"

    @property
    def selected(self) -> FlightOption | None:
        return next((o for o in self.options if o.recommended), self.options[0] if self.options else None)


class LodgingOption(_Lenient):
    name: Text = ""
    kind: Text = "hotel"
    area: Text = ""
    price_per_night: Money = 0.0
    rating: Money = 0.0
    amenities: TextList = Field(default_factory=list)
    why: Text = ""
    recommended: Flag = False

    nights: int = 1
    rooms: int = 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_price(self) -> float:
        return round(self.price_per_night * self.nights * self.rooms, 2)


class LodgingPlan(_Lenient):
    options: Annotated[list[LodgingOption], ListOf] = Field(default_factory=list)
    nights: int = 1
    rooms: int = 1
    source: Source = "ai"

    @property
    def selected(self) -> LodgingOption | None:
        return next((o for o in self.options if o.recommended), self.options[0] if self.options else None)


class TransportItem(_Lenient):
    mode: Text = ""
    description: Text = ""
    cost: Money = 0.0  # total for the whole group


class TransportPlan(_Lenient):
    items: Annotated[list[TransportItem], ListOf] = Field(default_factory=list)
    tips: TextList = Field(default_factory=list)
    source: Source = "ai"

    @property
    def total(self) -> float:
        return round(sum(i.cost for i in self.items), 2)


class ItineraryItem(_Lenient):
    time: Text = ""
    title: Text = ""
    description: Text = ""
    category: Text = "sightseeing"
    cost: Money = 0.0  # total for the whole group, 0 when free


class ItineraryDay(_Lenient):
    day: Count = 0
    date: Text = ""
    title: Text = ""
    items: Annotated[list[ItineraryItem], ListOf] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def day_cost(self) -> float:
        return round(sum(i.cost for i in self.items), 2)


class ItineraryPlan(_Lenient):
    days: Annotated[list[ItineraryDay], ListOf] = Field(default_factory=list)
    source: Source = "ai"

    @property
    def total(self) -> float:
        return round(sum(d.day_cost for d in self.days), 2)


class BudgetLine(BaseModel):
    category: str
    amount: float
    percent: float = 0.0


class BudgetSummary(BaseModel):
    currency: str
    total_budget: float
    estimated_total: float
    remaining: float
    status: Literal["within", "over"]
    breakdown: list[BudgetLine]
    tips: list[str] = Field(default_factory=list)


class TripPlan(BaseModel):
    id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request: TripRequest
    mode: Literal["ai", "estimate", "mixed"] = "ai"
    model: str = ""
    destination: Destination
    flights: FlightPlan
    lodging: LodgingPlan
    transport: TransportPlan
    itinerary: ItineraryPlan
    budget: BudgetSummary
    warnings: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0

    def sections(self) -> dict[str, Source]:
        return {
            "destination": self.destination.source,
            "flights": self.flights.source,
            "lodging": self.lodging.source,
            "transport": self.transport.source,
            "itinerary": self.itinerary.source,
        }
