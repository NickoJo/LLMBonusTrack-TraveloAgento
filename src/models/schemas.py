"""
Pydantic-схемы всех контрактов системы TraveloAgento.
Единственный источник правды для структур данных между агентами.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Currency(str, Enum):
    EUR = "EUR"
    USD = "USD"
    RUB = "RUB"


class AccommodationType(str, Enum):
    HOTEL = "hotel"
    RENTAL = "rental"
    ANY = "any"


class BudgetStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    EXCEEDED = "exceeded"


class AgentStep(str, Enum):
    INTENT = "INTENT"
    BUDGET_CHECK_EARLY = "BUDGET_CHECK_EARLY"
    SEARCH = "SEARCH"
    OPTIMIZE = "OPTIMIZE"
    BUDGET_CHECK_FINAL = "BUDGET_CHECK_FINAL"
    HITL_CONFIRM = "HITL_CONFIRM"
    HITL_BUDGET = "HITL_BUDGET"
    ITINERARY = "ITINERARY"
    REPORT = "REPORT"
    DONE = "DONE"
    ERROR = "ERROR"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"


# ---------------------------------------------------------------------------
# TripProfile
# ---------------------------------------------------------------------------

class TripProfile(BaseModel):
    destination: Optional[str] = None
    origin: Optional[str] = None
    departure_date: Optional[date] = None
    return_date: Optional[date] = None
    travelers: Optional[int] = Field(default=None, ge=1, le=9)
    budget: Optional[float] = Field(default=None, gt=0)
    currency: Currency = Currency.EUR
    accommodation_type: AccommodationType = AccommodationType.ANY
    preferences: List[str] = Field(default_factory=list)

    def is_complete(self) -> bool:
        required = [
            self.destination,
            self.origin,
            self.departure_date,
            self.return_date,
            self.travelers,
            self.budget,
        ]
        return all(f is not None for f in required)

    def nights(self) -> int:
        if self.departure_date and self.return_date:
            return (self.return_date - self.departure_date).days
        return 0

    @model_validator(mode="after")
    def dates_order(self) -> TripProfile:
        if self.departure_date and self.return_date:
            if self.return_date <= self.departure_date:
                raise ValueError("return_date must be after departure_date")
        return self


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------

class Flight(BaseModel):
    id: str
    origin: str
    destination: str
    departure: str          # ISO8601
    arrival: str            # ISO8601
    price: float            # total for all travelers
    currency: Currency
    airline: str
    stops: int = 0
    connection_time_min: Optional[int] = None
    source: str = "mock_flights"


class Accommodation(BaseModel):
    id: str
    name: str
    type: str               # hotel | apartment | house | room
    city: str
    price_per_night: float
    total_price: float      # price_per_night × nights
    currency: Currency
    rating: float = Field(ge=1.0, le=5.0)
    amenities: List[str] = Field(default_factory=list)
    source: str = "mock_booking"


class SearchResults(BaseModel):
    flights: List[Flight] = Field(default_factory=list)
    accommodations: List[Accommodation] = Field(default_factory=list)
    flights_source: str = "mock_flights"
    accommodation_source: str = "mock_booking"
    accommodation_degraded: bool = False  # True если жильё недоступно


# ---------------------------------------------------------------------------
# Package & Budget
# ---------------------------------------------------------------------------

class SelectedPackage(BaseModel):
    flight: Flight
    accommodation: Accommodation
    total_cost: float
    currency: Currency
    budget_remaining: float
    optimization_notes: str = ""


class BudgetState(BaseModel):
    budget: float
    currency: Currency
    spent: float
    remaining: float
    overage_pct: float = 0.0
    status: BudgetStatus = BudgetStatus.OK


class BudgetCheckResult(BaseModel):
    ok: bool
    min_estimate: Optional[float] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Itinerary
# ---------------------------------------------------------------------------

class ActivityItem(BaseModel):
    time: str           # "09:00"
    description: str
    location: Optional[str] = None


class DayPlan(BaseModel):
    day: int
    date: str           # "YYYY-MM-DD"
    title: str
    activities: List[ActivityItem]


class Itinerary(BaseModel):
    destination: str
    days: List[DayPlan]
    sources: List[str] = Field(default_factory=list)
    rag_used: bool = False


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str           # "user" | "assistant"
    content: str


# ---------------------------------------------------------------------------
# Session State
# ---------------------------------------------------------------------------

class SessionState(BaseModel):
    session_id: str
    created_at: str
    status: SessionStatus = SessionStatus.ACTIVE

    # Counters
    turn_count: int = 0
    llm_call_count: int = 0
    agent_step: AgentStep = AgentStep.INTENT

    # Travel data (filled progressively)
    trip_profile: TripProfile = Field(default_factory=TripProfile)
    search_results: Optional[SearchResults] = None
    selected_package: Optional[SelectedPackage] = None
    budget_state: Optional[BudgetState] = None
    itinerary: Optional[Itinerary] = None
    final_report: Optional[str] = None

    # Dialog
    dialog_history: List[Message] = Field(default_factory=list)
    dialog_summary: str = ""

    # PII (in-memory only, cleared on session end)
    pii_token_map: dict = Field(default_factory=dict)

    # Flags
    hitl_pending: bool = False
    risk_flags: List[str] = Field(default_factory=list)

    class Config:
        use_enum_values = False

    def clear_pii(self) -> None:
        self.pii_token_map.clear()

    def reset_search(self) -> None:
        """Partial reset when user changes destination."""
        self.search_results = None
        self.selected_package = None
        self.itinerary = None
        self.budget_state = None
        self.agent_step = AgentStep.SEARCH
