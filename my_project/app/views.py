from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    abort
)

from collections import defaultdict

from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP
import uuid

from app import db

from app.models import (
    Student,
    StudentType,
    StudentRemark,
    StudentPayment,
    StudentResult
)


main = Blueprint("main", __name__)


# ============================================================
# GENERIC FORM HELPERS
#
# These are small, generic parsing helpers used everywhere a
# Flask form is read. Extracted so the same parsing rules
# (and the same bugs/edge cases) aren't repeated in every view.
# ============================================================

def to_decimal(value):
    """
    Safely convert a value to Decimal.
    """

    if value is None:
        return Decimal("0")

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def to_money(value):
    """
    Convert a value to Decimal and round it to exactly 2
    decimal places - i.e. a normal currency amount.

    Deliberately separate from to_decimal(): not everything
    that goes through to_decimal is a 2-decimal money value.
    exchange_rate is stored to 4 decimal places, and discount
    percentages have their own precision - rounding those to
    cents would be wrong. This is only for amounts that
    represent actual money paid.
    """

    return to_decimal(value).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )


def normalize_currency(value, default="MMK"):
    """
    Make sure currency is either MMK or USD.
    """

    currency = (
        str(value or default)
        .strip()
        .upper()
    )

    if currency not in ("MMK", "USD"):
        return default

    return currency


def parse_bool_flag(form, field_name, default="no"):
    """
    Parse a Yes/No style form field into a bool.

    Accepts: "yes", "true", "1", "on" (case-insensitive).
    """

    return (
        form.get(field_name, default)
        .strip()
        .lower()
        in ("yes", "true", "1", "on")
    )


def parse_form_date(form, field_name):
    """
    Parse a "YYYY-MM-DD" form field into a date, or None if the
    field is empty. Raises ValueError if the value is present
    but not a valid date, so callers can decide how to handle it.
    """

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
    """
    Check whether a student_id is already used by another student.
    """

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
    Load a student and lock its row for the rest of this
    transaction (SELECT ... FOR UPDATE).

    Any other request that also tries to lock the SAME
    student's row through this helper will block until this
    transaction commits or rolls back, instead of both reading
    the same stale payment balances and racing to write them
    back (see rebuild_payment_balances / calculate_student_totals).

    Requests touching a DIFFERENT student are never affected -
    Postgres row locks only ever block on the same row.
    """

    student = (
        Student.query
        .filter_by(
            id=student_id
        )
        .with_for_update()
        .first()
    )

    if student is None:
        abort(404)

    return student


# ============================================================
# DISCOUNT CALCULATION
# ============================================================

def calculate_discount(
    total_amount,
    discount_type,
    discount,
    promotion_amount
):
    """
    Calculate discount BEFORE currency conversion.

    percentage:
        total * percentage / 100

    promotion:
        fixed cash amount
    """

    total_amount = max(
        to_decimal(total_amount),
        Decimal("0")
    )

    discount_type = (
        discount_type or "percentage"
    ).lower().strip()

    discount = max(
        to_decimal(discount),
        Decimal("0")
    )

    promotion_amount = max(
        to_decimal(promotion_amount),
        Decimal("0")
    )

    if discount_type == "promotion":

        discount_amount = min(
            promotion_amount,
            total_amount
        )

    elif discount_type == "percentage":

        discount = min(
            discount,
            Decimal("100")
        )

        discount_amount = (
            total_amount
            * discount
            / Decimal("100")
        )

    else:

        discount_amount = Decimal("0")

    total_after_discount = max(
        total_amount - discount_amount,
        Decimal("0")
    )

    return (
        discount_amount,
        total_after_discount
    )


# ============================================================
# CURRENCY PAYMENT VALIDATION
# ============================================================

def validate_currency_payment(
    payment_currency,
    course_currency,
    exchange_enabled,
    exchange_rate
):
    """
    Validate payment currency and exchange settings.

    Rules:

    1. Same currency:
       MMK -> MMK
       USD -> USD

       No exchange rate is required.

    2. Different currency:
       MMK -> USD
       USD -> MMK

       Exchange must be enabled and a valid exchange
       rate must be supplied.

    Exchange rate means:

        1 USD = X MMK

    Therefore:

        MMK -> USD = MMK / rate
        USD -> MMK = USD * rate
    """

    payment_currency = normalize_currency(
        payment_currency
    )

    course_currency = normalize_currency(
        course_currency
    )

    # --------------------------------------------------------
    # SAME CURRENCY
    # --------------------------------------------------------

    if payment_currency == course_currency:
        return True, None

    # --------------------------------------------------------
    # DIFFERENT CURRENCY
    # --------------------------------------------------------

    if payment_currency not in ("MMK", "USD"):
        return False, "Invalid payment currency."

    if course_currency not in ("MMK", "USD"):
        return False, "Invalid course currency."

    # Exchange must be enabled when currencies differ.
    if not exchange_enabled:
        return (
            False,
            "Exchange rate is required when payment "
            "currency and course currency are different."
        )

    # Exchange rate is required.
    if exchange_rate is None:
        return (
            False,
            "Please enter an exchange rate."
        )

    exchange_rate = to_decimal(exchange_rate)

    if exchange_rate <= 0:
        return (
            False,
            "Exchange rate must be greater than 0."
        )

    return True, None


# ============================================================
# RESOLVE PAYMENT-CURRENCY FORM FIELDS
#
# add_student, edit_student, add_payment and edit_payment all
# need to: read payment_currency / exchange_enabled /
# exchange_rate from the form, force-disable exchange when the
# currencies match, and validate the result. This used to be
# copy-pasted in all four views; now it lives in one place.
# ============================================================

def resolve_currency_settings(
    form,
    course_currency,
    default_payment_currency=None
):
    """
    Returns:
        (payment_currency, exchange_enabled, exchange_rate, valid, error)
    """

    course_currency = normalize_currency(
        course_currency
    )

    payment_currency = normalize_currency(
        form.get(
            "payment_currency",
            default_payment_currency or course_currency
        )
    )

    exchange_enabled = parse_bool_flag(
        form,
        "exchange_enabled"
    )

    exchange_rate_raw = form.get(
        "exchange_rate",
        ""
    ).strip()

    exchange_rate = (
        to_decimal(exchange_rate_raw)
        if exchange_rate_raw
        else None
    )

    # Same currency requires no conversion.
    if payment_currency == course_currency:

        exchange_enabled = False

        exchange_rate = None

    # --------------------------------------------------------
    # AUTO-ENABLE EXCHANGE WHEN A VALID RATE IS PROVIDED
    #
    # Not every form that can submit two different currencies
    # has an explicit "exchange_enabled" control (add_student.html
    # / edit_student.html have currency + payment_currency + an
    # exchange_rate input, but no separate toggle). If the
    # currencies genuinely differ and a valid, positive rate was
    # entered, that alone is enough to treat the exchange as
    # enabled - a form's explicit flag (if it sends one) still
    # works the same as before.
    # --------------------------------------------------------

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


# ============================================================
# CONVERT PAYMENT TO COURSE CURRENCY
# ============================================================

def convert_payment_to_course_currency(
    amount_paid,
    payment_currency,
    course_currency,
    exchange_enabled=False,
    exchange_rate=None
):
    """
    Convert the actual customer payment into the course currency.

    Exchange rate meaning:

        1 USD = X MMK

    Examples:

        Course = USD
        Payment = MMK
        Payment = 1,000,000 MMK
        Rate = 4,000

        1,000,000 / 4,000
        = 250 USD


        Course = MMK
        Payment = USD
        Payment = 250 USD
        Rate = 4,000

        250 * 4,000
        = 1,000,000 MMK

    Same currency requires NO conversion.
    """

    amount_paid = max(
        to_decimal(amount_paid),
        Decimal("0")
    )

    payment_currency = normalize_currency(
        payment_currency
    )

    course_currency = normalize_currency(
        course_currency
    )

    # --------------------------------------------------------
    # SAME CURRENCY
    # --------------------------------------------------------

    if payment_currency == course_currency:
        return to_money(amount_paid)

    # --------------------------------------------------------
    # DIFFERENT CURRENCY
    # --------------------------------------------------------

    if not exchange_enabled:
        return Decimal("0")

    exchange_rate = to_decimal(
        exchange_rate
    )

    if exchange_rate <= 0:
        return Decimal("0")

    # --------------------------------------------------------
    # MMK -> USD
    #
    # 1 USD = X MMK
    #
    # Example:
    # 400,000 MMK / 4,000 = 100 USD
    # --------------------------------------------------------

    if (
        payment_currency == "MMK"
        and course_currency == "USD"
    ):
        return to_money(
            amount_paid / exchange_rate
        )

    # --------------------------------------------------------
    # USD -> MMK
    #
    # Example:
    # 100 USD * 4,000 = 400,000 MMK
    # --------------------------------------------------------

    if (
        payment_currency == "USD"
        and course_currency == "MMK"
    ):
        return to_money(
            amount_paid * exchange_rate
        )

    return Decimal("0")


# ============================================================
# PAYMENT CURRENCY CONVERSION FOR EXISTING RECORDS
# ============================================================

def get_payment_amount_in_course_currency(
    payment,
    amount_received,
    course_currency,
    payment_currency
):
    """
    Convert an existing StudentPayment record into the
    course currency.

    IMPORTANT:

    amount_paid / amount_received terminology:

        amount_paid
            = actual amount physically paid by customer

        amount_received
            = normalized amount stored in COURSE currency

    For old records where amount_paid may not exist,
    amount_received is used as a fallback.
    """

    amount_received = to_decimal(
        amount_received
    )

    course_currency = normalize_currency(
        course_currency
    )

    payment_currency = normalize_currency(
        payment_currency,
        course_currency
    )

    # --------------------------------------------------------
    # SAME CURRENCY
    # --------------------------------------------------------

    if payment_currency == course_currency:
        return amount_received

    # --------------------------------------------------------
    # DIFFERENT CURRENCY
    # --------------------------------------------------------

    exchange_enabled = bool(
        getattr(
            payment,
            "exchange_enabled",
            False
        )
    )

    exchange_rate = to_decimal(
        getattr(
            payment,
            "exchange_rate",
            None
        )
    )

    if not exchange_enabled:
        return Decimal("0")

    if exchange_rate <= 0:
        return Decimal("0")

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Use amount_paid because it is the actual amount
    # paid by the customer.
    #
    # If an old database record does not have amount_paid,
    # use amount_received as fallback.
    # --------------------------------------------------------

    actual_amount = getattr(
        payment,
        "amount_paid",
        None
    )

    if actual_amount is None:
        actual_amount = amount_received

    actual_amount = to_decimal(
        actual_amount
    )

    return convert_payment_to_course_currency(
        actual_amount,
        payment_currency,
        course_currency,
        exchange_enabled,
        exchange_rate
    )


# ============================================================
# INVOICE IDs
#
# The old approach queried for the highest existing invoice
# number and added 1 in Python. That read-then-write gap is a
# race condition: two payments submitted at nearly the same
# instant can both read the same "last number" and compute the
# same next one, and the second insert then fails on the
# invoice_id UNIQUE constraint.
#
# Instead, the invoice number is derived from the payment row's
# own database-assigned primary key. Primary key assignment is
# atomic - the database itself serializes it - so two payments
# created at the same instant can never end up with the same
# id, and therefore never end up with the same invoice number.
# ============================================================

def format_invoice_id(payment_id):

    return f"INV-{payment_id:06d}"


def create_payment_with_invoice_id(**payment_fields):
    """
    Create and flush a StudentPayment, then set its invoice_id
    from the id the database just assigned it.

    invoice_id is NOT NULL, so a throwaway placeholder is used
    for the brief moment before the real id exists. It only
    needs to be unique for that instant - and to fit in the
    column's 30-character limit, unlike the previous "PENDING-"
    + full UUID (40 chars) which overflowed it.
    """

    payment_fields["invoice_id"] = (
        f"TMP-{uuid.uuid4().hex[:20]}"
    )

    payment = StudentPayment(
        **payment_fields
    )

    db.session.add(payment)

    db.session.flush()

    payment.invoice_id = format_invoice_id(
        payment.id
    )

    return payment


# ============================================================
# STUDENT PAYMENTS QUERY
#
# calculate_student_totals, rebuild_payment_balances,
# build_payment_rows, add_payment, edit_student and
# student_details all needed "all payments for this student,
# oldest first". Centralized so the ordering rule lives once.
# ============================================================

def get_student_payments(student_id):

    return (
        StudentPayment.query
        .filter_by(
            student_id=student_id
        )
        .order_by(
            StudentPayment.id.asc()
        )
        .all()
    )


def get_course_total_after_discount(initial_payment):
    """
    The "official" course total for a student, derived from
    their first (original) payment record. Falls back to the
    raw total_amount if no discount total was ever stored.
    """

    if initial_payment is None:
        return Decimal("0")

    total_after_discount = to_decimal(
        initial_payment.total_after_discount
    )

    if total_after_discount <= 0:

        total_after_discount = to_decimal(
            initial_payment.total_amount
        )

    return total_after_discount


# ============================================================
# CALCULATE STUDENT TOTALS
# ============================================================

def calculate_student_totals(student_id):
    """
    Calculate payment totals in the student's COURSE CURRENCY.

    The customer may pay in a different currency.

    Example:
        Course currency = USD
        Customer pays = 350,000 MMK
        Exchange rate = 3,500 MMK per USD

        Customer payment = 350,000 MMK
        Course payment   = 100 USD

    Therefore:
        total_received = 100 USD
    """

    payments = get_student_payments(
        student_id
    )

    if not payments:
        return (
            Decimal("0"),
            Decimal("0"),
            Decimal("0")
        )

    # --------------------------------------------------------
    # COURSE CURRENCY
    # --------------------------------------------------------

    student = payments[0].student

    course_currency = normalize_currency(
        student.currency
        if student
        else None
    )

    # --------------------------------------------------------
    # TOTAL COURSE FEE AFTER DISCOUNT
    # --------------------------------------------------------

    total_payment = get_course_total_after_discount(
        payments[0]
    )

    # --------------------------------------------------------
    # TOTAL RECEIVED
    #
    # IMPORTANT:
    # Convert every payment into COURSE CURRENCY.
    # --------------------------------------------------------

    total_received = Decimal("0")

    for payment in payments:

        payment_currency = normalize_currency(
            getattr(
                payment,
                "payment_currency",
                None
            ) or course_currency,
            course_currency
        )

        amount_received = to_decimal(
            getattr(
                payment,
                "amount_received",
                0
            )
        )

        amount_paid_course_currency = (
            get_payment_amount_in_course_currency(
                payment,
                amount_received,
                course_currency,
                payment_currency
            )
        )

        total_received += (
            amount_paid_course_currency
        )

    # --------------------------------------------------------
    # TOTAL PENDING
    # --------------------------------------------------------

    total_pending = max(
        total_payment - total_received,
        Decimal("0")
    )

    return (
        total_payment,
        total_received,
        total_pending
    )


# ============================================================
# UPDATE STUDENT PAYMENT STATUS
# ============================================================

def update_student_payment_status(student):

    (
        total_payment,
        total_received,
        total_pending
    ) = calculate_student_totals(
        student.id
    )

    if total_received <= 0:

        student.payment_status = "unpaid"

    elif total_pending > 0:

        student.payment_status = "partial"

    else:

        student.payment_status = "paid"


# ============================================================
# REBUILD PAYMENT BALANCES
# ============================================================

def rebuild_payment_balances(
    student_id,
    total_after_discount=None
):
    """
    Recalculate current receivable and pending amount
    for every payment.

    All calculations use amount_received, which is the
    converted value in the course currency.
    """

    payments = get_student_payments(
        student_id
    )

    if not payments:

        return

    if total_after_discount is None:

        total_after_discount = get_course_total_after_discount(
            payments[0]
        )

    total_after_discount = max(
        to_decimal(total_after_discount),
        Decimal("0")
    )

    cumulative_paid = Decimal("0")

    for payment in payments:

        payment.current_receivable = max(
            total_after_discount - cumulative_paid,
            Decimal("0")
        )

        cumulative_paid += to_decimal(
            payment.amount_received
        )

        payment.pending_amount = max(
            total_after_discount - cumulative_paid,
            Decimal("0")
        )

    student = Student.query.get(student_id)

    if student:

        update_student_payment_status(student)


# ============================================================
# BUILD PAYMENT ROWS
# ============================================================

def build_payment_rows(payments):
    """
    Build payment-history rows.

    All balance calculations are performed in the
    student's COURSE CURRENCY.

    Supported:

        USD -> USD
        MMK -> MMK
        MMK -> USD using exchange rate
        USD -> MMK using exchange rate

    The exchange conversion is applied to the actual
    amount paid.

    amount_paid:
        The actual amount physically paid by the customer,
        in their payment currency.

    amount_received:
        The same money, converted into the course currency.

    amount_paid_course_currency:
        The equivalent amount applied to the student's
        course balance.
    """

    rows = []

    if not payments:
        return rows

    ordered = sorted(
        payments,
        key=lambda p: (
            p.payment_date or date.min,
            p.id
        )
    )

    # --------------------------------------------------------
    # INITIAL PAYMENT / COURSE CURRENCY / COURSE TOTAL
    # --------------------------------------------------------

    initial_payment = sorted(
        payments,
        key=lambda p: p.id
    )[0]

    student = initial_payment.student

    course_currency = normalize_currency(
        student.currency
        if student
        else None
    )

    fixed_total = get_course_total_after_discount(
        initial_payment
    )

    # --------------------------------------------------------
    # CUMULATIVE PAID
    #
    # Always stored/calculated in COURSE CURRENCY.
    # --------------------------------------------------------

    cumulative_paid = Decimal("0")

    for payment in ordered:

        # ----------------------------------------------------
        # PAYMENT CURRENCY / ACTUAL CUSTOMER PAYMENT
        # ----------------------------------------------------

        payment_currency = normalize_currency(
            getattr(
                payment,
                "payment_currency",
                None
            ) or course_currency,
            course_currency
        )

        amount_received = to_decimal(
            getattr(
                payment,
                "amount_received",
                0
            )
        )

        # ----------------------------------------------------
        # CONVERT TO COURSE CURRENCY
        # ----------------------------------------------------

        amount_paid_course_currency = (
            get_payment_amount_in_course_currency(
                payment,
                amount_received,
                course_currency,
                payment_currency
            )
        )

        # ----------------------------------------------------
        # RECEIVABLE BEFORE THIS PAYMENT
        # ----------------------------------------------------

        cumulative_paid_before_this_row = cumulative_paid

        current_receivable = max(
            fixed_total - cumulative_paid,
            Decimal("0")
        )

        # ----------------------------------------------------
        # ADD PAYMENT / REMAINING BALANCE
        # ----------------------------------------------------

        cumulative_paid += (
            amount_paid_course_currency
        )

        pending = max(
            fixed_total - cumulative_paid,
            Decimal("0")
        )

        # ----------------------------------------------------
        # ACTUAL PHYSICAL AMOUNT PAID
        #
        # This must be the real amount_paid (what the customer
        # physically handed over, in payment_currency) - NOT
        # amount_received (the course-currency equivalent).
        # student_details.html's inline edit form re-submits
        # this value as amount_paid, and edit_payment() runs it
        # back through the currency conversion; feeding it an
        # already-converted number there would convert it AGAIN
        # and silently corrupt it whenever payment_currency
        # differs from course_currency.
        #
        # Older rows saved before amount_paid existed fall back
        # to amount_received, same as get_payment_amount_in_
        # course_currency() already does elsewhere.
        # ----------------------------------------------------

        raw_amount_paid = getattr(
            payment,
            "amount_paid",
            None
        )

        actual_amount_paid = (
            to_decimal(raw_amount_paid)
            if raw_amount_paid is not None
            else amount_received
        )

        rows.append({

            "payment": payment,

            # Actual physical amount paid, in payment_currency
            "amount_paid": actual_amount_paid,

            # The same money, converted into course currency
            "amount_received": amount_received,

            # Currency customer used
            "payment_currency": payment_currency,

            # Student/course currency
            "course_currency": course_currency,

            # Converted amount applied to course balance
            "amount_paid_course_currency":
                amount_paid_course_currency,

            # Balance before this payment
            "current_receivable":
                current_receivable,

            # Total paid by OTHER payments before this one -
            # what the live edit preview needs to correctly
            # account for prior payments, instead of comparing
            # this row's own payment against the whole course
            # total as if it were the only payment ever made.
            "cumulative_paid_before_this_row":
                cumulative_paid_before_this_row,

            # Total paid so far
            "cumulative_paid":
                cumulative_paid,

            # Remaining balance
            "pending":
                pending
        })

    return rows


# ============================================================
# SHARED FORM-ERROR RESPONSE FOR add_student / edit_student
#
# Both views render the same template with the same shape of
# context when validation fails; centralizing avoids the
# same five-keyword render_template call being copy-pasted.
# ============================================================

def render_student_form(
    student_types,
    error,
    form_data,
    edit_mode,
    student
):
    return render_template(
        "add_student.html",
        student_types=student_types,
        error=error,
        form_data=form_data,
        edit_mode=edit_mode,
        student=student
    )


# ============================================================
# DASHBOARD
# ============================================================

@main.route("/")
def dashboard():

    search = request.args.get(
        "search",
        ""
    ).strip()

    course_search = request.args.get(
        "course_search",
        ""
    ).strip()

    payment_status = request.args.get(
        "payment_status",
        "all"
    ).strip()

    selected_type = request.args.get(
        "student_type",
        "all"
    ).strip()

    page = request.args.get(
        "page",
        1,
        type=int
    )

    if page < 1:

        page = 1

    per_page = 10

    query = Student.query

    # --------------------------------------------------------
    # GENERAL SEARCH
    # --------------------------------------------------------

    if search:

        search_pattern = f"%{search}%"

        query = query.filter(
            db.or_(
                Student.full_name.ilike(
                    search_pattern
                ),
                Student.student_id.ilike(
                    search_pattern
                ),
                Student.nrc.ilike(
                    search_pattern
                )
            )
        )

    # --------------------------------------------------------
    # COURSE SEARCH
    # --------------------------------------------------------

    if course_search:

        course_pattern = f"%{course_search}%"

        query = query.filter(
            Student.id.in_(
                db.session.query(
                    StudentResult.student_id
                ).filter(
                    StudentResult.course_id.ilike(
                        course_pattern
                    )
                )
            )
        )

    # --------------------------------------------------------
    # PAYMENT FILTER
    # --------------------------------------------------------

    if (
        payment_status
        and payment_status != "all"
    ):

        query = query.filter(
            Student.payment_status
            == payment_status
        )

    # --------------------------------------------------------
    # STUDENT TYPE FILTER
    # --------------------------------------------------------

    if (
        selected_type
        and selected_type != "all"
    ):

        query = query.join(
            StudentType
        ).filter(
            StudentType.name
            == selected_type
        )

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------

    pagination = query.order_by(
        Student.id.desc()
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    students = pagination.items

    student_types = (
        StudentType.query
        .order_by(
            StudentType.name
        )
        .all()
    )

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


# ============================================================
# ADD STUDENT
# ============================================================

@main.route(
    "/add-student",
    methods=["GET", "POST"]
)
def add_student():

    student_types = (
        StudentType.query
        .order_by(
            StudentType.name
        )
        .all()
    )

    if request.method == "POST":

        # ----------------------------------------------------
        # STUDENT INFORMATION
        # ----------------------------------------------------

        student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        if student_id_taken(student_id):

            return render_student_form(
                student_types,
                f"Student ID '{student_id}' already exists.",
                request.form,
                False,
                None
            )

        intake_date = parse_form_date(
            request.form,
            "intake_date"
        )

        student_type_id = request.form.get(
            "student_type_id"
        )

        if not student_type_id:

            return render_student_form(
                student_types,
                "Please select a student type.",
                request.form,
                False,
                None
            )

        # ----------------------------------------------------
        # COURSE CURRENCY
        # ----------------------------------------------------

        currency = normalize_currency(
            request.form.get(
                "currency",
                "MMK"
            )
        )

        # ----------------------------------------------------
        # CREATE STUDENT
        # ----------------------------------------------------

        student = Student(

            student_id=student_id,

            full_name=request.form.get(
                "full_name",
                ""
            ).strip(),

            phone_number=request.form.get(
                "phone_number",
                ""
            ).strip(),

            nrc=request.form.get(
                "nrc",
                ""
            ).strip(),

            # COURSE FEE CURRENCY
            currency=currency,

            intake_date=intake_date,

            status="pending",

            payment_status="unpaid",

            student_type_id=int(
                student_type_id
            ),

            uniform_size=request.form.get(
                "uniform_size",
                ""
            ).strip()
        )

        db.session.add(student)

        db.session.flush()

        # ----------------------------------------------------
        # PAYMENT INFORMATION
        # ----------------------------------------------------

        payment_comment = request.form.get(
            "payment_comment",
            ""
        ).strip()

        account = request.form.get(
            "account",
            ""
        ).strip()

        total_amount = to_decimal(
            request.form.get(
                "total_amount",
                "0"
            )
        )

        amount_paid = max(
            to_money(
                request.form.get(
                    "amount_paid",
                    "0"
                )
            ),
            Decimal("0")
        )

        discount_type = request.form.get(
            "discount_type",
            "percentage"
        ).strip().lower()

        discount = to_decimal(
            request.form.get(
                "discount",
                "0"
            )
        )

        promotion_amount = to_decimal(
            request.form.get(
                "promotion_amount",
                "0"
            )
        )

        # ----------------------------------------------------
        # PAYMENT CURRENCY / EXCHANGE SETTINGS
        # ----------------------------------------------------

        (
            payment_currency,
            exchange_enabled,
            exchange_rate,
            valid,
            error
        ) = resolve_currency_settings(
            request.form,
            currency
        )

        if not valid:

            db.session.rollback()

            return render_student_form(
                student_types,
                error,
                request.form,
                False,
                None
            )

        # ----------------------------------------------------
        # CALCULATE DISCOUNT
        # ----------------------------------------------------

        (
            discount_amount,
            total_after_discount
        ) = calculate_discount(
            total_amount,
            discount_type,
            discount,
            promotion_amount
        )

        # ----------------------------------------------------
        # CONVERT PAYMENT AFTER DISCOUNT
        # ----------------------------------------------------

        converted_amount_paid = min(
            convert_payment_to_course_currency(
                amount_paid,
                payment_currency,
                currency,
                exchange_enabled,
                exchange_rate
            ),
            total_after_discount
        )

        # ----------------------------------------------------
        # CREATE INITIAL PAYMENT
        # ----------------------------------------------------

        if (
            total_amount > 0
            or amount_paid > 0
            or discount_amount > 0
        ):

            pending_amount = max(
                total_after_discount
                - converted_amount_paid,
                Decimal("0")
            )

            payment = create_payment_with_invoice_id(

                student_id=student.id,

                payment_date=date.today(),

                total_amount=total_amount,

                discount_type=discount_type,

                discount=(
                    discount
                    if discount_type == "percentage"
                    else Decimal("0")
                ),

                promotion_amount=(
                    promotion_amount
                    if discount_type == "promotion"
                    else Decimal("0")
                ),

                discount_amount=discount_amount,

                total_after_discount=(
                    total_after_discount
                ),

                # ACTUAL PAYMENT CURRENCY
                payment_currency=payment_currency,

                # EXCHANGE
                exchange_enabled=exchange_enabled,

                exchange_rate=exchange_rate,

                # ACTUAL AMOUNT CUSTOMER PAID
                amount_paid=amount_paid,

                # CONVERTED AMOUNT IN COURSE CURRENCY
                amount_received=converted_amount_paid,

                current_receivable=(
                    total_after_discount
                ),

                pending_amount=pending_amount,

                comment=payment_comment,

                account=account
            )

            # ------------------------------------------------
            # STATUS
            # ------------------------------------------------

            if converted_amount_paid <= 0:

                student.payment_status = "unpaid"

            elif pending_amount > 0:

                student.payment_status = "partial"

            else:

                student.payment_status = "paid"

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        db.session.commit()

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # --------------------------------------------------------
    # GET REQUEST
    # --------------------------------------------------------

    return render_student_form(
        student_types,
        None,
        {
            "invoice_id": "Will be generated automatically",

            # COURSE CURRENCY
            "currency": "MMK",

            # PAYMENT CURRENCY DEFAULT
            "payment_currency": "MMK",

            "exchange_enabled": "no",

            "exchange_rate": ""
        },
        False,
        None
    )


# ============================================================
# EDIT STUDENT
# ============================================================

@main.route(
    "/edit-student/<int:student_id>",
    methods=["GET", "POST"]
)
def edit_student(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    student_types = (
        StudentType.query
        .order_by(
            StudentType.name
        )
        .all()
    )

    payments = get_student_payments(
        student.id
    )

    initial_payment = (
        payments[0]
        if payments
        else None
    )

    if request.method == "POST":

        # ----------------------------------------------------
        # LOCK THE STUDENT ROW
        #
        # From here on we're about to read and rewrite this
        # student's payment balances. Locking now (and
        # re-reading payments under that lock) means a second
        # concurrent edit for the SAME student queues behind
        # this one instead of both racing to write balances
        # from stale data. GET requests above never lock -
        # they're read-only.
        # ----------------------------------------------------

        student = get_student_for_update(
            student.id
        )

        payments = get_student_payments(
            student.id
        )

        initial_payment = (
            payments[0]
            if payments
            else None
        )

        # ----------------------------------------------------
        # DUPLICATE STUDENT ID
        # ----------------------------------------------------

        new_student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        if student_id_taken(
            new_student_id,
            exclude_student_id=student.id
        ):

            return render_student_form(
                student_types,
                "Student ID already exists.",
                request.form,
                True,
                student
            )

        # ----------------------------------------------------
        # STUDENT INFORMATION
        # ----------------------------------------------------

        student.student_id = new_student_id

        student.full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        student.phone_number = request.form.get(
            "phone_number",
            ""
        ).strip()

        student.nrc = request.form.get(
            "nrc",
            ""
        ).strip()

        # ----------------------------------------------------
        # COURSE CURRENCY
        # ----------------------------------------------------

        currency = normalize_currency(
            request.form.get(
                "currency",
                student.currency or "MMK"
            )
        )

        student.currency = currency

        # ----------------------------------------------------
        # INTAKE DATE
        # ----------------------------------------------------

        student.intake_date = parse_form_date(
            request.form,
            "intake_date"
        )

        # ----------------------------------------------------
        # STUDENT TYPE
        # ----------------------------------------------------

        student.student_type_id = int(
            request.form["student_type_id"]
        )

        # ----------------------------------------------------
        # UNIFORM SIZE
        # ----------------------------------------------------

        student.uniform_size = request.form.get(
            "uniform_size",
            ""
        ).strip()

        # ----------------------------------------------------
        # UPDATE ORIGINAL PAYMENT
        # ----------------------------------------------------

        if initial_payment:

            total_amount = to_decimal(
                request.form.get(
                    "total_amount",
                    initial_payment.total_amount
                )
            )

            discount_type = request.form.get(
                "discount_type",
                initial_payment.discount_type
                or "percentage"
            ).strip().lower()

            discount = to_decimal(
                request.form.get(
                    "discount",
                    initial_payment.discount
                )
            )

            promotion_amount = to_decimal(
                request.form.get(
                    "promotion_amount",
                    initial_payment.promotion_amount
                )
            )

            # ------------------------------------------------
            # PAYMENT CURRENCY / EXCHANGE SETTINGS
            # ------------------------------------------------

            (
                payment_currency,
                exchange_enabled,
                exchange_rate,
                valid,
                error
            ) = resolve_currency_settings(
                request.form,
                currency,
                default_payment_currency=(
                    initial_payment.payment_currency
                )
            )

            if not valid:

                return render_student_form(
                    student_types,
                    error,
                    request.form,
                    True,
                    student
                )

            # ------------------------------------------------
            # CALCULATE DISCOUNT
            # ------------------------------------------------

            (
                discount_amount,
                total_after_discount
            ) = calculate_discount(
                total_amount,
                discount_type,
                discount,
                promotion_amount
            )

            # ------------------------------------------------
            # IMPORTANT:
            #
            # Existing payment amount_paid is the ACTUAL
            # amount originally paid by customer.
            #
            # Recalculate it using the NEW currency settings.
            # ------------------------------------------------

            actual_amount_paid = to_decimal(
                initial_payment.amount_paid
            )

            converted_amount_received = (
                convert_payment_to_course_currency(
                    actual_amount_paid,
                    payment_currency,
                    currency,
                    exchange_enabled,
                    exchange_rate
                )
            )

            # ------------------------------------------------
            # UPDATE ORIGINAL PAYMENT
            # ------------------------------------------------

            initial_payment.total_amount = (
                total_amount
            )

            initial_payment.discount_type = (
                discount_type
            )

            initial_payment.discount = (
                discount
                if discount_type == "percentage"
                else Decimal("0")
            )

            initial_payment.promotion_amount = (
                promotion_amount
                if discount_type == "promotion"
                else Decimal("0")
            )

            initial_payment.discount_amount = (
                discount_amount
            )

            initial_payment.total_after_discount = (
                total_after_discount
            )

            initial_payment.payment_currency = (
                payment_currency
            )

            initial_payment.exchange_enabled = (
                exchange_enabled
            )

            initial_payment.exchange_rate = (
                exchange_rate
            )

            initial_payment.amount_received = min(
                converted_amount_received,
                total_after_discount
            )

            initial_payment.pending_amount = max(
                total_after_discount
                - initial_payment.amount_received,
                Decimal("0")
            )

            # ------------------------------------------------
            # REBUILD ALL PAYMENT BALANCES
            #
            # Existing additional payments remain unchanged.
            # ------------------------------------------------

            rebuild_payment_balances(
                student.id,
                total_after_discount
            )

        db.session.commit()

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # --------------------------------------------------------
    # EXISTING FORM DATA
    # --------------------------------------------------------

    form_data = {

        "student_id":
            student.student_id,

        "full_name":
            student.full_name,

        "phone_number":
            student.phone_number or "",

        "nrc":
            student.nrc or "",

        "currency":
            student.currency or "MMK",

        "intake_date":
            (
                student.intake_date.strftime(
                    "%Y-%m-%d"
                )
                if student.intake_date
                else ""
            ),

        "student_type_id":
            str(student.student_type_id),

        "uniform_size":
            student.uniform_size or "",

        "voucher_id":
            (
                initial_payment.voucher_id
                if initial_payment
                and hasattr(
                    initial_payment,
                    "voucher_id"
                )
                else ""
            ),

        "total_amount":
            (
                str(
                    initial_payment.total_amount
                )
                if initial_payment
                else ""
            ),

        "discount_type":
            (
                initial_payment.discount_type
                if initial_payment
                else "percentage"
            ),

        "discount":
            (
                str(
                    initial_payment.discount
                )
                if initial_payment
                else "0"
            ),

        "promotion_amount":
            (
                str(
                    initial_payment.promotion_amount
                )
                if initial_payment
                else "0"
            ),

        "discount_amount":
            (
                str(
                    initial_payment.discount_amount
                )
                if initial_payment
                else "0"
            ),

        "total_after_discount":
            (
                str(
                    initial_payment.total_after_discount
                )
                if initial_payment
                else ""
            ),

        "amount_paid":
            (
                str(
                    initial_payment.amount_paid
                )
                if initial_payment
                else "0"
            ),

        "pending_amount":
            (
                str(
                    initial_payment.pending_amount
                )
                if initial_payment
                else ""
            ),

        "payment_currency":
            (
                initial_payment.payment_currency
                if initial_payment
                else student.currency
            ),

        "exchange_enabled":
            (
                "yes"
                if initial_payment
                and initial_payment.exchange_enabled
                else "no"
            ),

        "exchange_rate":
            (
                str(
                    initial_payment.exchange_rate
                )
                if initial_payment
                and initial_payment.exchange_rate
                else ""
            ),

        "payment_comment":
            (
                initial_payment.comment
                if initial_payment
                else ""
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


# ============================================================
# ADD PAYMENT
# ============================================================

@main.route(
    "/student/<int:student_id>/add-payment",
    methods=["POST"]
)
def add_payment(student_id):

    # See get_student_for_update: this queues concurrent
    # payments for the SAME student instead of letting them
    # race on stale balance data.

    student = get_student_for_update(
        student_id
    )

    payment_comment = request.form.get(
        "payment_comment",
        ""
    ).strip()

    amount_paid = max(
        to_money(
            request.form.get(
                "amount_paid",
                "0"
            )
        ),
        Decimal("0")
    )

    try:

        payment_date = (
            parse_form_date(
                request.form,
                "payment_date"
            )
            or date.today()
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
    # GET EXISTING PAYMENTS
    # --------------------------------------------------------

    payments = get_student_payments(
        student.id
    )

    if not payments:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    initial_payment = payments[0]

    total_after_discount = get_course_total_after_discount(
        initial_payment
    )

    # --------------------------------------------------------
    # PREVIOUSLY PAID
    #
    # amount_received is in COURSE CURRENCY.
    # --------------------------------------------------------

    total_received_before = sum(
        (
            to_decimal(
                payment.amount_received
            )
            for payment in payments
        ),
        Decimal("0")
    )

    current_receivable = max(
        total_after_discount
        - total_received_before,
        Decimal("0")
    )

    if current_receivable <= 0:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # --------------------------------------------------------
    # PAYMENT CURRENCY / EXCHANGE SETTINGS
    # --------------------------------------------------------

    course_currency = normalize_currency(
        student.currency
    )

    (
        payment_currency,
        exchange_enabled,
        exchange_rate,
        valid,
        error
    ) = resolve_currency_settings(
        request.form,
        course_currency
    )

    if not valid:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                error=error
            )
        )

    # --------------------------------------------------------
    # CONVERT PAYMENT TO COURSE CURRENCY
    #
    # Also caps the converted amount to what's still owed,
    # so overpayment is never recorded.
    # --------------------------------------------------------

    converted_amount = min(
        convert_payment_to_course_currency(
            amount_paid,
            payment_currency,
            course_currency,
            exchange_enabled,
            exchange_rate
        ),
        current_receivable
    )

    # --------------------------------------------------------
    # CREATE PAYMENT
    # --------------------------------------------------------

    pending_after_payment = max(
        current_receivable
        - converted_amount,
        Decimal("0")
    )

    payment = create_payment_with_invoice_id(

        student_id=student.id,

        payment_date=payment_date,

        # For additional payment records,
        # this represents the current course total.
        total_amount=total_after_discount,

        discount_type="none",

        discount=Decimal("0"),

        promotion_amount=Decimal("0"),

        discount_amount=Decimal("0"),

        total_after_discount=(
            total_after_discount
        ),

        # ----------------------------------------------------
        # PAYMENT CURRENCY
        # ----------------------------------------------------

        payment_currency=payment_currency,

        exchange_enabled=exchange_enabled,

        exchange_rate=exchange_rate,

        # ----------------------------------------------------
        # ACTUAL CUSTOMER PAYMENT
        # ----------------------------------------------------

        amount_paid=amount_paid,

        # ----------------------------------------------------
        # NORMALIZED PAYMENT
        # ----------------------------------------------------

        amount_received=converted_amount,

        current_receivable=current_receivable,

        pending_amount=pending_after_payment,

        comment=payment_comment,

        account=request.form.get(
            "account",
            ""
        ).strip()
    )

    # --------------------------------------------------------
    # UPDATE ALL PAYMENT RECORDS
    # --------------------------------------------------------

    rebuild_payment_balances(
        student.id,
        total_after_discount
    )

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student.id
        )
    )


# ============================================================
# DELETE STUDENT
# ============================================================

@main.route(
    "/delete-student/<int:student_id>",
    methods=["POST"]
)
def delete_student(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    db.session.delete(student)

    db.session.commit()

    return redirect(
        url_for(
            "main.dashboard"
        )
    )


# ============================================================
# ADD COURSE
# ============================================================

@main.route(
    "/student/<int:student_id>/add-course",
    methods=["POST"]
)
def add_course(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    course_name = request.form.get(
        "course_name",
        ""
    ).strip()

    course_id = request.form.get(
        "course_id",
        ""
    ).strip()

    if not course_name or not course_id:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    start_date = parse_form_date(
        request.form,
        "start_date"
    )

    end_date = parse_form_date(
        request.form,
        "end_date"
    )

    result = request.form.get(
        "result",
        ""
    ).strip()

    collected = (
        request.form.get(
            "collected",
            "no"
        ) == "yes"
    )

    published_date = (
        date.today()
        if result
        else None
    )

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

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student.id
        )
    )


# ============================================================
# EDIT COURSE
# ============================================================

@main.route(
    "/course/<int:course_id>/edit",
    methods=["POST"]
)
def edit_course(course_id):

    course = StudentResult.query.get_or_404(
        course_id
    )

    student_id = course.student_id

    course.course_name = request.form.get(
        "course_name",
        ""
    ).strip()

    course.course_id = request.form.get(
        "course_id",
        ""
    ).strip()

    course.start_date = parse_form_date(
        request.form,
        "start_date"
    )

    course.end_date = parse_form_date(
        request.form,
        "end_date"
    )

    result = request.form.get(
        "result",
        ""
    ).strip()

    course.result = result or None

    if result and not course.published_date:

        course.published_date = date.today()

    elif not result:

        course.published_date = None

    course.collected = (
        request.form.get(
            "collected",
            "no"
        ) == "yes"
    )

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student_id
        )
    )


# ============================================================
# DELETE COURSE
# ============================================================

@main.route(
    "/course/<int:course_id>/delete",
    methods=["POST"]
)
def delete_course(course_id):

    course = StudentResult.query.get_or_404(
        course_id
    )

    student_id = course.student_id

    db.session.delete(course)

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student_id
        )
    )


# ============================================================
# ADD REMARK
# ============================================================

@main.route(
    "/student/<int:student_id>/add-remark",
    methods=["POST"]
)
def add_remark(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    text = request.form.get(
        "text",
        ""
    ).strip()

    if text:

        remark = StudentRemark(

            student_id=student.id,

            text=text,

            written_date=date.today()
        )

        db.session.add(remark)

        db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student.id
        )
    )


# ============================================================
# DELETE REMARK
# ============================================================

@main.route(
    "/remark/<int:remark_id>/delete",
    methods=["POST"]
)
def delete_remark(remark_id):

    remark = StudentRemark.query.get_or_404(
        remark_id
    )

    student_id = remark.student_id

    db.session.delete(remark)

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student_id
        )
    )


# ============================================================
# OFFICIAL RECEIPT
# ============================================================

@main.route(
    "/student/<int:student_id>/receipt/<int:payment_id>"
)
def official_receipt(student_id, payment_id):

    student = Student.query.get_or_404(
        student_id
    )

    payment = (
        StudentPayment.query
        .filter_by(
            id=payment_id,
            student_id=student_id
        )
        .first_or_404()
    )

    latest_remark = (
        StudentRemark.query
        .filter_by(
            student_id=student_id
        )
        .order_by(
            StudentRemark.written_date.desc(),
            StudentRemark.id.desc()
        )
        .first()
    )

    remark = (
        latest_remark.text
        if latest_remark
        else ""
    )

    return render_template(
        "official_receipt.html",
        student=student,
        payment=payment,
        remark=remark,
        now=datetime.now()
    )


# ============================================================
# STUDENT DETAILS
# ============================================================

@main.route(
    "/student/<int:student_id>",
    methods=["GET", "POST"]
)
def student_details(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    # --------------------------------------------------------
    # UPDATE STUDENT DETAILS
    # --------------------------------------------------------

    if request.method == "POST":

        new_student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        if student_id_taken(
            new_student_id,
            exclude_student_id=student.id
        ):

            return redirect(
                url_for(
                    "main.student_details",
                    student_id=student.id,
                    error="Student ID already exists."
                )
            )

        if new_student_id:

            student.student_id = new_student_id

        student.full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        student.phone_number = request.form.get(
            "phone_number",
            ""
        ).strip()

        student.nrc = request.form.get(
            "nrc",
            ""
        ).strip()

        student.uniform_size = request.form.get(
            "uniform_size",
            ""
        ).strip()

        # ----------------------------------------------------
        # COURSE CURRENCY
        # ----------------------------------------------------

        student.currency = normalize_currency(
            request.form.get(
                "currency",
                student.currency or "MMK"
            )
        )

        # ----------------------------------------------------
        # INTAKE DATE
        # ----------------------------------------------------

        student.intake_date = parse_form_date(
            request.form,
            "intake_date"
        )

        # ----------------------------------------------------
        # STUDENT TYPE
        # ----------------------------------------------------

        student_type_id = request.form.get(
            "student_type_id",
            type=int
        )

        if student_type_id:

            student.student_type_id = (
                student_type_id
            )

        db.session.commit()

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id,
                success="1"
            )
        )

    # --------------------------------------------------------
    # STUDENT TYPES
    # --------------------------------------------------------

    student_types = (
        StudentType.query
        .order_by(
            StudentType.name
        )
        .all()
    )

    # --------------------------------------------------------
    # COURSES
    # --------------------------------------------------------

    results = (
        StudentResult.query
        .filter_by(
            student_id=student.id
        )
        .order_by(
            StudentResult.start_date.desc()
        )
        .all()
    )

    # --------------------------------------------------------
    # PAYMENTS
    # --------------------------------------------------------

    payments = get_student_payments(
        student.id
    )

    # --------------------------------------------------------
    # REMARKS
    # --------------------------------------------------------

    remarks = (
        StudentRemark.query
        .filter_by(
            student_id=student.id
        )
        .order_by(
            StudentRemark.written_date.desc()
        )
        .all()
    )

    # --------------------------------------------------------
    # PAYMENT TOTALS
    # --------------------------------------------------------

    (
        total_payment,
        total_received,
        total_pending
    ) = calculate_student_totals(
        student.id
    )

    # --------------------------------------------------------
    # PAYMENT HISTORY
    # --------------------------------------------------------

    payment_rows = build_payment_rows(
        payments
    )

    # --------------------------------------------------------
    # COURSE EDIT
    # --------------------------------------------------------

    edit_course_id = request.args.get(
        "edit_course",
        type=int
    )

    edit_course = None

    if edit_course_id:

        edit_course = (
            StudentResult.query
            .filter_by(
                id=edit_course_id,
                student_id=student.id
            )
            .first()
        )

    return render_template(
        "student_details.html",

        student=student,

        student_types=student_types,

        results=results,

        payments=payments,

        payment_rows=payment_rows,

        remarks=remarks,

        edit_course=edit_course,

        total_payment=total_payment,

        total_received=total_received,

        total_pending=total_pending
    )


# ============================================================
# EDIT PAYMENT
# ============================================================

@main.route(
    "/payment/<int:payment_id>/edit",
    methods=["POST"]
)
def edit_payment(payment_id):

    payment = StudentPayment.query.get_or_404(
        payment_id
    )

    # See get_student_for_update: this queues concurrent edits
    # for the SAME student instead of letting them race on
    # stale balance data. payment.student (a lazy relationship)
    # would not carry the lock, so the student is re-fetched
    # explicitly here.

    student = get_student_for_update(
        payment.student_id
    )

    course_currency = normalize_currency(
        student.currency
    )

    # --------------------------------------------------------
    # PAYMENT DATE / ACCOUNT
    # --------------------------------------------------------

    payment.account = request.form.get(
        "account",
        ""
    ).strip()

    try:

        payment.payment_date = parse_form_date(
            request.form,
            "payment_date"
        )

    except ValueError:

        return redirect(
            url_for(
                "main.student_details",
                student_id=payment.student_id,
                error="Invalid payment date."
            )
        )

    # --------------------------------------------------------
    # ORIGINAL AMOUNT
    # --------------------------------------------------------

    total_amount = max(
        to_decimal(
            request.form.get(
                "total_amount",
                "0"
            )
        ),
        Decimal("0")
    )

    payment.total_amount = total_amount

    # --------------------------------------------------------
    # DISCOUNT TYPE / VALUE
    # --------------------------------------------------------

    discount_type = (
        request.form.get(
            "discount_type"
        )
        or "none"
    ).strip().lower()

    if discount_type not in (
        "none",
        "percentage",
        "promotion"
    ):

        discount_type = "none"

    discount = max(
        to_decimal(
            request.form.get(
                "discount",
                "0"
            )
        ),
        Decimal("0")
    )

    # --------------------------------------------------------
    # PROMOTION AMOUNT
    #
    # The inline edit row on student_details.html has only ONE
    # input for this - name="discount" - reused for both the
    # percentage value and the promotion amount depending on
    # discount_type. There is no separate "promotion_amount"
    # field in that form. calculate_discount() below only ever
    # uses whichever of these two matches discount_type, so
    # reusing the same submitted value for both is correct -
    # reading a "promotion_amount" field that doesn't exist
    # would silently zero out every promotion edit instead.
    # --------------------------------------------------------

    promotion_amount = discount

    # --------------------------------------------------------
    # CALCULATE + SAVE DISCOUNT
    # --------------------------------------------------------

    (
        discount_amount,
        total_after_discount
    ) = calculate_discount(
        total_amount,
        discount_type,
        discount,
        promotion_amount
    )

    if discount_type == "percentage":

        payment.discount_type = "percentage"

        payment.discount = min(
            discount,
            Decimal("100")
        )

        payment.promotion_amount = Decimal("0")

    elif discount_type == "promotion":

        payment.discount_type = "promotion"

        payment.discount = Decimal("0")

        payment.promotion_amount = min(
            promotion_amount,
            total_amount
        )

    else:

        payment.discount_type = "none"

        payment.discount = Decimal("0")

        payment.promotion_amount = Decimal("0")

    payment.discount_amount = (
        discount_amount
    )

    payment.total_after_discount = (
        total_after_discount
    )

    # --------------------------------------------------------
    # PAYMENT CURRENCY / EXCHANGE SETTINGS
    #
    # The inline edit row on student_details.html has no
    # currency controls at all - it only lets someone adjust
    # date/amount/discount/comment/account. If this form didn't
    # submit a "payment_currency" field, this payment's existing
    # currency settings are left exactly as they were, instead
    # of being re-validated against an exchange rate that form
    # was never meant to submit in the first place.
    # --------------------------------------------------------

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
            default_payment_currency=(
                payment.payment_currency
            )
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

        payment_currency = normalize_currency(
            payment.payment_currency,
            course_currency
        )

        exchange_enabled = payment.exchange_enabled

        exchange_rate = payment.exchange_rate

    # --------------------------------------------------------
    # ACTUAL AMOUNT PAID / CONVERT TO COURSE CURRENCY
    # --------------------------------------------------------

    amount_paid = max(
        to_money(
            request.form.get(
                "amount_paid",
                "0"
            )
        ),
        Decimal("0")
    )

    amount_received = (
        convert_payment_to_course_currency(
            amount_paid,
            payment_currency,
            course_currency,
            exchange_enabled,
            exchange_rate
        )
    )

    # --------------------------------------------------------
    # SAVE CURRENCY DATA
    # --------------------------------------------------------

    payment.payment_currency = (
        payment_currency
    )

    payment.exchange_enabled = (
        exchange_enabled
    )

    payment.exchange_rate = (
        exchange_rate
    )

    payment.amount_paid = (
        amount_paid
    )

    payment.amount_received = (
        amount_received
    )

    # --------------------------------------------------------
    # REBUILD ALL PAYMENT BALANCES
    # --------------------------------------------------------

    rebuild_payment_balances(
        student.id,
        total_after_discount
    )

    # --------------------------------------------------------
    # COMMENT
    # --------------------------------------------------------

    payment.comment = request.form.get(
        "payment_comment",
        ""
    ).strip()

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    try:

        db.session.commit()

    except Exception as e:

        db.session.rollback()

        return redirect(
            url_for(
                "main.student_details",
                student_id=payment.student_id,
                error=(
                    f"Could not update payment: {str(e)}"
                )
            )
        )

    return redirect(
        url_for(
            "main.student_details",
            student_id=payment.student_id,
            success=1
        )
    )


# ============================================================
# SUMMARIZE AMOUNTS BY CURRENCY
#
# Payments received on the same day (or in the same account)
# are not necessarily all in the same currency. Adding them
# together as one number - as end_of_day.html used to do by
# just hardcoding "MMK" - silently mixes MMK and USD together.
# This groups actual amount_paid by its real currency so each
# total only ever contains money of one kind.
# ============================================================

def summarize_amounts_by_currency(rows):

    totals = defaultdict(
        lambda: Decimal("0")
    )

    for row in rows:

        totals[
            row["currency"]
        ] += row["amount_paid"]

    return dict(totals)


# ============================================================
# END OF DAY
# ============================================================

@main.route("/end-of-day")
def end_of_day():

    report_date = date.today()

    payments = (
        StudentPayment.query
        .filter(
            StudentPayment.payment_date
            == report_date
        )
        .order_by(
            StudentPayment.payment_date.asc()
        )
        .all()
    )

    account_groups = defaultdict(list)

    for payment in payments:

        # Actual amount customer paid
        amount_paid = to_decimal(
            payment.amount_paid
        )

        student = payment.student

        # Actual payment currency
        currency = normalize_currency(
            payment.payment_currency
            if payment.payment_currency
            else (
                student.currency
                if student
                else "MMK"
            )
        )

        row = {

            "payment": payment,

            "student": student,

            # Actual money received
            "amount_paid": amount_paid,

            "currency": currency,

            # Converted value
            "amount_received":
                to_decimal(
                    payment.amount_received
                ),

            "course_currency":
                (
                    student.currency
                    if student
                    else "MMK"
                ),

            "exchange_enabled":
                payment.exchange_enabled,

            "exchange_rate":
                payment.exchange_rate
        }

        account = (
            payment.account
            or "Unknown"
        )

        account_groups[
            account
        ].append(row)

    account_groups = dict(
        account_groups
    )

    # --------------------------------------------------------
    # TOTALS BY ACTUAL PAYMENT CURRENCY
    #
    # One breakdown per account, plus one across the whole day.
    # --------------------------------------------------------

    account_totals = {

        account: summarize_amounts_by_currency(rows)

        for account, rows in account_groups.items()
    }

    grand_totals = summarize_amounts_by_currency([
        row
        for rows in account_groups.values()
        for row in rows
    ])

    return render_template(
        "end_of_day.html",
        account_groups=account_groups,
        account_totals=account_totals,
        grand_totals=grand_totals,
        report_date=report_date
    )