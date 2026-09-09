from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()


def create_app():

    app = Flask(__name__)

    app.config.from_object("config")

    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)

    # Where @login_required (and our own before_request check)
    # sends someone who isn't logged in.
    login_manager.login_view = "main.login"

    from app.views import main

    app.register_blueprint(main)

    return app


@login_manager.user_loader
def load_user(user_id):

    # Imported here, not at the top of the file, to avoid a
    # circular import - app/models.py imports db from this
    # same file.
    from app.models import User

    return User.query.get(int(user_id))