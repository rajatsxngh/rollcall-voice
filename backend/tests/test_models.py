import sqlite3

from app.db import Database
from app.models import Household, Need, NeedCategory, Status, Urgency


def test_enums_are_stored_by_value_and_read_back_as_enums(tmp_path):
    db = Database(tmp_path / "run.db")
    db.create_all()
    with db.session() as session:
        home = Household(
            name="Maria Lopez",
            phone="+15550100001",
            address="1412 Oak St",
            lat=32.95,
            lng=-96.70,
            age=81,
            lives_alone=True,
            medical_device="oxygen concentrator",
            risk_factors={"age_75_plus": 20, "power_device": 30},
        )
        session.add(home)
        session.commit()
        session.add(
            Need(household_id=home.id, category=NeedCategory.POWER, details="oxygen on battery", urgency=Urgency.P2)
        )
        home.status = Status.URGENT
        session.add(home)
        session.commit()
        home_id = home.id

    with db.session() as session:
        again = session.get(Household, home_id)
        assert again.status is Status.URGENT
        assert again.risk_factors == {"age_75_plus": 20, "power_device": 30}
    db.dispose()

    raw = sqlite3.connect(tmp_path / "run.db")
    try:
        assert raw.execute("SELECT status FROM households").fetchone()[0] == "urgent"
        assert raw.execute("SELECT category, urgency FROM needs").fetchone() == ("power", "P2")
    finally:
        raw.close()
