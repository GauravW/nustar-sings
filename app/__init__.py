"""
Application factory for the NuSTAR SINGS review interface.
Author: Kush Rao

Note:
- Loads configuration, validates source schemas, and registers candidate and observation routes.
- Shares classification labels and profile information with the page templates.
- Closes the request database connection after each request.
- Application startup does not change pipeline schemas or import scientific data.
"""

from flask import Flask, g

from .constants import TAGS
from .db import validate_sources
from .config import load_settings


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(test_config if test_config is not None else load_settings(require_secret=True))
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    validate_sources(app.config)

    @app.teardown_appcontext
    def close_database(error):
        connection = g.pop('db', None)
        if connection is not None:
            connection.close()

    @app.context_processor
    def common_values():
        return {
            'tags': TAGS,
            'reviewer': 'Kush Rao',
            'reviewer_email': 'mr.kushrao@gmail.com',
        }

    from .routes import register_routes
    register_routes(app)
    from .observations import observations
    app.register_blueprint(observations)
    return app
