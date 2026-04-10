"""
OptimizationAgent — детерминированное ранжирование, без LLM.
Выбирает лучшую комбинацию рейс × жильё по взвешенной формуле:
    score = price_norm * 0.6 + rating_norm * 0.4
где price_norm = 1 - (price / max_price)  → меньше цена = лучше
    rating_norm = rating / 5.0            → выше рейтинг = лучше
"""
from models.schemas import (
    Accommodation, Currency, Flight,
    SearchResults, SelectedPackage, TripProfile,
)


def optimize(search_results: SearchResults, trip_profile: TripProfile) -> SelectedPackage:
    flights = search_results.flights
    accommodations = search_results.accommodations

    if not flights:
        raise ValueError("No flights available for optimization")

    budget = trip_profile.budget or float("inf")

    # 1. Собираем все комбинации
    all_combos: list[tuple[Flight, Accommodation | None]] = []

    if accommodations:
        for f in flights:
            for h in accommodations:
                all_combos.append((f, h))
    else:
        # Degraded mode — только рейсы
        for f in flights:
            all_combos.append((f, None))

    # 2. Фильтрация по бюджету
    def combo_cost(f: Flight, h: Accommodation | None) -> float:
        return f.price + (h.total_price if h else 0.0)

    valid = [(f, h) for f, h in all_combos if combo_cost(f, h) <= budget]

    # Если ничего не влезает — берём всё (overage будет зафиксирован BudgetTracker)
    if not valid:
        valid = all_combos

    # 3. Взвешенная формула
    max_cost = max(combo_cost(f, h) for f, h in valid) or 1.0
    max_rating = 5.0

    def score(f: Flight, h: Accommodation | None) -> float:
        price_norm = 1.0 - combo_cost(f, h) / max_cost
        rating_norm = (h.rating / max_rating) if h else 0.0
        return price_norm * 0.6 + rating_norm * 0.4

    best_f, best_h = max(valid, key=lambda c: score(*c))
    total = round(combo_cost(best_f, best_h), 2)

    # Создаём placeholder если нет жилья
    if best_h is None:
        best_h = Accommodation(
            id="none",
            name="Жильё не найдено — поиск был ограничен",
            type="unknown",
            city=trip_profile.destination or "",
            price_per_night=0,
            total_price=0,
            currency=best_f.currency,
            rating=1.0,  # minimum valid; placeholder has no real rating
        )

    notes = _build_notes(best_f, best_h, total, budget, search_results.accommodation_degraded)

    return SelectedPackage(
        flight=best_f,
        accommodation=best_h,
        total_cost=total,
        currency=best_f.currency,
        budget_remaining=round(budget - total, 2),
        optimization_notes=notes,
    )


def _build_notes(
    f: Flight, h: Accommodation, total: float, budget: float, degraded: bool
) -> str:
    parts = []
    if degraded:
        parts.append("Жильё: использован резервный источник (Airbnb).")
    if f.stops > 0 and f.connection_time_min and f.connection_time_min < 50:
        parts.append(f"⚠️ Короткая стыковка: {f.connection_time_min} мин.")
    if total > budget:
        parts.append(f"Стоимость ({total:.0f}) превышает бюджет ({budget:.0f}).")
    return " ".join(parts)
