# app/crud.py
from pathlib import Path

from pydantic import json
from sqlalchemy.orm import Session
from passlib.hash import bcrypt
from app import models, schemas

# ---------- DRIVER ----------
def create_driver(db: Session, driver: schemas.DriverCreate):
    obj = models.Driver(name=driver.name, car=driver.car, platform=driver.platform)
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def get_drivers(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Driver).offset(skip).limit(limit).all()

def update_driver(db: Session, driver_id: int, data: dict):
    obj = db.get(models.Driver, driver_id)
    if not obj:
        return None
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj

# ---------- TRIP ----------
def create_trip(db: Session, trip: schemas.TripCreate):
    obj = models.Trip(**trip.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def get_trips(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Trip).order_by(models.Trip.date.desc()).offset(skip).limit(limit).all()

def get_trip(db: Session, trip_id: int):
    return db.get(models.Trip, trip_id)

def update_trip(db: Session, trip_id: int, data: dict):
    obj = db.get(models.Trip, trip_id)
    if not obj: return None
    for k, v in data.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj

def delete_trip(db: Session, trip_id: int):
    obj = db.get(models.Trip, trip_id)
    if not obj: return False
    db.delete(obj); db.commit()
    return True

# ---------- EXPENSE ----------
def create_expense(db: Session, expense: schemas.ExpenseCreate):
    obj = models.Expense(**expense.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def get_expenses(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Expense).order_by(models.Expense.date.desc()).offset(skip).limit(limit).all()

def get_expense(db: Session, expense_id: int):
    return db.get(models.Expense, expense_id)

def update_expense(db: Session, expense_id: int, data: dict):
    obj = db.get(models.Expense, expense_id)
    if not obj: return None
    for k, v in data.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj

def delete_expense(db: Session, expense_id: int):
    obj = db.get(models.Expense, expense_id)
    if not obj: return False
    db.delete(obj); db.commit()
    return True

# ---------- DAILY ----------
def create_daily_log(db: Session, log: schemas.DailyLogCreate):
    obj = models.DailyLog(**log.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def get_daily_logs(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.DailyLog).order_by(models.DailyLog.date.desc()).offset(skip).limit(limit).all()

def get_daily(db: Session, daily_id: int):
    return db.get(models.DailyLog, daily_id)

def update_daily(db: Session, daily_id: int, data: dict):
    obj = db.get(models.DailyLog, daily_id)
    if not obj: return None
    for k, v in data.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj

def delete_daily(db: Session, daily_id: int):
    obj = db.get(models.DailyLog, daily_id)
    if not obj: return False
    db.delete(obj); db.commit()
    return True

# ---------- FUEL ----------
def create_fuel_log(db: Session, log: schemas.FuelLogCreate):
    obj = models.FuelLog(**log.model_dump())
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def get_fuel_logs(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.FuelLog).order_by(models.FuelLog.date.desc()).offset(skip).limit(limit).all()

def get_fuel(db: Session, fuel_id: int):
    return db.get(models.FuelLog, fuel_id)

def update_fuel(db: Session, fuel_id: int, data: dict):
    obj = db.get(models.FuelLog, fuel_id)
    if not obj: return None
    for k, v in data.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj

def delete_fuel(db: Session, fuel_id: int):
    obj = db.get(models.FuelLog, fuel_id)
    if not obj: return False
    db.delete(obj); db.commit()
    return True

# ---------- USER ----------
def get_user(db: Session, user_id: int):
    return db.get(models.User, user_id)

def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()

def count_admins(db: Session) -> int:
    return db.query(models.User).filter(models.User.is_admin == True).count()

def create_user(db: Session, user: schemas.UserCreate):
    hashed = bcrypt.hash(user.password)
    obj = models.User(
        username=user.username,
        password_hash=hashed,
        driver_id=user.driver_id,
        is_admin=user.is_admin,
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return obj

def update_user(db: Session, user_id: int, upd: schemas.UserUpdate):
    obj = db.get(models.User, user_id)
    if not obj: return None
    data = upd.model_dump(exclude_unset=True)
    if "password" in data:
        if data["password"]:
            obj.password_hash = bcrypt.hash(data["password"])
        del data["password"]
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit(); db.refresh(obj)
    return obj

def verify_user(db: Session, username: str, password: str):
    u = get_user_by_username(db, username)
    if not u: return None
    if bcrypt.verify(password, u.password_hash):
        return u
    return None

# --- States ---
def get_or_create_state(db: Session, code: str, name: str):
    code = code.upper()
    obj = db.query(models.State).filter_by(code=code).first()
    if obj:
        return obj
    obj = models.State(code=code, name=name)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def list_states(db: Session):
    return db.query(models.State).order_by(models.State.code.asc()).all()

# Seed common states (only creates if empty)
def seed_states_if_empty(db: Session):
    if db.query(models.State).count() > 0:
        return
    STATES = [
        ("AL","Alabama"),("AK","Alaska"),("AZ","Arizona"),("AR","Arkansas"),("CA","California"),
        ("CO","Colorado"),("CT","Connecticut"),("DE","Delaware"),("FL","Florida"),("GA","Georgia"),
        ("HI","Hawaii"),("ID","Idaho"),("IL","Illinois"),("IN","Indiana"),("IA","Iowa"),
        ("KS","Kansas"),("KY","Kentucky"),("LA","Louisiana"),("ME","Maine"),("MD","Maryland"),
        ("MA","Massachusetts"),("MI","Michigan"),("MN","Minnesota"),("MS","Mississippi"),("MO","Missouri"),
        ("MT","Montana"),("NE","Nebraska"),("NV","Nevada"),("NH","New Hampshire"),("NJ","New Jersey"),
        ("NM","New Mexico"),("NY","New York"),("NC","North Carolina"),("ND","North Dakota"),("OH","Ohio"),
        ("OK","Oklahoma"),("OR","Oregon"),("PA","Pennsylvania"),("RI","Rhode Island"),("SC","South Carolina"),
        ("SD","South Dakota"),("TN","Tennessee"),("TX","Texas"),("UT","Utah"),("VT","Vermont"),
        ("VA","Virginia"),("WA","Washington"),("WV","West Virginia"),("WI","Wisconsin"),("WY","Wyoming"),
        ("DC","District of Columbia")
    ]
    for code, name in STATES:
        get_or_create_state(db, code, name)

# --- Profiles ---
def get_tax_profile(db: Session, user_id: int, year: int):
    return db.query(models.StateTaxProfile).filter_by(user_id=user_id, year=year).first()

def upsert_tax_profile(db: Session, user_id: int, state_id: int, year: int, **fields):
    obj = get_tax_profile(db, user_id, year)
    if not obj:
        obj = models.StateTaxProfile(user_id=user_id, state_id=state_id, year=year)
        db.add(obj)
    for k, v in fields.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj

# --- Brackets ---
def get_brackets(db: Session, state_id: int, year: int, filing_status: str):
    return (db.query(models.StateTaxBracket)
            .filter_by(state_id=state_id, year=year, filing_status=filing_status)
            .order_by(models.StateTaxBracket.bracket_min.asc())
            .all())

def replace_brackets(db: Session, state_id: int, year: int, filing_status: str, rows: list[dict]):
    # delete existing group
    db.query(models.StateTaxBracket).filter_by(
        state_id=state_id, year=year, filing_status=filing_status
    ).delete()
    # insert new
    for r in rows:
        b = models.StateTaxBracket(
            state_id=state_id, year=year, filing_status=filing_status,
            bracket_min=float(r["min"]), bracket_max=(None if r.get("max") in (None, "", "inf") else float(r["max"])),
            rate=float(r["rate"])
        )
        db.add(b)
    db.commit()

# --- Load defaults from JSON snapshot (e.g., app/data/tax_2025.json) ---
def load_default_brackets(db: Session, state_code: str, year: int, filing_status: str):
    data_path = Path(__file__).resolve().parent / "data" / f"tax_{year}.json"
    if not data_path.exists():
        return False, f"Defaults for year {year} not found at {data_path.name}"
    with data_path.open("r", encoding="utf-8") as f:
        blob = json.load(f)
    st = db.query(models.State).filter(models.State.code == state_code.upper()).first()
    if not st:
        return False, f"Unknown state code {state_code}"
    # find matching state
    state_def = None
    for s in blob.get("states", []):
        if s.get("code","").upper() == st.code:
            state_def = s
            break
    if not state_def:
        return False, f"No defaults for state {state_code} in {data_path.name}"

    # status map e.g. "single","mfj","mfs","hoh"
    fs = filing_status
    rows = state_def.get("filing_status", {}).get(fs, [])
    if not rows:
        return False, f"No bracket set for {state_code} / {fs} in {year}"
    replace_brackets(db, st.id, year, fs, rows)
    return True, f"Loaded {len(rows)} bracket row(s) for {state_code} {year} {fs}"