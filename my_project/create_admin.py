"""
Create the first admin user (or any user, admin or staff).

Run from the my_project directory, with your virtual
environment activated:

    python create_admin.py

There's no public sign-up page by design - this is the only
way to create a login for this app, run directly by whoever
has access to the server/database.
"""

import getpass

from app import create_app, db
from app.models import User


def main():

    app = create_app()

    with app.app_context():

        username = input("Username: ").strip()

        if not username:
            print("Username can't be empty. Nothing created.")
            return

        if User.query.filter_by(username=username).first():
            print(f"A user named '{username}' already exists.")
            return

        # getpass hides the input instead of echoing it to the
        # terminal - same reason a browser password field shows
        # dots instead of the real characters.
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")

        if password != confirm:
            print("Passwords didn't match. Nothing created.")
            return

        if len(password) < 8:
            print("Password should be at least 8 characters. Nothing created.")
            return

        role = input("Role (admin/staff) [admin]: ").strip().lower()

        if role not in ("admin", "staff"):
            role = "admin"

        user = User(
            username=username,
            role=role
        )

        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        print()
        print(f"Created {role} user '{username}'.")


if __name__ == "__main__":
    main()