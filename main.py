# main.py  — Rideshare Profit Tracker (FastAPI)
# Launch:  .venv\Scripts\python -m uvicorn main:api --reload --log-level debug

from datetime import datetime, date
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from app.db import SessionLocal, engine, Base
from app import models
from app.config import settings

# ---------- DB init ----------
Base.metadata.create_all(bind=engine)

# ---------- App / templates ----------
api = FastAPI(title="Rideshare Profit Tracker")
templates = Jinja2Templates(directory="app/templates")

# Add a session secret (prefer SESSION_SECRET if present; else SECRET_KEY)
_session_secret = getattr(settings, "session_secret", None) or getattr(settings, "secret_key", "change-me")
api.add_middleware(SessionMiddleware, secret_key=_session_secret)


# ---------- bcrypt password helpers (with legacy fallback) ----------
import hashlib, hmac
try:
    import bcrypt as pybc  # python-bcrypt
except Exception:
    pybc = None


def _legacy_hash(password: str) -> str:
    """
    Legacy scheme used earlier: sha256( APP_SECRET + password )
    """
    secret = getattr(settings, "secret_key", "")
    return hashlib.sha256((secret + password).encode()).hexdigest()


def hash_password(password: str) -> str:
    """
    New scheme: bcrypt ($2b$…), 12 rounds. Falls back to legacy if bcrypt unavailable (not recommended).
    """
    if pybc:
        return pybc.hashpw(password.encode("utf-8"), pybc.gensalt(rounds=12)).decode("utf-8")
    return _legacy_hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Verify against bcrypt first; if the stored hash is legacy hex, verify with legacy recipe.
    """
    if not stored_hash:
        return False
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")) and pybc:
        try:
            return pybc.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    # legacy fallback
    return hmac.compare_digest(_legacy_hash(password), stored_hash)


# ---------- DB dependency ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Current user ----------
def get_current_user(request: Request, db: Optional[Session] = None) -> Optional[models.User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    close_after = False
    if db is None:
        db = SessionLocal()
        close_after = True
    try:
        return db.query(models.User).filter(models.User.id == user_id).first()
    finally:
        if close_after:
            db.close()


# ---------- Health / Ping ----------
@api.get("/__ping")
def __ping():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}

@api.get("/health")
def health():
    return {"ok": True}

# ---------- Initial setup ----------
@api.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_db)):
    # If users already exist, push to login
    exists = db.query(models.User.id).first() is not None
    if exists:
        return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("setup.html", {"request": request, "user": get_current_user(request)})

@api.post("/setup")
def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    driver_name: str = Form(""),
    db: Session = Depends(get_db),
):
    exists = db.query(models.User.id).first() is not None
    if exists:
        return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)

    driver = None
    dn = (driver_name or "").strip()
    if dn:
        driver = models.Driver(name=dn)
        db.add(driver)
        db.flush()

    user = models.User(
        username=(username or "").strip(),
        password_hash=hash_password(password),
        is_admin=True,
        driver_id=(driver.id if driver else None),
    )
    db.add(user)
    db.commit()

    # auto-login
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

# ---------- Login / Logout ----------
@api.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": get_current_user(request), "error": None})

@api.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    uname = (username or "").strip()
    user = db.query(models.User).filter(func.lower(models.User.username) == uname.lower()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "user": None, "error": "Invalid Credentials"}, status_code=400)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=HTTP_303_SEE_OTHER)

@api.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=HTTP_303_SEE_OTHER)

# ---------- Dashboard ----------
@api.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    q_driver = db.query(models.Driver)
    driver_ids: List[int] = []

    # normal users: restrict to their driver
    if user and not user.is_admin:
        if user.driver_id:
            driver_ids = [user.driver_id]
            q_driver = q_driver.filter(models.Driver.id == user.driver_id)
        else:
            driver_ids = []
            q_driver = q_driver.filter(models.Driver.id == -1)  # no data
    else:
        driver_ids = [d.id for d in q_driver.all()]

    # sums from DAILY logs
    daily_q = db.query(models.DailyLog)
    if driver_ids:
        daily_q = daily_q.filter(models.DailyLog.driver_id.in_(driver_ids))
    daily_logs = daily_q.all()

    daily_gross = sum((d.total_earned or 0.0) for d in daily_logs)
    daily_miles = sum(((d.odo_end or 0.0) - (d.odo_start or 0.0)) for d in daily_logs)
    daily_minutes = sum((d.minutes_driven or 0) for d in daily_logs)

    # to avoid double-counting, exclude per-trip entries on dates that have a DailyLog for the same driver
    daily_map = {}  # (driver_id -> set of dates)
    for d in daily_logs:
        if d.driver_id not in daily_map:
            daily_map[d.driver_id] = set()
        daily_map[d.driver_id].add(d.date)

    trips_q = db.query(models.Trip)
    if driver_ids:
        trips_q = trips_q.filter(models.Trip.driver_id.in_(driver_ids))
    trips = trips_q.all()

    trips_gross = 0.0
    trips_miles = 0.0
    trips_minutes = 0
    for t in trips:
        t_date = t.date.date() if isinstance(t.date, datetime) else t.date
        # exclude if daily exists same day for same driver
        if t.driver_id in daily_map and t_date in daily_map[t.driver_id]:
            continue
        trips_gross += (t.fare or 0) + (t.tip or 0) + (t.bonus or 0)
        trips_miles += (t.miles or 0.0)
        trips_minutes += int(t.duration_minutes or 0)

    gross = daily_gross + trips_gross
    miles = daily_miles + trips_miles
    minutes = daily_minutes + trips_minutes

    # expenses (sum all for visible drivers)
    exp_q = db.query(models.Expense)
    if driver_ids:
        exp_q = exp_q.filter(models.Expense.driver_id.in_(driver_ids))
    expenses = sum((e.amount or 0.0) for e in exp_q.all())

    net = gross - expenses

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "gross": round(gross, 2),
            "expenses": round(expenses, 2),
            "net": round(net, 2),
            "miles": round(miles, 1),
            "minutes": minutes,
        },
    )

# ---------- Drivers (list/add/edit) ----------
@api.get("/drivers/ui", response_class=HTMLResponse)
def drivers_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse("drivers.html", {"request": request, "user": user, "drivers": drivers, "edit_driver": None})

@api.post("/drivers/ui")
def drivers_create(
    request: Request,
    name: str = Form(...),
    car: str = Form(""),
    platform: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    d = models.Driver(name=name.strip(), car=(car or None), platform=(platform or None))
    db.add(d)
    db.commit()
    return RedirectResponse(url="/drivers/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/drivers/edit/{driver_id}", response_class=HTMLResponse)
def drivers_edit_form(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    driver = db.query(models.Driver).filter(models.Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    return templates.TemplateResponse("drivers_edit.html", {"request": request, "user": user, "driver": driver})

@api.post("/drivers/edit/{driver_id}")
def drivers_edit(driver_id: int, request: Request, name: str = Form(...), car: str = Form(""), platform: str = Form(""), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    driver = db.query(models.Driver).filter(models.Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    driver.name = name.strip()
    driver.car = car or None
    driver.platform = platform or None
    db.commit()
    return RedirectResponse(url="/drivers/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Users (list/add/edit) ----------
@api.get("/users/ui", response_class=HTMLResponse)
def users_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    users = db.query(models.User).all()
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": users, "drivers": drivers, "error": None})

@api.post("/users/ui")
def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(""),
    driver_id: Optional[int] = Form(None),
    is_admin: Optional[bool] = Form(False),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    uname = (username or "").strip()
    if not uname:
        return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": db.query(models.User).all(), "drivers": db.query(models.Driver).all(), "error": "Username required"}, status_code=400)

    exists = db.query(models.User).filter(func.lower(models.User.username) == uname.lower()).first()
    if exists:
        return templates.TemplateResponse("users.html", {"request": request, "user": user, "users": db.query(models.User).all(), "drivers": db.query(models.Driver).all(), "error": "Username already exists"}, status_code=400)

    u = models.User(
        username=uname,
        password_hash=hash_password(password) if password else hash_password("changeme123"),
        driver_id=(driver_id or None),
        is_admin=bool(is_admin),
    )
    db.add(u)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/users/edit/{user_id}", response_class=HTMLResponse)
def users_edit_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    drivers = db.query(models.Driver).order_by(models.Driver.name.asc()).all()
    return templates.TemplateResponse("users_edit.html", {"request": request, "user": user, "target": target, "drivers": drivers})

@api.post("/users/edit/{user_id}")
def users_edit(user_id: int, request: Request,
               username: str = Form(...),
               new_password: str = Form(""),
               driver_id: Optional[int] = Form(None),
               is_admin: Optional[bool] = Form(False),
               db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.username = (username or "").strip()
    target.driver_id = driver_id or None
    target.is_admin = bool(is_admin)
    if new_password:
        target.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse(url="/users/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Daily (list/add/edit) ----------
@api.get("/daily/ui", response_class=HTMLResponse)
def daily_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    drivers_q = db.query(models.Driver)
    if user and not user.is_admin and user.driver_id:
        drivers_q = drivers_q.filter(models.Driver.id == user.driver_id)
    drivers = drivers_q.all()

    logs_q = db.query(models.DailyLog).order_by(models.DailyLog.date.desc())
    if user and not user.is_admin and user.driver_id:
        logs_q = logs_q.filter(models.DailyLog.driver_id == user.driver_id)
    logs = logs_q.limit(200).all()

    return templates.TemplateResponse("daily.html", {"request": request, "user": user, "drivers": drivers, "logs": logs})

@api.post("/daily/ui")
def daily_create(
    request: Request,
    driver_id: int = Form(...),
    date_str: Optional[str] = Form(None, alias="date"),
    odo_start: Optional[float] = Form(None),
    odo_end: Optional[float] = Form(None),
    hours: Optional[int] = Form(0),
    mins: Optional[int] = Form(0),
    total_earned: Optional[float] = Form(0.0),
    platform: str = Form(""),
    trips_count: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    # normal users can only post to their driver
    if user and not user.is_admin and user.driver_id and user.driver_id != driver_id:
        raise HTTPException(status_code=403, detail="Cannot submit for another driver")

    d = datetime.fromisoformat(date_str).date() if date_str else date.today()
    minutes = int(max(0, (hours or 0))) * 60 + int(max(0, (mins or 0)))
    log = models.DailyLog(
        driver_id=driver_id,
        date=d,
        odo_start=odo_start,
        odo_end=odo_end,
        minutes_driven=minutes or None,
        total_earned=total_earned or 0.0,
        platform=(platform or None),
        trips_count=(trips_count or None),
    )
    db.add(log)
    db.commit()
    return RedirectResponse(url="/daily/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/daily/edit/{log_id}", response_class=HTMLResponse)
def daily_edit_form(log_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    log = db.query(models.DailyLog).filter(models.DailyLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Daily not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != log.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    drivers = db.query(models.Driver).all() if (user and user.is_admin) else db.query(models.Driver).filter(models.Driver.id == (user.driver_id if user else -1)).all()
    return templates.TemplateResponse("daily_edit.html", {"request": request, "user": user, "log": log, "drivers": drivers})

@api.post("/daily/edit/{log_id}")
def daily_edit(
    log_id: int,
    request: Request,
    driver_id: int = Form(...),
    date_str: Optional[str] = Form(None, alias="date"),
    odo_start: Optional[float] = Form(None),
    odo_end: Optional[float] = Form(None),
    hours: Optional[int] = Form(0),
    mins: Optional[int] = Form(0),
    total_earned: Optional[float] = Form(0.0),
    platform: str = Form(""),
    trips_count: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    log = db.query(models.DailyLog).filter(models.DailyLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Daily not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != log.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    log.driver_id = driver_id
    if date_str:
        log.date = datetime.fromisoformat(date_str).date()
    log.odo_start = odo_start
    log.odo_end = odo_end
    log.minutes_driven = int(max(0, hours or 0)) * 60 + int(max(0, mins or 0)) or None
    log.total_earned = total_earned or 0.0
    log.platform = platform or None
    log.trips_count = trips_count or None
    db.commit()
    return RedirectResponse(url="/daily/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Trips (list/add/edit) ----------
@api.get("/trips/ui", response_class=HTMLResponse)
def trips_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    drivers_q = db.query(models.Driver)
    trips_q = db.query(models.Trip).order_by(models.Trip.date.desc())

    if user and not user.is_admin and user.driver_id:
        drivers_q = drivers_q.filter(models.Driver.id == user.driver_id)
        trips_q = trips_q.filter(models.Trip.driver_id == user.driver_id)

    drivers = drivers_q.all()
    trips = trips_q.limit(200).all()
    return templates.TemplateResponse("trips.html", {"request": request, "user": user, "drivers": drivers, "trips": trips})

@api.post("/trips/ui")
def trips_create(
    request: Request,
    driver_id: int = Form(...),
    date: str = Form(...),
    platform: str = Form(""),
    fare: float = Form(...),
    tip: float = Form(0.0),
    bonus: float = Form(0.0),
    miles: float = Form(...),
    duration_minutes: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if user and not user.is_admin and user.driver_id and user.driver_id != driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    dt = datetime.fromisoformat(date)
    t = models.Trip(
        driver_id=driver_id,
        date=dt,
        platform=platform or None,
        fare=fare,
        tip=tip or 0.0,
        bonus=bonus or 0.0,
        miles=miles or 0.0,
        duration_minutes=(duration_minutes or None),
    )
    db.add(t)
    db.commit()
    return RedirectResponse(url="/trips/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/trips/edit/{trip_id}", response_class=HTMLResponse)
def trip_edit_form(trip_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != trip.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    drivers = db.query(models.Driver).all() if (user and user.is_admin) else db.query(models.Driver).filter(models.Driver.id == (user.driver_id if user else -1)).all()
    return templates.TemplateResponse("trip_edit.html", {"request": request, "user": user, "trip": trip, "drivers": drivers})

@api.post("/trips/edit/{trip_id}")
def trip_edit(
    trip_id: int, request: Request,
    driver_id: int = Form(...),
    date: str = Form(...),
    platform: str = Form(""),
    fare: float = Form(...),
    tip: float = Form(0.0),
    bonus: float = Form(0.0),
    miles: float = Form(...),
    duration_minutes: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    trip = db.query(models.Trip).filter(models.Trip.id == trip_id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != trip.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    trip.driver_id = driver_id
    trip.date = datetime.fromisoformat(date)
    trip.platform = platform or None
    trip.fare = fare
    trip.tip = tip or 0.0
    trip.bonus = bonus or 0.0
    trip.miles = miles or 0.0
    trip.duration_minutes = duration_minutes or None
    db.commit()
    return RedirectResponse(url="/trips/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Expenses (list/add/edit) ----------
@api.get("/expenses/ui", response_class=HTMLResponse)
def expenses_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    drivers_q = db.query(models.Driver)
    exp_q = db.query(models.Expense).order_by(models.Expense.date.desc())
    if user and not user.is_admin and user.driver_id:
        drivers_q = drivers_q.filter(models.Driver.id == user.driver_id)
        exp_q = exp_q.filter(models.Expense.driver_id == user.driver_id)
    drivers = drivers_q.all()
    expenses = exp_q.limit(200).all()
    return templates.TemplateResponse("expenses.html", {"request": request, "user": user, "drivers": drivers, "expenses": expenses})

@api.post("/expenses/ui")
def expenses_create(
    request: Request,
    driver_id: int = Form(...),
    date: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if user and not user.is_admin and user.driver_id and user.driver_id != driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    dt = datetime.fromisoformat(date)
    e = models.Expense(driver_id=driver_id, date=dt, category=category, amount=amount, notes=(notes or None))
    db.add(e)
    db.commit()
    return RedirectResponse(url="/expenses/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/expenses/edit/{expense_id}", response_class=HTMLResponse)
def expense_edit_form(expense_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != expense.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    drivers = db.query(models.Driver).all() if (user and user.is_admin) else db.query(models.Driver).filter(models.Driver.id == (user.driver_id if user else -1)).all()
    return templates.TemplateResponse("expense_edit.html", {"request": request, "user": user, "expense": expense, "drivers": drivers})

@api.post("/expenses/edit/{expense_id}")
def expense_edit(expense_id: int, request: Request,
                 driver_id: int = Form(...),
                 date: str = Form(...),
                 category: str = Form(...),
                 amount: float = Form(...),
                 notes: str = Form(""),
                 db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != expense.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    expense.driver_id = driver_id
    expense.date = datetime.fromisoformat(date)
    expense.category = category
    expense.amount = amount
    expense.notes = notes or None
    db.commit()
    return RedirectResponse(url="/expenses/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Fuel (list/add/edit) ----------
@api.get("/fuel/ui", response_class=HTMLResponse)
def fuel_ui(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    drivers_q = db.query(models.Driver)
    fuel_q = db.query(models.Fuel).order_by(models.Fuel.date.desc())
    if user and not user.is_admin and user.driver_id:
        drivers_q = drivers_q.filter(models.Driver.id == user.driver_id)
        fuel_q = fuel_q.filter(models.Fuel.driver_id == user.driver_id)
    drivers = drivers_q.all()
    fuels = fuel_q.limit(200).all()
    return templates.TemplateResponse("fuel.html", {"request": request, "user": user, "drivers": drivers, "fuels": fuels})

@api.post("/fuel/ui")
def fuel_create(
    request: Request,
    driver_id: int = Form(...),
    date: str = Form(...),
    odometer: Optional[float] = Form(None),
    gallons: Optional[float] = Form(None),
    total_paid: Optional[float] = Form(None),
    vendor: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if user and not user.is_admin and user.driver_id and user.driver_id != driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    dt = datetime.fromisoformat(date)
    f = models.Fuel(
        driver_id=driver_id,
        date=dt,
        odometer=odometer,
        gallons=gallons,
        total_paid=total_paid,
        vendor=(vendor or None),
        notes=(notes or None),
    )
    db.add(f)
    db.commit()
    return RedirectResponse(url="/fuel/ui", status_code=HTTP_303_SEE_OTHER)

@api.get("/fuel/edit/{fuel_id}", response_class=HTMLResponse)
def fuel_edit_form(fuel_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    fuel = db.query(models.Fuel).filter(models.Fuel.id == fuel_id).first()
    if not fuel:
        raise HTTPException(status_code=404, detail="Fuel not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != fuel.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    drivers = db.query(models.Driver).all() if (user and user.is_admin) else db.query(models.Driver).filter(models.Driver.id == (user.driver_id if user else -1)).all()
    return templates.TemplateResponse("fuel_edit.html", {"request": request, "user": user, "fuel": fuel, "drivers": drivers})

@api.post("/fuel/edit/{fuel_id}")
def fuel_edit(fuel_id: int, request: Request,
              driver_id: int = Form(...),
              date: str = Form(...),
              odometer: Optional[float] = Form(None),
              gallons: Optional[float] = Form(None),
              total_paid: Optional[float] = Form(None),
              vendor: str = Form(""),
              notes: str = Form(""),
              db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    fuel = db.query(models.Fuel).filter(models.Fuel.id == fuel_id).first()
    if not fuel:
        raise HTTPException(status_code=404, detail="Fuel not found")
    if user and not user.is_admin and user.driver_id and user.driver_id != fuel.driver_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    fuel.driver_id = driver_id
    fuel.date = datetime.fromisoformat(date)
    fuel.odometer = odometer
    fuel.gallons = gallons
    fuel.total_paid = total_paid
    fuel.vendor = vendor or None
    fuel.notes = notes or None
    db.commit()
    return RedirectResponse(url="/fuel/ui", status_code=HTTP_303_SEE_OTHER)

# ---------- Reports (summary UI) ----------
@api.get("/reports/ui", response_class=HTMLResponse)
def reports_ui(
    request: Request,
    start: Optional[str] = None,
    end: Optional[str] = None,
    driver_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    # parse dates
    start_d = datetime.fromisoformat(start).date() if start else date(date.today().year, 1, 1)
    end_d = datetime.fromisoformat(end).date() if end else date(date.today().year, 12, 31)

    drivers_q = db.query(models.Driver)
    if user and not user.is_admin and user.driver_id:
        drivers_q = drivers_q.filter(models.Driver.id == user.driver_id)
        driver_id = user.driver_id
    drivers = drivers_q.order_by(models.Driver.name.asc()).all()

    # daily within range/driver
    daily_q = db.query(models.DailyLog).filter(models.DailyLog.date >= start_d, models.DailyLog.date <= end_d)
    trips_q = db.query(models.Trip).filter(func.date(models.Trip.date) >= start_d, func.date(models.Trip.date) <= end_d)
    exp_q = db.query(models.Expense).filter(func.date(models.Expense.date) >= start_d, func.date(models.Expense.date) <= end_d)

    if driver_id:
        daily_q = daily_q.filter(models.DailyLog.driver_id == driver_id)
        trips_q = trips_q.filter(models.Trip.driver_id == driver_id)
        exp_q = exp_q.filter(models.Expense.driver_id == driver_id)
    elif user and not user.is_admin and user.driver_id:
        # already filtered above
        pass

    daily_logs = daily_q.all()
    daily_dates_by_driver = {}
    for dlog in daily_logs:
        daily_dates_by_driver.setdefault(dlog.driver_id, set()).add(dlog.date)

    # sums
    gross = sum((d.total_earned or 0.0) for d in daily_logs)
    miles = sum(((d.odo_end or 0.0) - (d.odo_start or 0.0)) for d in daily_logs)
    minutes = sum((d.minutes_driven or 0) for d in daily_logs)
    platform_income: Dict[str, float] = {}

    for dlog in daily_logs:
        if dlog.platform:
            platform_income[dlog.platform] = platform_income.get(dlog.platform, 0.0) + float(dlog.total_earned or 0.0)

    # add trips not overlapped by daily
    trips = trips_q.all()
    for t in trips:
        tdate = t.date.date() if isinstance(t.date, datetime) else t.date
        if t.driver_id in daily_dates_by_driver and tdate in daily_dates_by_driver[t.driver_id]:
            continue
        total = (t.fare or 0.0) + (t.tip or 0.0) + (t.bonus or 0.0)
        gross += total
        miles += (t.miles or 0.0)
        minutes += int(t.duration_minutes or 0)
        if t.platform:
            platform_income[t.platform] = platform_income.get(t.platform, 0.0) + total

    return templates.TemplateResponse(
        "reports.html",
        {
            "request": request,
            "user": user,
            "drivers": drivers,
            "driver_id": driver_id,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "gross": round(gross, 2),
            "miles": round(miles, 1),
            "minutes": minutes,
            "platform_income": platform_income,
        },
    )
