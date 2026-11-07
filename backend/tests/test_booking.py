from datetime import timedelta

from app.clock import SIM_START as NOW
from app.engine.booking import Booker
from app.models import ActionType, Household, Resource, ResourceType


def crew(i, lat, lng, free_at=None):
    return Resource(id=i, type=ResourceType.CREW, name=f"Crew {i}", lat=lat, lng=lng, free_at=free_at)


def shelter(i, lat, lng, beds):
    return Resource(id=i, type=ResourceType.SHELTER, name=f"Shelter {i}", lat=lat, lng=lng, capacity=beds)


def home(i, lat, lng):
    return Household(id=i, name="Test Home", phone="+15550000000", address="1 Oak St, Richardson, TX",
                     lat=lat, lng=lng, age=40)


def test_the_nearest_free_crew_is_sent():
    booker = Booker([crew(1, 32.950, -96.730), crew(2, 33.050, -96.600)])
    booking = booker.book(ActionType.DISPATCH_CREW, home(1, 32.951, -96.731), NOW)
    assert booking.resource.id == 1
    assert booking.start_at == NOW and booking.arrive_at > NOW
    assert booking.done_at == booking.arrive_at + timedelta(minutes=45)


def test_one_crew_never_has_two_jobs_at_once():
    booker = Booker([crew(1, 32.95, -96.73)])
    bookings = [booker.book(ActionType.DISPATCH_CREW, home(i, 32.95 + i * 0.002, -96.73), NOW) for i in range(5)]
    for earlier, later in zip(bookings, bookings[1:]):
        assert later.start_at >= earlier.done_at


def test_a_free_crew_farther_away_beats_a_busy_one_nearby():
    busy_nearby = crew(1, 32.950, -96.730, free_at=NOW + timedelta(hours=3))
    free_far = crew(2, 33.000, -96.680)  # about 7 km away
    booking = Booker([busy_nearby, free_far]).book(ActionType.DISPATCH_CREW, home(1, 32.950, -96.730), NOW)
    assert booking.resource.id == 2


def test_shelter_beds_are_never_overbooked():
    near, far = shelter(1, 32.95, -96.73, beds=4), shelter(2, 33.05, -96.60, beds=10)
    booker = Booker([near, far])
    h = home(1, 32.951, -96.731)
    assert booker.book(ActionType.RESERVE_SHELTER_BED, h, NOW, people=3).resource.id == 1
    assert booker.book(ActionType.RESERVE_SHELTER_BED, h, NOW, people=3).resource.id == 2  # only 1 bed left nearby
    assert booker.book(ActionType.RESERVE_SHELTER_BED, h, NOW, people=7).resource.id == 2
    assert booker.book(ActionType.RESERVE_SHELTER_BED, h, NOW, people=2) is None
    assert (near.used, far.used) == (3, 10)


def test_all_busy_and_next_free():
    booker = Booker([crew(1, 32.95, -96.73)])
    assert not booker.all_busy(ResourceType.CREW, NOW)
    booking = booker.book(ActionType.DISPATCH_CREW, home(1, 32.96, -96.73), NOW)
    assert booker.all_busy(ResourceType.CREW, NOW)
    assert booker.next_free(ResourceType.CREW, NOW) == booking.done_at
