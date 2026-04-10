"""
BudgetTracker — полностью детерминированная логика, без LLM.
Две точки проверки: ранняя (до поиска) и финальная (после сборки пакета).
"""
from models.schemas import (
    BudgetCheckResult, BudgetState, BudgetStatus,
    Currency, SelectedPackage, TripProfile,
)

# Минимальные рыночные цены для ранней проверки бюджета
# flight_per_person — цена одного перелёта в одну сторону на человека
MIN_MARKET_PRICES: dict[str, dict] = {
    Currency.EUR: {"flight_per_person": 80.0,   "accommodation_per_night": 40.0},
    Currency.USD: {"flight_per_person": 90.0,   "accommodation_per_night": 45.0},
    Currency.RUB: {"flight_per_person": 5000.0, "accommodation_per_night": 2500.0},
}


def early_check(trip_profile: TripProfile) -> BudgetCheckResult:
    """
    Быстрая проверка до поиска: реалистичен ли бюджет?
    Предупреждаем если budget < 80% от минимальной рыночной цены.
    Фактор 0.8: даём 20% запас — вдруг дешёвые рейсы и хостел.
    """
    currency = trip_profile.currency
    prices = MIN_MARKET_PRICES.get(currency, MIN_MARKET_PRICES[Currency.EUR])

    # round trip: ×2 на перелёт
    min_flight = prices["flight_per_person"] * (trip_profile.travelers or 1) * 2
    nights = trip_profile.nights()
    min_hotel = prices["accommodation_per_night"] * max(nights, 1)
    min_total = min_flight + min_hotel

    budget = trip_profile.budget or 0.0

    if budget < min_total * 0.8:
        msg = (
            f"Бюджет {budget:.0f} {currency.value} выглядит нереалистично. "
            f"Минимальная оценка для этой поездки: ~{min_total:.0f} {currency.value} "
            f"(перелёт ~{min_flight:.0f} + жильё ~{min_hotel:.0f})."
        )
        return BudgetCheckResult(ok=False, min_estimate=min_total, message=msg)

    return BudgetCheckResult(ok=True)


def final_check(package: SelectedPackage, budget: float) -> BudgetState:
    """
    Финальная проверка после сборки пакета.
    Статусы: ok (≤0%), warning (0–10%), exceeded (>10%).
    """
    spent = package.total_cost
    remaining = budget - spent
    overage_pct = max(0.0, (spent - budget) / budget * 100) if budget > 0 else 0.0

    if overage_pct == 0:
        status = BudgetStatus.OK
    elif overage_pct <= 10:
        status = BudgetStatus.WARNING
    else:
        status = BudgetStatus.EXCEEDED

    return BudgetState(
        budget=budget,
        currency=package.currency,
        spent=round(spent, 2),
        remaining=round(remaining, 2),
        overage_pct=round(overage_pct, 1),
        status=status,
    )
