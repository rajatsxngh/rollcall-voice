"""Matching help to resources, in plain code (the PRD keeps this math out of the model).

Crews, adjusters and generators do one job at a time. A booking takes the one that can arrive
soonest (when it frees up, plus travel from where it is now), so a busy crew nearby can still beat
an idle one across town, and two jobs on one resource never overlap. Shelters have beds: the
nearest shelter with room wins, and when every shelter is full the booking fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.geo import haversine_km
from app.models import ActionType, Household, Resource, ResourceType

TRAVEL_KMH = 40.0  # storm-slowed driving
JOB_MINUTES = {
    ActionType.DISPATCH_CREW: 45,
    ActionType.IN_PERSON_CHECK: 30,
    ActionType.SCHEDULE_ADJUSTER: 60,
    ActionType.DELIVER_GENERATOR: 12 * 60,  # a generator stays for the night
}
RESOURCE_FOR = {
    ActionType.DISPATCH_CREW: ResourceType.CREW,
    ActionType.IN_PERSON_CHECK: ResourceType.CREW,
    ActionType.SCHEDULE_ADJUSTER: ResourceType.ADJUSTER,
    ActionType.DELIVER_GENERATOR: ResourceType.GENERATOR,
    ActionType.RESERVE_SHELTER_BED: ResourceType.SHELTER,
}
# Dollars per action; M8's approvals use them. Shelter beds, adjuster visits and claims cost nothing extra.
COSTS = {
    ActionType.DISPATCH_CREW: 150.0,
    ActionType.IN_PERSON_CHECK: 100.0,
    ActionType.DELIVER_GENERATOR: 800.0,
    ActionType.HOTEL_VOUCHER: 250.0,
}


@dataclass
class Booking:
    resource: Resource
    start_at: datetime  # when it sets off for this job (now, or when it frees up)
    arrive_at: datetime
    done_at: datetime | None  # crews, adjusters, generators: free again after this
    distance_km: float
    beds: int = 0  # shelters only

    def eta_minutes(self, now: datetime) -> int:
        return max(0, round((self.arrive_at - now).total_seconds() / 60))


def travel(distance_km: float) -> timedelta:
    return timedelta(hours=distance_km / TRAVEL_KMH)


class Booker:
    def __init__(self, resources: list[Resource]) -> None:
        self.resources = resources
        # Where each crew, adjuster or generator is now: its base, then its last job.
        self._where = {r.id: (r.lat, r.lng) for r in resources}

    def pool(self, rtype: ResourceType) -> list[Resource]:
        return [r for r in self.resources if r.type is rtype]

    def book(self, kind: ActionType, h: Household, now: datetime, people: int = 1) -> Booking | None:
        rtype = RESOURCE_FOR[kind]
        pool = self.pool(rtype)
        if rtype is ResourceType.SHELTER:
            open_shelters = [r for r in pool if r.capacity - r.used >= people]
            if not open_shelters:
                return None
            r = min(open_shelters, key=lambda r: (haversine_km(h.lat, h.lng, r.lat, r.lng), r.id))
            r.used += people
            distance = haversine_km(h.lat, h.lng, r.lat, r.lng)
            return Booking(r, now, now + travel(distance), None, distance, beds=people)
        if not pool:
            return None

        def arrival(r: Resource) -> datetime:
            start = max(now, r.free_at or now)
            return start + travel(haversine_km(*self._where[r.id], h.lat, h.lng))

        r = min(pool, key=lambda r: (arrival(r), r.id))
        distance = haversine_km(*self._where[r.id], h.lat, h.lng)
        start = max(now, r.free_at or now)
        arrive = start + travel(distance)
        done = arrive + timedelta(minutes=JOB_MINUTES[kind])
        r.free_at = done
        self._where[r.id] = (h.lat, h.lng)
        return Booking(r, start, arrive, done, distance)

    def all_busy(self, rtype: ResourceType, now: datetime) -> bool:
        pool = self.pool(rtype)
        return bool(pool) and all((r.free_at or now) > now for r in pool)

    def next_free(self, rtype: ResourceType, now: datetime) -> datetime | None:
        times = [max(now, r.free_at or now) for r in self.pool(rtype)]
        return min(times) if times else None
