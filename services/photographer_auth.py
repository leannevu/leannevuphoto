"""Email-based workspace routing; mailbox ownership is not verified."""
import os
import secrets
import time
from datetime import timedelta

from flask import jsonify, redirect, request, session

OWNER_EMAIL = 'leannevuphoto@gmail.com'


def open_workspace(email):
    if str(email).strip().casefold() != OWNER_EMAIL:
        return None
    session.clear()
    session.permanent = True
    session['photographer_email'] = OWNER_EMAIL
    session['photographer_until'] = time.time() + 8 * 3600
    return jsonify(redirect='/photographer')


def register(app):
    app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_ENVIRONMENT_ID')),
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    def authenticated():
        return session.get('photographer_email') == OWNER_EMAIL and session.get('photographer_until', 0) > time.time()

    @app.before_request
    def protect_workspace():
        path = request.path
        data = request.get_json(silent=True) if path == '/api/gallery' else None
        owner_gallery = isinstance(data, dict) and data.get('photographer_mode') is True
        protected = path == '/photographer' or path.startswith('/photographer/') or path.startswith('/api/photographer/') or owner_gallery
        if path == '/photographer/login':
            return redirect('/')
        if protected and not authenticated():
            if path.startswith('/api/'):
                return jsonify(error='Enter your photographer email on the home page.', code='PHOTOGRAPHER_AUTH_REQUIRED'), 401
            return redirect('/')
        if protected and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Please submit from this website.'), 403

    @app.after_request
    def private_workspace(response):
        if request.path.startswith(('/photographer', '/api/photographer')) or request.path == '/api/gallery':
            response.headers['Cache-Control'] = 'private, no-store'
        return response
