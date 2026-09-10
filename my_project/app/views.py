from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    abort,
    jsonify
)
from sqlalchemy.exc import SQLAlchemyError
from collections import defaultdict
from functools import wraps

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
import uuid

from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user
)

from app import db

from sqlalchemy import func

from app.models import (
    Student,
    StudentType,
    StudentRemark,
    StudentPayment,
    StudentResult,
    Teacher,
    Attendance,
    User,
    Room,
    RoomAssignment
)


main = Blueprint("main", __name__)


# ============================================================
# LOGIN REQUIRED, BY DEFAULT, FOR EVERYTHING ON THIS BLUEPRINT
#
# Rather than adding @login_required to every single route
# (20+ of them, and easy to forget on a new one later), this
# runs before every request to this blueprint and blocks
# anyone who isn't logged in - except the login page itself,
# which obviously can't require being logged in first.
# ============================================================

@main.before_request
def require_login():

    if request.endpoint == "main.login":
        return

    if not current_user.is_authenticated:

        return redirect(
            url_for(
                "main.login",
                next=request.path
            )
        )


# ============================================================
# ADMIN-ONLY ROUTES
#
# Applied on top of the login check above, for actions that
# should be restricted to admins specifically - currently just
# deletions. A logged-in "staff" user gets a 403 if they hit
# one of these directly.
# ============================================================

def admin_required(view_function):

    @wraps(view_function)
    def wrapped(*args, **kwargs):

        if not current_user.is_admin():
            abort(403)

        return view_function(*args, **kwargs)

    return wrapped


# ============================================================
# TEACHER ACCOUNTS ARE FULLY SANDBOXED
#
# A "teacher" role login can ONLY reach the endpoints listed
# here - everything else (the Dashboard, student profiles,
# payments, admin-wide Attendance overview, and so on) is
# blocked outright. This is a whitelist, not a blocklist, on
# purpose: a new route added later is blocked for teachers by
# default, until someone deliberately adds it here.
#
# Being on this list only proves the ENDPOINT is teacher-safe
# in general - it does NOT prove a specific course belongs to
# THIS teacher. That check happens separately, inside
# course_info() and take_attendance() themselves, since it
# needs to look at which course_id was actually requested.
# ============================================================

TEACHER_ALLOWED_ENDPOINTS = {
    "main.login",
    "main.logout",
    "main.teachers_ui",
    "main.course_info",
    "main.take_attendance",
    "main.room_assign",
    "main.room_assign_data",
}


@main.before_request
def restrict_teacher_access():

    if not current_user.is_authenticated:
        return

    if (
        current_user.is_teacher()
        and request.endpoint not in TEACHER_ALLOWED_ENDPOINTS
    ):
        abort(403)


def to_decimal(value):
    """Safely convert any value to a Decimal, returning 0 instead of raising if it's missing or not a valid number."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def to_money(value):
    """Convert to Decimal and round to exactly 2 decimal places - for actual money amounts only, not exchange rates (4 decimals) or percentages."""
    return to_decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def normalize_currency(value, default="MMK"):
    """Force a currency value to be either 'MMK' or 'USD', falling back to default for anything else (missing, blank, typo)."""
    currency = (
        str(value or default)
        .strip()
        .upper()
    )
    if currency not in ("MMK", "USD"):
        return default
    return currency


def parse_bool_flag(form, field_name, default="no"):
    """Read a yes/no style form field as a real bool. Accepts 'yes', 'true', '1', 'on' (case-insensitive) as true."""
    return (
        form.get(field_name, default)
        .strip()
        .lower()
        in ("yes", "true", "1", "on")
    )


def parse_form_date(form, field_name):
    """Parse a 'YYYY-MM-DD' form/query-string field into a date, or None if it's empty. Raises ValueError if it's present but not a valid date."""
    raw_value = form.get(field_name, "")
    if raw_value:
        raw_value = raw_value.strip()
    if not raw_value:
        return None
    return datetime.strptime(
        raw_value,
        "%Y-%m-%d"
    ).date()


def student_id_taken(student_id, exclude_student_id=None):
    """Check whether a student_id is already used by a DIFFERENT student. Pass exclude_student_id when editing a student, so it doesn't flag itself."""
    query = Student.query.filter(
        Student.student_id == student_id
    )
    if exclude_student_id is not None:
        query = query.filter(
            Student.id != exclude_student_id
        )
    return query.first() is not None


def get_student_for_update(student_id):
    """
    Load a student and LOCK their row for the rest of this
    transaction (SELECT ... FOR UPDATE). Any other request that
    also locks the SAME student's row through this helper will
    wait until this one commits or rolls back, instead of both
    racing to read/write that student's payment balances at
    the same time. Requests for a DIFFERENT student are never
    affected - the lock is per-row, not per-table.
    """
    student = (
        Student.query
        .filter_by(id=student_id)
        .with_for_update()
        .first()
    )
    if student is None:
        abort(404)
    return student


def calculate_discount(
    total_amount,
    discount_type,
    discount,
    promotion_amount
):
    """
    Apply a discount BEFORE any currency conversion happens.
    "percentage" takes discount as a % of total_amount.
    "promotion" takes promotion_amount as a fixed cash amount
    (capped at total_amount, so it can never go negative).
    Returns (discount_amount, total_after_discount).
    """
    total_amount = max(to_decimal(total_amount), Decimal("0"))
    discount_type = (discount_type or "percentage").lower().strip()
    discount = max(to_decimal(discount), Decimal("0"))
    promotion_amount = max(to_decimal(promotion_amount), Decimal("0"))

    if discount_type == "promotion":
        discount_amount = min(promotion_amount, total_amount)
    elif discount_type == "percentage":
        discount = min(discount, Decimal("100"))
        discount_amount = total_amount * discount / Decimal("100")
    else:
        discount_amount = Decimal("0")

    total_after_discount = max(total_amount - discount_amount, Decimal("0"))

    return (discount_amount, total_after_discount)


def validate_currency_payment(
    payment_currency,
    course_currency,
    exchange_enabled,
    exchange_rate
):
    """
    Check that a payment's currency setup makes sense. Same
    currency needs nothing extra. Different currencies need
    exchange_enabled=True and a positive exchange_rate.
    Returns (True, None) if valid, or (False, "reason").
    """
    payment_currency = normalize_currency(payment_currency)
    course_currency = normalize_currency(course_currency)

    if payment_currency == course_currency:
        return True, None

    if payment_currency not in ("MMK", "USD"):
        return False, "Invalid payment currency."
    if course_currency not in ("MMK", "USD"):
        return False, "Invalid course currency."

    if not exchange_enabled:
        return (
            False,
            "Exchange rate is required when payment "
            "currency and course currency are different."
        )

    if exchange_rate is None:
        return (False, "Please enter an exchange rate.")

    exchange_rate = to_decimal(exchange_rate)

    if exchange_rate <= 0:
        return (False, "Exchange rate must be greater than 0.")

    return True, None


def resolve_currency_settings(
    form,
    course_currency,
    default_payment_currency=None
):
    """
    Shared by add_student, edit_student, add_payment, and
    edit_payment: reads payment_currency/exchange_enabled/
    exchange_rate from a form, auto-disables exchange when
    currencies match, auto-enables it when a valid rate was
    given even without an explicit toggle, then validates the
    result via validate_currency_payment(). Returns
    (payment_currency, exchange_enabled, exchange_rate, valid, error).
    """
    course_currency = normalize_currency(course_currency)

    payment_currency = normalize_currency(
        form.get(
            "payment_currency",
            default_payment_currency or course_currency
        )
    )

    exchange_enabled = parse_bool_flag(form, "exchange_enabled")

    exchange_rate_raw = form.get("exchange_rate", "").strip()
    exchange_rate = (
        to_decimal(exchange_rate_raw)
        if exchange_rate_raw
        else None
    )

    if payment_currency == course_currency:
        exchange_enabled = False
        exchange_rate = None
    elif (
        exchange_rate is not None
        and exchange_rate > 0
    ):
        exchange_enabled = True

    valid, error = validate_currency_payment(
        payment_currency,
        course_currency,
        exchange_enabled,
        exchange_rate
    )

    return (
        payment_currency,
        exchange_enabled,
        exchange_rate,
        valid,
        error
    )


def convert_payment_to_course_currency(
    amount_paid,
    payment_currency,
    course_currency,
    exchange_enabled=False,
    exchange_rate=None
):
    """
    Convert what a customer actually paid into the course's
    own currency. Same currency = no conversion. Different
    currency needs exchange_enabled + a positive exchange_rate
    (meaning 1 USD = exchange_rate MMK): MMK->USD divides,
    USD->MMK multiplies. Returns 0 if exchange isn't properly
    set up, rather than guessing.
    """
    amount_paid = max(to_decimal(amount_paid), Decimal("0"))
    payment_currency = normalize_currency(payment_currency)
    course_currency = normalize_currency(course_currency)

    if payment_currency == course_currency:
        return to_money(amount_paid)

    if not exchange_enabled:
        return Decimal("0")

    exchange_rate = to_decimal(exchange_rate)

    if exchange_rate <= 0:
        return Decimal("0")

    if payment_currency == "MMK" and course_currency == "USD":
        return to_money(amount_paid / exchange_rate)

    if payment_currency == "USD" and course_currency == "MMK":
        return to_money(amount_paid * exchange_rate)

    return Decimal("0")


def get_payment_amount_in_course_currency(
    payment,
    amount_received,
    course_currency,
    payment_currency
):
    """
    Same idea as convert_payment_to_course_currency, but for
    an EXISTING StudentPayment row - re-derives the converted
    amount from the payment's own stored exchange settings and
    its amount_paid (falling back to amount_received for older
    rows saved before amount_paid existed).
    """
    amount_received = to_decimal(amount_received)
    course_currency = normalize_currency(course_currency)
    payment_currency = normalize_currency(payment_currency, course_currency)

    if payment_currency == course_currency:
        return amount_received

    exchange_enabled = bool(getattr(payment, "exchange_enabled", False))
    exchange_rate = to_decimal(getattr(payment, "exchange_rate", None))

    if not exchange_enabled:
        return Decimal("0")
    if exchange_rate <= 0:
        return Decimal("0")

    actual_amount = getattr(payment, "amount_paid", None)
    if actual_amount is None:
        actual_amount = amount_received
    actual_amount = to_decimal(actual_amount)

    return convert_payment_to_course_currency(
        actual_amount,
        payment_currency,
        course_currency,
        exchange_enabled,
        exchange_rate
    )


INVOICE_ID_OFFSET = 1
LOW_ATTENDANCE_THRESHOLD = 80


# ============================================================
# FIELD LENGTH LIMITS
#
# Matches the actual column limits in models.py. One shared
# copy so add_student(), edit_student(), and student_details()
# can never drift out of sync with each other or with the
# database - previously each view had its own copy of this
# same dict.
# ============================================================

FIELD_MAX_LENGTHS = {
    "student_id": 20,
    "full_name": 100,
    "phone_number": 100,
    "nrc": 50,
    "father_name": 100,
    "education": 150,
}


def check_field_lengths(form):
    """
    Check submitted form fields against FIELD_MAX_LENGTHS.
    Returns an error message for the first field that's too
    long, or None if everything fits - the caller decides how
    to respond (render a form again, or redirect with an
    error=), since that differs by view.
    """

    for field_name, max_length in FIELD_MAX_LENGTHS.items():

        value = form.get(field_name, "").strip()

        if len(value) > max_length:

            return (
                f"{field_name.replace('_', ' ').title()} is too long "
                f"(maximum {max_length} characters)."
            )

    return None


def format_invoice_id(payment_id):
    """Turn a payment's own database id into its invoice number, e.g. id=5 -> 'INV-000004' (see INVOICE_ID_OFFSET above)."""
    return f"INV-{payment_id - INVOICE_ID_OFFSET:06d}"


def create_payment_with_invoice_id(**payment_fields):
    """
    Create a StudentPayment and derive its invoice_id from the
    id the database assigns it - this is what makes invoice
    numbering race-safe (see the big comment above). Uses a
    short-lived placeholder invoice_id until the real id
    exists, then flushes and overwrites it.
    """
    payment_fields["invoice_id"] = f"TMP-{uuid.uuid4().hex[:20]}"
    payment = StudentPayment(**payment_fields)
    db.session.add(payment)
    db.session.flush()
    payment.invoice_id = format_invoice_id(payment.id)
    return payment


def get_student_payments(student_id):
    """All of one student's payments, oldest first - the ordering every balance calculation below depends on."""
    return (
        StudentPayment.query
        .filter_by(student_id=student_id)
        .order_by(StudentPayment.id.asc())
        .all()
    )


def get_course_total_after_discount(initial_payment):
    """The 'official' course fee, taken from the student's FIRST payment record. Falls back to raw total_amount if no discount was ever recorded."""
    if initial_payment is None:
        return Decimal("0")
    total_after_discount = to_decimal(initial_payment.total_after_discount)
    if total_after_discount <= 0:
        total_after_discount = to_decimal(initial_payment.total_amount)
    return total_after_discount


def calculate_student_totals(student_id):
    """
    A student's total course fee, total received, and total
    still pending - all converted into the course's own
    currency, since payments can come in different currencies.
    Returns (total_payment, total_received, total_pending).
    """
    payments = get_student_payments(student_id)

    if not payments:
        return (Decimal("0"), Decimal("0"), Decimal("0"))

    student = payments[0].student
    course_currency = normalize_currency(student.currency if student else None)
    total_payment = get_course_total_after_discount(payments[0])

    total_received = Decimal("0")
    for payment in payments:
        payment_currency = normalize_currency(
            getattr(payment, "payment_currency", None) or course_currency,
            course_currency
        )
        amount_received = to_decimal(getattr(payment, "amount_received", 0))
        amount_paid_course_currency = get_payment_amount_in_course_currency(
            payment, amount_received, course_currency, payment_currency
        )
        total_received += amount_paid_course_currency

    total_pending = max(total_payment - total_received, Decimal("0"))

    return (total_payment, total_received, total_pending)


def update_student_payment_status(student):
    """Recompute and set a student's payment_status ('unpaid'/'partial'/'paid') from their current totals."""
    (total_payment, total_received, total_pending) = calculate_student_totals(student.id)

    if total_received <= 0:
        student.payment_status = "unpaid"
    elif total_pending > 0:
        student.payment_status = "partial"
    else:
        student.payment_status = "paid"


def rebuild_payment_balances(student_id, total_after_discount=None):
    """
    Recalculate current_receivable and pending_amount on EVERY
    payment for a student, in order - needed whenever a
    payment is added, edited, or the course total changes,
    since each payment's balance depends on all the ones
    before it. Also refreshes the student's payment_status.
    """
    payments = get_student_payments(student_id)

    if not payments:
        return

    if total_after_discount is None:
        total_after_discount = get_course_total_after_discount(payments[0])

    total_after_discount = max(to_decimal(total_after_discount), Decimal("0"))

    cumulative_paid = Decimal("0")

    for payment in payments:
        payment.current_receivable = max(
            total_after_discount - cumulative_paid, Decimal("0")
        )
        cumulative_paid += to_decimal(payment.amount_received)
        payment.pending_amount = max(
            total_after_discount - cumulative_paid, Decimal("0")
        )

    student = Student.query.get(student_id)
    if student:
        update_student_payment_status(student)


def build_payment_rows(payments):
    """
    Build the payment-history table shown on student_details.html.
    For each payment, works out its currency-converted amount,
    the balance BEFORE it (for the live edit preview), and the
    running balance AFTER it - all in the course's currency.
    Returns a list of dicts, one per payment, oldest first.
    """
    rows = []

    if not payments:
        return rows

    ordered = sorted(payments, key=lambda p: (p.payment_date or date.min, p.id))
    initial_payment = sorted(payments, key=lambda p: p.id)[0]
    student = initial_payment.student
    course_currency = normalize_currency(student.currency if student else None)
    fixed_total = get_course_total_after_discount(initial_payment)

    cumulative_paid = Decimal("0")

    for payment in ordered:
        payment_currency = normalize_currency(
            getattr(payment, "payment_currency", None) or course_currency,
            course_currency
        )
        amount_received = to_decimal(getattr(payment, "amount_received", 0))

        amount_paid_course_currency = get_payment_amount_in_course_currency(
            payment, amount_received, course_currency, payment_currency
        )

        cumulative_paid_before_this_row = cumulative_paid
        current_receivable = max(fixed_total - cumulative_paid, Decimal("0"))

        cumulative_paid += amount_paid_course_currency
        pending = max(fixed_total - cumulative_paid, Decimal("0"))

        raw_amount_paid = getattr(payment, "amount_paid", None)
        actual_amount_paid = (
            to_decimal(raw_amount_paid)
            if raw_amount_paid is not None
            else amount_received
        )

        rows.append({
            "payment": payment,
            "amount_paid": actual_amount_paid,
            "amount_received": amount_received,
            "payment_currency": payment_currency,
            "course_currency": course_currency,
            "amount_paid_course_currency": amount_paid_course_currency,
            "current_receivable": current_receivable,
            "cumulative_paid_before_this_row": cumulative_paid_before_this_row,
            "cumulative_paid": cumulative_paid,
            "pending": pending
        })

    return rows


def render_student_form(student_types, error, form_data, edit_mode, student):
    """Shared render_template call for add_student.html - used by both add_student() and edit_student() so their error/validation responses stay in sync."""
    return render_template(
        "add_student.html",
        student_types=student_types,
        error=error,
        form_data=form_data,
        edit_mode=edit_mode,
        student=student
    )


# ============================================================
# LOGIN
# ============================================================

@main.route(
    "/login",
    methods=["GET", "POST"]
)
def login():
    """Log-in form. Redirects already-logged-in users straight to their landing page; teachers always land on their course list, others return to wherever they were headed (via 'next') or the dashboard."""

    if current_user.is_authenticated:

        if current_user.is_teacher():
            return redirect(url_for("main.teachers_ui"))

        return redirect(url_for("main.dashboard"))

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        user = User.query.filter_by(
            username=username
        ).first()

        if user is None or not user.check_password(password):

            return render_template(
                "login.html",
                error="Incorrect username or password."
            )

        login_user(user)

        # Teachers always go to their course list, regardless
        # of "next" - "next" could point at a page they're not
        # allowed to see (e.g. someone shared a direct link to
        # a student's profile), and there's no sensible fallback
        # for a teacher other than their own courses anyway.
        if user.is_teacher():
            return redirect(url_for("main.teachers_ui"))

        # Only ever redirect to a path on this same site - a
        # "next" value like "http://evil.example.com" would be
        # an open-redirect vulnerability if used as-is.
        next_path = request.form.get("next", "")

        if next_path and next_path.startswith("/"):
            return redirect(next_path)

        return redirect(url_for("main.dashboard"))

    return render_template(
        "login.html",
        error=None,
        next=request.args.get("next", "")
    )


# ============================================================
# LOGOUT
# ============================================================

@main.route(
    "/logout",
    methods=["POST"]
)
def logout():
    """Log the current user out and send them back to the login page."""

    logout_user()

    return redirect(url_for("main.login"))


@main.route("/")
def dashboard():
    """Main student list - search by name/ID/NRC, filter by course/payment status/student type, paginated 10 per page."""
    search = request.args.get("search", "").strip()
    course_search = request.args.get("course_search", "").strip()
    payment_status = request.args.get("payment_status", "all").strip()
    selected_type = request.args.get("student_type", "all").strip()
    page = request.args.get("page", 1, type=int)

    if page < 1:
        page = 1

    per_page = 10
    query = Student.query

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                Student.full_name.ilike(search_pattern),
                Student.student_id.ilike(search_pattern),
                Student.nrc.ilike(search_pattern)
            )
        )

    if course_search:
        course_pattern = f"%{course_search}%"
        query = query.filter(
            Student.id.in_(
                db.session.query(StudentResult.student_id).filter(
                    StudentResult.course_id.ilike(course_pattern)
                )
            )
        )

    if payment_status and payment_status != "all":
        query = query.filter(Student.payment_status == payment_status)

    if selected_type and selected_type != "all":
        query = query.join(StudentType).filter(StudentType.name == selected_type)

    pagination = query.order_by(Student.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    students = pagination.items
    student_types = StudentType.query.order_by(StudentType.name).all()

    return render_template(
        "dashboard.html",
        students=students,
        student_types=student_types,
        search=search,
        course_search=course_search,
        payment_status=payment_status,
        selected_type=selected_type,
        pagination=pagination
    )


@main.route("/add-student", methods=["GET", "POST"])
def add_student():
    """
    Create a new student, optionally with their first payment
    in the same submission. GET shows a blank form; POST
    validates (duplicate ID, field lengths, currency/discount
    settings), creates the student, and creates a payment
    record only if an amount was actually entered.
    """
    student_types = StudentType.query.order_by(StudentType.name).all()

    if request.method == "POST":

        student_id = request.form.get("student_id", "").strip()

        if student_id_taken(student_id):
            return render_student_form(
                student_types,
                f"Student ID '{student_id}' already exists.",
                request.form,
                False,
                None
            )

        length_error = check_field_lengths(request.form)

        if length_error:
            return render_student_form(
                student_types,
                length_error,
                request.form,
                False,
                None
            )

        intake_date = parse_form_date(request.form, "intake_date")
        date_of_birth = parse_form_date(request.form, "date_of_birth")
        student_type_id = request.form.get("student_type_id")

        if not student_type_id:
            return render_student_form(
                student_types,
                "Please select a student type.",
                request.form,
                False,
                None
            )

        currency = normalize_currency(request.form.get("currency", "MMK"))

        student = Student(
            student_id=student_id,
            full_name=request.form.get("full_name", "").strip(),
            phone_number=request.form.get("phone_number", "").strip(),
            nrc=request.form.get("nrc", "").strip(),
            date_of_birth=date_of_birth,
            father_name=request.form.get("father_name", "").strip(),
            education=request.form.get("education", "").strip(),
            address=request.form.get("address", "").strip(),
            currency=currency,
            intake_date=intake_date,
            status="pending",
            payment_status="unpaid",
            student_type_id=int(student_type_id),
            uniform_size=request.form.get("uniform_size", "").strip()
        )

        db.session.add(student)

        try:
            db.session.flush()
        except SQLAlchemyError as e:
            db.session.rollback()
            return render_student_form(
                student_types,
                "Could not save this student - check that all fields are within a reasonable length.",
                request.form,
                False,
                None
            )

        payment_comment = request.form.get("payment_comment", "").strip()
        account = request.form.get("account", "").strip()
        total_amount = to_decimal(request.form.get("total_amount", "0"))
        amount_paid = max(to_money(request.form.get("amount_paid", "0")), Decimal("0"))
        discount_type = request.form.get("discount_type", "percentage").strip().lower()
        discount = to_decimal(request.form.get("discount", "0"))
        promotion_amount = to_decimal(request.form.get("promotion_amount", "0"))

        (
            payment_currency,
            exchange_enabled,
            exchange_rate,
            valid,
            error
        ) = resolve_currency_settings(request.form, currency)

        if not valid:
            db.session.rollback()
            return render_student_form(
                student_types,
                error,
                request.form,
                False,
                None
            )

        (discount_amount, total_after_discount) = calculate_discount(
            total_amount, discount_type, discount, promotion_amount
        )

        converted_amount_paid = min(
            convert_payment_to_course_currency(
                amount_paid, payment_currency, currency,
                exchange_enabled, exchange_rate
            ),
            total_after_discount
        )

        if (total_amount > 0 or amount_paid > 0 or discount_amount > 0):

            pending_amount = max(
                total_after_discount - converted_amount_paid, Decimal("0")
            )

            payment = create_payment_with_invoice_id(
                student_id=student.id,
                payment_date=date.today(),
                total_amount=total_amount,
                discount_type=discount_type,
                discount=(discount if discount_type == "percentage" else Decimal("0")),
                promotion_amount=(promotion_amount if discount_type == "promotion" else Decimal("0")),
                discount_amount=discount_amount,
                total_after_discount=total_after_discount,
                payment_currency=payment_currency,
                exchange_enabled=exchange_enabled,
                exchange_rate=exchange_rate,
                amount_paid=amount_paid,
                amount_received=converted_amount_paid,
                current_receivable=total_after_discount,
                pending_amount=pending_amount,
                comment=payment_comment,
                account=account
            )

            if converted_amount_paid <= 0:
                student.payment_status = "unpaid"
            elif pending_amount > 0:
                student.payment_status = "partial"
            else:
                student.payment_status = "paid"

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return redirect(
                url_for(
                    "main.add_student",
                    error=f"Student could not be added: {str(e)}"
                )
            )

        return redirect(url_for("main.student_details", student_id=student.id))

    return render_student_form(
        student_types,
        request.args.get("error"),
        {
            "invoice_id": "Will be generated automatically",
            "currency": "MMK",
            "payment_currency": "MMK",
            "exchange_enabled": "no",
            "exchange_rate": ""
        },
        False,
        None
    )


@main.route("/edit-student/<int:student_id>", methods=["GET", "POST"])
def edit_student(student_id):
    """
    Full-page edit for a student's info AND their original
    payment/discount/currency settings together (the separate
    inline edit on student_details.html only covers basic
    info - see student_details() below). Locks the student row
    on POST since it rewrites payment balances.
    """
    student = Student.query.get_or_404(student_id)
    student_types = StudentType.query.order_by(StudentType.name).all()
    payments = get_student_payments(student.id)
    initial_payment = payments[0] if payments else None

    if request.method == "POST":

        student = get_student_for_update(student.id)
        payments = get_student_payments(student.id)
        initial_payment = payments[0] if payments else None

        new_student_id = request.form.get("student_id", "").strip()

        if student_id_taken(new_student_id, exclude_student_id=student.id):
            return render_student_form(
                student_types,
                "Student ID already exists.",
                request.form,
                True,
                student
            )

        length_error = check_field_lengths(request.form)

        if length_error:
            return render_student_form(
                student_types,
                length_error,
                request.form,
                True,
                student
            )

        student.student_id = new_student_id
        student.full_name = request.form.get("full_name", "").strip()
        student.phone_number = request.form.get("phone_number", "").strip()
        student.nrc = request.form.get("nrc", "").strip()
        student.date_of_birth = parse_form_date(request.form, "date_of_birth")
        student.father_name = request.form.get("father_name", "").strip()
        student.education = request.form.get("education", "").strip()
        student.address = request.form.get("address", "").strip()

        currency = normalize_currency(
            request.form.get("currency", student.currency or "MMK")
        )
        student.currency = currency

        student.intake_date = parse_form_date(request.form, "intake_date")
        student.student_type_id = int(request.form["student_type_id"])
        student.uniform_size = request.form.get("uniform_size", "").strip()

        if initial_payment:

            total_amount = to_decimal(
                request.form.get("total_amount", initial_payment.total_amount)
            )
            discount_type = request.form.get(
                "discount_type", initial_payment.discount_type or "percentage"
            ).strip().lower()
            discount = to_decimal(
                request.form.get("discount", initial_payment.discount)
            )
            promotion_amount = to_decimal(
                request.form.get("promotion_amount", initial_payment.promotion_amount)
            )

            (
                payment_currency,
                exchange_enabled,
                exchange_rate,
                valid,
                error
            ) = resolve_currency_settings(
                request.form,
                currency,
                default_payment_currency=initial_payment.payment_currency
            )

            if not valid:
                return render_student_form(
                    student_types, error, request.form, True, student
                )

            (discount_amount, total_after_discount) = calculate_discount(
                total_amount, discount_type, discount, promotion_amount
            )

            actual_amount_paid = to_decimal(initial_payment.amount_paid)

            converted_amount_received = convert_payment_to_course_currency(
                actual_amount_paid, payment_currency, currency,
                exchange_enabled, exchange_rate
            )

            initial_payment.total_amount = total_amount
            initial_payment.discount_type = discount_type
            initial_payment.discount = (
                discount if discount_type == "percentage" else Decimal("0")
            )
            initial_payment.promotion_amount = (
                promotion_amount if discount_type == "promotion" else Decimal("0")
            )
            initial_payment.discount_amount = discount_amount
            initial_payment.total_after_discount = total_after_discount
            initial_payment.payment_currency = payment_currency
            initial_payment.exchange_enabled = exchange_enabled
            initial_payment.exchange_rate = exchange_rate
            initial_payment.amount_received = min(
                converted_amount_received, total_after_discount
            )
            initial_payment.pending_amount = max(
                total_after_discount - initial_payment.amount_received, Decimal("0")
            )

            rebuild_payment_balances(student.id, total_after_discount)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error=f"Could not edit student: {str(e)}"
                )
            )

        return redirect(
            url_for("main.student_details", student_id=student.id, success=1)
        )

    form_data = {
        "student_id": student.student_id,
        "full_name": student.full_name,
        "phone_number": student.phone_number or "",
        "nrc": student.nrc or "",
        "date_of_birth": (
            student.date_of_birth.strftime("%Y-%m-%d")
            if student.date_of_birth else ""
        ),
        "father_name": student.father_name or "",
        "education": student.education or "",
        "address": student.address or "",
        "currency": student.currency or "MMK",
        "intake_date": (
            student.intake_date.strftime("%Y-%m-%d")
            if student.intake_date else ""
        ),
        "student_type_id": str(student.student_type_id),
        "uniform_size": student.uniform_size or "",
        "voucher_id": (
            initial_payment.voucher_id
            if initial_payment and hasattr(initial_payment, "voucher_id")
            else ""
        ),
        "total_amount": (
            str(initial_payment.total_amount) if initial_payment else ""
        ),
        "discount_type": (
            initial_payment.discount_type if initial_payment else "percentage"
        ),
        "discount": (
            str(initial_payment.discount) if initial_payment else "0"
        ),
        "promotion_amount": (
            str(initial_payment.promotion_amount) if initial_payment else "0"
        ),
        "discount_amount": (
            str(initial_payment.discount_amount) if initial_payment else "0"
        ),
        "total_after_discount": (
            str(initial_payment.total_after_discount) if initial_payment else ""
        ),
        "amount_paid": (
            str(initial_payment.amount_paid) if initial_payment else "0"
        ),
        "pending_amount": (
            str(initial_payment.pending_amount) if initial_payment else ""
        ),
        "payment_currency": (
            initial_payment.payment_currency if initial_payment else student.currency
        ),
        "exchange_enabled": (
            "yes" if initial_payment and initial_payment.exchange_enabled else "no"
        ),
        "exchange_rate": (
            str(initial_payment.exchange_rate)
            if initial_payment and initial_payment.exchange_rate else ""
        ),
        "payment_comment": (
            initial_payment.comment if initial_payment else ""
        )
    }

    return render_template(
        "add_student.html",
        student_types=student_types,
        form_data=form_data,
        edit_mode=True,
        student=student,
        error=None,
        latest_payment=initial_payment
    )


@main.route("/student/<int:student_id>/add-payment", methods=["POST"])
def add_payment(student_id):
    """Record an additional payment toward a student's existing course fee, converting to course currency and capping at what's still owed."""
    student = get_student_for_update(student_id)

    payment_comment = request.form.get("payment_comment", "").strip()
    amount_paid = max(to_money(request.form.get("amount_paid", "0")), Decimal("0"))

    try:
        payment_date = (
            parse_form_date(request.form, "payment_date") or date.today()
        )
    except ValueError:
        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                error="Invalid payment date."
            )
        )

    payments = get_student_payments(student.id)

    if not payments:
        return redirect(url_for("main.student_details", student_id=student.id))

    initial_payment = payments[0]
    total_after_discount = get_course_total_after_discount(initial_payment)

    total_received_before = sum(
        (to_decimal(payment.amount_received) for payment in payments),
        Decimal("0")
    )

    current_receivable = max(
        total_after_discount - total_received_before, Decimal("0")
    )

    if current_receivable <= 0:
        return redirect(url_for("main.student_details", student_id=student.id))

    course_currency = normalize_currency(student.currency)

    (
        payment_currency,
        exchange_enabled,
        exchange_rate,
        valid,
        error
    ) = resolve_currency_settings(request.form, course_currency)

    if not valid:
        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                error=error
            )
        )

    converted_amount = min(
        convert_payment_to_course_currency(
            amount_paid, payment_currency, course_currency,
            exchange_enabled, exchange_rate
        ),
        current_receivable
    )

    pending_after_payment = max(
        current_receivable - converted_amount, Decimal("0")
    )

    payment = create_payment_with_invoice_id(
        student_id=student.id,
        payment_date=payment_date,
        total_amount=total_after_discount,
        discount_type="none",
        discount=Decimal("0"),
        promotion_amount=Decimal("0"),
        discount_amount=Decimal("0"),
        total_after_discount=total_after_discount,
        payment_currency=payment_currency,
        exchange_enabled=exchange_enabled,
        exchange_rate=exchange_rate,
        amount_paid=amount_paid,
        amount_received=converted_amount,
        current_receivable=current_receivable,
        pending_amount=pending_after_payment,
        comment=payment_comment,
        account=request.form.get("account", "").strip()
    )

    rebuild_payment_balances(student.id, total_after_discount)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                error=f"Could not add payment, check the information and try again: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=student.id, success=1)
    )


@main.route("/delete-student/<int:student_id>", methods=["POST"])
@admin_required
def delete_student(student_id):
    """Permanently delete a student and everything attached to them (payments, courses, remarks, attendance) via cascade. Admin only."""
    student = Student.query.get_or_404(student_id)
    db.session.delete(student)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.dashboard",
                error=f"Could not delete student, try again: {str(e)}"
            )
        )

    return redirect(url_for("main.dashboard"))


@main.route("/student/<int:student_id>/add-course", methods=["POST"])
def add_course(student_id):
    """Enroll a student in an additional course (StudentResult row) - a student can be enrolled in more than one at once."""
    student = Student.query.get_or_404(student_id)

    course_name = request.form.get("course_name", "").strip()
    course_id = request.form.get("course_id", "").strip()

    if not course_name or not course_id:
        return redirect(url_for("main.student_details", student_id=student.id))

    start_date = parse_form_date(request.form, "start_date")
    end_date = parse_form_date(request.form, "end_date")
    result = request.form.get("result", "").strip()
    collected = (request.form.get("collected", "no") == "yes")
    published_date = date.today() if result else None

    course = StudentResult(
        student_id=student.id,
        course_name=course_name,
        course_id=course_id,
        start_date=start_date,
        end_date=end_date,
        result=result or None,
        published_date=published_date,
        collected=collected
    )

    db.session.add(course)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                error=f"Could not add course, check the information and try again: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=student.id, success=1)
    )


@main.route("/course/<int:course_id>/edit", methods=["POST"])
def edit_course(course_id):
    """Update a course enrollment's details - name, ID, dates, result, collected status. Setting a result for the first time stamps published_date; clearing it resets that too."""
    course = StudentResult.query.get_or_404(course_id)
    student_id = course.student_id

    course.course_name = request.form.get("course_name", "").strip()
    course.course_id = request.form.get("course_id", "").strip()
    course.start_date = parse_form_date(request.form, "start_date")
    course.end_date = parse_form_date(request.form, "end_date")

    result = request.form.get("result", "").strip()
    course.result = result or None

    if result and not course.published_date:
        course.published_date = date.today()
    elif not result:
        course.published_date = None

    course.collected = (request.form.get("collected", "no") == "yes")

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=student_id,
                error=f"Could not edit course, check the information and try again: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=student_id, success=1)
    )


@main.route("/course/<int:course_id>/delete", methods=["POST"])
@admin_required
def delete_course(course_id):
    """Remove a single course enrollment (and its attendance records, via cascade) without touching the rest of the student's record. Admin only."""
    course = StudentResult.query.get_or_404(course_id)
    student_id = course.student_id

    db.session.delete(course)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=student_id,
                error=f"Could not delete course, check the information and try again: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=student_id, success=1)
    )


@main.route("/student/<int:student_id>/add-remark", methods=["POST"])
def add_remark(student_id):
    """Add a dated note to a student's record. Silently does nothing if the text field was empty - not an error, just nothing to save."""
    student = Student.query.get_or_404(student_id)
    text = request.form.get("text", "").strip()

    if text:
        remark = StudentRemark(
            student_id=student.id,
            text=text,
            written_date=date.today()
        )
        db.session.add(remark)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error=f"Could not add remark, check the information and try again: {str(e)}"
                )
            )

    return redirect(
        url_for("main.student_details", student_id=student.id, success=1)
    )


@main.route("/remark/<int:remark_id>/delete", methods=["POST"])
@admin_required
def delete_remark(remark_id):
    """Delete one remark. Admin only."""
    remark = StudentRemark.query.get_or_404(remark_id)
    student_id = remark.student_id

    db.session.delete(remark)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=student_id,
                error=f"Could not delete remark, please try again: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=student_id, success=1)
    )


@main.route("/student/<int:student_id>/receipt/<int:payment_id>")
def official_receipt(student_id, payment_id):
    """Printable receipt for one specific payment, with the student's most recent remark included as a footer note."""
    student = Student.query.get_or_404(student_id)

    payment = (
        StudentPayment.query
        .filter_by(id=payment_id, student_id=student_id)
        .first_or_404()
    )

    latest_remark = (
        StudentRemark.query
        .filter_by(student_id=student_id)
        .order_by(StudentRemark.written_date.desc(), StudentRemark.id.desc())
        .first()
    )

    remark = latest_remark.text if latest_remark else ""

    return render_template(
        "official_receipt.html",
        student=student,
        payment=payment,
        remark=remark,
        now=datetime.now()
    )


@main.route("/student/<int:student_id>", methods=["GET", "POST"])
def student_details(student_id):
    """
    The main student page: GET shows everything (courses,
    payments, remarks, totals); POST handles the inline
    'Student Information' edit form on this same page (a
    lighter version of edit_student() - no payment/currency
    fields, just the basic profile).
    """
    student = Student.query.get_or_404(student_id)

    if request.method == "POST":

        new_student_id = request.form.get("student_id", "").strip()

        if student_id_taken(new_student_id, exclude_student_id=student.id):
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error="Student ID already exists."
                )
            )

        length_error = check_field_lengths(request.form)

        if length_error:
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error=length_error
                )
            )

        if new_student_id:
            student.student_id = new_student_id

        student.full_name = request.form.get("full_name", "").strip()
        student.phone_number = request.form.get("phone_number", "").strip()
        student.nrc = request.form.get("nrc", "").strip()
        student.date_of_birth = parse_form_date(request.form, "date_of_birth")
        student.father_name = request.form.get("father_name", "").strip()
        student.education = request.form.get("education", "").strip()
        student.address = request.form.get("address", "").strip()
        student.uniform_size = request.form.get("uniform_size", "").strip()

        student.currency = normalize_currency(
            request.form.get("currency", student.currency or "MMK")
        )

        student.intake_date = parse_form_date(request.form, "intake_date")

        student_type_id = request.form.get("student_type_id", type=int)
        if student_type_id:
            student.student_type_id = student_type_id

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error=f"Could not save changes, please check and try again: {str(e)}"
                )
            )

        return redirect(
            url_for("main.student_details", student_id=student.id, success="1")
        )

    student_types = StudentType.query.order_by(StudentType.name).all()

    results = (
        StudentResult.query
        .filter_by(student_id=student.id)
        .order_by(StudentResult.start_date.desc())
        .all()
    )

    for result in results:
        result.attendance_rate = calculate_attendance_rate(result.id)

    payments = get_student_payments(student.id)

    remarks = (
        StudentRemark.query
        .filter_by(student_id=student.id)
        .order_by(StudentRemark.written_date.desc())
        .all()
    )

    (total_payment, total_received, total_pending) = calculate_student_totals(student.id)

    payment_rows = build_payment_rows(payments)

    edit_course_id = request.args.get("edit_course", type=int)
    edit_course = None

    if edit_course_id:
        edit_course = (
            StudentResult.query
            .filter_by(id=edit_course_id, student_id=student.id)
            .first()
        )

    teachers = Teacher.query.order_by(Teacher.name).all()

    return render_template(
        "student_details.html",
        student=student,
        student_types=student_types,
        results=results,
        payments=payments,
        payment_rows=payment_rows,
        remarks=remarks,
        edit_course=edit_course,
        teachers=teachers,
        total_payment=total_payment,
        total_received=total_received,
        total_pending=total_pending
    )


@main.route("/payment/<int:payment_id>/edit", methods=["POST"])
def edit_payment(payment_id):
    """
    Inline edit for one payment row on student_details.html -
    date, amount, discount, comment, account. Has no currency
    controls of its own, so it only touches currency settings
    if the submitting form explicitly included them. Rebuilds
    every payment's balance afterward since editing one
    payment shifts all the running totals after it.
    """
    payment = StudentPayment.query.get_or_404(payment_id)
    student = get_student_for_update(payment.student_id)
    course_currency = normalize_currency(student.currency)

    payment.account = request.form.get("account", "").strip()

    try:
        payment.payment_date = parse_form_date(request.form, "payment_date")
    except ValueError:
        return redirect(
            url_for(
                "main.student_details",
                student_id=payment.student_id,
                error="Invalid payment date."
            )
        )

    total_amount = max(
        to_decimal(request.form.get("total_amount", "0")), Decimal("0")
    )
    payment.total_amount = total_amount

    discount_type = (
        request.form.get("discount_type") or "none"
    ).strip().lower()

    if discount_type not in ("none", "percentage", "promotion"):
        discount_type = "none"

    discount = max(
        to_decimal(request.form.get("discount", "0")), Decimal("0")
    )

    promotion_amount = discount

    (discount_amount, total_after_discount) = calculate_discount(
        total_amount, discount_type, discount, promotion_amount
    )

    if discount_type == "percentage":
        payment.discount_type = "percentage"
        payment.discount = min(discount, Decimal("100"))
        payment.promotion_amount = Decimal("0")
    elif discount_type == "promotion":
        payment.discount_type = "promotion"
        payment.discount = Decimal("0")
        payment.promotion_amount = min(promotion_amount, total_amount)
    else:
        payment.discount_type = "none"
        payment.discount = Decimal("0")
        payment.promotion_amount = Decimal("0")

    payment.discount_amount = discount_amount
    payment.total_after_discount = total_after_discount

    if "payment_currency" in request.form:
        (
            payment_currency,
            exchange_enabled,
            exchange_rate,
            valid,
            error
        ) = resolve_currency_settings(
            request.form,
            course_currency,
            default_payment_currency=payment.payment_currency
        )

        if not valid:
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=payment.student_id,
                    error=error
                )
            )
    else:
        payment_currency = normalize_currency(payment.payment_currency, course_currency)
        exchange_enabled = payment.exchange_enabled
        exchange_rate = payment.exchange_rate

    amount_paid = max(to_money(request.form.get("amount_paid", "0")), Decimal("0"))

    amount_received = convert_payment_to_course_currency(
        amount_paid, payment_currency, course_currency,
        exchange_enabled, exchange_rate
    )

    payment.payment_currency = payment_currency
    payment.exchange_enabled = exchange_enabled
    payment.exchange_rate = exchange_rate
    payment.amount_paid = amount_paid
    payment.amount_received = amount_received

    rebuild_payment_balances(student.id, total_after_discount)

    payment.comment = request.form.get("payment_comment", "").strip()

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.student_details",
                student_id=payment.student_id,
                error=f"Could not update payment: {str(e)}"
            )
        )

    return redirect(
        url_for("main.student_details", student_id=payment.student_id, success=1)
    )


def summarize_amounts_by_currency(rows):
    """Group a list of payment-report rows by their actual currency, so MMK and USD totals never get silently added together into one meaningless number."""
    totals = defaultdict(lambda: Decimal("0"))
    for row in rows:
        totals[row["currency"]] += row["amount_paid"]
    return dict(totals)


@main.route("/end-of-day")
def end_of_day():
    """Today's payments, grouped by account (Cash, KBZ-Bank, etc.) with per-currency totals per account and for the whole day - the printable cashier's report."""
    report_date = date.today()

    payments = (
        StudentPayment.query
        .filter(StudentPayment.payment_date == report_date)
        .order_by(StudentPayment.payment_date.asc())
        .all()
    )

    account_groups = defaultdict(list)

    for payment in payments:
        amount_paid = to_decimal(payment.amount_paid)
        student = payment.student

        currency = normalize_currency(
            payment.payment_currency
            if payment.payment_currency
            else (student.currency if student else "MMK")
        )

        row = {
            "payment": payment,
            "student": student,
            "amount_paid": amount_paid,
            "currency": currency,
            "amount_received": to_decimal(payment.amount_received),
            "course_currency": (student.currency if student else "MMK"),
            "exchange_enabled": payment.exchange_enabled,
            "exchange_rate": payment.exchange_rate
        }

        account = payment.account or "Unknown"
        account_groups[account].append(row)

    account_groups = dict(account_groups)

    account_totals = {
        account: summarize_amounts_by_currency(rows)
        for account, rows in account_groups.items()
    }

    grand_totals = summarize_amounts_by_currency([
        row for rows in account_groups.values() for row in rows
    ])

    return render_template(
        "end_of_day.html",
        account_groups=account_groups,
        account_totals=account_totals,
        grand_totals=grand_totals,
        report_date=report_date
    )


def get_course_summaries():
    """
    Course-level view built by grouping StudentResult rows by
    course_id, since there's no separate Course table - name,
    date range, enrolled count, and assigned teacher, one
    entry per distinct course. Powers Teachers' UI and the
    Attendance overview page.
    """
    rows = (
        db.session.query(
            StudentResult.course_id,
            func.min(StudentResult.course_name).label("course_name"),
            func.min(StudentResult.start_date).label("start_date"),
            func.max(StudentResult.end_date).label("end_date"),
            func.count(StudentResult.id).label("enrolled_count"),
            func.max(StudentResult.teacher_id).label("teacher_id")
        )
        .group_by(StudentResult.course_id)
        .order_by(StudentResult.course_id)
        .all()
    )

    teacher_ids = [row.teacher_id for row in rows if row.teacher_id]

    teachers_by_id = (
        {
            teacher.id: teacher
            for teacher in Teacher.query.filter(Teacher.id.in_(teacher_ids)).all()
        }
        if teacher_ids else {}
    )

    summaries = []
    for row in rows:
        summaries.append({
            "course_id": row.course_id,
            "course_name": row.course_name,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "enrolled_count": row.enrolled_count,
            "teacher": teachers_by_id.get(row.teacher_id)
        })

    return summaries


def calculate_attendance_rate(student_result_id):
    """Attendance rate (0-100) for one student's enrollment in one course. Returns None (not 0) if attendance has never been taken for it."""
    records = Attendance.query.filter_by(student_result_id=student_result_id).all()

    if not records:
        return None

    present_or_late = sum(
        1 for record in records if record.status in ("present", "late")
    )

    return round(present_or_late / len(records) * 100)


@main.route("/teachers")
def teachers_ui():
    """
    Course-card grid with each course's teacher and enrolled
    count, plus the form for adding new teachers. For a
    teacher-role login, this doubles as their "My Courses"
    landing page - only their own courses are shown, and the
    template hides the add-teacher form and full teacher list
    for them (see is_teacher_view below).
    """
    courses = get_course_summaries()

    is_teacher_view = current_user.is_teacher()

    if is_teacher_view:

        courses = [
            course
            for course in courses
            if course["teacher"]
            and course["teacher"].id == current_user.teacher_id
        ]

        teachers = []

    else:

        teachers = Teacher.query.order_by(Teacher.name).all()

    # Today's room, if one's been assigned - shown on every
    # course card so a teacher (or staff) can see at a glance
    # where a class is happening today without visiting the
    # separate Room Assign page.
    todays_room_assignments = {
        assignment.course_id: assignment.room
        for assignment in RoomAssignment.query.filter_by(
            date=date.today()
        ).all()
    }

    for course in courses:
        course["room_today"] = todays_room_assignments.get(
            course["course_id"]
        )

    return render_template(
        "teachers_ui.html",
        courses=courses,
        teachers=teachers,
        is_teacher_view=is_teacher_view
    )


@main.route("/teachers/add", methods=["POST"])
def add_teacher():
    """Add a new teacher to the roster. No-op if the name field was empty."""
    name = request.form.get("name", "").strip()

    if name:
        teacher = Teacher(name=name)
        db.session.add(teacher)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return redirect(
                url_for(
                    "main.teachers_ui",
                    error=f"Could not add teacher, please try again: {str(e)}"
                )
            )

        return redirect(url_for("main.teachers_ui", success="1"))

    return redirect(url_for("main.teachers_ui"))


@main.route("/course/<path:course_id>/info")
def course_info(course_id):
    """
    One course's roster with attendance-taking for a chosen
    date (?date=YYYY-MM-DD, defaults to today) - shows and
    lets you edit each student's status for that day, plus
    each student's overall attendance rate and the course's
    combined rate across every date ever recorded.
    """
    enrollments = (
        StudentResult.query
        .filter_by(course_id=course_id)
        .join(Student)
        .order_by(Student.full_name)
        .all()
    )

    if not enrollments:
        abort(404)

    first = enrollments[0]

    teacher = (
        Teacher.query.get(first.teacher_id) if first.teacher_id else None
    )

    # A teacher-role login being on this endpoint at all only
    # proves the ENDPOINT is teacher-safe (see
    # TEACHER_ALLOWED_ENDPOINTS) - it says nothing about
    # whether THIS course belongs to THEM. Without this check,
    # a teacher could edit the URL to any other course_id and
    # see (and take attendance for) someone else's class.
    if current_user.is_teacher() and (
        teacher is None
        or teacher.id != current_user.teacher_id
    ):
        abort(403)

    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    enrollment_ids = [enrollment.id for enrollment in enrollments]

    attendance_today = {
        record.student_result_id: record.status
        for record in Attendance.query.filter(
            Attendance.student_result_id.in_(enrollment_ids),
            Attendance.date == selected_date
        ).all()
    }

    for enrollment in enrollments:
        enrollment.attendance_status_today = attendance_today.get(enrollment.id)
        enrollment.attendance_rate = calculate_attendance_rate(enrollment.id)

    all_records = (
        Attendance.query
        .filter(Attendance.student_result_id.in_(enrollment_ids))
        .all()
    )

    if all_records:
        present_or_late = sum(
            1 for record in all_records if record.status in ("present", "late")
        )
        attendance_rate = round(present_or_late / len(all_records) * 100)
    else:
        attendance_rate = None

    return render_template(
        "course_info.html",
        course_id=course_id,
        course_name=first.course_name,
        start_date=first.start_date,
        end_date=first.end_date,
        teacher=teacher,
        enrollments=enrollments,
        selected_date=selected_date,
        attendance_rate=attendance_rate,
        is_teacher_view=current_user.is_teacher()
    )


@main.route("/course/<path:course_id>/assign-teacher", methods=["POST"])
def assign_teacher(course_id):
    """
    Assign a teacher to a course - applies to EVERY student
    enrolled in that course_id, not just whichever student's
    row the form was submitted from (a course has one teacher,
    not a different one per student). Redirects back to
    wherever it was triggered from - a student's page, or
    Teachers' UI.
    """
    teacher_id = request.form.get("teacher_id", type=int)
    student_id = request.form.get("student_id", type=int)

    if teacher_id:
        teacher = Teacher.query.get_or_404(teacher_id)

        StudentResult.query.filter_by(course_id=course_id).update(
            {"teacher_id": teacher.id}
        )

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()

            error_message = f"Could not assign teacher, please try again: {str(e)}"

            if student_id:
                return redirect(
                    url_for(
                        "main.student_details",
                        student_id=student_id,
                        error=error_message
                    )
                )

            return redirect(url_for("main.teachers_ui", error=error_message))

        if student_id:
            return redirect(
                url_for("main.student_details", student_id=student_id, success=1)
            )

        return redirect(url_for("main.teachers_ui", success=1))

    if student_id:
        return redirect(url_for("main.student_details", student_id=student_id))

    return redirect(url_for("main.teachers_ui"))


@main.route("/course/<path:course_id>/take-attendance", methods=["POST"])
def take_attendance(course_id):
    """
    Save the whole class's attendance for one date in one
    submission. Re-marking the same student/date updates the
    existing record rather than duplicating it (see the unique
    constraint on Attendance).
    """
    attendance_date = parse_form_date(request.form, "date")

    if not attendance_date:
        return redirect(url_for("main.course_info", course_id=course_id))

    enrollments = StudentResult.query.filter_by(course_id=course_id).all()

    if not enrollments:
        abort(404)

    # Same reasoning as the check in course_info(): being
    # allowed to POST to this endpoint at all doesn't mean this
    # particular course is theirs to mark attendance for.
    if current_user.is_teacher():

        course_teacher_id = enrollments[0].teacher_id

        if course_teacher_id != current_user.teacher_id:
            abort(403)

    for enrollment in enrollments:
        status = request.form.get(f"status_{enrollment.id}")

        if status not in ("present", "absent", "late"):
            continue

        record = Attendance.query.filter_by(
            student_result_id=enrollment.id, date=attendance_date
        ).first()

        if record:
            record.status = status
        else:
            record = Attendance(
                student_result_id=enrollment.id,
                date=attendance_date,
                status=status
            )
            db.session.add(record)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.course_info",
                course_id=course_id,
                error=f"Could not save attendance, please try again: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.course_info",
            course_id=course_id,
            date=attendance_date.strftime("%Y-%m-%d")
        )
    )


def get_attendance_overview(target_date):
    """
    Present/absent/late/unmarked counts for EVERY course, for
    one given date - the numbers behind the Attendance overview
    page's course cards, so staff can see at a glance which
    courses still need attendance taken.
    """
    courses = get_course_summaries()

    enrollments_by_course = {}
    for enrollment in StudentResult.query.all():
        enrollments_by_course.setdefault(enrollment.course_id, []).append(enrollment.id)

    records_today = Attendance.query.filter_by(date=target_date).all()

    status_by_result_id = {
        record.student_result_id: record.status for record in records_today
    }

    for course in courses:
        result_ids = enrollments_by_course.get(course["course_id"], [])

        present = sum(
            1 for result_id in result_ids
            if status_by_result_id.get(result_id) == "present"
        )
        absent = sum(
            1 for result_id in result_ids
            if status_by_result_id.get(result_id) == "absent"
        )
        late = sum(
            1 for result_id in result_ids
            if status_by_result_id.get(result_id) == "late"
        )

        course["present"] = present
        course["absent"] = absent
        course["late"] = late
        course["unmarked"] = len(result_ids) - present - absent - late

    return courses


@main.route("/attendance")
def attendance_overview():
    """Admin-wide attendance dashboard for a chosen date (?date=YYYY-MM-DD, defaults to today) - every course, at a glance."""
    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    courses = get_attendance_overview(selected_date)

    return render_template(
        "attendance_overview.html",
        courses=courses,
        selected_date=selected_date
    )


# ============================================================
# ADD ROOM
# ============================================================

@main.route(
    "/rooms/add",
    methods=["GET", "POST"]
)
def add_room():
    """Create a new teaching room. A dedicated page, same shape as add_student.html, even though a room has far fewer fields."""

    if request.method == "POST":

        name = request.form.get("name", "").strip()

        if not name:
            return render_template(
                "add_room.html",
                error="Room name is required.",
                form_data=request.form
            )

        if len(name) > 50:
            return render_template(
                "add_room.html",
                error="Room name is too long (maximum 50 characters).",
                form_data=request.form
            )

        if Room.query.filter_by(name=name).first():
            return render_template(
                "add_room.html",
                error=f"A room named '{name}' already exists.",
                form_data=request.form
            )

        room = Room(
            name=name,
            notes=request.form.get("notes", "").strip()
        )

        db.session.add(room)

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return render_template(
                "add_room.html",
                error=f"Could not save this room: {str(e)}",
                form_data=request.form
            )

        return redirect(
            url_for("main.room_assign", success="1")
        )

    return render_template(
        "add_room.html",
        error=None,
        form_data={}
    )


# ============================================================
# ROOM ASSIGN
#
# Which room each course is using on a given day. course_id is
# free text (see RoomAssignment's own comment in models.py),
# so "assigning a room" just means creating a row that links a
# room_id + course_id + date together - it's not a real FK to
# a Course table, because there isn't one.
# ============================================================

@main.route("/room-assign")
def room_assign():
    """Room-scheduling page: pick a date, see every room's status for that day. Staff/admin can book or unassign rooms; teachers get a read-only view (see is_teacher_view below)."""

    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    rooms = Room.query.order_by(Room.name).all()

    assignments_for_date = {
        assignment.room_id: assignment
        for assignment in RoomAssignment.query.filter_by(
            date=selected_date
        ).all()
    }

    courses = get_course_summaries()

    courses_by_id = {
        course["course_id"]: course
        for course in courses
    }

    # Attach the selected date's assignment (if any) directly
    # onto each room object, same "not a real database column"
    # pattern used for enrollment.attendance_status_today
    # elsewhere - just convenient for the template to read.
    for room in rooms:

        assignment = assignments_for_date.get(room.id)

        room.assignment_for_date = assignment

        room.assigned_course = (
            courses_by_id.get(assignment.course_id)
            if assignment
            else None
        )

    return render_template(
        "room_assign.html",
        rooms=rooms,
        courses=courses,
        selected_date=selected_date,
        is_teacher_view=current_user.is_teacher()
    )


@main.route("/room-assign/data")
def room_assign_data():
    """
    JSON snapshot of every room's booking status for a given
    date - polled by room_assign.html every ~10 seconds so
    everyone viewing the page sees someone else's changes
    without refreshing. Same underlying data as room_assign()
    itself, just as JSON instead of a full page.
    """

    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    rooms = Room.query.order_by(Room.name).all()

    assignments_for_date = {
        assignment.room_id: assignment
        for assignment in RoomAssignment.query.filter_by(
            date=selected_date
        ).all()
    }

    courses_by_id = {
        course["course_id"]: course
        for course in get_course_summaries()
    }

    rooms_data = []

    for room in rooms:

        assignment = assignments_for_date.get(room.id)

        course = (
            courses_by_id.get(assignment.course_id)
            if assignment
            else None
        )

        rooms_data.append({
            "id": room.id,
            "information": room.notes or "",
            "assignment_id": (
                assignment.id if assignment else None
            ),
            "course_name": (
                course["course_name"] if course else None
            ),
            "course_id": (
                course["course_id"] if course else None
            ),
            "course_url": (
                url_for(
                    "main.course_info",
                    course_id=course["course_id"]
                )
                if course
                else None
            ),
            "unassign_url": (
                url_for(
                    "main.unassign_room",
                    assignment_id=assignment.id
                )
                if assignment
                else None
            ),
            "teacher_name": (
                course["teacher"].name
                if course and course["teacher"]
                else None
            )
        })

    return jsonify(rooms=rooms_data)


@main.route(
    "/rooms/<int:room_id>/update-information",
    methods=["POST"]
)
def update_room_information(room_id):
    """
    Save the free-text Information cell for one room, edited
    inline on the Room Assign table (not a separate form page -
    the person just types in the cell and it submits on blur).
    Not on TEACHER_ALLOWED_ENDPOINTS, so this stays staff/admin
    only even though teachers can view the page itself.
    """

    room = Room.query.get_or_404(room_id)

    room.notes = request.form.get("information", "").strip()

    selected_date = (
        parse_form_date(request.form, "date") or date.today()
    )

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.room_assign",
                date=selected_date.strftime("%Y-%m-%d"),
                error=f"Could not save that: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.room_assign",
            date=selected_date.strftime("%Y-%m-%d")
        )
    )


@main.route(
    "/room-assign/assign",
    methods=["POST"]
)
def assign_room():
    """Book a room for a course on a specific date. Blocks outright if that room is already booked for that date - no double-booking."""

    room_id = request.form.get("room_id", type=int)
    course_id = request.form.get("course_id", "").strip()

    assignment_date = parse_form_date(request.form, "date")

    if not room_id or not course_id or not assignment_date:
        return redirect(
            url_for(
                "main.room_assign",
                error="Please choose a room, a course, and a date."
            )
        )

    room = Room.query.get_or_404(room_id)

    # Checked explicitly here (not just left to the database's
    # unique constraint) so the person gets a clear, specific
    # message instead of a raw constraint-violation error.
    existing = RoomAssignment.query.filter_by(
        room_id=room_id,
        date=assignment_date
    ).first()

    if existing:
        return redirect(
            url_for(
                "main.room_assign",
                date=assignment_date.strftime("%Y-%m-%d"),
                error=(
                    f"{room.name} is already booked for "
                    f"{assignment_date.strftime('%d %b %Y')}."
                )
            )
        )

    assignment = RoomAssignment(
        room_id=room_id,
        course_id=course_id,
        date=assignment_date
    )

    db.session.add(assignment)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.room_assign",
                date=assignment_date.strftime("%Y-%m-%d"),
                error=f"Could not save this assignment: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.room_assign",
            date=assignment_date.strftime("%Y-%m-%d"),
            success="1"
        )
    )


@main.route(
    "/room-assign/<int:assignment_id>/unassign",
    methods=["POST"]
)
def unassign_room(assignment_id):
    """Free up a room that was booked in error, or because a class was cancelled/moved."""

    assignment = RoomAssignment.query.get_or_404(assignment_id)

    assignment_date = assignment.date

    db.session.delete(assignment)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.room_assign",
                date=assignment_date.strftime("%Y-%m-%d"),
                error=f"Could not remove this assignment: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.room_assign",
            date=assignment_date.strftime("%Y-%m-%d"),
            success="1"
        )
    )


# ============================================================
# SETTINGS
#
# Currently just student-type management - the only piece of
# reference data in the app with no other page to manage it
# from (unlike Teachers or Rooms, which each have their own
# page). Admin-only, unlike Add Teacher/Add Room which any
# logged-in staff can use.
# ============================================================

@main.route("/settings")
@admin_required
def settings():
    """Admin settings page - currently just the student-type list and the form to add new ones."""

    student_types = StudentType.query.order_by(StudentType.name).all()

    return render_template(
        "settings.html",
        student_types=student_types
    )


@main.route(
    "/student-types/add",
    methods=["POST"]
)
@admin_required
def add_student_type():
    """Create a new student type. Admin only."""

    name = request.form.get("name", "").strip()

    if not name:
        return redirect(
            url_for(
                "main.settings",
                error="Student type name is required."
            )
        )

    if len(name) > 50:
        return redirect(
            url_for(
                "main.settings",
                error="Student type name is too long (maximum 50 characters)."
            )
        )

    if StudentType.query.filter_by(name=name).first():
        return redirect(
            url_for(
                "main.settings",
                error=f"'{name}' already exists."
            )
        )

    student_type = StudentType(name=name)

    db.session.add(student_type)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.settings",
                error=f"Could not save this student type: {str(e)}"
            )
        )

    return redirect(
        url_for("main.settings", success="1")
    )