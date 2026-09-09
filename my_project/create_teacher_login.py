"""
Create a login for a teacher, linked to an existing Teacher
record - so they can log in and see only their own courses.

Run from the my_project directory, with your virtual
environment activated:

    python create_teacher_login.py

There's no in-app way to do this by design, same as
create_admin.py - user accounts are created directly by
whoever has access to the server/database, not through a
public form.
"""

import getpass

from app import create_app, db
from app.models import Teacher, User


def main():

    app = create_app()

    with app.app_context():

        teachers = Teacher.query.order_by(Teacher.name).all()

        if not teachers:
            print("No teachers exist yet - add one in Teachers' UI first.")
            return

        print("Teachers:")
        print()

        for teacher in teachers:

            existing_login = (
                teacher.user_account[0]
                if teacher.user_account
                else None
            )

            status = (
                f"already has login '{existing_login.username}'"
                if existing_login
                else "no login yet"
            )

            print(f"  {teacher.id}. {teacher.name} ({status})")

        print()

        teacher_id_input = input("Teacher number to create a login for: ").strip()

        if not teacher_id_input.isdigit():
            print("That's not a valid number. Nothing created.")
            return

        teacher = Teacher.query.get(int(teacher_id_input))

        if teacher is None:
            print("No teacher with that number. Nothing created.")
            return

        if teacher.user_account:
            print(
                f"'{teacher.name}' already has a login: "
                f"'{teacher.user_account[0].username}'. Nothing created."
            )
            return

        username = input("Username for this login: ").strip()

        if not username:
            print("Username can't be empty. Nothing created.")
            return

        if User.query.filter_by(username=username).first():
            print(f"A user named '{username}' already exists.")
            return

        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")

        if password != confirm:
            print("Passwords didn't match. Nothing created.")
            return

        if len(password) < 8:
            print("Password should be at least 8 characters. Nothing created.")
            return

        user = User(
            username=username,
            role="teacher",
            teacher_id=teacher.id
        )

        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        print()
        print(f"Created login '{username}' for {teacher.name}.")


if __name__ == "__main__":
    main()