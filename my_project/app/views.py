from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

from datetime import datetime, date
from decimal import Decimal, InvalidOperation

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
# HELPER FUNCTIONS
# ============================================================

def decimal_value(value):
    """
    Safely convert a value to Decimal.
    """

    try:
        return Decimal(str(value or 0))
    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):
        return Decimal("0")


def money(value):
    """
    Return Decimal rounded to 2 decimal places.
    """

    return decimal_value(value).quantize(
        Decimal("0.01")
    )


def calculate_discount(
    total_amount,
    discount_percent
):
    """
    Calculate discount amount and final amount.

    Example:

        total = 5000
        discount = 20

        discount amount = 1000
        final amount = 4000
    """

    total_amount = decimal_value(
        total_amount
    )

    discount_percent = decimal_value(
        discount_percent
    )

    if discount_percent < 0:
        discount_percent = Decimal("0")

    if discount_percent > 100:
        discount_percent = Decimal("100")

    discount_amount = (
        total_amount
        * discount_percent
        / Decimal("100")
    )

    total_after_discount = (
        total_amount
        - discount_amount
    )

    return (
        money(discount_amount),
        money(total_after_discount)
    )


# ============================================================
# PAYMENT CALCULATION
# ============================================================

def get_payment_information(
    student_id,
    payments=None
):
    """
    Calculate all payment information.

    IMPORTANT:

    total_after_discount is a FIXED amount.

    Example:

        Fixed total = 4000

        Payment 1:
            current receivable = 4000
            paid = 2000
            pending = 2000

        Payment 2:
            current receivable = 2000
            paid = 2000
            pending = 0
    """

    if payments is None:

        payments = StudentPayment.query.filter_by(
            student_id=student_id
        ).order_by(
            StudentPayment.id.asc()
        ).all()

    if not payments:

        return {
            "total_payment": Decimal("0.00"),
            "total_received": Decimal("0.00"),
            "total_pending": Decimal("0.00"),
            "payment_rows": []
        }

    # --------------------------------------------------------
    # FIXED TOTAL
    #
    # Never sum total_after_discount.
    # Take the original/fixed total.
    # --------------------------------------------------------

    fixed_total = money(
        payments[0].total_after_discount
    )

    cumulative_paid = Decimal("0.00")

    payment_rows = []

    for payment in payments:

        individual_paid = money(
            payment.amount_received
        )

        # Amount that was still receivable BEFORE
        # this particular payment was made.
        current_receivable = max(
            fixed_total - cumulative_paid,
            Decimal("0.00")
        )

        # Do not allow the payment to make the
        # cumulative total exceed the fixed price.
        effective_paid = min(
            individual_paid,
            current_receivable
        )

        cumulative_paid += effective_paid

        pending_after_payment = max(
            fixed_total - cumulative_paid,
            Decimal("0.00")
        )

        payment_rows.append({
            "payment": payment,
            "current_receivable": money(
                current_receivable
            ),
            "amount_paid": money(
                individual_paid
            ),
            "cumulative_paid": money(
                cumulative_paid
            ),
            "pending": money(
                pending_after_payment
            )
        })

    total_received = money(
        cumulative_paid
    )

    total_pending = max(
        fixed_total - total_received,
        Decimal("0.00")
    )

    return {
        "total_payment": money(fixed_total),
        "total_received": money(total_received),
        "total_pending": money(total_pending),
        "payment_rows": payment_rows
    }


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
    # STUDENT TYPE
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

    student_types = StudentType.query.order_by(
        StudentType.name
    ).all()

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

    student_types = StudentType.query.order_by(
        StudentType.name
    ).all()

    if request.method == "POST":

        student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        existing_student = Student.query.filter_by(
            student_id=student_id
        ).first()

        if existing_student:

            return render_template(
                "form.html",
                student_types=student_types,
                error=(
                    f"Student ID '{student_id}' "
                    "already exists."
                ),
                form_data=request.form,
                edit_mode=False,
                student=None,
                latest_payment=None
            )

        # ----------------------------------------------------
        # INTAKE DATE
        # ----------------------------------------------------

        intake_date = None

        if request.form.get(
            "intake_date"
        ):

            intake_date = datetime.strptime(
                request.form["intake_date"],
                "%Y-%m-%d"
            ).date()

        # ----------------------------------------------------
        # STUDENT TYPE
        # ----------------------------------------------------

        student_type_id = request.form.get(
            "student_type_id"
        )

        if not student_type_id:

            return render_template(
                "form.html",
                student_types=student_types,
                error="Please select a student type.",
                form_data=request.form,
                edit_mode=False,
                student=None,
                latest_payment=None
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

        # ====================================================
        # INITIAL PAYMENT
        # ====================================================

        voucher_id = request.form.get(
            "voucher_id",
            ""
        ).strip()

        payment_comment = request.form.get(
            "payment_comment",
            ""
        ).strip()

        total_amount = money(
            request.form.get(
                "total_amount",
                0
            )
        )

        discount_percent = decimal_value(
            request.form.get(
                "discount",
                0
            )
        )

        amount_paid = money(
            request.form.get(
                "amount_paid",
                0
            )
        )

        # ----------------------------------------------------
        # CALCULATE DISCOUNT
        # ----------------------------------------------------

        discount_amount, total_after_discount = (
            calculate_discount(
                total_amount,
                discount_percent
            )
        )

        # ----------------------------------------------------
        # DO NOT ALLOW FIRST PAYMENT TO EXCEED TOTAL
        # ----------------------------------------------------

        amount_paid = min(
            amount_paid,
            total_after_discount
        )

        current_receivable = money(
        total_after_discount
        )

        # ========================================================
        # PENDING AFTER INITIAL PAYMENT
        # ========================================================

        pending_amount = max(
            total_after_discount - amount_paid,
            Decimal("0.00")
        )

        pending_amount = money(
            pending_amount
        )

        # ----------------------------------------------------
        # CREATE INITIAL PAYMENT
        # ----------------------------------------------------

        if (
            total_amount > 0
            or amount_paid > 0
        ):

            if not voucher_id:

                db.session.rollback()

                return render_template(
                    "form.html",
                    student_types=student_types,
                    error=(
                        "Voucher ID is required "
                        "when entering payment."
                    ),
                    form_data=request.form,
                    edit_mode=False,
                    student=None,
                    latest_payment=None
                )

            payment = StudentPayment(

            student_id=student.id,

            voucher_id=voucher_id,

            payment_date=date.today(),

            total_amount=total_amount,

            discount=discount_percent,

            total_after_discount=(
                total_after_discount
            ),

            amount_received=amount_paid,

            current_receivable=current_receivable,

            pending_amount=pending_amount,

            comment=payment_comment,

            currency="MMK"
        )
            db.session.add(payment)

            # ------------------------------------------------
            # PAYMENT STATUS
            # ------------------------------------------------

            if amount_paid <= 0:

                student.payment_status = "unpaid"

            elif pending_amount > 0:

                student.payment_status = "partial"

            else:

                student.payment_status = "paid"

        db.session.commit()

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    return render_template(
        "form.html",
        student_types=student_types,
        error=None,
        form_data={},
        edit_mode=False,
        student=None,
        latest_payment=None
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

    student_types = StudentType.query.order_by(
        StudentType.name
    ).all()

    # --------------------------------------------------------
    # LATEST PAYMENT
    # --------------------------------------------------------

    latest_payment = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.id.desc()
    ).first()

    if request.method == "POST":

        # ----------------------------------------------------
        # DUPLICATE STUDENT ID
        # ----------------------------------------------------

        new_student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        existing_student = Student.query.filter(
            Student.student_id
            == new_student_id,
            Student.id != student.id
        ).first()

        if existing_student:

            return render_template(
                "form.html",
                student_types=student_types,
                form_data=request.form,
                error="Student ID already exists.",
                edit_mode=True,
                student=student,
                latest_payment=latest_payment
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
        # INTAKE DATE
        # ----------------------------------------------------

        if request.form.get(
            "intake_date"
        ):

            student.intake_date = datetime.strptime(
                request.form["intake_date"],
                "%Y-%m-%d"
            ).date()

        else:

            student.intake_date = None

        # ----------------------------------------------------
        # STUDENT TYPE
        # ----------------------------------------------------

        student.student_type_id = int(
            request.form["student_type_id"]
        )

        # ----------------------------------------------------
        # UNIFORM
        # ----------------------------------------------------

        student.uniform_size = request.form.get(
            "uniform_size",
            ""
        ).strip()

        # ----------------------------------------------------
        # PAYMENT STATUS
        #
        # Payment history is NOT modified.
        # ----------------------------------------------------

        payment_info = get_payment_information(
            student.id
        )

        total_paid = payment_info[
            "total_received"
        ]

        total_pending = payment_info[
            "total_pending"
        ]

        if total_paid <= 0:

            student.payment_status = "unpaid"

        elif total_pending > 0:

            student.payment_status = "partial"

        else:

            student.payment_status = "paid"

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

        "student_id": student.student_id,

        "full_name": student.full_name,

        "phone_number": (
            student.phone_number or ""
        ),

        "nrc": student.nrc or "",

        "intake_date": (
            student.intake_date.strftime(
                "%Y-%m-%d"
            )
            if student.intake_date
            else ""
        ),

        "student_type_id": str(
            student.student_type_id
        ),

        "uniform_size": (
            student.uniform_size or ""
        ),

        "voucher_id": (
            str(latest_payment.voucher_id)
            if latest_payment
            and latest_payment.voucher_id
            else ""
        ),

        "total_amount": (
            str(latest_payment.total_amount)
            if latest_payment
            else ""
        ),

        "discount": (
            str(latest_payment.discount)
            if latest_payment
            else ""
        ),

        "total_after_discount": (
            str(
                latest_payment.total_after_discount
            )
            if latest_payment
            else ""
        ),

        "amount_paid": (
            str(
                latest_payment.amount_received
            )
            if latest_payment
            else ""
        ),

        "pending_amount": (
            str(
                latest_payment.pending_amount
            )
            if latest_payment
            else ""
        ),

        "payment_comment": (
            latest_payment.comment
            if latest_payment
            else ""
        )
    }

    return render_template(
        "form.html",
        student_types=student_types,
        form_data=form_data,
        edit_mode=True,
        student=student,
        latest_payment=latest_payment,
        error=None
    )


# ============================================================
# ADD PAYMENT RECORD
# ============================================================

@main.route(
    "/student/<int:student_id>/add-payment",
    methods=["POST"]
)
def add_payment(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    voucher_id = request.form.get(
        "voucher_id",
        ""
    ).strip()

    payment_comment = request.form.get(
        "payment_comment",
        ""
    ).strip()

    amount_paid = money(
        request.form.get(
            "amount_paid",
            0
        )
    )

    payment_date = date.today()

    if request.form.get("payment_date"):

        payment_date = datetime.strptime(
            request.form["payment_date"],
            "%Y-%m-%d"
        ).date()

    if (
        not voucher_id
        or amount_paid <= 0
    ):

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # ========================================================
    # FIND ORIGINAL PAYMENT
    # ========================================================

    original_payment = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.id.asc()
    ).first()

    if not original_payment:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # ========================================================
    # FIXED TOTAL
    # ========================================================

    fixed_total = money(
        original_payment.total_after_discount
    )

    # ========================================================
    # CALCULATE ALL EXISTING PAYMENTS
    # ========================================================

    all_payments = (
        StudentPayment.query
        .filter_by(student_id=student.id)
        .order_by(
            StudentPayment.id.asc()
        )
        .all()
    )

    current_total_paid = sum(
        (
            decimal_value(
                p.amount_received
            )
            for p in all_payments
        ),
        Decimal("0.00")
    )

    current_total_paid = money(
        current_total_paid
    )

    # ========================================================
    # CURRENT RECEIVABLE BEFORE THIS NEW PAYMENT
    #
    # THIS VALUE WILL BE SAVED TO THE NEW PAYMENT RECORD.
    # ========================================================

    current_receivable = max(
        fixed_total - current_total_paid,
        Decimal("0.00")
    )

    current_receivable = money(
        current_receivable
    )

    # ========================================================
    # DO NOT ALLOW OVERPAYMENT
    # ========================================================

    amount_paid = min(
        amount_paid,
        current_receivable
    )

    amount_paid = money(
        amount_paid
    )

    # ========================================================
    # NEW CUMULATIVE TOTAL
    # ========================================================

    new_total_paid = money(
        current_total_paid
        + amount_paid
    )

    # ========================================================
    # NEW PENDING
    # ========================================================

    new_pending = max(
        fixed_total - new_total_paid,
        Decimal("0.00")
    )

    new_pending = money(
        new_pending
    )

    # ========================================================
    # CREATE NEW PAYMENT
    # ========================================================

    payment = StudentPayment(

        student_id=student.id,

        voucher_id=voucher_id,

        payment_date=payment_date,

        total_amount=(
            original_payment.total_amount
        ),

        discount=(
            original_payment.discount
        ),

        total_after_discount=(
            fixed_total
        ),

        # IMPORTANT:
        # This is the amount the student is paying NOW.
        amount_received=(
            amount_paid
        ),

        # IMPORTANT:
        # This is the receivable BEFORE this payment.
        current_receivable=(
            current_receivable
        ),

        # IMPORTANT:
        # This is the remaining balance AFTER this payment.
        pending_amount=(
            new_pending
        ),

        comment=(
            payment_comment
        ),

        currency=(
            original_payment.currency
            or "MMK"
        )
    )

    db.session.add(payment)

    # ========================================================
    # UPDATE STUDENT STATUS
    # ========================================================

    if new_total_paid <= 0:

        student.payment_status = "unpaid"

    elif new_pending > 0:

        student.payment_status = "partial"

    else:

        student.payment_status = "paid"

    db.session.commit()

    return redirect(
        url_for(
            "main.student_details",
            student_id=student.id
        )
    )

#Offiial_receipt

@main.route("/student/<int:student_id>/receipt/<int:payment_id>")
def official_receipt(student_id, payment_id):

    # ========================================================
    # STUDENT
    # ========================================================

    student = Student.query.get_or_404(
        student_id
    )


    # ========================================================
    # PAYMENT
    #
    # Get the specific payment being printed.
    # All payment values are already stored in
    # StudentPayment by add_payment().
    # ========================================================

    payment = (
        StudentPayment.query
        .filter_by(
            id=payment_id,
            student_id=student_id
        )
        .first_or_404()
    )


    # ========================================================
    # ALL PAYMENTS
    #
    # Keep this available in case the template or
    # payment history needs it.
    #
    # No calculations are performed here.
    # ========================================================

    payments = (
        StudentPayment.query
        .filter_by(
            student_id=student_id
        )
        .order_by(
            StudentPayment.payment_date.asc(),
            StudentPayment.id.asc()
        )
        .all()
    )


    # ========================================================
    # STUDENT REMARK
    #
    # StudentRemark is connected to Student, NOT Payment.
    #
    # Get the most recent remark for this student.
    # ========================================================

    remark_record = (
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


    if remark_record:

        remark = remark_record.text

    else:

        remark = ""


    # ========================================================
    # STUDENT RESULTS
    # ========================================================

    results = (
        StudentResult.query
        .filter_by(
            student_id=student_id
        )
        .all()
    )


    # ========================================================
    # RENDER RECEIPT
    #
    # IMPORTANT:
    #
    # We DO NOT calculate:
    #
    # course_total
    # discount
    # total_after_discount
    # total_received
    # total_pending
    #
    # The receipt gets the already-calculated values directly
    # from `payment`.
    #
    # In particular:
    #
    # payment.pending_amount
    #
    # is the balance calculated and saved by add_payment().
    # ========================================================

    return render_template(

        "official_receipt.html",

        student=student,

        payment=payment,

        payments=payments,

        results=results,

        remark=remark,

        now=datetime.now()

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
# STUDENT DETAILS
# ============================================================

@main.route(
    "/student/<int:student_id>"
)
def student_details(student_id):

    student = Student.query.get_or_404(
        student_id
    )

    # --------------------------------------------------------
    # COURSES
    # --------------------------------------------------------

    results = StudentResult.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentResult.start_date.desc()
    ).all()

    # --------------------------------------------------------
    # PAYMENTS
    # --------------------------------------------------------

    payments = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.id.asc()
    ).all()

    # --------------------------------------------------------
    # PAYMENT INFORMATION
    # --------------------------------------------------------

    payment_info = get_payment_information(
        student.id,
        payments
    )

    # --------------------------------------------------------
    # REMARKS
    # --------------------------------------------------------

    remarks = StudentRemark.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentRemark.written_date.desc()
    ).all()

    # --------------------------------------------------------
    # COURSE EDIT
    # --------------------------------------------------------

    edit_course_id = request.args.get(
        "edit_course",
        type=int
    )

    edit_course = None

    if edit_course_id:

        edit_course = StudentResult.query.filter_by(
            id=edit_course_id,
            student_id=student.id
        ).first()

    return render_template(
        "student.html",

        student=student,

        results=results,

        payments=payments,

        payment_rows=payment_info[
            "payment_rows"
        ],

        remarks=remarks,

        edit_course=edit_course,

        total_payment=payment_info[
            "total_payment"
        ],

        total_received=payment_info[
            "total_received"
        ],

        total_pending=payment_info[
            "total_pending"
        ]
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

    if (
        not course_name
        or not course_id
    ):

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    start_date = None
    end_date = None

    if request.form.get(
        "start_date"
    ):

        start_date = datetime.strptime(
            request.form["start_date"],
            "%Y-%m-%d"
        ).date()

    if request.form.get(
        "end_date"
    ):

        end_date = datetime.strptime(
            request.form["end_date"],
            "%Y-%m-%d"
        ).date()

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

    published_date = None

    if result:

        published_date = date.today()

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

    if request.form.get(
        "start_date"
    ):

        course.start_date = datetime.strptime(
            request.form["start_date"],
            "%Y-%m-%d"
        ).date()

    else:

        course.start_date = None

    if request.form.get(
        "end_date"
    ):

        course.end_date = datetime.strptime(
            request.form["end_date"],
            "%Y-%m-%d"
        ).date()

    else:

        course.end_date = None

    result = request.form.get(
        "result",
        ""
    ).strip()

    course.result = result or None

    if result:

        if not course.published_date:

            course.published_date = date.today()

    else:

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