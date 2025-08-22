# main.py (project root)

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Dict, Any, List
from collections import defaultdict
from types import SimpleNamespace as NS

import bcrypt
from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, engine, Base
from app import models

# ---------- App & Templates ----------

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Rideshare Profit Tracker", version="1.0.0")
_session_secret = getattr(settings, "session_secret", None) or settings.secret_key
app.add_middleware(SessionMiddleware, secret_key=_session_secret)

templates = Jinja2Templates(directory="app/templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Helpers: Auth / Users ----------

def sanitize_hash(h: str) -> str:
    # guard against accidental backticks / whitespace from pasting in SQL tools
    return (h or "").strip().replace("`", "")

def bcrypt_verify(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), sanitize_hash(hashed).encode("utf-8"))
    except Exception:
        return False

def bcrypt_hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

def get_current_user(request: Request, db: Session) -> Optional[models.User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get(models.User, uid)

def require_login(request: Request, db: Session) -> models.User | RedirectResponse:
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return user  # type: ignore[return-value]


# ---------- General Routes ----------

@app.get("/__ping")
def ping():
    return {"pong": True}

@app.get("/health")
def health():
    return {"ok": True}

# ---------- Setup (first-time) ----------

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, db: Session = Depends(get_db)):
    # Only show if no users exist
    any_user = db.query(models.User).first()
    if any_user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request})

@app.post("/setup")
def setup_do(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    driver_name: str = Form(""),
    db: Session = Depends(get_db)
):
    any_user = db.query(models.User).first()
    if any_user:
        return RedirectResponse(url="/", status_code=303)

    driver = None
    if driver_name.strip():
        driver = models.Driver(name=driver_name.strip())
        db.add(driver)
        db.flush()

    user = models.User(
        username=username.strip(),
        password_hash=bcrypt_hash(password),
        driver_id=(driver.id if driver else None),
        is_admin=True,
    )
    db.add(user)
    db.commit()

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)

# ---------- Auth ----------

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
    db: Session = Depends(get_db)
):
    u = (
        db.query(models.User)
        .filter(models.User.username == username.strip())
        .first()
    )
    if not u or not bcrypt_verify(password, u.password_hash or ""):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials."}, status_code=400)
    request.session["user_id"] = u.id
    return RedirectResponse(url="/", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/login", status_code=303)


# ---------- Dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Combine daily + trips (excluding trips on days with daily) across:
    # If admin, show totals for all drivers; else only their driver.
    driver_ids: List[int] = []
    if user.is_admin:
        driver_ids = [d.id for d in db.query(models.Driver).all()]
    elif user.driver_id:
        driver_ids = [user.driver_id]

    gross = 0.0
    expenses_total = 0.0
    miles_total = 0.0

    for did in driver_ids:
        dailies = db.query(models.DailyLog).filter(models.DailyLog.driver_id == did).all()
        daily_dates = {dl.date for dl in dailies}
        gross += sum(dl.total_earned or 0 for dl in dailies)
        miles_total += sum(max(0.0, (dl.odo_end or 0) - (dl.odo_start or 0)) for dl in dailies)

        trips = db.query(models.Trip).filter(models.Trip.driver_id == did).all()
        trips_included = [t for t in trips if t.date.date() not in daily_dates]
        gross += sum((t.fare or 0) + (t.tip or 0) + (t.bonus or 0) for t in trips_included)
        miles_total += sum(t.miles or 0 for t in trips_included)

        expenses_total += sum(e.amount or 0 for e in db.query(models.Expense).filter(models.Expense.driver_id == did).all())

    net = gross - expenses_total

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "gross": gross,
            "expenses": expenses_total,
            "net": net,
            "miles": miles_total,
            "user": user,
        },
    )


# ---------- Drivers (admins can edit everyone; non-admins can only see their driver) ----------

@app.get("/drivers/ui", response_class=HTMLResponse)
def drivers_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]
    return templates.TemplateResponse("drivers.html", {"request": request, "drivers": drivers, "user": user})

@app.post("/drivers/ui")
def drivers_create(
    request: Request,
    name: str = Form(...),
    car: str = Form(""),
    platform: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin:
        # Non-admins should not create drivers
        return RedirectResponse(url="/drivers/ui", status_code=303)

    d = models.Driver(name=name.strip(), car=(car.strip() or None), platform=(platform.strip() or None))
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
        return templates.TemplateResponse("message.html", {"request": request, "title": "Not found", "message": "Driver not found."}, status_code=404)
    # Non-admins can only edit their own driver
    if not user.is_admin and user.driver_id != d.id:
        return RedirectResponse(url="/drivers/ui", status_code=303)
    return templates.TemplateResponse("drivers_edit.html", {"request": request, "driver": d, "user": user})

@app.post("/drivers/edit/{driver_id}")
def drivers_edit(
    driver_id: int,
    request: Request,
    name: str = Form(...),
    car: str = Form(""),
    platform: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    d = db.get(models.Driver, driver_id)
    if not d:
        return RedirectResponse(url="/drivers/ui", status_code=303)
    if not user.is_admin and user.driver_id != d.id:
        return RedirectResponse(url="/drivers/ui", status_code=303)

    d.name = name.strip()
    d.car = car.strip() or None
    d.platform = platform.strip() or None
    db.commit()
    return RedirectResponse(url="/drivers/ui", status_code=303)


# ---------- Users (admin only) ----------

@app.get("/users/ui", response_class=HTMLResponse)
def users_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    users = db.query(models.User).order_by(models.User.username.asc()).all()
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse("users.html", {"request": request, "users": users, "drivers": drivers, "user": user})

@app.post("/users/ui")
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    driver_id: Optional[int] = Form(None),
    is_admin: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    new_u = models.User(
        username=username.strip(),
        password_hash=bcrypt_hash(password),
        driver_id=(int(driver_id) if driver_id else None),
        is_admin=bool(is_admin),
    )
    db.add(new_u)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=303)

@app.get("/users/edit/{user_id}", response_class=HTMLResponse)
def users_edit_page(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse(url="/users/ui", status_code=303)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse("users_edit.html", {"request": request, "u": target, "drivers": drivers, "user": user})

@app.post("/users/edit/{user_id}")
def users_edit(
    user_id: int,
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    driver_id: Optional[int] = Form(None),
    is_admin: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin:
        return RedirectResponse(url="/", status_code=303)

    target = db.get(models.User, user_id)
    if not target:
        return RedirectResponse(url="/users/ui", status_code=303)

    target.username = username.strip()
    if password.strip():
        target.password_hash = bcrypt_hash(password.strip())
    target.driver_id = int(driver_id) if driver_id else None
    target.is_admin = bool(is_admin)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=303)


# ---------- Daily Logs ----------

@app.get("/daily/ui", response_class=HTMLResponse)
def daily_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
        logs = db.query(models.DailyLog).order_by(models.DailyLog.date.desc()).limit(200).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]
        logs = db.query(models.DailyLog).filter(models.DailyLog.driver_id == user.driver_id).order_by(models.DailyLog.date.desc()).limit(200).all()

    return templates.TemplateResponse("daily.html", {"request": request, "drivers": drivers, "logs": logs, "user": user})

@app.post("/daily/ui")
def daily_create(
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(""),
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
    if not user.is_admin and user.driver_id != driver_id:
        return RedirectResponse(url="/daily/ui", status_code=303)

    minutes = (hours or 0) * 60 + (mins or 0)
    d = models.DailyLog(
        driver_id=driver_id,
        date=datetime.fromisoformat(date_str).date() if date_str else date.today(),
        odo_start=odo_start,
        odo_end=odo_end,
        minutes_driven=minutes,
        total_earned=total_earned,
        platform=platform.strip() or None,
        trips_count=trips_count,
    )
    db.add(d)
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
    if not user.is_admin and user.driver_id != log.driver_id:
        return RedirectResponse(url="/daily/ui", status_code=303)

    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all() if user.is_admin else [db.get(models.Driver, user.driver_id)]
    return templates.TemplateResponse("daily_edit.html", {"request": request, "log": log, "drivers": drivers, "user": user})

@app.post("/daily/edit/{log_id}")
def daily_edit(
    log_id: int,
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(""),
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
    if not user.is_admin and user.driver_id != log.driver_id:
        return RedirectResponse(url="/daily/ui", status_code=303)

    log.driver_id = driver_id
    log.date = datetime.fromisoformat(date_str).date() if date_str else log.date
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
    if log:
        if user.is_admin or user.driver_id == log.driver_id:
            db.delete(log)
            db.commit()
    return RedirectResponse(url="/daily/ui", status_code=303)


# ---------- Trips ----------

@app.get("/trips/ui", response_class=HTMLResponse)
def trips_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
        trips = db.query(models.Trip).order_by(models.Trip.date.desc()).limit(200).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]
        trips = db.query(models.Trip).filter(models.Trip.driver_id == user.driver_id).order_by(models.Trip.date.desc()).limit(200).all()

    return templates.TemplateResponse("trips.html", {"request": request, "drivers": drivers, "trips": trips, "user": user})

@app.post("/trips/ui")
def trips_create(
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),  # "YYYY-MM-DDTHH:MM"
    platform: str = Form(""),
    fare: float = Form(...),
    tip: float = Form(0.0),
    bonus: float = Form(0.0),
    miles: float = Form(...),
    duration_minutes: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin and user.driver_id != driver_id:
        return RedirectResponse(url="/trips/ui", status_code=303)

    dt = datetime.fromisoformat(date_str)
    t = models.Trip(
        driver_id=driver_id,
        date=dt,
        platform=platform.strip() or None,
        fare=fare,
        tip=tip,
        bonus=bonus,
        miles=miles,
        duration_minutes=duration_minutes,
    )
    db.add(t)
    db.commit()
    return RedirectResponse(url="/trips/ui", status_code=303)

@app.get("/trips/edit/{trip_id}", response_class=HTMLResponse)
def trips_edit_page(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = db.get(models.Trip, trip_id)
    if not trip:
        return RedirectResponse(url="/trips/ui", status_code=303)
    if not user.is_admin and user.driver_id != trip.driver_id:
        return RedirectResponse(url="/trips/ui", status_code=303)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all() if user.is_admin else [db.get(models.Driver, user.driver_id)]
    return templates.TemplateResponse("trips_edit.html", {"request": request, "trip": trip, "drivers": drivers, "user": user})

@app.post("/trips/edit/{trip_id}")
def trips_edit(
    trip_id: int,
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),
    platform: str = Form(""),
    fare: float = Form(...),
    tip: float = Form(0.0),
    bonus: float = Form(0.0),
    miles: float = Form(...),
    duration_minutes: Optional[float] = Form(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = db.get(models.Trip, trip_id)
    if not trip:
        return RedirectResponse(url="/trips/ui", status_code=303)
    if not user.is_admin and user.driver_id != trip.driver_id:
        return RedirectResponse(url="/trips/ui", status_code=303)

    trip.driver_id = driver_id
    trip.date = datetime.fromisoformat(date_str)
    trip.platform = platform.strip() or None
    trip.fare = fare
    trip.tip = tip
    trip.bonus = bonus
    trip.miles = miles
    trip.duration_minutes = duration_minutes
    db.commit()
    return RedirectResponse(url="/trips/ui", status_code=303)

@app.post("/trips/delete/{trip_id}")
def trips_delete(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    trip = db.get(models.Trip, trip_id)
    if trip:
        if user.is_admin or user.driver_id == trip.driver_id:
            db.delete(trip)
            db.commit()
    return RedirectResponse(url="/trips/ui", status_code=303)


# ---------- Expenses ----------

@app.get("/expenses/ui", response_class=HTMLResponse)
def expenses_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
        expenses = db.query(models.Expense).order_by(models.Expense.date.desc()).limit(200).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]
        expenses = db.query(models.Expense).filter(models.Expense.driver_id == user.driver_id).order_by(models.Expense.date.desc()).limit(200).all()

    return templates.TemplateResponse("expenses.html", {"request": request, "drivers": drivers, "expenses": expenses, "user": user})

@app.post("/expenses/ui")
def expenses_create(
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin and user.driver_id != driver_id:
        return RedirectResponse(url="/expenses/ui", status_code=303)

    e = models.Expense(
        driver_id=driver_id,
        date=datetime.fromisoformat(date_str),
        category=category.strip(),
        amount=amount,
        notes=notes.strip() or None,
    )
    db.add(e)
    db.commit()
    return RedirectResponse(url="/expenses/ui", status_code=303)

@app.get("/expenses/edit/{expense_id}", response_class=HTMLResponse)
def expenses_edit_page(expense_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    e = db.get(models.Expense, expense_id)
    if not e:
        return RedirectResponse(url="/expenses/ui", status_code=303)
    if not user.is_admin and user.driver_id != e.driver_id:
        return RedirectResponse(url="/expenses/ui", status_code=303)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all() if user.is_admin else [db.get(models.Driver, user.driver_id)]
    return templates.TemplateResponse("expenses_edit.html", {"request": request, "expense": e, "drivers": drivers, "user": user})

@app.post("/expenses/edit/{expense_id}")
def expenses_edit(
    expense_id: int,
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    e = db.get(models.Expense, expense_id)
    if not e:
        return RedirectResponse(url="/expenses/ui", status_code=303)
    if not user.is_admin and user.driver_id != e.driver_id:
        return RedirectResponse(url="/expenses/ui", status_code=303)

    e.driver_id = driver_id
    e.date = datetime.fromisoformat(date_str)
    e.category = category.strip()
    e.amount = amount
    e.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url="/expenses/ui", status_code=303)

@app.post("/expenses/delete/{expense_id}")
def expenses_delete(expense_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    e = db.get(models.Expense, expense_id)
    if e:
        if user.is_admin or user.driver_id == e.driver_id:
            db.delete(e)
            db.commit()
    return RedirectResponse(url="/expenses/ui", status_code=303)


# ---------- Fuel ----------

@app.get("/fuel/ui", response_class=HTMLResponse)
def fuel_ui(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
        fuels = db.query(models.Fuel).order_by(models.Fuel.date.desc()).limit(200).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]
        fuels = db.query(models.Fuel).filter(models.Fuel.driver_id == user.driver_id).order_by(models.Fuel.date.desc()).limit(200).all()

    return templates.TemplateResponse("fuel.html", {"request": request, "drivers": drivers, "fuels": fuels, "user": user})

@app.post("/fuel/ui")
def fuel_create(
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),
    odometer: Optional[float] = Form(None),
    gallons: Optional[float] = Form(None),
    total_paid: Optional[float] = Form(None),
    vendor: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not user.is_admin and user.driver_id != driver_id:
        return RedirectResponse(url="/fuel/ui", status_code=303)

    f = models.Fuel(
        driver_id=driver_id,
        date=datetime.fromisoformat(date_str),
        odometer=odometer,
        gallons=gallons,
        total_paid=total_paid,
        vendor=vendor.strip() or None,
        notes=notes.strip() or None,
    )
    db.add(f)
    db.commit()
    return RedirectResponse(url="/fuel/ui", status_code=303)

@app.get("/fuel/edit/{fuel_id}", response_class=HTMLResponse)
def fuel_edit_page(fuel_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    f = db.get(models.Fuel, fuel_id)
    if not f:
        return RedirectResponse(url="/fuel/ui", status_code=303)
    if not user.is_admin and user.driver_id != f.driver_id:
        return RedirectResponse(url="/fuel/ui", status_code=303)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all() if user.is_admin else [db.get(models.Driver, user.driver_id)]
    return templates.TemplateResponse("fuel_edit.html", {"request": request, "fuel": f, "drivers": drivers, "user": user})

@app.post("/fuel/edit/{fuel_id}")
def fuel_edit(
    fuel_id: int,
    request: Request,
    driver_id: int = Form(...),
    date_str: str = Form(...),
    odometer: Optional[float] = Form(None),
    gallons: Optional[float] = Form(None),
    total_paid: Optional[float] = Form(None),
    vendor: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    f = db.get(models.Fuel, fuel_id)
    if not f:
        return RedirectResponse(url="/fuel/ui", status_code=303)
    if not user.is_admin and user.driver_id != f.driver_id:
        return RedirectResponse(url="/fuel/ui", status_code=303)

    f.driver_id = driver_id
    f.date = datetime.fromisoformat(date_str)
    f.odometer = odometer
    f.gallons = gallons
    f.total_paid = total_paid
    f.vendor = vendor.strip() or None
    f.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url="/fuel/ui", status_code=303)

@app.post("/fuel/delete/{fuel_id}")
def fuel_delete(fuel_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if isinstance(user, RedirectResponse):
        return user
    f = db.get(models.Fuel, fuel_id)
    if f:
        if user.is_admin or user.driver_id == f.driver_id:
            db.delete(f)
            db.commit()
    return RedirectResponse(url="/fuel/ui", status_code=303)


# ---------- Reports (per-driver, itemized) ----------

MILEAGE_RATES: Dict[int, float] = {2023: 0.655, 2024: 0.670, 2025: 0.670}

def _parse_date(s: Optional[str], fallback: date) -> date:
    if not s:
        return fallback
    return datetime.fromisoformat(s).date()

def _sum_miles_by_year(daily_logs: List[models.DailyLog], trips_included: List[models.Trip]) -> Dict[int, float]:
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

    # Drivers list: admin sees all; non-admin sees their own driver
    if user.is_admin:
        drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    else:
        drivers = []
        if user.driver_id:
            d = db.get(models.Driver, user.driver_id)
            if d:
                drivers = [d]

    if not drivers:
        return templates.TemplateResponse("message.html", {"request": request, "title": "No drivers", "message": "Create a driver first."})

    if driver_id is None:
        driver_id = drivers[0].id

    today = date.today()
    start_d = _parse_date(start, date(today.year, 1, 1))
    end_d = _parse_date(end, date(today.year, 12, 31))

    # DAILY
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

    # TRIPS (exclude days with a daily log)
    trips = (
        db.query(models.Trip)
        .filter(
            models.Trip.driver_id == driver_id,
            models.Trip.date >= datetime.combine(start_d, datetime.min.time()),
            models.Trip.date <= datetime.combine(end_d, datetime.max.time()),
        )
        .all()
    )
    trips_included = [t for t in trips if t.date.date() not in daily_dates]
    trip_income = sum((t.fare or 0.0) + (t.tip or 0.0) + (t.bonus or 0.0) for t in trips_included)
    trip_minutes = sum(t.duration_minutes or 0.0 for t in trips_included)
    trip_miles = sum(t.miles or 0.0 for t in trips_included)

    # Platform income (from both)
    platform_income = defaultdict(float)
    for d in daily:
        platform_income[d.platform or "(unspecified)"] += d.total_earned or 0.0
    for t in trips_included:
        platform_income[t.platform or "(unspecified)"] += (t.fare or 0.0) + (t.tip or 0.0) + (t.bonus or 0.0)

    # EXPENSES
    expenses = (
        db.query(models.Expense)
        .filter(
            models.Expense.driver_id == driver_id,
            models.Expense.date >= datetime.combine(start_d, datetime.min.time()),
            models.Expense.date <= datetime.combine(end_d, datetime.max.time()),
        )
        .all()
    )
    expenses_by_cat = defaultdict(float)
    for e in expenses:
        expenses_by_cat[e.category or "(uncategorized)"] += e.amount or 0.0

    # FUEL
    fuels = (
        db.query(models.Fuel)
        .filter(
            models.Fuel.driver_id == driver_id,
            models.Fuel.date >= datetime.combine(start_d, datetime.min.time()),
            models.Fuel.date <= datetime.combine(end_d, datetime.max.time()),
        )
        .all()
    )
    fuel_paid_total = sum(f.total_paid or 0.0 for f in fuels)

    vehicle_cats = {"Oil Change", "Tires", "Maintenance", "Repairs", "Car Wash"}
    vehicle_op_total = sum(amt for cat, amt in expenses_by_cat.items() if cat in vehicle_cats)
    non_vehicle_exp_total = sum(amt for cat, amt in expenses_by_cat.items() if cat not in vehicle_cats and cat not in {"Fuel", "Gas"})

    # mileage & totals
    business_miles = daily_miles + trip_miles
    total_minutes = daily_minutes + trip_minutes

    miles_by_year = _sum_miles_by_year(daily, trips_included)
    std_mileage_deduction = _std_mileage_deduction(miles_by_year)

    # If you later add personal miles share, you can allocate fuel; for now, take full fuel in "actual" bucket.
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
        business_ratio=None,
        fuel_paid_allocated=None,
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
    }
    return templates.TemplateResponse("reports.html", ctx)


# Alias so you can run: uvicorn main:api --reload
api = app
