"""
Remove test data created by seed_test_data.py.

Run from the my_project directory, with your virtual
environment activated:

    python remove_test_data.py

Only removes students whose student_id starts with "TEST-" -
every student seed_test_data.py creates is tagged that way, so
real students are never touched regardless of how many times
the seed script has been run. Shows exactly what it's about to
delete and requires a typed confirmation first.
"""

from app import create_app, db
from app.models import Student


def main():

    app = create_app()

    with app.app_context():

        test_students = Student.query.filter(
            Student.student_id.like("TEST-%")
        ).all()

        if not test_students:
            print("No test students found - nothing to remove.")
            return

        print(f"Found {len(test_students)} test student(s):")
        print()

        for student in test_students:
            print(f"  {student.student_id} - {student.full_name}")

        print()
        print(
            "This will also remove each one's payments, course "
            "enrollments, and attendance records."
        )
        print()

        confirmation = input(
            "Type DELETE (all caps) to remove these, "
            "anything else to cancel: "
        )

        if confirmation != "DELETE":
            print("Cancelled - nothing was deleted.")
            return

        try:

            for student in test_students:

                # db.session.delete(), not a bulk Student.query.delete() -
                # this goes through the ORM, so the cascade="all,
                # delete-orphan" relationships actually fire: each
                # student's payments, course enrollments, remarks, and
                # (via the enrollment's own cascade) attendance records
                # all get removed automatically, without listing every
                # table by hand.
                db.session.delete(student)

            db.session.commit()

            print()
            print(
                f"Removed {len(test_students)} test student(s) and "
                "all their related records."
            )

        except Exception as e:

            db.session.rollback()

            print()
            print("Something went wrong - nothing was deleted.")
            print(f"Error: {e}")


if __name__ == "__main__":
    main()