"""
Local entry point for the NuSTAR SINGS review interface.
Author: Kush Rao

Note:
- Creates the Flask application using the configured database and product paths.
- Runs the local development server on 127.0.0.1:5001 when executed directly.
- Does not run pipeline searches or initialize review tables.
"""
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001, debug=False)
