from flask import Flask, render_template
from .extensions import db, login_manager
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import timedelta

def create_app():
    app = Flask(__name__, instance_relative_config=True)

    # PostgreSQL Configuration
    app.config.from_mapping(
        SECRET_KEY='your-secret-key',
        SQLALCHEMY_DATABASE_URI='postgresql://sonde_user:securepassword@localhost/weather_sonde',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        REMEMBER_COOKIE_DURATION=timedelta(minutes=5),  # Set the remember duration for 5 minutes
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=5)  # Force logout after 5 minutes of inactivity
    )

    # Initialize Extensions
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    migrate = Migrate(app, db)

    # Register Blueprints
    from . import routes
    app.register_blueprint(routes.bp)

    # User Loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))

    # Error Handlers
    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('500.html'), 500

    return app
