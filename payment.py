from flask import Blueprint, render_template, request, redirect, url_for, flash
import json

payment_bp = Blueprint('payment', __name__, template_folder='templates')

BOOKINGS_FILE = "bookings.json"

def load_bookings():
    try:
        with open(BOOKINGS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_bookings(bookings):
    with open(BOOKINGS_FILE, "w") as f:
        json.dump(bookings, f, indent=4)

@payment_bp.route("/payment_page/<booking_id>", methods=["GET", "POST"])
def payment_page(booking_id):
    bookings = load_bookings()
    booking = next((b for b in bookings if b["id"] == booking_id), None)
    if not booking:
        flash("Booking not found.")
        return redirect(url_for('home'))
    if request.method == "POST":
        method = request.form.get("payment_method")
        if method == "cod":
            booking["payment_method"] = "Cash on Delivery"
            message = f"Cash on Delivery selected for Booking ID: {booking_id}"
        else:
            # store only last4 for card to avoid sensitive storage
            card = request.form.get("card_number", "")
            last4 = card[-4:] if card else ""
            booking["payment_method"] = "Card (last4:" + last4 + ")"
            message = f"Card Payment successful for Booking ID: {booking_id}"
        save_bookings(bookings)
        return render_template("payment_success.html", booking_id=booking_id, message=message)
    return render_template("payment_form.html", booking=booking)
