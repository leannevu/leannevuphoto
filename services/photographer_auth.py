"""Mailbox verification for the single-worker photographer workspace."""
import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
import threading
import time
from datetime import timedelta
from email.message import EmailMessage

from flask import jsonify, redirect, render_template, request, session, url_for

OWNER_EMAIL = 'leannevuphoto@gmail.com'


def send_code(code):
    host, user, password = (os.getenv(key) for key in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD'))
    if not all((host, user, password)):
        raise RuntimeError('Email delivery is not configured.')
    message = EmailMessage()
    message['From'], message['To'] = user, OWNER_EMAIL
    message['Subject'] = 'Your photographer workspace sign-in code'
    message.set_content(f'Your sign-in code is: {code}\n\nIt expires in 10 minutes. If you did not request it, ignore this email.')
    port = int(os.getenv('SMTP_PORT', '587'))
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    with server:
        if port != 465:
            server.starttls(context=context)
        server.login(user, password)
        server.send_message(message)


def register(app):
    app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_ENVIRONMENT_ID')),
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    lock = threading.Lock()
    challenges = {}
    last_send = [0.0]

    def authenticated():
        return session.get('photographer_email') == OWNER_EMAIL and session.get('photographer_until', 0) > time.time()

    @app.before_request
    def protect_workspace():
        path = request.path
        data = request.get_json(silent=True) if path == '/api/gallery' else None
        owner_gallery = isinstance(data, dict) and data.get('photographer_mode') is True
        protected = path == '/photographer' or path.startswith('/photographer/') or path.startswith('/api/photographer/') or owner_gallery
        if path == '/photographer/login':
            return None
        if protected and not authenticated():
            if path.startswith('/api/'):
                return jsonify(error='Sign in to the photographer workspace.', code='PHOTOGRAPHER_AUTH_REQUIRED'), 401
            return redirect(url_for('photographer_login'))
        if protected and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Please submit from this website.'), 403

    @app.after_request
    def private_workspace(response):
        if request.path.startswith(('/photographer', '/api/photographer')) or request.path == '/api/gallery':
            response.headers['Cache-Control'] = 'private, no-store'
        return response

    @app.route('/photographer/login', methods=['GET', 'POST'])
    def photographer_login():
        if authenticated():
            return redirect('/photographer')
        session.setdefault('login_csrf', secrets.token_urlsafe(32))
        error, notice = '', ''
        if request.method == 'POST':
            if not hmac.compare_digest(request.form.get('csrf', ''), session['login_csrf']):
                return render_template('photographer_login.html', error='Reload this page and try again.'), 400
            if request.form.get('action') == 'send':
                if request.form.get('email', '').strip().casefold() != OWNER_EMAIL:
                    error = 'This workspace is available only to Leanne’s photographer email.'
                else:
                    now = time.time()
                    with lock:
                        allowed = now - last_send[0] >= 60
                        if allowed:
                            last_send[0] = now
                            challenges.clear()
                            identifier, code = secrets.token_urlsafe(32), secrets.token_hex(6).upper()
                            challenges[identifier] = [hashlib.sha256(code.encode()).digest(), now + 600, 0]
                    if not allowed:
                        error = 'Please wait one minute before requesting another code.'
                    else:
                        try:
                            send_code(code)
                        except (OSError, RuntimeError, ValueError, smtplib.SMTPException):
                            with lock:
                                challenges.pop(identifier, None)
                            error = 'The sign-in email could not be sent. Please try again shortly.'
                        else:
                            session['login_challenge'] = identifier
                            notice = 'A sign-in code was sent to your email. It expires in 10 minutes.'
            elif request.form.get('action') == 'verify':
                identifier = session.get('login_challenge')
                code = request.form.get('code', '').strip().upper()
                with lock:
                    challenge = challenges.get(identifier)
                    valid = False
                    if challenge and challenge[1] > time.time() and challenge[2] < 5:
                        challenge[2] += 1
                        valid = hmac.compare_digest(challenge[0], hashlib.sha256(code.encode()).digest())
                    if valid or (challenge and (challenge[2] >= 5 or challenge[1] <= time.time())):
                        challenges.pop(identifier, None)
                if valid:
                    session.clear()
                    session.permanent = True
                    session['photographer_email'] = OWNER_EMAIL
                    session['photographer_until'] = time.time() + 8 * 3600
                    session['logout_csrf'] = secrets.token_urlsafe(32)
                    return redirect('/photographer')
                error = 'Invalid or expired code. Try again or request a new code.'
        return render_template('photographer_login.html', error=error, notice=notice)

    @app.post('/photographer/logout')
    def photographer_logout():
        if not hmac.compare_digest(request.form.get('csrf', ''), session.get('logout_csrf', 'missing')):
            return 'Invalid sign-out request.', 403
        session.clear()
        return redirect('/photographer/login')
