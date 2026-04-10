"""
ReportFormatter — детерминированный шаблонный рендер, без LLM.
Принимает SelectedPackage + Itinerary + BudgetState → Markdown строка.
"""
from models.schemas import BudgetState, BudgetStatus, Itinerary, SelectedPackage, SessionState


def run(session: SessionState) -> str:
    package = session.selected_package
    itinerary = session.itinerary
    budget = session.budget_state
    risk_flags = session.risk_flags

    lines: list[str] = []

    # Заголовок
    dest = itinerary.destination if itinerary else (package.flight.destination if package else "")
    lines.append(f"# Ваш план поездки в {dest}")
    lines.append("")

    # Блок рейса
    if package:
        f = package.flight
        lines.append("## Перелёт")
        lines.append(f"- **Маршрут:** {f.origin} → {f.destination}")
        lines.append(f"- **Вылет:** {_fmt_dt(f.departure)}")
        lines.append(f"- **Прилёт:** {_fmt_dt(f.arrival)}")
        lines.append(f"- **Авиакомпания:** {f.airline}")
        stops_str = "прямой" if f.stops == 0 else f"{f.stops} пересадка"
        if f.stops > 0 and f.connection_time_min:
            stops_str += f" ({f.connection_time_min} мин)"
        lines.append(f"- **Пересадки:** {stops_str}")
        lines.append(f"- **Стоимость перелёта:** {f.price:.0f} {f.currency.value}")
        lines.append("")

        # Жильё
        h = package.accommodation
        if h.id != "none":
            lines.append("## Жильё")
            lines.append(f"- **Название:** {h.name}")
            lines.append(f"- **Тип:** {h.type}")
            lines.append(f"- **Рейтинг:** {'⭐' * round(h.rating)} ({h.rating:.1f}/5)")
            if h.amenities:
                lines.append(f"- **Удобства:** {', '.join(h.amenities)}")
            lines.append(f"- **Стоимость:** {h.price_per_night:.0f} {h.currency.value}/ночь "
                         f"× {_nights(package)} ночей = **{h.total_price:.0f} {h.currency.value}**")
            lines.append("")

        # Бюджет
        if budget:
            lines.append("## Бюджет")
            lines.append(f"| Статья | Сумма |")
            lines.append(f"|---|---|")
            lines.append(f"| Перелёт | {f.price:.0f} {budget.currency.value} |")
            if h.id != "none":
                lines.append(f"| Жильё | {h.total_price:.0f} {budget.currency.value} |")
            lines.append(f"| **Итого** | **{budget.spent:.0f} {budget.currency.value}** |")
            lines.append(f"| Бюджет | {budget.budget:.0f} {budget.currency.value} |")
            remaining_str = f"{budget.remaining:.0f}" if budget.remaining >= 0 else f"−{abs(budget.remaining):.0f}"
            lines.append(f"| Остаток | {remaining_str} {budget.currency.value} |")
            lines.append("")

            if budget.status == BudgetStatus.WARNING:
                lines.append(f"> ⚠️ Стоимость превышает бюджет на {budget.overage_pct:.1f}%.")
                lines.append("")
            elif budget.status == BudgetStatus.EXCEEDED:
                lines.append(f"> 🔴 Стоимость превышает бюджет на {budget.overage_pct:.1f}%. "
                              f"Вы подтвердили продолжение.")
                lines.append("")

    # Предупреждения
    if "short_connection" in risk_flags:
        lines.append("> ⚠️ **Внимание:** в маршруте есть стыковка менее 50 минут. "
                     "Рекомендуем иметь ручную кладь и не задерживаться при посадке.")
        lines.append("")

    # Итинерарий
    if itinerary and itinerary.days:
        lines.append("## Программа по дням")
        lines.append("")
        for day in itinerary.days:
            lines.append(f"### День {day.day}: {day.title}")
            if day.date:
                lines.append(f"*{day.date}*")
            lines.append("")
            for act in day.activities:
                loc = f" — *{act.location}*" if act.location else ""
                lines.append(f"- **{act.time}** {act.description}{loc}")
            lines.append("")

    # Источники и дисклеймер
    if itinerary and itinerary.sources:
        unique_sources = list(dict.fromkeys(
            s.split("/")[0] for s in itinerary.sources
        ))
        lines.append(f"*Источники данных: {', '.join(unique_sources)}*")
        lines.append("")

    lines.append("---")
    lines.append("> **⚠️ Важно:** данный маршрут носит рекомендательный характер и не является "
                 "бронированием. Информация о часах работы и ценах может устареть — "
                 "уточняйте актуальные данные перед поездкой.")

    report = "\n".join(lines)
    session.final_report = report
    return report


def _fmt_dt(dt_str: str) -> str:
    """Форматирует ISO datetime в читаемый вид."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return dt_str


def _nights(package: SelectedPackage) -> int:
    """Вычисляет количество ночей из стоимости."""
    if package.accommodation.price_per_night > 0:
        return round(package.accommodation.total_price / package.accommodation.price_per_night)
    return 0
