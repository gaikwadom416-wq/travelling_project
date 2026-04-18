from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename
import json, uuid, os
import requests
import time
from datetime import datetime

# Email imports
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- SQLAlchemy Imports ---
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, select, func

# Assuming payment.py contains the payment_bp Blueprint.
try:
    from payment import payment_bp
except ImportError:
    from flask import Blueprint
    payment_bp = Blueprint('payment', __name__, url_prefix='/payment')

    @payment_bp.route('/payment_page')
    def payment_page():
        booking_id = request.args.get('booking_id')
        flash(f"Payment processed for Booking ID: {booking_id}. (Simulation)", "success")
        return redirect(url_for('home'))

app = Flask(__name__)

# --- Database Configuration (PostgreSQL) ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:postgres@localhost:5432/auto_booking_db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "auto_booking_secret"
app.register_blueprint(payment_bp)

db = SQLAlchemy(app)

# --- File Upload Configuration ---
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
DEFAULT_PHOTO_PATH = '/static/download.jpeg'

# --- Gemini API Configuration ---
GEMINI_API_KEY = ""
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"

# --- Email Configuration ---
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))  # TLS
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "roshansg2005@gmail.com")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "noyrmdmwndeibbqj")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)


def send_email(subject, body, recipients):
    """Send a plain-text email to one or more recipients."""
    if not recipients:
        return  # nothing to send

    try:
        msg = MIMEMultipart()
        msg["From"] = DEFAULT_FROM_EMAIL
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            if EMAIL_USE_TLS:
                server.starttls()
            server.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        # Log the error but don't break the app
        print(f"[EMAIL ERROR] {e}")


# ------------------------------------
# --- DATABASE MODELS (ROLE ADDED) ---
# ------------------------------------

class User(db.Model):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    mobile: Mapped[str] = mapped_column(String(20), nullable=True)
    address: Mapped[str] = mapped_column(String(255), nullable=True)
    photo_path: Mapped[str] = mapped_column(String(255), default=DEFAULT_PHOTO_PATH)
    # NEW: Store user role (e.g., 'passenger', 'driver', 'admin')
    role: Mapped[str] = mapped_column(String(20), default='passenger')


class Booking(db.Model):
    __tablename__ = 'bookings'
    id: Mapped[str] = mapped_column(String(8), primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    user_id: Mapped[int] = mapped_column(db.ForeignKey('users.id'), nullable=False)
    vehicle: Mapped[str] = mapped_column(String(50))
    pickup: Mapped[str] = mapped_column(String(255))
    drop_off: Mapped[str] = mapped_column(String(255))
    date: Mapped[str] = mapped_column(String(20))
    seats: Mapped[int] = mapped_column(Integer)
    distance: Mapped[float] = mapped_column(Float)
    total_fare: Mapped[float] = mapped_column(Float, default=0.0)
    payment_method: Mapped[str] = mapped_column(String(50), default='Pending')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # NEW: which driver accepted this booking
    driver_id: Mapped[int | None] = mapped_column(
        db.ForeignKey('users.id'),
        nullable=True
    )

# ------------------------------------
# --- UTILITY FUNCTIONS & GEMINI ---
# ------------------------------------

def allowed_file(filename):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_user_by_email(email):
    """Finds a User object given their email address."""
    stmt = select(User).where(func.lower(User.email) == func.lower(email))
    return db.session.execute(stmt).scalar_one_or_none()


def calculate_fare(distance, seats):
    """Calculates the total fare based on distance and number of seats."""
    rate_per_km = 10
    return distance * rate_per_km * seats


def get_gemini_advice(prompt, max_retries=3):
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "systemInstruction": {
            "parts": [{
                "text": "You are a concise travel assistant. Provide a helpful, personalized travel guide and summary, including local weather and 3 points of interest, based on the provided destination and date. Format the output using markdown."
            }]
        }
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                headers=headers,
                data=json.dumps(payload)
            )
            response.raise_for_status()

            result = response.json()

            candidate = result.get('candidates', [{}])[0]
            text = candidate.get('content', {}).get('parts', [{}])[0].get('text', 'No advice generated.')

            sources = []
            grounding = candidate.get('groundingMetadata', {}).get('groundingAttributions', [])
            for attr in grounding:
                if attr.get('web'):
                    sources.append({
                        'uri': attr['web'].get('uri'),
                        'title': attr['web'].get('title')
                    })

            return {"text": text, "sources": sources}

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                return {"text": f"Error contacting AI service: {e}", "sources": []}
    return {"text": "Failed to generate advice after multiple retries.", "sources": []}


# ------------------------------------
# --- ROLE BASED REDIRECTION LOGIC ---
# ------------------------------------

def role_redirect(user):
    """Redirects the user to the appropriate dashboard based on role."""
    if user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif user.role == 'driver':
        return redirect(url_for('driver_dashboard'))
    else:  # Default or Passenger
        return redirect(url_for('home'))


# ------------------------------------
# --- ROUTES (UPDATED FOR ROLE) ---
# ------------------------------------

@app.route("/")
def home():
    logged_in = 'user_id' in session
    username = session.get('username', None)
    return render_template("home.html", logged_in=logged_in, username=username)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        role = request.form["role"]  # NEW: Get role from form

        user_exists = db.session.execute(
            select(User).filter((User.username == username) | (User.email == email))
        ).scalar_one_or_none()

        if user_exists:
            flash("Username or Email already registered.", "error")
            return render_template("register.html")

        new_user = User(
            username=username,
            password=request.form["password"],
            name=request.form.get("name", username.capitalize()),
            email=email,
            mobile=request.form.get("mobile"),
            address=request.form.get("address", ""),
            role=role  # NEW: Save role
        )
        db.session.add(new_user)
        db.session.commit()

        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if 'user_id' in session:
        user = db.session.get(User, session['user_id'])
        return role_redirect(user)  # Check role on subsequent visits

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = db.session.execute(select(User).filter_by(username=username)).scalar_one_or_none()

        if user and user.password == password:
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role  # NEW: Store role in session
            flash(f"Welcome back, {user.username}!", "success")

            return role_redirect(user)  # NEW: Redirect based on role after login
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


# --- Admin Dashboard Route ---
@app.route("/admin_dashboard")
def admin_dashboard():
    if "user_id" not in session:
        flash("Please log in to access the admin dashboard.", "warning")
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied. Admin access only.", "error")
        return redirect(url_for("home"))

    admin_user = db.session.get(User, session["user_id"])

    total_users = db.session.execute(select(func.count(User.id))).scalar()
    total_drivers = db.session.execute(
        select(func.count(User.id)).where(User.role == "driver")
    ).scalar()
    total_passengers = db.session.execute(
        select(func.count(User.id)).where(User.role == "passenger")
    ).scalar()
    total_bookings = db.session.execute(
        select(func.count(Booking.id))
    ).scalar()

    recent_bookings = db.session.execute(
        select(Booking).order_by(Booking.created_at.desc()).limit(10)
    ).scalars().all()

    recent_with_users = []
    for b in recent_bookings:
        passenger = db.session.get(User, b.user_id)
        recent_with_users.append({
            "booking": b,
            "passenger_name": passenger.name if passenger else "Unknown",
            "passenger_email": passenger.email if passenger else "N/A"
        })

    stats = {
        "total_users": total_users or 0,
        "total_drivers": total_drivers or 0,
        "total_passengers": total_passengers or 0,
        "total_bookings": total_bookings or 0,
    }

    return render_template(
        "admin_dashboard.html",
        admin_user=admin_user,
        stats=stats,
        recent_bookings=recent_with_users
    )


# --- Driver Dashboard Route ---
@app.route("/driver_dashboard")
def driver_dashboard():
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    driver_user = db.session.get(User, session["user_id"])

    # 🔥 Only show bookings that are NOT yet assigned to a driver
    bookings = db.session.execute(
        select(Booking)
        .where(Booking.driver_id.is_(None))
        .order_by(Booking.created_at.desc())
    ).scalars().all()

    job_listings = []
    for b in bookings:
        passenger = db.session.get(User, b.user_id)
        job_listings.append({
            "booking": b,
            "passenger_name": passenger.name if passenger else "Unknown",
            "passenger_mobile": passenger.mobile if passenger else "N/A",
        })

    return render_template(
        "driver_dashboard.html",
        driver_user=driver_user,
        bookings=job_listings
    )


@app.route("/forgot_password", methods=["POST"])
def forgot_password():
    recovery_email = request.form.get('recovery_email')
    user = get_user_by_email(recovery_email)
    # You can also trigger a reset email here if you implement tokens
    flash("If an account with that email exists, a password reset link has been sent.", "info")
    return redirect(url_for("login"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    if 'user_id' not in session:
        flash("You must be logged in to view your profile.", "warning")
        return redirect(url_for("login"))

    user = db.session.get(User, session['user_id'])

    if request.method == "POST":
        user.name = request.form.get("name")
        user.email = request.form.get("email")
        user.mobile = request.form.get("mobile")
        user.address = request.form.get("address")

        if 'photo' in request.files:
            file = request.files['photo']

            if file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(user.username + "_" + file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)

                user.photo_path = url_for('static', filename='uploads/' + filename)
                flash("Photo uploaded successfully!", "success")
            elif file.filename != '':
                flash("Invalid file type. Only PNG, JPG, JPEG, GIF are allowed.", "error")

        db.session.commit()
        flash("Profile details updated successfully!", "success")
        return role_redirect(user)

    return render_template("profile.html", user=user, username=user.username, default_photo=DEFAULT_PHOTO_PATH)


@app.route("/delete_photo", methods=["POST"])
def delete_photo():
    if 'user_id' not in session:
        flash("You must be logged in to modify your profile.", "warning")
        return redirect(url_for("login"))

    user = db.session.get(User, session['user_id'])
    current_path = user.photo_path

    if current_path and current_path != DEFAULT_PHOTO_PATH:

        if current_path.startswith('/static/'):
            relative_path_segment = current_path.lstrip('/')
            file_to_delete = os.path.join(app.root_path, relative_path_segment)

            if os.path.exists(file_to_delete):
                os.remove(file_to_delete)
                flash("Profile photo deleted.", "info")
            else:
                flash("Warning: File not found on server, but path cleared.", "warning")

        user.photo_path = DEFAULT_PHOTO_PATH
        db.session.commit()

    else:
        flash("No custom photo to delete.", "warning")

    return redirect(url_for("profile"))


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("username", None)
    session.pop("role", None)  # NEW: Remove role from session
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/change_password", methods=["GET", "POST"])
def change_password():
    if 'user_id' not in session:
        flash("You must be logged in to change your password.", "warning")
        return redirect(url_for("login"))

    user = db.session.get(User, session['user_id'])

    if request.method == "POST":
        current_password = request.form["current_password"]
        new_password = request.form["new_password"]

        if user.password == current_password:
            user.password = new_password
            db.session.commit()
            flash("Your password has been changed successfully.", "success")
            return redirect(url_for("home"))
        else:
            flash("Current password incorrect.", "error")

    return render_template("change_password.html")


# ------------------------------------
# --- BOOKING ROUTES (WITH EMAILS) ---
# ------------------------------------

@app.route("/book", methods=["GET", "POST"])
def book():
    if 'user_id' not in session:
        flash("Please log in to make a booking.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        distance = float(request.form["distance"])
        seats = int(request.form["seats"])
        total_fare = calculate_fare(distance, seats)

        new_booking = Booking(
            vehicle=request.form.get("vehicle", "Car"),
            pickup=request.form["pickup"],
            drop_off=request.form["drop"],
            date=request.form["date"],
            seats=seats,
            distance=distance,
            user_id=session["user_id"],
            total_fare=total_fare,
            payment_method="Pending"
        )
        db.session.add(new_booking)
        db.session.commit()

        # EMAIL: Passenger confirmation
        passenger = db.session.get(User, session["user_id"])

        if passenger and passenger.email:
            subject_p = f"Booking Created - ID {new_booking.id}"
            body_p = f"""
Hello {passenger.name},

Your booking has been created successfully on Auto Booking System.

Booking Details:
- Booking ID: {new_booking.id}
- Vehicle: {new_booking.vehicle}
- Pickup: {new_booking.pickup}
- Drop-off: {new_booking.drop_off}
- Date: {new_booking.date}
- Distance: {new_booking.distance} km
- Seats: {new_booking.seats}
- Estimated Fare: ₹{new_booking.total_fare:.2f}

Current Payment Status: {new_booking.payment_method}

You can view all your bookings here:
{request.url_root.strip('/') + url_for('view_all')}

Thank you for using our service!
"""
            send_email(subject_p, body_p, [passenger.email])

        # EMAIL: Notify drivers of new trip (broadcast)
        drivers = db.session.execute(
            select(User).where(User.role == "driver")
        ).scalars().all()

        driver_emails = [d.email for d in drivers if d.email]

        if driver_emails:
            subject_d = f"New Trip Request Available - Booking {new_booking.id}"
            body_d = f"""
Dear Driver,

A new trip request is available on the platform.

Trip Details:
- Booking ID: {new_booking.id}
- Passenger: {passenger.name if passenger else "N/A"}
- Pickup: {new_booking.pickup}
- Drop-off: {new_booking.drop_off}
- Date: {new_booking.date}
- Distance: {new_booking.distance} km
- Vehicle: {new_booking.vehicle}
- Estimated Fare: ₹{new_booking.total_fare:.2f}

Please log in to your Driver Dashboard to review and accept jobs:
{request.url_root.strip('/') + url_for('driver_dashboard')}

This is an automated message. Please do not reply.
"""
            send_email(subject_d, body_d, driver_emails)

        flash("Booking created. Proceed to payment.")
        return redirect(url_for("payment.payment_page", booking_id=new_booking.id))

    vehicle_pref = request.args.get("vehicle", "")
    return render_template("booking.html", vehicle_pref=vehicle_pref)


@app.route("/view_ticket/<booking_id>")
def view_ticket(booking_id):
    booking = db.session.get(Booking, booking_id)

    if booking and booking.user_id == session.get('user_id'):
        return render_template("view_ticket.html", booking=booking)

    flash("Booking ID not found or access denied!", "error")
    return redirect(url_for("home"))


@app.route("/cancel_booking", methods=["GET", "POST"])
def cancel_booking():
    if 'user_id' not in session:
        flash("Please log in to cancel a booking.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        booking_id = request.form["booking_id"]

        booking = db.session.get(Booking, booking_id)

        if booking and booking.user_id == session['user_id']:
            # Copy before delete
            booking_copy = booking
            passenger = db.session.get(User, booking.user_id)

            db.session.delete(booking)
            db.session.commit()
            flash(f"Booking {booking_id} cancelled successfully!", "success")

            # Email passenger about cancellation
            if passenger and passenger.email:
                subject_p = f"Booking Cancelled - ID {booking_id}"
                body_p = f"""
Hello {passenger.name},

Your booking has been cancelled successfully.

Cancelled Booking Details:
- Booking ID: {booking_id}
- Vehicle: {booking_copy.vehicle}
- Pickup: {booking_copy.pickup}
- Drop-off: {booking_copy.drop_off}
- Date: {booking_copy.date}

If this was not you, please contact support.

Thank you.
"""
                send_email(subject_p, body_p, [passenger.email])

        else:
            flash(f"Booking {booking_id} not found or you do not have permission to cancel it.", "error")

        return redirect(url_for("home"))

    return render_template("cancel_booking.html")


@app.route("/view_all")
def view_all():
    if 'user_id' not in session:
        flash("Please log in to view all bookings.", "warning")
        return redirect(url_for("login"))

    user_bookings = db.session.execute(
        select(Booking).filter_by(user_id=session['user_id']).order_by(Booking.created_at.desc())
    ).scalars().all()

    return render_template("view_all.html", bookings=user_bookings)

@app.route("/driver_accept/<booking_id>")
def driver_accept(booking_id):
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    booking = db.session.get(Booking, booking_id)
    driver = db.session.get(User, session["user_id"])

    if not booking:
        flash("Booking not found.", "error")
        return redirect(url_for("driver_dashboard"))

    passenger = db.session.get(User, booking.user_id)

    # Mark booking as assigned
    booking.driver_id = driver.id
    booking.payment_method = "Driver Assigned"
    db.session.commit()

    # Email Passenger
    if passenger and passenger.email:
        subject_p = "Your Driver Has Accepted Your Booking"
        body_p = f"""
Hello {passenger.name},

Good news! A driver has accepted your booking.

Booking Details:
- Booking ID: {booking.id}
- Vehicle: {booking.vehicle}
- Pickup: {booking.pickup}
- Drop-off: {booking.drop_off}
- Date: {booking.date}

Driver Assigned:
- Name: {driver.name}
- Mobile: {driver.mobile}

Your driver will contact you soon.
"""
        send_email(subject_p, body_p, [passenger.email])

    # Email Driver
    if driver and driver.email:
        subject_d = "Trip Accepted Successfully"
        body_d = f"""
Hello {driver.name},

You have successfully accepted a new trip.

Trip Details:
- Booking ID: {booking.id}
- Pickup: {booking.pickup}
- Drop-off: {booking.drop_off}
- Date: {booking.date}
- Estimated Fare: ₹{booking.total_fare:.2f}
"""
        send_email(subject_d, body_d, [driver.email])

    flash("Trip accepted successfully!", "success")
    return redirect(url_for("driver_assigned"))

# -----------------------------
# DRIVER: TRIP HISTORY
# -----------------------------
@app.route("/driver_trip_history")
def driver_trip_history():
    # Only drivers can see this page
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    driver = db.session.get(User, session["user_id"])

    # Simple version: show all bookings marked as 'Driver Assigned'
    trips = db.session.execute(
        select(Booking).where(Booking.payment_method == "Driver Assigned")
    ).scalars().all()

    return render_template("driver_trip_history.html", driver=driver, trips=trips)


# -----------------------------
# DRIVER: EARNINGS PAGE
# -----------------------------
@app.route("/driver_earnings")
def driver_earnings():
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    driver = db.session.get(User, session["user_id"])

    # For now: earnings = sum of assigned bookings
    assigned = db.session.execute(
        select(Booking).where(Booking.payment_method == "Driver Assigned")
    ).scalars().all()

    total_earnings = sum(b.total_fare for b in assigned)

    return render_template(
        "driver_earnings.html",
        driver=driver,
        bookings=assigned,
        total_earnings=total_earnings
    )


# -----------------------------
# DRIVER: ADD VEHICLE (SIMPLE FORM)
# -----------------------------
@app.route("/driver_add_vehicle", methods=["GET", "POST"])
def driver_add_vehicle():
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        flash("Vehicle added successfully! (Simulation Only)", "success")
        return redirect(url_for("driver_view_vehicles"))

    return render_template("driver_add_vehicle.html")


# -----------------------------
# DRIVER: VIEW VEHICLES (Simulated)
# -----------------------------
@app.route("/driver_view_vehicles")
def driver_view_vehicles():
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    # Simulated list
    vehicles = [
        {"name": "Auto Rickshaw", "number": "MH-12-AB-1234"},
        {"name": "Car - WagonR", "number": "MH-12-CD-5678"},
    ]

    return render_template("driver_view_vehicles.html", vehicles=vehicles)

@app.route("/driver_assigned")
def driver_assigned():
    if "user_id" not in session or session.get("role") != "driver":
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    driver = db.session.get(User, session["user_id"])

    # Only bookings assigned to THIS driver
    assigned_bookings = db.session.execute(
        select(Booking)
        .where(Booking.driver_id == driver.id)
        .order_by(Booking.created_at.desc())
    ).scalars().all()

    # Build list with passenger info so template has everything
    bookings_with_passenger = []
    for b in assigned_bookings:
        passenger = db.session.get(User, b.user_id)
        bookings_with_passenger.append({
            "booking": b,
            "passenger_name": passenger.name if passenger else "Unknown",
            "passenger_mobile": passenger.mobile if passenger else "N/A",
        })

    return render_template(
        "driver_assigned.html",
        driver_user=driver,
        bookings=bookings_with_passenger   # <-- template will use "bookings"
    )



if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    app.run(debug=True)
