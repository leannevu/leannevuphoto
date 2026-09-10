"""Activity APIs; monitoring follows the direct-access photographer workspace."""
from flask import Blueprint, jsonify, request, render_template
from . import activity, database


def register(app, client_folder_or_error, selected_folder):
    routes = Blueprint('activity', __name__)

    @routes.get('/photographer/activity')
    def page():
        return render_template('activity.html')

    @routes.post('/api/activity/start')
    def start():
        if not database.enabled():
            return jsonify(error='Activity tracking requires PostgreSQL.'), 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Provide gallery visit details.'), 400
        email, access, error = client_folder_or_error(data)
        if error:
            return error
        url = selected_folder(access, data.get('gallery_id'))
        if not url:
            return jsonify(error='Choose a gallery.'), 400
        try:
            return jsonify(visit_id=activity.start(data.get('visit_id'), email, url))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except RuntimeError:
            return jsonify(error='Activity tracking is temporarily unavailable.'), 503

    @routes.post('/api/activity/heartbeat')
    def heartbeat():
        if not database.enabled():
            return jsonify(error='Activity tracking requires PostgreSQL.'), 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error='Provide visit timing details.'), 400
        sequence = data.get('sequence')
        if (type(sequence) is not int or not 0 < sequence < 2**53
                or type(data.get('active')) is not bool or type(data.get('ended')) is not bool):
            return jsonify(error='Invalid visit timing details.'), 400
        try:
            activity.heartbeat(data.get('visit_id'), sequence, data['active'], data['ended'])
            return jsonify(ok=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except RuntimeError:
            return jsonify(error='Activity tracking is temporarily unavailable.'), 503

    @routes.get('/api/photographer/activity')
    def overview():
        if not database.enabled():
            return jsonify(error='Activity tracking requires PostgreSQL.'), 503
        try:
            return jsonify(activity.overview())
        except RuntimeError:
            return jsonify(error='Activity is unavailable. Please try again shortly.'), 503

    @routes.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    app.register_blueprint(routes)
