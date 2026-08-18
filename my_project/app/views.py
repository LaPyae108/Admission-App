from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for
)

from datetime import datetime, date

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

    # GENERAL SEARCH

    if search:

        search_pattern = f"%{search}%"

        query = query.filter(
            db.or_(
                Student.full_name.ilike(search_pattern),
                Student.student_id.ilike(search_pattern),
                Student.nrc.ilike(search_pattern)
            )
        )

    # COURSE SEARCH

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

    # PAYMENT FILTER

    if payment_status and payment_status != "all":

        query = query.filter(
            Student.payment_status == payment_status
        )

    # STUDENT TYPE FILTER

    if selected_type and selected_type != "all":

        query = query.join(
            StudentType
        ).filter(
            StudentType.name == selected_type
        )

    # PAGINATION

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

        # INTAKE DATE

        intake_date = None

        if request.form.get("intake_date"):

            intake_date = datetime.strptime(
                request.form["intake_date"],
                "%Y-%m-%d"
            ).date()

        # STUDENT TYPE

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

        # CREATE STUDENT

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

        # INITIAL PAYMENT

        voucher_id = request.form.get(
            "voucher_id",
            ""
        ).strip()

        total_amount = float(
            request.form.get(
                "total_amount",
                0
            ) or 0
        )

        amount_paid = float(
            request.form.get(
                "amount_paid",
                0
            ) or 0
        )

        pending_amount = max(
            total_amount - amount_paid,
            0
        )

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

                total_amount=total_amount,

                amount_received=amount_paid,

                pending_amount=pending_amount
            )

            db.session.add(payment)

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

        # ----------------------------------------------------
        # DUPLICATE STUDENT ID
        # ----------------------------------------------------

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
        # DO NOT OVERWRITE PAYMENT HISTORY
        # ----------------------------------------------------
        #
        # Payment records are now managed separately.
        #
        # We only recalculate the student's overall status.
        #

        total_amount = db.session.query(
            db.func.sum(
                StudentPayment.total_amount
            )
        ).filter(
            StudentPayment.student_id == student.id
        ).scalar() or 0

        total_paid = db.session.query(
            db.func.sum(
                StudentPayment.amount_received
            )
        ).filter(
            StudentPayment.student_id == student.id
        ).scalar() or 0

        total_pending = max(
            total_amount - total_paid,
            0
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

        "amount_paid": (
            str(latest_payment.amount_received)
            if latest_payment
            else ""
        ),

        "pending_amount": (
            str(latest_payment.pending_amount)
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

    total_amount = float(
        request.form.get(
            "total_amount",
            0
        ) or 0
    )

    amount_paid = float(
        request.form.get(
            "amount_paid",
            0
        ) or 0
    )

    payment_date = date.today()

    if request.form.get("payment_date"):

        payment_date = datetime.strptime(
            request.form["payment_date"],
            "%Y-%m-%d"
        ).date()

    pending_amount = max(
        total_amount - amount_paid,
        0
    )

    if not voucher_id or total_amount <= 0:

        return redirect(
            url_for(
                "main.student_details",
                student_id=student.id
            )
        )

    payment = StudentPayment(

        student_id=student.id,

        voucher_id=voucher_id,

        payment_date=payment_date,

        total_amount=total_amount,

        amount_received=amount_paid,

        pending_amount=pending_amount
    )

    db.session.add(payment)

    db.session.flush()

    # --------------------------------------------------------
    # RECALCULATE ALL PAYMENT TOTALS
    # --------------------------------------------------------

    total_amount_all = db.session.query(
        db.func.sum(
            StudentPayment.total_amount
        )
    ).filter(
        StudentPayment.student_id == student.id
    ).scalar() or 0

    total_paid_all = db.session.query(
        db.func.sum(
            StudentPayment.amount_received
        )
    ).filter(
        StudentPayment.student_id == student.id
    ).scalar() or 0

    total_pending_all = max(
        total_amount_all - total_paid_all,
        0
    )

    # --------------------------------------------------------
    # UPDATE STUDENT PAYMENT STATUS
    # --------------------------------------------------------

    if total_paid_all <= 0:

        student.payment_status = "unpaid"

    elif total_pending_all > 0:

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

    # COURSES

    results = StudentResult.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentResult.start_date.desc()
    ).all()

    # PAYMENTS

    payments = StudentPayment.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentPayment.payment_date.desc(),
        StudentPayment.id.desc()
    ).all()

    # REMARKS

    remarks = StudentRemark.query.filter_by(
        student_id=student.id
    ).order_by(
        StudentRemark.written_date.desc()
    ).all()

    # PAYMENT TOTALS

    total_payment = max(
        (payment.total_amount or 0)
        for payment in payments
    )

    total_received = sum(
        (payment.amount_received or 0)
        for payment in payments
    )

    total_pending = max(
        total_payment - total_received,
        0
    )

    # COURSE EDIT

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

        published_date=published_date
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