"""
One-time script: wipe test data and reset ID sequences so the
next payment created gets invoice number INV-000000.

Run from the my_project directory (same level as run.py),
with your virtual environment activated:

    python reset_test_data.py

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
    StudentPayment,
    StudentResult,
    StudentRemark
)


def main():

    app = create_app()

    with app.app_context():

        student_count = Student.query.count()
        payment_count = StudentPayment.query.count()
        result_count = StudentResult.query.count()
        remark_count = StudentRemark.query.count()

        print("About to permanently delete:")
        print(f"  {student_count} students")
        print(f"  {payment_count} payments")
        print(f"  {result_count} course results")
        print(f"  {remark_count} remarks")
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

            # Delete children first - foreign keys require this order
            StudentPayment.query.delete()
            StudentResult.query.delete()
            StudentRemark.query.delete()
            Student.query.delete()

            # Reset the auto-increment counters so new records
            # start at id=1 again instead of continuing from
            # wherever the old test data left off.
            db.session.execute(
                text(
                    "ALTER SEQUENCE student_payments_id_seq "
                    "RESTART WITH 1"
                )
            )

            db.session.execute(
                text(
                    "ALTER SEQUENCE student_results_id_seq "
                    "RESTART WITH 1"
                )
            )

            db.session.execute(
                text(
                    "ALTER SEQUENCE student_remarks_id_seq "
                    "RESTART WITH 1"
                )
            )

            db.session.execute(
                text(
                    "ALTER SEQUENCE students_id_seq "
                    "RESTART WITH 1"
                )
            )

            db.session.commit()

            print()
            print("Done. All test data wiped and ID sequences reset.")

        except Exception as e:

            db.session.rollback()

            print()
            print("Something went wrong - nothing was changed.")
            print(f"Error: {e}")


if __name__ == "__main__":
    main()