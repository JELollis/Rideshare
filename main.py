# main.py (project root)

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as dtime
from types import SimpleNamespace as NS
from typing import Dict, List, Optional, Tuple

import bcrypt
from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import Base, SessionLocal, engine
from app import models

# ---------------- App & Templates ----------------

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Rideshare Profit Tracker", version="1.2.0")

# Use a single, correct session secret
_session_secret = getattr(settings, "session_secret", None) or getattr(settings, "secret_key", None) or "change-me"
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

templates = Jinja2Templates(directory="app/templates")


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- Helpers ----------------

PLATFORM_CHOICES = ["Lyft", "Uber", "DoorDash", "Instacart", "Delivery"]


def _sanitize_hash(h: Optional[str]) -> str:
    return (h or "").strip().replace("`", "")


def bcrypt_verify(plain: str, hashed: Optional[str]) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), _sanitize_hash(hashed).encode("utf-8"))
    except Exception:
        return False


def bcrypt_hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def get_current_user(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("user_id")
    return db.get(models.User, uid) if uid else None


def require_login(request: Request, db: Session) -> models.User | RedirectResponse:
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return user  # type: ignore[return-value]


def parse_date_from_form(date_str: Optional[str], fallback_dt: Optional[datetime] = None) -> datetime:
    """Accept 'YYYY-MM-DDTHH:MM' or 'YYYY-MM-DD'."""
    if date_str:
        try:
            return datetime.fromisoformat(date_str)
        except ValueError:
            try:
                return datetime.combine(datetime.fromisoformat(date_str).date(), dtime.min)
            except ValueError:
                pass
    return fallback_dt or datetime.now()


def parse_day_from_form(date_str: Optional[str], fallback: Optional[date] = None) -> date:
    return parse_date_from_form(date_str, datetime.combine(fallback or date.today(), dtime.min)).date()


def to_int_or_none(val: Optional[str]) -> Optional[int]:
    try:
        return int(val) if val not in (None, "", "None") else None
    except ValueError:
        return None


def parse_platforms_csv(csv_val: Optional[str]) -> List[str]:
    if not csv_val:
        return []
    return [p for p in (x.strip() for x in csv_val.split(",")) if p]


def to_platforms_csv(items: Optional[List[str]]) -> str:
    if not items:
        return ""
    uniq = sorted(set(x.strip() for x in items if x and x.strip()))
    return ",".join(uniq)


def _drivers_for_user(user: models.User, db: Session) -> List[models.Driver]:
    if user.is_admin:
        return db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return [db.get(models.Driver, user.driver_id)] if user.driver_id else []


def _user_can_access_driver(user: models.User, driver_id: int) -> bool:
    return user.is_admin or (user.driver_id == driver_id)


def drivers_default_vehicle(db: Session, driver_id: int) -> Optional[models.Vehicle]:
    return (
        db.query(models.Vehicle)
        .filter(models.Vehicle.driver_id == driver_id)
        .order_by(models.Vehicle.is_default.desc(), models.Vehicle.id.asc())
        .first()
    )


def rolling_mpg_for_vehicle(db: Session, vehicle_id: int) -> Tuple[Optional[float], List[dict]]:
    """
    Compute weighted average MPG and a series from fuel records.
    MPG point = (odometer[n] - odometer[n-1]) / gallons[n].
    Needs >= 2 fills; otherwise returns (None, series-with-one-entry-or-empty).
    """
    fuels = (
        db.query(models.Fuel)
        .filter(models.Fuel.vehicle_id == vehicle_id)
        .order_by(models.Fuel.date.asc(), models.Fuel.id.asc())
        .all()
    )
    series: List[dict] = []
    if len(fuels) < 2:
        if fuels:
            series.append({"date": fuels[0].date, "mpg": None, "miles": None, "gallons": fuels[0].gallons})
        return (None, series)

    total_miles = 0.0
    total_gallons = 0.0
    prev = fuels[0]
    for f in fuels[1:]:
        miles = max(0.0, (f.odometer or 0.0) - (prev.odometer or 0.0))
        gallons = f.gallons or 0.0
        mpg = (miles / gallons) if gallons > 0 else None
        series.append({"date": f.date, "mpg": mpg, "miles": miles, "gallons": gallons})
        total_miles += miles
        total_gallons += gallons
        prev = f

    weighted_avg = (total_miles / total_gallons) if total_gallons > 0 else None
    return (weighted_avg, series)


# ---------------- Health / Ping ----------------

@app.get("/__ping")
def ping() -> Dict[str, bool]:
    return {"pong": True}


@app.get("/health")
def health() -> Dict[str, bool]:
    return {"ok": True}


# ---------------- First-time Setup ----------------

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    if db.query(models.User).first():
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request})


@app.post("/setup")
def setup_do(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    driver_name: str = Form(""),
    db: Session = Depends(get_db),
):
    if db.query(models.User).first():
        return RedirectResponse(url="/", status_code=303)

    driver = models.Driver(name=(driver_name.strip() or username.strip()))
    db.add(driver)
    db.flush()

    user = models.User(
        username=username.strip(),
        password_hash=bcrypt_hash(password),
        driver_id=driver.id,
        is_admin=True,
    )
    db.add(user)
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


# ---------------- Auth ----------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_do(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    u = db.query(models.User).filter(models.User.username == username.strip()).first()
    if not u or not bcrypt_verify(password, u.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials."},
            status_code=400,
        )
    request.session["user_id"] = u.id
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/login", status_code=303)


# ---------------- Dashboard ----------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Top-level totals. Net = Gross - (Expenses + Fuel).
    Also computes overall average MPG across vehicles (weighted by each segment).
    """
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    driver_ids: List[int] = (
        [d.id for d in db.query(models.Driver).all()] if user.is_admin else ([user.driver_id] if user.driver_id else [])
    )

    gross = 0.0
    expenses_total = 0.0
    fuel_total = 0.0
    miles_total = 0.0
    mpg_samples: List[float] = []

    for did in driver_ids:
        # daily logs
        dailies = db.query(models.DailyLog).filter(models.DailyLog.driver_id == did).all()
        daily_dates = {dl.date for dl in dailies}
        gross += sum(dl.total_earned or 0 for dl in dailies)
        miles_total += sum(max(0.0, (dl.odo_end or 0) - (dl.odo_start or 0)) for dl in dailies)

        # trips (only those not already covered by a daily log)
        trips = db.query(models.Trip).filter(models.Trip.driver_id == did).all()
        trips_included = [t for t in trips if t.date.date() not in daily_dates]
        gross += sum((t.fare or 0) + (t.tip or 0) + (t.bonus or 0) for t in trips_included)
        miles_total += sum(t.miles or 0 for t in trips_included)

        # expenses
        expenses_total += sum(
            e.amount or 0 for e in db.query(models.Expense).filter(models.Expense.driver_id == did).all()
        )

        # fuel (deducted separately)
        fuel_total += sum(
            f.total_paid or 0 for f in db.query(models.Fuel).filter(models.Fuel.driver_id == did).all()
        )

        # per-vehicle weighted MPG
        vehicles = db.query(models.Vehicle).filter(models.Vehicle.driver_id == did).all()
        for v in vehicles:
            avg_mpg, _ = rolling_mpg_for_vehicle(db, v.id)
            if avg_mpg is not None:
                mpg_samples.append(avg_mpg)

    net = gross - (expenses_total + fuel_total)
    avg_mpg_overall = (sum(mpg_samples) / len(mpg_samples)) if mpg_samples else None

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "gross": gross,
            "expenses": expenses_total,
            "fuel": fuel_total,
            "net": net,
            "miles": miles_total,
            "avg_mpg": avg_mpg_overall,
            "user": user,
        },
    )


# ---------------- Drivers ----------------

@app.get("/drivers/ui", response_class=HTMLResponse)
def drivers_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    drivers = _drivers_for_user(user, db)
    return templates.TemplateResponse("drivers.html", {"request": request, "drivers": drivers, "user": user})


@app.post("/drivers/ui")
def drivers_create(
    request: Request,
    name: str = Form(...),
    car: str = Form(""),
    platform: str = Form(""),                 # legacy single string (safe to keep)
    platforms: List[str] = Form(default=[]),  # NEW multi-select
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse(url="/drivers/ui", status_code=303)

    d = models.Driver(
        name=name.strip(),
        car=(car.strip() or None),
        platform=(platform.strip() or None),
        platforms_csv=to_platforms_csv(platforms),
    )
    db.add(d)
    db.commit()
    return RedirectResponse(url="/drivers/ui", status_code=303)


@app.get("/drivers/edit/{driver_id}", response_class=HTMLResponse)
def drivers_edit_page(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    d = db.get(models.Driver, driver_id)
    if not d:
        return templates.TemplateResponse(
            "message.html",
            {"request": request, "title": "Not found", "message": "Driver not found."},
            status_code=404,
        )
    if not _user_can_access_driver(user, d.id):
        return RedirectResponse(url="/drivers/ui", status_code=303)

    v_list = db.query(models.Vehicle).filter(models.Vehicle.driver_id == d.id).order_by(
        models.Vehicle.is_default.desc(), models.Vehicle.name.asc()
    ).all()

    return templates.TemplateResponse(
        "drivers_edit.html",
        {"request": request, "driver": d, "vehicles_for_driver": v_list, "user": user},
    )


@app.post("/drivers/edit/{driver_id}")
def drivers_edit(
    driver_id: int,
    request: Request,
    name: str = Form(...),
    car: str = Form(""),
    platform: str = Form(""),                 # legacy
    platforms: List[str] = Form(default=[]),  # NEW
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    d = db.get(models.Driver, driver_id)
    if not d or not _user_can_access_driver(user, d.id):
        return RedirectResponse(url="/drivers/ui", status_code=303)

    d.name = name.strip()
    d.car = car.strip() or None
    d.platform = platform.strip() or None
    d.platforms_csv = to_platforms_csv(platforms)
    db.commit()
    return RedirectResponse(url="/drivers/ui", status_code=303)


@app.post("/drivers/delete/{driver_id}")
def drivers_delete(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse("/drivers/ui", status_code=303)

    d = db.get(models.Driver, driver_id)
    if not d:
        return RedirectResponse("/drivers/ui", status_code=303)

    has_rows = (
        db.query(models.Trip).filter_by(driver_id=driver_id).first()
        or db.query(models.DailyLog).filter_by(driver_id=driver_id).first()
        or db.query(models.Expense).filter_by(driver_id=driver_id).first()
        or db.query(models.Fuel).filter_by(driver_id=driver_id).first()
        or db.query(models.User).filter_by(driver_id=driver_id).first()
    )
    if has_rows:
        return templates.TemplateResponse(
            "message.html",
            {
                "request": request,
                "title": "Cannot delete",
                "message": "Driver has related logs/users. Reassign or delete related data first.",
                "user": user,
            },
            status_code=400,
        )

    db.delete(d)
    db.commit()
    return RedirectResponse("/drivers/ui", status_code=303)

# ---------------- Users (admin only) ----------------

@app.get("/users/ui", response_class=HTMLResponse)
def users_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    users = db.query(models.User).order_by(models.User.username.asc()).all()
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "drivers": drivers, "user": user},
    )


@app.post("/users/ui")
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    driver_id: Optional[str] = Form(None),   # accept empty (unassigned ok)
    is_admin: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    new_u = models.User(
        username=username.strip(),
        password_hash=bcrypt_hash(password),
        driver_id=to_int_or_none(driver_id),
        is_admin=bool(is_admin),
    )
    db.add(new_u)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=303)


@app.get("/users/edit/{user_id}", response_class=HTMLResponse)
def users_edit_page(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse(url="/users/ui", status_code=303)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse(
        "users_edit.html",
        {"request": request, "u": target, "target": target, "drivers": drivers, "user": user},
    )


@app.post("/users/edit/{user_id}")
def users_edit(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    driver_id: Optional[str] = Form(None),
    is_admin: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse(url="/users/ui", status_code=303)

    target.username = username.strip()
    if password.strip():
        target.password_hash = bcrypt_hash(password.strip())
    target.driver_id = to_int_or_none(driver_id)
    target.is_admin = bool(is_admin)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=303)


@app.post("/users/delete/{user_id}")
def users_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse) or not user.is_admin:
        return RedirectResponse("/users/ui", status_code=303)
    target = db.get(models.User, user_id)
    if target:
        db.delete(target)
        db.commit()
    return RedirectResponse("/users/ui", status_code=303)

# ---------------- Vehicles ----------------

@app.get("/vehicles/ui", response_class=HTMLResponse)
def vehicles_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    drivers = _drivers_for_user(user, db)
    q = db.query(models.Vehicle)
    if not user.is_admin and user.driver_id:
        q = q.filter(models.Vehicle.driver_id == user.driver_id)
    vehicles = q.order_by(
        models.Vehicle.driver_id.asc(), models.Vehicle.is_default.desc(), models.Vehicle.name.asc()
    ).all()

    # MPG per vehicle
    mpg_by_vid: Dict[int, Optional[float]] = {}
    for v in vehicles:
        avg_mpg, _ = rolling_mpg_for_vehicle(db, v.id)
        mpg_by_vid[v.id] = avg_mpg

    return templates.TemplateResponse(
        "vehicles.html",
        {"request": request, "drivers": drivers, "vehicles": vehicles, "mpg_by_vid": mpg_by_vid, "user": user},
    )


@app.post("/vehicles/ui")
def vehicles_create(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    name: str = Form(...),
    make: str = Form(""),
    model: str = Form(""),
    year: str = Form(""),
    plate: str = Form(""),
    is_default: Optional[str] = Form(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    did = to_int_or_none(driver_id)
    if not user.is_admin:
        did = user.driver_id
    if not did:
        return RedirectResponse("/vehicles/ui", status_code=303)

    if is_default:
        db.query(models.Vehicle).filter(models.Vehicle.driver_id == did).update({"is_default": False})

    v = models.Vehicle(
        driver_id=did,
        name=name.strip(),
        make=make.strip() or None,
        model=model.strip() or None,
        year=year.strip() or None,
        plate=plate.strip() or None,
        is_default=bool(is_default),
    )
    db.add(v)
    db.commit()
    return RedirectResponse("/vehicles/ui", status_code=303)


@app.get("/vehicles/edit/{vehicle_id}", response_class=HTMLResponse)
def vehicles_edit_page(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    v = db.get(models.Vehicle, vehicle_id)
    if not v or (not user.is_admin and v.driver_id != user.driver_id):
        return RedirectResponse("/vehicles/ui", status_code=303)

    drivers = _drivers_for_user(user, db)
    return templates.TemplateResponse("vehicles_edit.html", {"request": request, "vehicle": v, "drivers": drivers, "user": user})


@app.post("/vehicles/edit/{vehicle_id}")
def vehicles_edit(
    vehicle_id: int,
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    name: str = Form(...),
    make: str = Form(""),
    model: str = Form(""),
    year: str = Form(""),
    plate: str = Form(""),
    is_default: Optional[str] = Form(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    v = db.get(models.Vehicle, vehicle_id)
    if not v or (not user.is_admin and v.driver_id != user.driver_id):
        return RedirectResponse("/vehicles/ui", status_code=303)

    did = to_int_or_none(driver_id) or v.driver_id
    if not user.is_admin:
        did = user.driver_id

    if is_default:
        db.query(models.Vehicle).filter(models.Vehicle.driver_id == did).update({"is_default": False})

    v.driver_id = did
    v.name = name.strip()
    v.make = make.strip() or None
    v.model = model.strip() or None
    v.year = year.strip() or None
    v.plate = plate.strip() or None
    v.is_default = bool(is_default)
    db.commit()
    return RedirectResponse("/vehicles/ui", status_code=303)


@app.post("/vehicles/delete/{vehicle_id}")
def vehicles_delete(vehicle_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    v = db.get(models.Vehicle, vehicle_id)
    if v and (user.is_admin or v.driver_id == user.driver_id):
        db.delete(v)
        db.commit()
    return RedirectResponse("/vehicles/ui", status_code=303)


# ---------------- Daily Logs ----------------

@app.get("/daily/ui", response_class=HTMLResponse)
def daily_ui(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[int] = Query(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
        q = db.query(models.DailyLog)
        if driver_id:
            q = q.filter(models.DailyLog.driver_id == driver_id)
        logs = q.order_by(models.DailyLog.date.desc()).limit(200).all()
    else:
        drivers = [db.get(models.Driver, user.driver_id)] if user.driver_id else []
        logs = (
            db.query(models.DailyLog)
            .filter(models.DailyLog.driver_id == user.driver_id)
            .order_by(models.DailyLog.date.desc())
            .limit(200)
            .all()
        )

    return templates.TemplateResponse("daily.html", {"request": request, "drivers": drivers, "logs": logs, "user": user})


@app.post("/daily/ui")
def daily_create(
    request: Request,
    driver_id: str = Form(...),
    date_str: str | None = Form(None),
    odo_start: float = Form(0.0),
    odo_end: float = Form(0.0),
    hours: Optional[int] = Form(None),
    mins: Optional[int] = Form(None),
    total_earned: float = Form(0.0),
    platform: str = Form(""),
    trips_count: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    did = to_int_or_none(driver_id)
    if not user.is_admin:
        did = user.driver_id
    if not did:
        return RedirectResponse("/daily/ui", status_code=303)

    minutes = (hours or 0) * 60 + (mins or 0)
    dlog = models.DailyLog(
        driver_id=did,
        date=parse_day_from_form(date_str),
        odo_start=odo_start,
        odo_end=odo_end,
        minutes_driven=minutes,
        total_earned=total_earned,
        platform=platform.strip() or None,
        trips_count=trips_count,
    )
    db.add(dlog)
    db.commit()
    return RedirectResponse(url="/daily/ui", status_code=303)


@app.get("/daily/edit/{log_id}", response_class=HTMLResponse)
def daily_edit_page(log_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    log = db.get(models.DailyLog, log_id)
    if not log:
        return RedirectResponse(url="/daily/ui", status_code=303)
    if not _user_can_access_driver(user, log.driver_id):
        return RedirectResponse(url="/daily/ui", status_code=303)

    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all() if user.is_admin else [
        db.get(models.Driver, user.driver_id)
    ]
    return templates.TemplateResponse("daily_edit.html", {"request": request, "log": log, "drivers": drivers, "user": user})


@app.post("/daily/edit/{log_id}")
def daily_edit(
    log_id: int,
    request: Request,
    driver_id: str = Form(...),
    date_str: str | None = Form(None),
    odo_start: float = Form(0.0),
    odo_end: float = Form(0.0),
    hours: Optional[int] = Form(None),
    mins: Optional[int] = Form(None),
    total_earned: float = Form(0.0),
    platform: str = Form(""),
    trips_count: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    log = db.get(models.DailyLog, log_id)
    if not log:
        return RedirectResponse(url="/daily/ui", status_code=303)
    if not _user_can_access_driver(user, log.driver_id):
        return RedirectResponse(url="/daily/ui", status_code=303)

    did = to_int_or_none(driver_id) or log.driver_id
    log.driver_id = did
    if date_str:
        log.date = parse_day_from_form(date_str, log.date)
    log.odo_start = odo_start
    log.odo_end = odo_end
    log.minutes_driven = (hours or 0) * 60 + (mins or 0)
    log.total_earned = total_earned
    log.platform = platform.strip() or None
    log.trips_count = trips_count
    db.commit()
    return RedirectResponse(url="/daily/ui", status_code=303)


@app.post("/daily/delete/{log_id}")
def daily_delete(log_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    log = db.get(models.DailyLog, log_id)
    if log and _user_can_access_driver(user, log.driver_id):
        db.delete(log)
        db.commit()
    return RedirectResponse("/daily/ui", status_code=303)


# ---------------- Trips ----------------

@app.get("/trips/ui", response_class=HTMLResponse)
def trips_ui(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: int | None = Query(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if not user.is_admin:
        driver_id = user.driver_id

    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    q = db.query(models.Trip).options(joinedload(models.Trip.driver))
    if driver_id:
        q = q.filter(models.Trip.driver_id == driver_id)
    trips = q.order_by(models.Trip.date.desc()).limit(200).all()

    # Vehicles for the dropdown
    vehicles_q = db.query(models.Vehicle)
    if not user.is_admin:
        vehicles_q = vehicles_q.filter(models.Vehicle.driver_id == user.driver_id)
    elif driver_id:
        vehicles_q = vehicles_q.filter(models.Vehicle.driver_id == driver_id)
    vehicles = vehicles_q.order_by(
        models.Vehicle.driver_id.asc(), models.Vehicle.is_default.desc(), models.Vehicle.name.asc()
    ).all()

    return templates.TemplateResponse(
        "trips.html",
        {"request": request, "drivers": drivers, "trips": trips, "vehicles": vehicles, "user": user, "driver_id": driver_id},
    )


@app.post("/trips/ui")
def trips_ui_create(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    vehicle_id: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    platform: str = Form(""),
    fare: float = Form(...),
    tip: float = Form(0.0),
    bonus: float = Form(0.0),
    miles: float = Form(...),
    mins: int | None = Form(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    did = to_int_or_none(driver_id)
    if not user.is_admin:
        did = user.driver_id
    if not did:
        return RedirectResponse("/trips/ui", status_code=303)

    vid = to_int_or_none(vehicle_id)
    if not vid:
        dv = drivers_default_vehicle(db, did)
        vid = dv.id if dv else None

    dt = parse_date_from_form(date_str or date)
    trip = models.Trip(
        driver_id=did,
        vehicle_id=vid,
        date=dt,
        platform=(platform or None),
        fare=fare,
        tip=tip,
        bonus=bonus,
        miles=miles,
        duration_minutes=(mins or 0),
    )
    db.add(trip)
    db.commit()
    return RedirectResponse("/trips/ui", status_code=303)


@app.post("/trips/delete/{trip_id}")
def trips_delete(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    t = db.get(models.Trip, trip_id)
    if t and _user_can_access_driver(user, t.driver_id):
        db.delete(t)
        db.commit()
    return RedirectResponse("/trips/ui", status_code=303)


# ---------------- Expenses ----------------

@app.get("/expenses/ui", response_class=HTMLResponse)
def expenses_ui(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: int | None = Query(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if not user.is_admin:
        driver_id = user.driver_id

    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    q = db.query(models.Expense).options(joinedload(models.Expense.driver))
    if driver_id:
        q = q.filter(models.Expense.driver_id == driver_id)
    expenses = q.order_by(models.Expense.date.desc()).limit(200).all()

    return templates.TemplateResponse(
        "expenses.html",
        {"request": request, "drivers": drivers, "expenses": expenses, "user": user, "driver_id": driver_id},
    )


@app.post("/expenses/ui")
def expenses_ui_create(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    category: str = Form(...),
    amount: float = Form(...),
    notes: str = Form(""),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    did = to_int_or_none(driver_id)
    if not user.is_admin:
        did = user.driver_id
    if not did:
        return RedirectResponse("/expenses/ui", status_code=303)

    dt = parse_date_from_form(date_str or date)
    exp = models.Expense(
        driver_id=did,
        date=dt,
        category=category,
        amount=amount,
        notes=(notes or None),
    )
    db.add(exp)
    db.commit()
    return RedirectResponse("/expenses/ui", status_code=303)


@app.post("/expenses/delete/{expense_id}")
def expenses_delete(expense_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    e = db.get(models.Expense, expense_id)
    if e and _user_can_access_driver(user, e.driver_id):
        db.delete(e)
        db.commit()
    return RedirectResponse("/expenses/ui", status_code=303)


# ---------------- Fuel ----------------

@app.get("/fuel/ui", response_class=HTMLResponse)
def fuel_ui(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: int | None = Query(None),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if not user.is_admin:
        driver_id = user.driver_id

    drivers = db.query(models.Driver).order_by(models.Driver.name).all()
    q = db.query(models.Fuel).options(joinedload(models.Fuel.driver))
    if driver_id:
        q = q.filter(models.Fuel.driver_id == driver_id)
    fuels = q.order_by(models.Fuel.date.desc()).limit(200).all()

    # Vehicles for the dropdown
    vehicles_q = db.query(models.Vehicle)
    if not user.is_admin:
        vehicles_q = vehicles_q.filter(models.Vehicle.driver_id == user.driver_id)
    elif driver_id:
        vehicles_q = vehicles_q.filter(models.Vehicle.driver_id == driver_id)
    vehicles = vehicles_q.order_by(
        models.Vehicle.driver_id.asc(), models.Vehicle.is_default.desc(), models.Vehicle.name.asc()
    ).all()

    return templates.TemplateResponse(
        "fuel.html",
        {"request": request, "drivers": drivers, "fuels": fuels, "vehicles": vehicles, "user": user, "driver_id": driver_id},
    )


@app.post("/fuel/ui")
def fuel_ui_create(
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    vehicle_id: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    odometer: float = Form(...),
    gallons: float = Form(...),
    total_paid: float = Form(...),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    did = to_int_or_none(driver_id)
    if not user.is_admin:
        did = user.driver_id
    if not did:
        return RedirectResponse("/fuel/ui", status_code=303)

    vid = to_int_or_none(vehicle_id)
    if not vid:
        dv = drivers_default_vehicle(db, did)
        vid = dv.id if dv else None

    dt = parse_date_from_form(date_str or date)
    f = models.Fuel(
        driver_id=did,
        vehicle_id=vid,
        date=dt,
        odometer=odometer,
        gallons=gallons,
        total_paid=total_paid,
        vendor=(vendor or None),
        notes=(notes or None),
    )
    db.add(f)
    db.commit()
    return RedirectResponse("/fuel/ui", status_code=303)


@app.post("/fuel/delete/{fuel_id}")
def fuel_delete(fuel_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    f = db.get(models.Fuel, fuel_id)
    if f and _user_can_access_driver(user, f.driver_id):
        db.delete(f)
        db.commit()
    return RedirectResponse("/fuel/ui", status_code=303)

# ---------------- Fuel (continued) ----------------

@app.get("/fuel/edit/{fuel_id}", response_class=HTMLResponse)
def fuel_edit_page(fuel_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    f = db.get(models.Fuel, fuel_id)
    if not f or not _user_can_access_driver(user, f.driver_id):
        return RedirectResponse("/fuel/ui", status_code=303)

    # drivers for dropdown
    drivers = _drivers_for_user(user, db)

    # vehicles for dropdown
    vehicles_q = db.query(models.Vehicle)
    if not user.is_admin:
        vehicles_q = vehicles_q.filter(models.Vehicle.driver_id == user.driver_id)
    vehicles = vehicles_q.order_by(
        models.Vehicle.driver_id.asc(),
        models.Vehicle.is_default.desc(),
        models.Vehicle.name.asc(),
    ).all()

    return templates.TemplateResponse(
        "fuel_edit.html",
        {"request": request, "fuel": f, "drivers": drivers, "vehicles": vehicles, "user": user},
    )


@app.post("/fuel/edit/{fuel_id}")
def fuel_edit(
    fuel_id: int,
    request: Request,
    db: Session = Depends(get_db),
    driver_id: Optional[str] = Form(None),
    vehicle_id: Optional[str] = Form(None),
    date: Optional[str] = Form(None),
    date_str: Optional[str] = Form(None),
    odometer: float = Form(...),
    gallons: float = Form(...),
    total_paid: float = Form(...),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    f = db.get(models.Fuel, fuel_id)
    if not f or not _user_can_access_driver(user, f.driver_id):
        return RedirectResponse("/fuel/ui", status_code=303)

    did = to_int_or_none(driver_id) or f.driver_id
    if not user.is_admin:
        did = user.driver_id
    vid = to_int_or_none(vehicle_id) or f.vehicle_id
    if not vid:
        dv = drivers_default_vehicle(db, did)
        vid = dv.id if dv else None

    f.driver_id = did
    f.vehicle_id = vid
    f.date = parse_date_from_form(date_str or date, f.date)
    f.odometer = odometer
    f.gallons = gallons
    f.total_paid = total_paid
    f.vendor = vendor.strip() or None
    f.notes = notes.strip() or None

    db.commit()
    return RedirectResponse("/fuel/ui", status_code=303)


# ---------------- Reports (itemized, per-driver) ----------------

MILEAGE_RATES: Dict[int, float] = {2023: 0.655, 2024: 0.670, 2025: 0.670}


def _sum_miles_by_year(
    daily_logs: List[models.DailyLog], trips_included: List[models.Trip]
) -> Dict[int, float]:
    miles_by_year: Dict[int, float] = defaultdict(float)
    for dl in daily_logs:
        miles_by_year[dl.date.year] += max(0.0, (dl.odo_end or 0) - (dl.odo_start or 0))
    for t in trips_included:
        miles_by_year[t.date.year] += t.miles or 0.0
    return miles_by_year


def _std_mileage_deduction(miles_by_year: Dict[int, float]) -> float:
    total = 0.0
    for y, miles in miles_by_year.items():
        rate = MILEAGE_RATES.get(y, max(MILEAGE_RATES.values()))
        total += miles * rate
    return total


@app.get("/reports/ui", response_class=HTMLResponse)
def reports_ui(
    request: Request,
    driver_id: Optional[int] = None,
    start: Optional[str] = None,  # YYYY-MM-DD
    end: Optional[str] = None,    # YYYY-MM-DD
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    drivers = _drivers_for_user(user, db)
    if not drivers:
        return templates.TemplateResponse(
            "message.html", {"request": request, "title": "No drivers", "message": "Create a driver first."}
        )

    if driver_id is None:
        driver_id = drivers[0].id

    today = date.today()

    def _parse_date(s: Optional[str], fallback: date) -> date:
        return parse_day_from_form(s, fallback)

    start_d = _parse_date(start, date(today.year, 1, 1))
    end_d = _parse_date(end, date(today.year, 12, 31))

    # Daily
    daily = (
        db.query(models.DailyLog)
        .filter(
            models.DailyLog.driver_id == driver_id,
            models.DailyLog.date >= start_d,
            models.DailyLog.date <= end_d,
        )
        .all()
    )
    daily_dates = {d.date for d in daily}
    daily_income = sum(d.total_earned or 0.0 for d in daily)
    daily_minutes = sum(d.minutes_driven or 0.0 for d in daily)
    daily_miles = sum(max(0.0, (d.odo_end or 0) - (d.odo_start or 0)) for d in daily)

    # Trips (excluding days with a daily log)
    trips = (
        db.query(models.Trip)
        .filter(
            models.Trip.driver_id == driver_id,
            models.Trip.date >= datetime.combine(start_d, dtime.min),
            models.Trip.date <= datetime.combine(end_d, dtime.max),
        )
        .all()
    )
    trips_included = [t for t in trips if t.date.date() not in daily_dates]
    trip_income = sum((t.fare or 0.0) + (t.tip or 0.0) + (t.bonus or 0.0) for t in trips_included)
    trip_minutes = sum(t.duration_minutes or 0.0 for t in trips_included)
    trip_miles = sum(t.miles or 0.0 for t in trips_included)

    platform_income = defaultdict(float)
    for d in daily:
        platform_income[d.platform or "(unspecified)"] += d.total_earned or 0.0
    for t in trips_included:
        platform_income[t.platform or "(unspecified)"] += (t.fare or 0.0) + (t.tip or 0.0) + (t.bonus or 0.0)

    expenses = (
        db.query(models.Expense)
        .filter(
            models.Expense.driver_id == driver_id,
            models.Expense.date >= datetime.combine(start_d, dtime.min),
            models.Expense.date <= datetime.combine(end_d, dtime.max),
        )
        .all()
    )
    expenses_by_cat = defaultdict(float)
    for e in expenses:
        expenses_by_cat[e.category or "(uncategorized)"] += e.amount or 0.0

    fuels = (
        db.query(models.Fuel)
        .filter(
            models.Fuel.driver_id == driver_id,
            models.Fuel.date >= datetime.combine(start_d, dtime.min),
            models.Fuel.date <= datetime.combine(end_d, dtime.max),
        )
        .all()
    )
    fuel_paid_total = sum(f.total_paid or 0.0 for f in fuels)

    vehicle_cats = {"Oil Change", "Tires", "Maintenance", "Repairs", "Car Wash"}
    vehicle_op_total = sum(amt for cat, amt in expenses_by_cat.items() if cat in vehicle_cats)
    non_vehicle_exp_total = sum(
        amt for cat, amt in expenses_by_cat.items() if cat not in vehicle_cats and cat not in {"Fuel", "Gas"}
    )

    business_miles = daily_miles + trip_miles
    total_minutes = daily_minutes + trip_minutes

    miles_by_year = _sum_miles_by_year(daily, trips_included)
    std_mileage_deduction = _std_mileage_deduction(miles_by_year)

    actual_vehicle_expenses = vehicle_op_total + fuel_paid_total
    gross = daily_income + trip_income

    total_deduct_standard = std_mileage_deduction + non_vehicle_exp_total
    net_standard = gross - total_deduct_standard
    se_tax_standard = max(0.0, net_standard * 0.9235) * 0.153

    total_deduct_actual = actual_vehicle_expenses + non_vehicle_exp_total
    net_actual = gross - total_deduct_actual
    se_tax_actual = max(0.0, net_actual * 0.9235) * 0.153

    result = NS(
        gross=gross,
        business_miles=business_miles,
        minutes=total_minutes,
        platform_income=dict(sorted(platform_income.items(), key=lambda kv: (-kv[1], kv[0]))),
        expenses_by_cat=dict(sorted(expenses_by_cat.items(), key=lambda kv: (-kv[1], kv[0]))),
        fuel_paid_total=fuel_paid_total,
        vehicle_op_total=vehicle_op_total,
        non_vehicle_exp_total=non_vehicle_exp_total,
        std_mileage_deduction=std_mileage_deduction,
        total_deduct_standard=total_deduct_standard,
        net_standard=net_standard,
        se_tax_standard=se_tax_standard,
        actual_vehicle_expenses=actual_vehicle_expenses,
        total_deduct_actual=total_deduct_actual,
        net_actual=net_actual,
        se_tax_actual=se_tax_actual,
    )

    ctx = {
        "request": request,
        "user": user,
        "drivers": drivers,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "result": result,
        "mileage_rates": MILEAGE_RATES,
        "driver_id": driver_id,
    }
    return templates.TemplateResponse("reports.html", ctx)


# -------------- Favicon --------------

api = app


@api.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("favicon.ico")
