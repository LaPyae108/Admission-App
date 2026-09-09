"""
Seed the database with realistic test data - students, course
enrollments, payments, teachers, and attendance history - all
dated relative to today, so it stays "recent" no matter when
this is run.

Run from the my_project directory, with your virtual
environment activated:

    python seed_test_data.py

This only ADDS data - it never deletes anything, so it's safe
to run against a database that already has real records. Run
it more than once and you'll just get more test students, not
duplicates of the same ones (student_id includes a timestamp
to guarantee that).
"""

from datetime import date, timedelta
import random

from app import create_app, db
from app.models import (
    Student,
    StudentType,
    StudentResult,
    Teacher,
    Attendance
)
from app.views import (
    create_payment_with_invoice_id,
    calculate_discount,
    to_decimal
)


FIRST_NAMES = [
    "Aye", "Hla", "Kyaw", "Mya", "Nandar", "Thiri", "Zaw",
    "Su", "Htet", "Wai", "Mon", "Thura"
]

LAST_NAMES = [
    "Aung", "Win", "Htun", "Naing", "Oo", "Thant", "Lwin",
    "Soe", "Myint", "San"
]

COURSES = [
    {
        "course_name": "Business English",
        "course_id": "ENG-201"
    },
    {
        "course_name": "Web Development Fundamentals",
        "course_id": "DEV-301"
    }
]

TEACHER_NAMES = [
    "Daw Hla Hla",
    "U Kyaw Zin",
    "Saya Thiri Nandar",
    "Saya Min Thu"
]

STUDENT_TYPE_NAMES = [
    "Regular",
    "VIP",
    "Scholarship"
]

ACCOUNTS = [
    "Cash", "KBZ-Bank", "AYA-Pay", "K-Pay", "Wave-Pay"
]


def get_or_create_student_types():

    types = []

    for name in STUDENT_TYPE_NAMES:

        student_type = StudentType.query.filter_by(name=name).first()

        if not student_type:

            student_type = StudentType(name=name)

            db.session.add(student_type)
            db.session.flush()

        types.append(student_type)

    return types


def get_or_create_teachers():

    teachers = []

    for name in TEACHER_NAMES:

        teacher = Teacher.query.filter_by(name=name).first()

        if not teacher:

            teacher = Teacher(name=name)

            db.session.add(teacher)
            db.session.flush()

        teachers.append(teacher)

    return teachers


def random_name():

    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def create_student(index, student_type, run_id):

    today = date.today()

    # run_id keeps student_id unique across multiple runs of
    # this script, so re-running it never collides with an
    # earlier seeded batch.
    student_id = f"TEST-{run_id}-{index:03d}"

    student = Student(
        student_id=student_id,
        full_name=random_name(),
        phone_number=f"09{random.randint(100000000, 999999999)}",
        nrc=f"{random.randint(1,14)}/AhMaNa(N){random.randint(100000,999999)}",
        date_of_birth=today - timedelta(days=random.randint(6500, 10000)),
        father_name=random_name(),
        education="High School Graduate",
        address="Yangon, Myanmar",
        currency=random.choice(["MMK", "MMK", "MMK", "USD"]),
        intake_date=today - timedelta(days=random.randint(1, 30)),
        status="pending",
        payment_status="unpaid",
        student_type_id=student_type.id,
        uniform_size=random.choice(["S", "M", "L", "XL"])
    )

    db.session.add(student)
    db.session.flush()

    return student


def create_enrollment_with_payment(student, course, teacher):

    today = date.today()

    start_date = today - timedelta(days=random.randint(10, 25))
    end_date = start_date + timedelta(days=60)

    enrollment = StudentResult(
        student_id=student.id,
        course_name=course["course_name"],
        course_id=course["course_id"],
        teacher_id=teacher.id,
        start_date=start_date,
        end_date=end_date,
        result=None,
        collected=False
    )

    db.session.add(enrollment)
    db.session.flush()

    # --------------------------------------------------------
    # PAYMENT
    #
    # Randomly simulate paid / partial / unpaid so the
    # dashboard and reports have a realistic mix to show.
    # --------------------------------------------------------

    total_amount = to_decimal(
        random.choice([300000, 450000, 600000, 150])
        if student.currency == "MMK"
        else random.choice([80, 120, 150])
    )

    discount_type = random.choice(["none", "percentage", "promotion"])
    discount = to_decimal(10) if discount_type == "percentage" else to_decimal(0)
    promotion_amount = (
        to_decimal(20000) if discount_type == "promotion" else to_decimal(0)
    )

    discount_amount, total_after_discount = calculate_discount(
        total_amount, discount_type, discount, promotion_amount
    )

    payment_fraction = random.choice([1, 1, 0.5, 0])
    amount_paid = (total_after_discount * to_decimal(payment_fraction)).quantize(
        to_decimal("0.01")
    )

    pending_amount = max(total_after_discount - amount_paid, to_decimal(0))

    payment = create_payment_with_invoice_id(
        student_id=student.id,
        payment_date=start_date,
        total_amount=total_amount,
        discount_type=discount_type,
        discount=discount,
        promotion_amount=promotion_amount,
        discount_amount=discount_amount,
        total_after_discount=total_after_discount,
        payment_currency=student.currency,
        exchange_enabled=False,
        exchange_rate=None,
        amount_paid=amount_paid,
        amount_received=amount_paid,
        current_receivable=total_after_discount,
        pending_amount=pending_amount,
        comment="Seeded test payment",
        account=random.choice(ACCOUNTS)
    )

    if amount_paid <= 0:
        student.payment_status = "unpaid"
    elif pending_amount > 0:
        student.payment_status = "partial"
    else:
        student.payment_status = "paid"

    return enrollment


def create_recent_attendance(enrollment):

    # Attendance for the last 10 weekdays - gives the
    # Attendance overview page and per-student rate something
    # real to show immediately.

    today = date.today()

    days_created = 0
    days_back = 0

    while days_created < 10:

        days_back += 1

        check_date = today - timedelta(days=days_back)

        if check_date.weekday() >= 5:
            continue

        status = random.choices(
            ["present", "absent", "late"],
            weights=[75, 15, 10]
        )[0]

        record = Attendance(
            student_result_id=enrollment.id,
            date=check_date,
            status=status
        )

        db.session.add(record)

        days_created += 1


def main():

    app = create_app()

    with app.app_context():

        run_id = date.today().strftime("%Y%m%d")

        print("Seeding test data...")

        student_types = get_or_create_student_types()
        teachers = get_or_create_teachers()

        for index in range(1, 11):

            student = create_student(
                index,
                random.choice(student_types),
                run_id
            )

            course = COURSES[index % len(COURSES)]
            teacher = teachers[index % len(teachers)]

            enrollment = create_enrollment_with_payment(
                student, course, teacher
            )

            create_recent_attendance(enrollment)

            print(f"  Created {student.student_id} - {student.full_name}")

        db.session.commit()

        print()
        print("Done. Added 10 test students with course enrollments,")
        print("payments, and 10 days of attendance history each.")


if __name__ == "__main__":
    main()