from app import db

from datetime import datetime

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from flask_login import UserMixin


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

    date_of_birth = db.Column(
        db.Date,
        nullable=True
    )

    father_name = db.Column(
        db.String(100),
        nullable=True
    )

    education = db.Column(
        db.String(150),
        nullable=True
    )

    address = db.Column(
        db.Text,
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
        db.ForeignKey("student_types.id"),
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

    currency = db.Column(
        db.String(10),
        nullable=False,
        default="MMK"
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
# TEACHER
# ============================================================

class Teacher(db.Model):

    __tablename__ = "teachers"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(100),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    # Every course-enrollment row (StudentResult) currently
    # assigned to this teacher. One teacher can be assigned to
    # many students' course rows, across many different courses.
    course_enrollments = db.relationship(
        "StudentResult",
        back_populates="teacher"
    )


# ============================================================
# STUDENT RESULT
# ============================================================

class StudentResult(db.Model):

    __tablename__ = "student_results"

    id = db.Column(
        db.Integer,
        unique=True,
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

    # --------------------------------------------------------
    # ASSIGNED TEACHER
    #
    # Nullable: a course can exist with no teacher assigned
    # yet. Assigning a teacher to one student's course row
    # applies it to every student enrolled in that same
    # course_id (see assign_teacher() in views.py) - a course
    # has one teacher, not a different one per student.
    # --------------------------------------------------------

    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("teachers.id"),
        nullable=True
    )

    teacher = db.relationship(
        "Teacher",
        back_populates="course_enrollments"
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

    collected = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    student = db.relationship(
        "Student",
        back_populates="results"
    )

    attendance_records = db.relationship(
        "Attendance",
        back_populates="student_result",
        cascade="all, delete-orphan"
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

    # --------------------------------------------------------
    # PAYMENT INFORMATION
    # --------------------------------------------------------

    invoice_id = db.Column(
        db.String(30),
        unique=True,
        nullable=False
    )

    payment_date = db.Column(
        db.Date
    )

    # --------------------------------------------------------
    # ORIGINAL COURSE PRICE
    # --------------------------------------------------------

    total_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # --------------------------------------------------------
    # DISCOUNT SYSTEM
    #
    # discount_type:
    #     "percentage"
    #     "promotion"
    #
    # discount:
    #     percentage value
    #
    # promotion_amount:
    #     fixed cash promotion
    #
    # discount_amount:
    #     actual cash amount deducted
    # --------------------------------------------------------

    discount_type = db.Column(
        db.String(20),
        nullable=False,
        default="percentage"
    )

    discount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    promotion_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    discount_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # --------------------------------------------------------
    # FINAL COURSE PRICE
    # --------------------------------------------------------

    total_after_discount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # --------------------------------------------------------
    # PAYMENT
    # --------------------------------------------------------

    amount_received = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    current_receivable = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    pending_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # --------------------------------------------------------
    # PAYMENT METHOD
    # --------------------------------------------------------

    account = db.Column(
        db.String(50),
        nullable=True
    )

    # --------------------------------------------------------
    # PAYMENT CURRENCY / EXCHANGE
    # --------------------------------------------------------

    payment_currency = db.Column(
        db.String(10),
        nullable=False,
        default="MMK"
    )

    exchange_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    exchange_rate = db.Column(
        db.Numeric(12, 4),
        nullable=True
    )

    # Actual amount paid by customer in their payment currency
    amount_paid = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # --------------------------------------------------------
    # OTHER
    # --------------------------------------------------------

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


# ============================================================
# ATTENDANCE
#
# Tied to a specific course enrollment (StudentResult), not
# just the student generally - a student can be enrolled in
# several courses that meet on different days, so attendance
# has to be per-course, not one blanket record per student.
# ============================================================

class Attendance(db.Model):

    __tablename__ = "attendance"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    student_result_id = db.Column(
        db.Integer,
        db.ForeignKey("student_results.id"),
        nullable=False
    )

    date = db.Column(
        db.Date,
        nullable=False
    )

    # "present" / "absent" / "late"
    status = db.Column(
        db.String(10),
        nullable=False
    )

    recorded_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    student_result = db.relationship(
        "StudentResult",
        back_populates="attendance_records"
    )

    __table_args__ = (

        # One attendance record per enrollment per day -
        # marking the same student twice for the same date
        # updates the existing record instead of duplicating.
        db.UniqueConstraint(
            "student_result_id",
            "date",
            name="uq_attendance_student_result_date"
        ),

    )


# ============================================================
# USER
#
# UserMixin (from Flask-Login) supplies is_authenticated,
# is_active, is_anonymous, and get_id() automatically, based
# on this class's own "id" column - nothing extra to write.
# ============================================================

class User(db.Model, UserMixin):

    __tablename__ = "users"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    username = db.Column(
        db.String(50),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    # "admin", "staff", or "teacher". Admins can delete
    # records; staff can do everything else (add/edit students,
    # payments, courses, attendance) but not delete; teachers
    # can only see and take attendance for their OWN courses -
    # see teacher_id below, and TEACHER_ALLOWED_ENDPOINTS in
    # views.py.
    role = db.Column(
        db.String(20),
        nullable=False,
        default="staff"
    )

    # Only set when role == "teacher" - links this login to a
    # specific Teacher record, so we know which courses this
    # person is allowed to see. Nullable because admin/staff
    # logins have no associated Teacher, and because not every
    # Teacher record needs a login at all.
    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("teachers.id"),
        nullable=True
    )

    teacher = db.relationship(
        "Teacher",
        backref="user_account"
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    def set_password(self, password):

        self.password_hash = generate_password_hash(
            password
        )

    def check_password(self, password):

        return check_password_hash(
            self.password_hash,
            password
        )

    def is_admin(self):

        return self.role == "admin"

    def is_teacher(self):

        return self.role == "teacher"


# ============================================================
# ROOM
#
# A physical teaching space. Kept separate from RoomAssignment
# below - a Room exists once and gets reused across many days,
# while a RoomAssignment is one specific room-course-date
# booking.
# ============================================================

class Room(db.Model):

    __tablename__ = "rooms"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(50),
        unique=True,
        nullable=False
    )

    capacity = db.Column(
        db.String(100),
        nullable=True
    )

    notes = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    assignments = db.relationship(
        "RoomAssignment",
        back_populates="room",
        cascade="all, delete-orphan"
    )


# ============================================================
# ROOM ASSIGNMENT
#
# One room, booked for one course, on one day. course_id is a
# free-text string (not a foreign key) for the same reason
# StudentResult.course_id is - there's no separate Course
# table, courses only exist as the course_id/course_name
# repeated across StudentResult rows (see get_course_summaries
# in views.py).
# ============================================================

class RoomAssignment(db.Model):

    __tablename__ = "room_assignments"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    room_id = db.Column(
        db.Integer,
        db.ForeignKey("rooms.id"),
        nullable=False
    )

    course_id = db.Column(
        db.String(50),
        nullable=False
    )

    date = db.Column(
        db.Date,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    room = db.relationship(
        "Room",
        back_populates="assignments"
    )

    __table_args__ = (

        # No double-booking: a room can only be assigned to ONE
        # course per day. This is the database-level backstop -
        # the view also checks this explicitly first, so the
        # person gets a clear message instead of a raw
        # constraint-violation error.
        db.UniqueConstraint(
            "room_id",
            "date",
            name="uq_room_assignment_room_date"
        ),

    )