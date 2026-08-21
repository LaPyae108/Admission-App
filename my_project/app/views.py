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
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def calculate_discount(total_amount, discount_percent):
    """
    Calculate discount amount and final amount.

    Example:
        total = 5000
        discount = 20%

        discount amount = 1000
        final amount = 4000
    """

    total_amount = decimal_value(total_amount)
    discount_percent = decimal_value(discount_percent)

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
        discount_amount.quantize(Decimal("0.01")),
        total_after_discount.quantize(Decimal("0.01"))
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
                Student.full_name.ilike(search_pattern),
                Student.student_id.ilike(search_pattern),
                Student.nrc.ilike(search_pattern)
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
            Student.payment_status == payment_status
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
            StudentType.name == selected_type
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
                    f"Student ID '{student_id}' already exists. "
                    "Please use a different Student ID."
                ),
                form_data=request.form,
                edit_mode=False
            )

        # ----------------------------------------------------
        # INTAKE DATE
        # ----------------------------------------------------

        intake_date = None

        if request.form.get("intake_date"):

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
                edit_mode=False
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

        total_amount = decimal_value(
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

        amount_paid = decimal_value(
            request.form.get(
                "amount_paid",
                0
            )
        )

        # ----------------------------------------------------
        # CALCULATE DISCOUNT
        # ----------------------------------------------------

        (
            discount_amount,
            total_after_discount
        ) = calculate_discount(
            total_amount,
            discount_percent
        )

        # Never allow first payment to exceed total
        if amount_paid > total_after_discount:
            amount_paid = total_after_discount

        pending_amount = max(
            total_after_discount - amount_paid,
            Decimal("0")
        )

        # ----------------------------------------------------
        # CREATE INITIAL PAYMENT
        # ----------------------------------------------------

        if total_amount > 0 or amount_paid > 0:

            if not voucher_id:

                db.session.rollback()

                return render_template(
                    "form.html",
                    student_types=student_types,
                    error=(
                        "Voucher ID is required when "
                        "entering payment information."
                    ),
                    form_data=request.form,
                    edit_mode=False
                )

            payment = StudentPayment(

                student_id=student.id,

                voucher_id=voucher_id,

                payment_date=date.today(),

                # ORIGINAL PRICE
                total_amount=total_amount,

                # DISCOUNT %
                discount=discount_percent,

                # FIXED FINAL PRICE
                total_after_discount=total_after_discount,

                # THIS TRANSACTION'S PAYMENT
                amount_received=amount_paid,

                # BALANCE AFTER THIS TRANSACTION
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
        edit_mode=False
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

    if request.method == "POST":

        new_student_id = request.form.get(
            "student_id",
            ""
        ).strip()

        existing_student = Student.query.filter(
            Student.student_id == new_student_id,
            Student.id != student.id
        ).first()

        if existing_student:

            return render_template(
                "form.html",
                student_types=student_types,
                form_data=request.form,
                error="Student ID already exists.",
                edit_mode=True,
                student=student
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

        if request.form.get("intake_date"):

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
        # IMPORTANT:
        # Do NOT create a new payment here.
        # ----------------------------------------------------

        payments = StudentPayment.query.filter_by(
            student_id=student.id
        ).order_by(
            StudentPayment.id.asc()
        ).all()

        fixed_total = Decimal("0")
        total_paid = Decimal("0")

        if payments:

            fixed_total = decimal_value(
                payments[0].total_after_discount
            )

            total_paid = sum(
                (
                    decimal_value(
                        payment.amount_received
                    )
                    for payment in payments
                ),
                Decimal("0")
            )

        total_pending = max(
            fixed_total - total_paid,
            Decimal("0")
        )

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
    # EXISTING DATA
    # --------------------------------------------------------

    latest_payment = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.id.desc()
    ).first()

    form_data = {

        "student_id": student.student_id,

        "full_name": student.full_name,

        "phone_number": student.phone_number,

        "nrc": student.nrc,

        "intake_date": (
            student.intake_date.strftime("%Y-%m-%d")
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
            str(latest_payment.total_after_discount)
            if latest_payment
            else ""
        ),

        "amount_paid": (
            str(latest_payment.amount_received)
            if latest_payment
            else ""
        ),

        "pending_amount": (
            str(latest_payment.pending_amount)
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

    amount_paid = decimal_value(
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

    if not voucher_id or amount_paid <= 0:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    # ========================================================
    # GET ORIGINAL PAYMENT
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
    # FIXED PAYMENT INFORMATION
    # ========================================================

    fixed_total = decimal_value(
        original_payment.total_after_discount
    )

    # ========================================================
    # TOTAL PAID SO FAR
    # ========================================================

    current_total_paid = db.session.query(
        db.func.sum(
            StudentPayment.amount_received
        )
    ).filter(
        StudentPayment.student_id == student.id
    ).scalar() or Decimal("0")

    current_total_paid = decimal_value(
        current_total_paid
    )

    # ========================================================
    # CURRENT RECEIVABLE
    #
    # This is the amount still owed BEFORE the new payment.
    # ========================================================

    current_receivable = max(
        fixed_total - current_total_paid,
        Decimal("0")
    )

    # ========================================================
    # DO NOT ALLOW OVERPAYMENT
    # ========================================================

    if amount_paid > current_receivable:

        amount_paid = current_receivable

    # ========================================================
    # NEW TOTAL PAID
    # ========================================================

    new_total_paid = (
        current_total_paid
        + amount_paid
    )

    # ========================================================
    # NEW PENDING
    # ========================================================

    new_pending = max(
        fixed_total - new_total_paid,
        Decimal("0")
    )

    # ========================================================
    # CREATE PAYMENT TRANSACTION
    #
    # Notice:
    #
    # total_after_discount stays FIXED.
    #
    # amount_received contains ONLY this transaction.
    #
    # pending_amount contains the balance AFTER this
    # transaction.
    # ========================================================

    payment = StudentPayment(

        student_id=student.id,

        voucher_id=voucher_id,

        payment_date=payment_date,

        # Original price
        total_amount=original_payment.total_amount,

        # Original discount
        discount=original_payment.discount,

        # FIXED TOTAL
        total_after_discount=fixed_total,

        # ONLY THIS PAYMENT
        amount_received=amount_paid,

        # BALANCE AFTER THIS PAYMENT
        pending_amount=new_pending,

        comment=payment_comment,

        currency=original_payment.currency or "MMK"
    )

    db.session.add(payment)

    # ========================================================
    # UPDATE STUDENT PAYMENT STATUS
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

    # ========================================================
    # COURSE RECORDS
    # ========================================================

    results = StudentResult.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentResult.start_date.desc()
    ).all()

    # ========================================================
    # PAYMENT RECORDS
    #
    # VERY IMPORTANT:
    # Oldest payment FIRST.
    #
    # This allows us to calculate:
    #
    # Payment 1:
    # current receivable = 4000
    #
    # Payment 2:
    # current receivable = 2000
    #
    # Payment 3:
    # current receivable = 0
    # ========================================================

    payments = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.id.asc()
    ).all()

    # ========================================================
    # REMARKS
    # ========================================================

    remarks = StudentRemark.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentRemark.written_date.desc()
    ).all()

    # ========================================================
    # PAYMENT CALCULATION
    # ========================================================

    total_payment = Decimal("0")

    total_received = Decimal("0")

    total_pending = Decimal("0")

    payment_number = 0

    # --------------------------------------------------------
    # GET FIXED TOTAL FROM FIRST PAYMENT
    # --------------------------------------------------------

    if payments:

        total_payment = decimal_value(
            payments[0].total_after_discount
        )

    # --------------------------------------------------------
    # BUILD PAYMENT HISTORY
    # --------------------------------------------------------

    running_paid = Decimal("0")

    for payment in payments:

        payment_number += 1

        amount_paid = decimal_value(
            payment.amount_received
        )

        # ----------------------------------------------------
        # CURRENT RECEIVABLE
        #
        # Amount owed BEFORE THIS PAYMENT.
        # ----------------------------------------------------

        current_receivable = max(
            total_payment - running_paid,
            Decimal("0")
        )

        # ----------------------------------------------------
        # PENDING AFTER THIS PAYMENT
        # ----------------------------------------------------

        pending_after_payment = max(
            current_receivable - amount_paid,
            Decimal("0")
        )

        # ----------------------------------------------------
        # STORE DISPLAY VALUES
        #
        # These are temporary attributes used by Jinja.
        # They do NOT modify the database.
        # ----------------------------------------------------

        payment.payment_number = payment_number

        payment.current_receivable = (
            current_receivable
        )

        payment.display_pending = (
            pending_after_payment
        )

        # ----------------------------------------------------
        # UPDATE RUNNING TOTAL
        # ----------------------------------------------------

        running_paid += amount_paid

        total_received = running_paid

    # --------------------------------------------------------
    # FINAL OVERALL PENDING
    # --------------------------------------------------------

    total_pending = max(
        total_payment - total_received,
        Decimal("0")
    )

    # ========================================================
    # UPDATE PAYMENT STATUS
    # ========================================================

    if total_received <= 0:

        student.payment_status = "unpaid"

    elif total_pending > 0:

        student.payment_status = "partial"

    else:

        student.payment_status = "paid"

    db.session.commit()

    # ========================================================
    # COURSE EDIT
    # ========================================================

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

        remarks=remarks,

        edit_course=edit_course,

        total_payment=total_payment,

        total_received=total_received,

        total_pending=total_pending
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

    start_date = None
    end_date = None

    if request.form.get("start_date"):

        start_date = datetime.strptime(
            request.form["start_date"],
            "%Y-%m-%d"
        ).date()

    if request.form.get("end_date"):

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

    if request.form.get("start_date"):

        course.start_date = datetime.strptime(
            request.form["start_date"],
            "%Y-%m-%d"
        ).date()

    else:

        course.start_date = None

    if request.form.get("end_date"):

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