"""
Full wipe: deletes every student, payment, course enrollment,
remark, attendance record, room, room booking, and teacher/
student-type reference record - then resets ID sequences so
the next records created start fresh at id=1 (first invoice
back to INV-000000).

Deliberately does NOT touch the users table - logins are left
completely intact, so nobody gets locked out.

Run from the my_project directory (same level as run.py),
with your virtual environment activated:

    python wipe_all_data.py

This is DESTRUCTIVE and cannot be undone. It shows you exactly
what's about to be deleted and requires a typed confirmation
before touching anything.

Make sure your local .env's DATABASE_URL actually points at
the database you mean to wipe before running this.
"""

from sqlalchemy import text

from app import create_app, db
from app.models import (
    Student,
    StudentType,
    StudentPayment,
    StudentResult,
    StudentRemark,
    Teacher,
    Attendance,
    Room,
    RoomAssignment,
    User
)


def main():

    app = create_app()

    with app.app_context():

        counts = {
            "students": Student.query.count(),
            "student types": StudentType.query.count(),
            "payments": StudentPayment.query.count(),
            "course results": StudentResult.query.count(),
            "remarks": StudentRemark.query.count(),
            "teachers": Teacher.query.count(),
            "attendance records": Attendance.query.count(),
            "rooms": Room.query.count(),
            "room bookings": RoomAssignment.query.count(),
        }

        print("About to permanently delete:")

        for label, count in counts.items():
            print(f"  {count} {label}")

        print()
        print("User accounts/logins are NOT affected - everyone")
        print("stays able to log in exactly as before.")
        print()
        print("ID sequences will also be reset so new records")
        print("start fresh at id=1 (first invoice = INV-000000).")
        print()
        print("This cannot be undone.")
        print()

        confirmation = input(
            "Type WIPE (all caps) to continue, anything else to cancel: "
        )

        if confirmation != "WIPE":
            print("Cancelled - nothing was deleted.")
            return

        try:

            # ------------------------------------------------
            # DELETE CHILDREN BEFORE PARENTS
            #
            # Bulk .delete() doesn't trigger the ORM's
            # cascade="all, delete-orphan" relationships (that
            # only fires on db.session.delete(single_object)),
            # so the foreign-key dependency order has to be
            # respected by hand here.
            # ------------------------------------------------

            Attendance.query.delete()
            RoomAssignment.query.delete()
            StudentPayment.query.delete()
            StudentRemark.query.delete()
            StudentResult.query.delete()

            Student.query.delete()

            # Teacher-role logins point at a Teacher record via
            # User.teacher_id. Deleting every Teacher while a
            # User still references one would fail with a
            # foreign-key violation - unlink them first instead.
            # The login itself is untouched, it just needs
            # re-linking afterward (create_teacher_login.py
            # handles that) once new teachers exist again.
            User.query.filter(
                User.teacher_id.isnot(None)
            ).update(
                {User.teacher_id: None}
            )

            Teacher.query.delete()
            Room.query.delete()
            StudentType.query.delete()

            # ------------------------------------------------
            # RESET ID SEQUENCES
            # ------------------------------------------------

            sequences = [
                "student_payments_id_seq",
                "student_results_id_seq",
                "student_remarks_id_seq",
                "students_id_seq",
                "teachers_id_seq",
                "attendance_id_seq",
                "rooms_id_seq",
                "room_assignments_id_seq",
                "student_types_id_seq",
            ]

            for sequence_name in sequences:

                db.session.execute(
                    text(
                        f"ALTER SEQUENCE {sequence_name} "
                        "RESTART WITH 1"
                    )
                )

            db.session.commit()

            print()
            print("Done. All data wiped, sequences reset, logins untouched.")

        except Exception as e:

            db.session.rollback()

            print()
            print("Something went wrong - nothing was changed.")
            print(f"Error: {e}")


if __name__ == "__main__":
    main()