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
    RoomAssignment,
    EndOfDayReport,
    EndOfDayReportLine
)


main = Blueprint("main", __name__)


# ============================================================
# Start
# Before Request the fucntion check if the user is authentic 
# or not (for example checks username and password)
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
# Indentify the admin user before any functions and block anyone who is not admin
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
# TEACHER uSERS cANNOT aCCESS TO OTHER PAGES THAN THE LISTED ONES BELOW
# ============================================================

TEACHER_ALLOWED_ENDPOINTS = {
    "main.login",
    "main.logout",
    "main.teachers_ui",
    "main.course_info",
    "main.take_attendance",
    "main.room_assign",
}

#Checks if the Teacher users' access point 
@main.before_request
def restrict_teacher_access():

    if not current_user.is_authenticated:
        return

    if (
        current_user.is_teacher()
        and request.endpoint not in TEACHER_ALLOWED_ENDPOINTS
    ):
        abort(403)

#convert any value to a Decimal, 
# returning 0 instead of raising if it's missing or not a valid number.
def to_decimal(value):
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")

#Convert to Decimal and round to exactly 2 decimal places 
def to_money(value):
    return to_decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

#Currency Indentifier
def normalize_currency(value, default="MMK"):
    #Forces the currency to String and Turn the input into Upperletter with white spaces removed
    currency = (
        str(value or default)
        .strip()
        .upper()
    )
    #Indentifies the currency and set default
    if currency not in ("MMK", "USD"):
        return default
    return currency


#Read the input from the form as in Boolean value0
def parse_bool_flag(form, field_name, default="no"):
    value = form.get(field_name, default)
    if isinstance(value, bool):
        return value
    return (form.get(field_name, default).strip().lower() in ("yes", "true", "1", "on"))

#Set the date into year/month/day format
def parse_form_date(form, field_name):
    raw_value = form.get(field_name, "")
    if raw_value:
        raw_value = raw_value.strip()
    if not raw_value:
        return None
    try:return datetime.strptime(
            raw_value,
            "%Y-%m-%d"
        ).date()
    
    except ValueError:
        return None
    
#Checks if the student_id is alreay used when adding the student
def student_id_taken(student_id, exclude_student_id=None):
    query = Student.query.filter(
        Student.student_id == student_id
    )
    if exclude_student_id is not None:
        query = query.filter(
            Student.id != exclude_student_id
        )
    return query.first() is not None # returns True if the Id is used and False else

#Lccks the student record by id and follows Atomicity rules
def get_student_for_update(student_id):
    student = (
        Student.query
        .filter_by(id=student_id)
        .with_for_update()
        .first()
    )
    if student is None:
        abort(404)
    return student

# Discount Fucntion
def calculate_discount(
    total_amount,
    discount_type,
    discount,
    promotion_amount
):
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

#Exchange rate logic
def validate_currency_payment(
    payment_currency,
    course_currency,
    exchange_enabled,
    exchange_rate
):
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
    """All of one student's payments, oldest first, across every course - the ordering every balance calculation below depends on."""
    return (
        StudentPayment.query
        .filter_by(student_id=student_id)
        .order_by(StudentPayment.id.asc())
        .all()
    )


def get_course_payments(student_result_id):
    """All payments tagged to ONE course enrollment, oldest first - same ordering rule as get_student_payments, just scoped to one course instead of the whole student."""
    return (
        StudentPayment.query
        .filter_by(student_result_id=student_result_id)
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


def calculate_totals_for_payments(payments):
    """
    Shared by calculate_student_totals (a student's payments
    combined, across every course) and calculate_course_totals
    (just one course's tagged payments) - same discount/
    currency-conversion math either way, just given a
    different pre-fetched list. Returns (total_payment,
    total_received, total_pending).
    """
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


def calculate_student_totals(student_id):
    """
    A student's total course fee, total received, and total
    still pending - across EVERY course combined (unchanged
    behavior; this is what the Dashboard's payment_status still
    reflects). For just one course, see calculate_course_totals.
    """
    return calculate_totals_for_payments(
        get_student_payments(student_id)
    )


def calculate_course_totals(student_result_id):
    """Same as calculate_student_totals, but scoped to just ONE course enrollment's tagged payments."""
    return calculate_totals_for_payments(
        get_course_payments(student_result_id)
    )


def update_student_payment_status(student):
    """Recompute and set a student's payment_status ('unpaid'/'partial'/'paid') from their COMBINED totals across every course."""
    (total_payment, total_received, total_pending) = calculate_student_totals(student.id)

    if total_received <= 0:
        student.payment_status = "unpaid"
    elif total_pending > 0:
        student.payment_status = "partial"
    else:
        student.payment_status = "paid"


def rebuild_balances_for_payments(payments, total_after_discount=None):
    """
    Shared by rebuild_payment_balances (all of a student's
    payments) and rebuild_course_payment_balances (just one
    course's tagged payments) - recalculates current_receivable
    and pending_amount on every payment IN ORDER, since each
    one's balance depends on all the ones before it.
    """
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


def rebuild_payment_balances(student_id, total_after_discount=None):
    """Recalculate running balances for ALL of a student's payments combined (unchanged behavior). Also refreshes the student's overall payment_status."""
    payments = get_student_payments(student_id)

    rebuild_balances_for_payments(payments, total_after_discount)

    student = Student.query.get(student_id)
    if student:
        update_student_payment_status(student)


def rebuild_course_payment_balances(student_result_id, total_after_discount=None):
    """
    Same as rebuild_payment_balances, but scoped to just ONE
    course enrollment's tagged payments - used once a payment
    is tagged to a specific course, so its running balance is
    tracked against that course's own total, separately from
    any other course the same student is taking. Also refreshes
    the student's overall payment_status, which still reflects
    everything combined.
    """
    payments = get_course_payments(student_result_id)

    rebuild_balances_for_payments(payments, total_after_discount)

    if payments:
        student = payments[0].student
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


def render_student_form(student_types, error, form_data, edit_mode, student, teachers=None):
    """Shared render_template call for add_student.html - used by both add_student() and edit_student() so their error/validation responses stay in sync."""
    return render_template(
        "add_student.html",
        student_types=student_types,
        error=error,
        form_data=form_data,
        edit_mode=edit_mode,
        student=student,
        teachers=(teachers or [])
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

    template_context = dict(
        students=students,
        student_types=student_types,
        search=search,
        course_search=course_search,
        payment_status=payment_status,
        selected_type=selected_type,
        pagination=pagination
    )

    # The live search JS fetches with this header set so it can
    # swap in just the table + pagination fragment - no full
    # page reload, so the search box never loses focus and the
    # page never visibly flashes while someone is typing.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("_dashboard_results.html", **template_context)

    return render_template("dashboard.html", **template_context)


def create_course_with_optional_payment(student, form):
    """
    Shared by add_student() and add_course() - both let staff
    enroll a student in a course and optionally record its
    first payment in the same submission, with identical
    fields either way. Creates nothing at all if course_name
    or course_id is missing - a course is only ever created
    when someone deliberately provided both. Any payment
    fields (total_amount, amount_paid, etc.) are read the same
    way regardless of caller, and the payment - if one gets
    created - is tagged to this course directly.

    Returns (course_or_none, error_or_none). A returned course
    with no error means success (possibly with no payment, if
    none was filled in). A returned error always means nothing
    was committed - the caller should roll back and show it.
    """

    course_name = form.get("course_name", "").strip()
    course_id_value = form.get("course_id", "").strip()

    if not (course_name and course_id_value):
        return None, None

    course_start_date = parse_form_date(form, "start_date")
    course_end_date = parse_form_date(form, "end_date")
    course_result = form.get("result", "").strip()
    course_collected = (form.get("collected", "no") == "yes")
    course_teacher_id = form.get("teacher_id", type=int)

    course = StudentResult(
        student_id=student.id,
        course_name=course_name,
        course_id=course_id_value,
        start_date=course_start_date,
        end_date=course_end_date,
        result=course_result or None,
        published_date=(date.today() if course_result else None),
        collected=course_collected,
        teacher_id=course_teacher_id
    )

    db.session.add(course)

    try:
        db.session.flush()
    except SQLAlchemyError as e:
        db.session.rollback()
        return None, f"Could not save the course: {str(e)}"

    # ----------------------------------------------------
    # OPTIONAL INITIAL PAYMENT FOR THIS COURSE
    # ----------------------------------------------------

    total_amount = to_decimal(form.get("total_amount", "0"))

    amount_paid = max(
        to_money(form.get("amount_paid", "0")), Decimal("0")
    )

    discount_type = form.get("discount_type", "none").strip().lower()
    discount = to_decimal(form.get("discount", "0"))

    # Two different forms feed this helper with two different
    # shapes: Add Student has a separate promotion_amount field
    # from its percentage discount field, while Add Course uses
    # one combined "discount" field for both cases (labeled
    # "Discount (%) / Promotion Amount"). Prefer the dedicated
    # field when it's actually present in the submission -
    # otherwise the combined field IS the promotion amount.
    if "promotion_amount" in form:
        promotion_amount = to_decimal(form.get("promotion_amount", "0"))
    else:
        promotion_amount = discount

    course_currency = normalize_currency(student.currency)

    (
        payment_currency,
        exchange_enabled,
        exchange_rate,
        valid,
        currency_error
    ) = resolve_currency_settings(form, course_currency)

    if not valid:
        # The course itself is already flushed and fine - only
        # the optional payment's currency settings were invalid,
        # so only that part is reported as an error.
        return course, currency_error

    (discount_amount, total_after_discount) = calculate_discount(
        total_amount, discount_type, discount, promotion_amount
    )

    converted_amount_paid = min(
        convert_payment_to_course_currency(
            amount_paid, payment_currency, course_currency,
            exchange_enabled, exchange_rate
        ),
        total_after_discount
    )

    if total_amount > 0 or amount_paid > 0 or discount_amount > 0:

        pending_amount = max(
            total_after_discount - converted_amount_paid, Decimal("0")
        )

        create_payment_with_invoice_id(
            student_id=student.id,
            student_result_id=course.id,
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
            comment=form.get("payment_comment", "").strip(),
            account=form.get("account", "").strip()
        )

        update_student_payment_status(student)

    return course, None


@main.route("/add-student", methods=["GET", "POST"])
def add_student():
    """
    Create a new student, optionally with their first course
    (and its teacher) and first payment, all in the same
    submission. GET shows a blank form; POST validates
    (duplicate ID, field lengths, currency/discount settings),
    creates the student, creates the course only if both
    course_name and course_id were given, and creates a payment
    record only if an amount was actually entered.
    """
    student_types = StudentType.query.order_by(StudentType.name).all()
    teachers = Teacher.query.order_by(Teacher.name).all()

    if request.method == "POST":

        student_id = request.form.get("student_id", "").strip()

        if student_id_taken(student_id):
            return render_student_form(
                student_types,
                f"Student ID '{student_id}' already exists.",
                request.form,
                False,
                None,
                teachers
            )

        length_error = check_field_lengths(request.form)

        if length_error:
            return render_student_form(
                student_types,
                length_error,
                request.form,
                False,
                None,
                teachers
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
                None,
                teachers
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
                None,
                teachers
            )

        # ----------------------------------------------------
        # OPTIONAL FIRST COURSE (+ ITS PAYMENT, IF ANY)
        #
        # Handled by the shared helper - also used by the
        # dedicated Add Course page for existing students, so
        # this logic only lives in one place.
        # ----------------------------------------------------

        course, course_error = create_course_with_optional_payment(
            student, request.form
        )

        if course_error:
            db.session.rollback()
            return render_student_form(
                student_types,
                course_error,
                request.form,
                False,
                None,
                teachers
            )

        # A course was created (and its payment, if any, is
        # already handled by the helper above) - nothing left
        # to do here but commit.
        if course is not None:

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

        # ----------------------------------------------------
        # NO COURSE WAS GIVEN - a payment can still be recorded
        # here, same as before, just left untagged to any
        # specific course (the "General" bucket).
        # ----------------------------------------------------

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
                None,
                teachers
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

            create_payment_with_invoice_id(
                student_id=student.id,
                student_result_id=None,
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
        None,
        teachers
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
    """
    Record an additional payment toward a student's course fee,
    converting to course currency and capping at what's still
    owed. If the student has more than one course, the payment
    should be tagged to a specific one (student_result_id) so
    its balance is tracked against THAT course's own total
    rather than the student's combined total across every
    course - an untagged payment falls back to the old,
    combined-total behavior.
    """
    student = get_student_for_update(student_id)

    payment_comment = request.form.get("payment_comment", "").strip()
    amount_paid = max(to_money(request.form.get("amount_paid", "0")), Decimal("0"))

    student_result_id = request.form.get("student_result_id", type=int)

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

    # --------------------------------------------------------
    # SCOPE: one specific course, or the student overall
    # --------------------------------------------------------

    if student_result_id:

        course = StudentResult.query.filter_by(
            id=student_result_id,
            student_id=student.id
        ).first()

        if course is None:
            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error="That course doesn't belong to this student."
                )
            )

        payments = get_course_payments(student_result_id)

    else:

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
        student_result_id=student_result_id,
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

    if student_result_id:
        rebuild_course_payment_balances(student_result_id, total_after_discount)
    else:
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


@main.route(
    "/student/<int:student_id>/add-course",
    methods=["GET", "POST"]
)
def add_course(student_id):
    """
    Dedicated page for enrolling an EXISTING student in an
    additional course - same fields and shape as Add Student's
    Course Information + Payment Information sections (course
    details, teacher, and an optional initial payment tagged
    to it), just reached from that student's own profile
    instead of at signup. Uses the same shared helper as
    add_student() so the two stay in sync.
    """
    student = Student.query.get_or_404(student_id)
    teachers = Teacher.query.order_by(Teacher.name).all()

    if request.method == "POST":

        course, error = create_course_with_optional_payment(
            student, request.form
        )

        if error:
            db.session.rollback()
            return render_template(
                "add_course.html",
                student=student,
                teachers=teachers,
                error=error,
                form_data=request.form
            )

        if course is None:
            return render_template(
                "add_course.html",
                student=student,
                teachers=teachers,
                error="Course Name and Course ID are both required.",
                form_data=request.form
            )

        try:
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            return render_template(
                "add_course.html",
                student=student,
                teachers=teachers,
                error=f"Could not save the course: {str(e)}",
                form_data=request.form
            )

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                success=1
            )
        )

    return render_template(
        "add_course.html",
        student=student,
        teachers=teachers,
        error=None,
        form_data={}
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

    # --------------------------------------------------------
    # PAYMENT RECORD SECTIONS - one per course
    #
    # Every course only ever shows payments explicitly tagged
    # to IT - no special-casing for "this student only has one
    # course, so show everything." That fallback used to exist,
    # but it meant an older untagged payment (from before this
    # course even existed) could get silently absorbed into a
    # brand new course just because it happened to be the
    # student's only one - including one deliberately added
    # with no payment at all. Any payment tagged to no course
    # always collects into the separate General section below,
    # regardless of how many courses the student has.
    # --------------------------------------------------------

    course_payment_summaries = []

    for result in results:

        course_payments = get_course_payments(result.id)

        # Courses with no payment at all (e.g. added via the
        # "No" option on Add Course) don't get a Payment Record
        # Section - nothing to show yet, and an empty 0.00
        # template was more clutter than useful.
        if not course_payments:
            continue

        (
            course_total,
            course_received,
            course_pending
        ) = calculate_totals_for_payments(course_payments)

        initial_course_payment = course_payments[0]

        original_total_cost = to_decimal(
            initial_course_payment.total_amount
        )

        discount_type = (
            initial_course_payment.discount_type or "none"
        )

        if discount_type == "percentage":
            discount_display = f"{to_decimal(initial_course_payment.discount)}%"
        elif discount_type == "promotion":
            discount_display = (
                f"{to_decimal(initial_course_payment.promotion_amount):,.2f} "
                f"{student.currency or 'MMK'}"
            )
        else:
            discount_display = "None"

        course_payment_summaries.append({
            "result": result,
            "total_payment": course_total,
            "total_received": course_received,
            "total_pending": course_pending,
            "original_total_cost": original_total_cost,
            "discount_type": discount_type,
            "discount_display": discount_display,
            "discount_amount": to_decimal(initial_course_payment.discount_amount),
            "payment_rows": build_payment_rows(course_payments)
        })

    # Payments tagged to no course at all - always checked for,
    # regardless of how many courses the student has (see the
    # comment above for why this is no longer conditional on
    # course count).
    general_payment_summary = None

    general_payments = [
        payment for payment in payments
        if payment.student_result_id is None
    ]

    if general_payments:

        (
            general_total,
            general_received,
            general_pending
        ) = calculate_totals_for_payments(general_payments)

        initial_general_payment = general_payments[0]

        general_discount_type = (
            initial_general_payment.discount_type or "none"
        )

        if general_discount_type == "percentage":
            general_discount_display = f"{to_decimal(initial_general_payment.discount)}%"
        elif general_discount_type == "promotion":
            general_discount_display = (
                f"{to_decimal(initial_general_payment.promotion_amount):,.2f} "
                f"{student.currency or 'MMK'}"
            )
        else:
            general_discount_display = "None"

        general_payment_summary = {
            "total_payment": general_total,
            "total_received": general_received,
            "total_pending": general_pending,
            "original_total_cost": to_decimal(
                initial_general_payment.total_amount
            ),
            "discount_type": general_discount_type,
            "discount_display": general_discount_display,
            "discount_amount": to_decimal(initial_general_payment.discount_amount),
            "payment_rows": build_payment_rows(general_payments)
        }

    # Grand totals across every section (courses + General, if
    # shown) - what the new top-level Total Summary displays.
    grand_total_receivable = sum(
        (summary["total_payment"] for summary in course_payment_summaries),
        Decimal("0")
    )

    grand_total_received = sum(
        (summary["total_received"] for summary in course_payment_summaries),
        Decimal("0")
    )

    grand_total_pending = sum(
        (summary["total_pending"] for summary in course_payment_summaries),
        Decimal("0")
    )

    if general_payment_summary:
        grand_total_receivable += general_payment_summary["total_payment"]
        grand_total_received += general_payment_summary["total_received"]
        grand_total_pending += general_payment_summary["total_pending"]

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
        course_payment_summaries=course_payment_summaries,
        general_payment_summary=general_payment_summary,
        grand_total_receivable=grand_total_receivable,
        grand_total_received=grand_total_received,
        grand_total_pending=grand_total_pending,
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
    date, amount, discount, comment, account, and (new) which
    course it's tagged to. Rebuilds balances for BOTH the old
    and new course when reassigned, since moving a payment
    out of one bucket and into another changes both.
    """
    payment = StudentPayment.query.get_or_404(payment_id)
    student = get_student_for_update(payment.student_id)
    course_currency = normalize_currency(student.currency)

    # Captured before any changes below, so both the bucket
    # this payment is LEAVING and the one it's ARRIVING at can
    # be correctly rebuilt afterward.
    previous_student_result_id = payment.student_result_id

    if "student_result_id" in request.form:

        new_student_result_id = request.form.get(
            "student_result_id", type=int
        )

        if new_student_result_id:

            new_course = StudentResult.query.filter_by(
                id=new_student_result_id,
                student_id=student.id
            ).first()

            if new_course is None:
                return redirect(
                    url_for(
                        "main.student_details",
                        student_id=student.id,
                        error="That course doesn't belong to this student."
                    )
                )

        payment.student_result_id = new_student_result_id

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

    if payment.student_result_id:
        rebuild_course_payment_balances(payment.student_result_id, total_after_discount)
    else:
        rebuild_payment_balances(student.id, total_after_discount)

    # If this payment moved to a different bucket, the one it
    # LEFT also needs rebuilding - its own total is re-derived
    # from whatever remains there, not the total_after_discount
    # above (which describes the payment's NEW bucket, not the
    # old one it's no longer part of).
    if previous_student_result_id != payment.student_result_id:

        if previous_student_result_id:
            rebuild_course_payment_balances(previous_student_result_id)
        else:
            rebuild_payment_balances(student.id)

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


def compute_end_of_day_totals(report_date):
    """
    Live computation of one day's account_groups/account_totals/
    grand_totals from StudentPayment - shared by the End of Day
    page (viewing an un-closed day) and close_end_of_day() (the
    snapshot taken when closing one), so both always agree on
    the numbers at the moment they're computed.
    """

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

    return (account_groups, account_totals, grand_totals)


@main.route("/end-of-day")
def end_of_day():
    """
    Cashier's report for one day. The itemized transaction
    list always shows live, current data - closing a day never
    hides individual payments. Only the TOTALS differ: once a
    day is closed, the totals shown are the frozen snapshot
    (what was true AT CLOSING TIME) rather than recomputed
    live, so a closed report's summary numbers don't silently
    shift if a payment dated that day gets edited afterward.
    """

    report_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    (
        account_groups,
        live_account_totals,
        live_grand_totals
    ) = compute_end_of_day_totals(report_date)

    closed_report = EndOfDayReport.query.filter_by(
        date=report_date
    ).first()

    if closed_report:

        account_totals = defaultdict(dict)

        for line in closed_report.lines:
            account_totals[line.account][line.currency] = line.amount

        account_totals = dict(account_totals)

        grand_totals = defaultdict(lambda: Decimal("0"))

        for line in closed_report.lines:
            grand_totals[line.currency] += line.amount

        grand_totals = dict(grand_totals)

    else:

        account_totals = live_account_totals
        grand_totals = live_grand_totals

    return render_template(
        "end_of_day.html",
        is_closed=(closed_report is not None),
        closed_report=closed_report,
        account_groups=account_groups,
        account_totals=account_totals,
        grand_totals=grand_totals,
        report_date=report_date
    )


@main.route(
    "/end-of-day/close",
    methods=["POST"]
)
def close_end_of_day():
    """
    Save a permanent snapshot of one day's totals. Closing a
    day that's already closed overwrites the previous snapshot
    (the confirm() on the button warns about this) rather than
    keeping both - there's only ever one closing per date.
    """

    close_date = (
        parse_form_date(request.form, "date") or date.today()
    )

    (
        account_groups,
        account_totals,
        grand_totals
    ) = compute_end_of_day_totals(close_date)

    if not account_groups:
        return redirect(
            url_for(
                "main.end_of_day",
                date=close_date.strftime("%Y-%m-%d"),
                error="No payments recorded for this day - nothing to close."
            )
        )

    existing_report = EndOfDayReport.query.filter_by(
        date=close_date
    ).first()

    if existing_report:
        db.session.delete(existing_report)
        db.session.flush()

    report = EndOfDayReport(
        date=close_date,
        closed_by_id=current_user.id
    )

    db.session.add(report)
    db.session.flush()

    for account, totals_by_currency in account_totals.items():

        for currency, amount in totals_by_currency.items():

            db.session.add(
                EndOfDayReportLine(
                    report_id=report.id,
                    account=account,
                    currency=currency,
                    amount=amount
                )
            )

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.end_of_day",
                date=close_date.strftime("%Y-%m-%d"),
                error=f"Could not close this day: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.end_of_day",
            date=close_date.strftime("%Y-%m-%d"),
            success="1"
        )
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


@main.route(
    "/course/<path:course_id>/update-dates",
    methods=["POST"]
)
@admin_required
def update_course_dates(course_id):
    """
    Set a course's Start Date and End Date for EVERY student
    enrolled in it, not just one - a course has one start and
    end date, not a different one per student, same reasoning
    as assign_teacher() just above. Admin-only: this silently
    overwrites whatever individual dates each enrollment had
    before, so it's a bulk action worth restricting.
    """
    start_date = parse_form_date(request.form, "start_date")
    end_date = parse_form_date(request.form, "end_date")

    if start_date and end_date and end_date < start_date:
        return redirect(
            url_for(
                "main.course_info",
                course_id=course_id,
                error="End Date can't be before Start Date."
            )
        )

    StudentResult.query.filter_by(course_id=course_id).update(
        {
            "start_date": start_date,
            "end_date": end_date
        }
    )

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.course_info",
                course_id=course_id,
                error=f"Could not update the course dates: {str(e)}"
            )
        )

    return redirect(
        url_for("main.course_info", course_id=course_id, success=1)
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

# Save the whole class's attendance for one date on submit and resubmit updates the current one
@main.route("/course/<path:course_id>/take-attendance", methods=["POST"])
def take_attendance(course_id):
    
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

#Summary of the present, late and absence on the course card
def get_attendance_overview(target_date):
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

#Admin-wide attendance dashboard for a chosen date .
@main.route("/attendance")
def attendance_overview():
    
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
# ADD ROOM or Create Room
# ============================================================

@main.route(
    "/rooms/add",
    methods=["GET", "POST"]
)
def add_room():

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
# ROOM ASSIGN Fliter By the User Roles
# ============================================================

@main.route("/room-assign")
def room_assign():
    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    is_teacher_view = current_user.is_teacher()

    courses = get_course_summaries()

    courses_by_id = {
        course["course_id"]: course
        for course in courses
    }

    # --------------------------------------------------------
    # TEACHER VIEW - a short personal list, not the full board
    # --------------------------------------------------------

    if is_teacher_view:

        my_course_ids = {
            course["course_id"]
            for course in courses
            if course["teacher"]
            and course["teacher"].id == current_user.teacher_id
        }

        my_assignments = (
            RoomAssignment.query
            .filter(
                RoomAssignment.date == selected_date,
                RoomAssignment.course_id.in_(my_course_ids)
            )
            .order_by(RoomAssignment.id)
            .all()
            if my_course_ids
            else []
        )

        my_bookings = [
            {
                "period": assignment.period,
                "room": assignment.room,
                "course": courses_by_id.get(assignment.course_id)
            }
            for assignment in my_assignments
        ]

        return render_template(
            "room_assign.html",
            is_teacher_view=True,
            my_bookings=my_bookings,
            selected_date=selected_date
        )

    # --------------------------------------------------------
    # STAFF/ADMIN VIEW - the whiteboard itself
    # --------------------------------------------------------

    rooms = Room.query.order_by(Room.name).all()

    assignments = (
        RoomAssignment.query
        .filter_by(date=selected_date)
        .join(Room)
        .order_by(Room.name, RoomAssignment.id)
        .all()
    )

    bookings = [
        {
            "id": assignment.id,
            "room": assignment.room,
            "period": assignment.period,
            "course": courses_by_id.get(assignment.course_id)
        }
        for assignment in assignments
    ]

    return render_template(
        "room_assign.html",
        rooms=rooms,
        courses=courses,
        bookings=bookings,
        selected_date=selected_date,
        is_teacher_view=False
    )

#THis Function allows the user to see live updates on the booking system
@main.route("/room-assign/data")
def room_assign_data():

    selected_date = (
        parse_form_date(request.args, "date") or date.today()
    )

    courses_by_id = {
        course["course_id"]: course
        for course in get_course_summaries()
    }

    assignments = (
        RoomAssignment.query
        .filter_by(date=selected_date)
        .join(Room)
        .order_by(Room.name, RoomAssignment.id)
        .all()
    )

    bookings_data = []

    for assignment in assignments:

        course = courses_by_id.get(assignment.course_id)

        bookings_data.append({
            "id": assignment.id,
            "room_name": assignment.room.name,
            "period": assignment.period,
            "course_name": (
                course["course_name"] if course else None
            ),
            "course_id": assignment.course_id,
            "course_url": (
                url_for(
                    "main.course_info",
                    course_id=assignment.course_id
                )
                if course
                else None
            ),
            "teacher_name": (
                course["teacher"].name
                if course and course["teacher"]
                else None
            )
        })

    return jsonify(bookings=bookings_data)

#Add Room
@main.route(
    "/room-assign/add",
    methods=["POST"]
)
def add_room_booking():
    room_id = request.form.get("room_id", type=int)
    period = request.form.get("period", "").strip()
    course_id = request.form.get("course_id", "").strip()

    assignment_date = parse_form_date(request.form, "date")

    if not room_id or not period or not course_id or not assignment_date:
        return redirect(
            url_for(
                "main.room_assign",
                error="Please fill in room, time period, and course."
            )
        )

    if len(period) > 100:
        return redirect(
            url_for(
                "main.room_assign",
                date=assignment_date.strftime("%Y-%m-%d"),
                error="Time period is too long (maximum 100 characters)."
            )
        )

    booking = RoomAssignment(
        room_id=room_id,
        period=period,
        course_id=course_id,
        date=assignment_date
    )

    db.session.add(booking)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.room_assign",
                date=assignment_date.strftime("%Y-%m-%d"),
                error=f"Could not save that: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.room_assign",
            date=assignment_date.strftime("%Y-%m-%d"),
            success="1"
        )
    )

#UPDATE Booking# 
@main.route(
    "/room-assign/<int:booking_id>/edit",
    methods=["POST"]
)
def edit_room_booking(booking_id):

    booking = RoomAssignment.query.get_or_404(booking_id)

    period = request.form.get("period", "").strip()
    course_id = request.form.get("course_id", "").strip()

    selected_date = booking.date

    if not period or not course_id:
        return redirect(
            url_for(
                "main.room_assign",
                date=selected_date.strftime("%Y-%m-%d"),
                error="Time period and course can't be empty - delete the entry instead if it should be removed."
            )
        )

    if len(period) > 100:
        return redirect(
            url_for(
                "main.room_assign",
                date=selected_date.strftime("%Y-%m-%d"),
                error="Time period is too long (maximum 100 characters)."
            )
        )

    booking.period = period
    booking.course_id = course_id

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
            date=selected_date.strftime("%Y-%m-%d"),
            success="1"
        )
    )

#Erase Booking Room.
@main.route(
    "/room-assign/<int:booking_id>/delete",
    methods=["POST"]
)
def delete_room_booking(booking_id):
    booking = RoomAssignment.query.get_or_404(booking_id)

    selected_date = booking.date

    db.session.delete(booking)

    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        return redirect(
            url_for(
                "main.room_assign",
                date=selected_date.strftime("%Y-%m-%d"),
                error=f"Could not remove that: {str(e)}"
            )
        )

    return redirect(
        url_for(
            "main.room_assign",
            date=selected_date.strftime("%Y-%m-%d"),
            success="1"
        )
    )


# ============================================================
# SETTINGS
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

#Create Admin
@main.route(
    "/student-types/add",
    methods=["POST"]
)
@admin_required
def add_student_type():
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