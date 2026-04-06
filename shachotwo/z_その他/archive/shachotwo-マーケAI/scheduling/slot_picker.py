"""空き枠→候補6枠に絞り込み"""

from __future__ import annotations

from scheduling.calendar_api import TimeSlot


def pick_slots(available: list[TimeSlot], max_slots: int = 6) -> list[TimeSlot]:
    """午前2枠+午後2枠×2-3日分で最大6枠に絞り込み

    偏りがないように分散させる。
    """
    by_date: dict[str, dict[str, list[TimeSlot]]] = {}
    for slot in available:
        date_key = slot.start.date().isoformat()
        period = "am" if slot.start.hour < 12 else "pm"
        by_date.setdefault(date_key, {"am": [], "pm": []})
        by_date[date_key][period].append(slot)

    picked: list[TimeSlot] = []
    for date_key in sorted(by_date.keys()):
        if len(picked) >= max_slots:
            break
        day = by_date[date_key]
        # 午前から最大2枠
        for slot in day["am"][:2]:
            if len(picked) < max_slots:
                picked.append(slot)
        # 午後から最大2枠
        for slot in day["pm"][:2]:
            if len(picked) < max_slots:
                picked.append(slot)

    return picked
