from app import db
from datetime import datetime


# ============================================================
# STUDENT TYPE
# ============================================================

class StudentType(db.Model):

    __tablename__ = "student_types"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(50),
        unique=True,
        nullable=False
    )

    students = db.relationship(
        "Student",
        back_populates="student_type"
    )


# ============================================================
# STUDENT
# ============================================================

class Student(db.Model):

    __tablename__ = "students"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    student_id = db.Column(
        db.String(20),
        unique=True,
        nullable=False
    )

    full_name = db.Column(
        db.String(100),
        nullable=False
    )

    phone_number = db.Column(
        db.String(100),
        nullable=True
    )

    nrc = db.Column(
        db.String(50),
        nullable=True
    )

    intake_date = db.Column(
        db.Date,
        nullable=True
    )

    status = db.Column(
        db.String(20),
        default="pending"
    )

    payment_status = db.Column(
        db.String(20),
        default="unpaid"
    )

    student_type_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "student_types.id"
        ),
        nullable=False
    )

    student_type = db.relationship(
        "StudentType",
        back_populates="students"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    uniform_size = db.Column(
        db.String(20)
    )

    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    results = db.relationship(
        "StudentResult",
        back_populates="student",
        cascade="all, delete-orphan"
    )

    payments = db.relationship(
        "StudentPayment",
        back_populates="student",
        cascade="all, delete-orphan"
    )

    remarks = db.relationship(
        "StudentRemark",
        back_populates="student",
        cascade="all, delete-orphan"
    )


# ============================================================
# STUDENT RESULT
# ============================================================

class StudentResult(db.Model):

    __tablename__ = "student_results"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id"),
        nullable=False
    )

    course_name = db.Column(
        db.String(150),
        nullable=False
    )

    course_id = db.Column(
        db.String(50),
        nullable=False
    )

    start_date = db.Column(
        db.Date
    )

    end_date = db.Column(
        db.Date
    )

    result = db.Column(
        db.String(100)
    )

    published_date = db.Column(
        db.Date
    )

    student = db.relationship(
        "Student",
        back_populates="results"
    )


# ============================================================
# STUDENT PAYMENT
# ============================================================

class StudentPayment(db.Model):

    __tablename__ = "student_payments"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id"),
        nullable=False
    )

    voucher_id = db.Column(
        db.String(50),
        nullable=True
    )

    payment_date = db.Column(
        db.Date
    )

    total_amount = db.Column(
        db.Numeric(12, 2),
        default=0
    )

    amount_received = db.Column(
        db.Numeric(12, 2),
        default=0
    )

    pending_amount = db.Column(
        db.Numeric(12, 2),
        default=0
    )
    comment = db.Column(
    db.Text,
    nullable=True
                )

    student = db.relationship(
        "Student",
        back_populates="payments"
    )


# ============================================================
# STUDENT REMARK
# ============================================================

class StudentRemark(db.Model):

    __tablename__ = "student_remarks"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id"),
        nullable=False
    )

    text = db.Column(
        db.Text,
        nullable=False
    )

    written_date = db.Column(
        db.Date,
        default=datetime.utcnow
    )

    student = db.relationship(
        "Student",
        back_populates="remarks"
    )